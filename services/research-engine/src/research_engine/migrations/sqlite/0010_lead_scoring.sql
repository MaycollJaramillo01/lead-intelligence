-- 0010 · ICP profiles, scoring models, lead scores and outcomes, data contract v1 (phase 11).
--
-- Phase 03 reserved the scoring tables; nothing ever wrote them. This migration shapes them for
-- scores that can be explained, reproduced and compared without rewriting history:
--
--   * icp_profiles and scoring_models gain the version that is active and an archive mark; their
--     versions stay immutable and become permanent;
--   * score_runs: one ICP version and one scoring version over a project, or over the facts another
--     run stored (re-scoring the same facts with other versions), with the engine version;
--   * business_scores: the tier, total and confidence of one business in one run, the facts the
--     engine read (ICP independent) and the complete scorecard; component_scores keep each
--     component's value, weight, contribution and coverage for lists and distributions. Both are
--     immutable and permanent. score_explanations and explanation_kind are dropped: reasons, gates,
--     risks, missing data and their evidence live in the scorecard;
--   * lead_outcomes: what happened when a lead was worked, the feedback loop for calibration.
--     Immutable and permanent; the business's lead_stage follows its state machine.

CREATE TABLE lead_tier (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO lead_tier VALUES ('priority'), ('review'), ('low'), ('insufficient_data'), ('excluded');

-- ============================================================================ scores (rebuilt)

DROP TABLE score_explanations;
DROP TABLE explanation_kind;
DROP TABLE component_scores;
DROP TABLE business_scores;
DROP TABLE score_runs;
DROP TABLE scoring_model_versions;

CREATE TABLE scoring_model_versions (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    scoring_model_id TEXT NOT NULL REFERENCES scoring_models (id),
    version INTEGER NOT NULL CHECK (version >= 1),
    definition_json TEXT NOT NULL CHECK (json_valid(definition_json)),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    is_heuristic INTEGER NOT NULL CHECK (is_heuristic IN (0, 1)),
    created_by TEXT NOT NULL CHECK (length(created_by) >= 1),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    UNIQUE (scoring_model_id, version)
) STRICT;

CREATE TRIGGER scoring_model_versions_immutable BEFORE UPDATE ON scoring_model_versions
BEGIN SELECT RAISE(ABORT, 'scoring_model_versions are immutable'); END;
CREATE TRIGGER scoring_model_versions_permanent BEFORE DELETE ON scoring_model_versions
BEGIN SELECT RAISE(ABORT, 'scoring_model_versions are history: they are never deleted'); END;
CREATE TRIGGER icp_profile_versions_permanent BEFORE DELETE ON icp_profile_versions
BEGIN SELECT RAISE(ABORT, 'icp_profile_versions are history: they are never deleted'); END;

ALTER TABLE scoring_models ADD COLUMN active_version_id TEXT
    REFERENCES scoring_model_versions (id);
ALTER TABLE scoring_models ADD COLUMN archived_at TEXT
    CHECK (archived_at IS strftime('%Y-%m-%dT%H:%M:%fZ', archived_at));
ALTER TABLE icp_profiles ADD COLUMN active_version_id TEXT REFERENCES icp_profile_versions (id);
ALTER TABLE icp_profiles ADD COLUMN archived_at TEXT
    CHECK (archived_at IS strftime('%Y-%m-%dT%H:%M:%fZ', archived_at));

-- the active version of a model or profile is one of its own versions
CREATE TRIGGER scoring_models_active_version BEFORE UPDATE OF active_version_id ON scoring_models
WHEN NEW.active_version_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM scoring_model_versions v
    WHERE v.id = NEW.active_version_id AND v.scoring_model_id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'the active version must belong to its scoring model'); END;
CREATE TRIGGER icp_profiles_active_version BEFORE UPDATE OF active_version_id ON icp_profiles
WHEN NEW.active_version_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM icp_profile_versions v
    WHERE v.id = NEW.active_version_id AND v.icp_profile_id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'the active version must belong to its ICP profile'); END;

CREATE TABLE score_runs (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    project_id TEXT NOT NULL REFERENCES projects (id),
    job_id TEXT REFERENCES jobs (id),
    scoring_model_version_id TEXT NOT NULL REFERENCES scoring_model_versions (id),
    icp_profile_version_id TEXT NOT NULL REFERENCES icp_profile_versions (id),
    -- the run whose stored facts this one scores again; null when facts were read from the store
    facts_from_run_id TEXT REFERENCES score_runs (id),
    engine_version TEXT NOT NULL CHECK (length(engine_version) BETWEEN 1 AND 64),
    started_at TEXT NOT NULL CHECK (started_at IS strftime('%Y-%m-%dT%H:%M:%fZ', started_at)),
    finished_at TEXT CHECK (finished_at IS strftime('%Y-%m-%dT%H:%M:%fZ', finished_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    CHECK (facts_from_run_id IS NOT id)
) STRICT;
CREATE INDEX score_runs_by_project ON score_runs (project_id, created_at);
CREATE UNIQUE INDEX score_runs_one_per_job ON score_runs (job_id) WHERE job_id IS NOT NULL;

CREATE TRIGGER score_runs_fixed_request BEFORE UPDATE ON score_runs
WHEN NEW.id IS NOT OLD.id OR NEW.project_id IS NOT OLD.project_id OR NEW.job_id IS NOT OLD.job_id
    OR NEW.scoring_model_version_id IS NOT OLD.scoring_model_version_id
    OR NEW.icp_profile_version_id IS NOT OLD.icp_profile_version_id
    OR NEW.facts_from_run_id IS NOT OLD.facts_from_run_id
    OR NEW.engine_version IS NOT OLD.engine_version OR NEW.started_at IS NOT OLD.started_at
    OR NEW.created_at IS NOT OLD.created_at
    OR (OLD.finished_at IS NOT NULL AND NEW.finished_at IS NOT OLD.finished_at)
BEGIN SELECT RAISE(ABORT, 'a score run only records when it finished, once'); END;
CREATE TRIGGER score_runs_permanent BEFORE DELETE ON score_runs
BEGIN SELECT RAISE(ABORT, 'score_runs are history: they are never deleted'); END;

CREATE TABLE business_scores (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    score_run_id TEXT NOT NULL REFERENCES score_runs (id),
    business_id TEXT NOT NULL REFERENCES businesses (id),
    tier TEXT NOT NULL REFERENCES lead_tier (value),
    total_score REAL CHECK (total_score BETWEEN 0 AND 100),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    -- when the facts were read: outcomes recorded before it never calibrate this score (leakage)
    facts_collected_at TEXT NOT NULL
        CHECK (facts_collected_at IS strftime('%Y-%m-%dT%H:%M:%fZ', facts_collected_at)),
    facts_json TEXT NOT NULL CHECK (json_valid(facts_json)),
    scorecard_json TEXT NOT NULL CHECK (json_valid(scorecard_json)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    UNIQUE (score_run_id, business_id),
    -- a scored tier has a total, an excluded business never has one; insufficient data may
    CHECK (tier NOT IN ('priority', 'review', 'low') OR total_score IS NOT NULL),
    CHECK (tier <> 'excluded' OR total_score IS NULL)
) STRICT;
CREATE INDEX business_scores_by_business ON business_scores (business_id, created_at);

CREATE TRIGGER business_scores_immutable BEFORE UPDATE ON business_scores
BEGIN SELECT RAISE(ABORT, 'business_scores are immutable'); END;
CREATE TRIGGER business_scores_permanent BEFORE DELETE ON business_scores
BEGIN SELECT RAISE(ABORT, 'business_scores are history: they are never deleted'); END;

CREATE TABLE component_scores (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    business_score_id TEXT NOT NULL REFERENCES business_scores (id),
    component TEXT NOT NULL
        CHECK (length(component) <= 64 AND component GLOB '[A-Z]*' AND component NOT GLOB '*[^A-Z_]*'),
    value REAL CHECK (value BETWEEN 0 AND 100),
    weight REAL NOT NULL CHECK (weight >= 0),
    contribution REAL,
    coverage REAL NOT NULL CHECK (coverage BETWEEN 0 AND 1),
    UNIQUE (business_score_id, component)
) STRICT;

CREATE TRIGGER component_scores_immutable BEFORE UPDATE ON component_scores
BEGIN SELECT RAISE(ABORT, 'component_scores are immutable'); END;
CREATE TRIGGER component_scores_permanent BEFORE DELETE ON component_scores
BEGIN SELECT RAISE(ABORT, 'component_scores are history: they are never deleted'); END;

-- ============================================================================ outcomes

CREATE TABLE lead_outcomes (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    business_id TEXT NOT NULL REFERENCES businesses (id),
    -- the run the operator worked from, when there was one
    score_run_id TEXT REFERENCES score_runs (id),
    stage TEXT NOT NULL REFERENCES lead_stage (value),
    previous_stage TEXT NOT NULL REFERENCES lead_stage (value),
    lost_reason TEXT CHECK (lost_reason IS NULL OR length(trim(lost_reason)) BETWEEN 1 AND 500),
    reason TEXT NOT NULL CHECK (length(trim(reason)) BETWEEN 1 AND 2000),
    actor TEXT NOT NULL CHECK (length(actor) >= 1),
    occurred_at TEXT NOT NULL CHECK (occurred_at IS strftime('%Y-%m-%dT%H:%M:%fZ', occurred_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    CHECK ((stage = 'lost') = (lost_reason IS NOT NULL))
) STRICT;
CREATE INDEX lead_outcomes_by_business ON lead_outcomes (business_id, occurred_at);

CREATE TRIGGER lead_outcomes_immutable BEFORE UPDATE ON lead_outcomes
BEGIN SELECT RAISE(ABORT, 'lead_outcomes are immutable'); END;
CREATE TRIGGER lead_outcomes_permanent BEFORE DELETE ON lead_outcomes
BEGIN SELECT RAISE(ABORT, 'lead_outcomes are history: they are never deleted'); END;
