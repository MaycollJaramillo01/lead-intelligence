-- 0005 · Normalisation, entity resolution and deduplication, data contract v1 (phase 06).
--
-- Three levels, kept apart:
--   raw_records / discovery_candidates   what a source said, immutable (phases 03-05)
--   observations (business_names, ...)   normalised values, each through a versioned extraction
--   businesses                           the resolved entity: the root of a cluster of records
--
-- Every candidate becomes one "record" business of its own (`entity_records`), and keeps it for
-- ever. Resolution never moves an observation: it links record businesses into clusters through
-- `businesses.merged_into_id` (always the root), which phase 03 already reserved for merges. So a
-- merge is reversible by construction: unmerging changes two columns, and every observation is still
-- where it was written.
--
-- `match_candidates` holds each pair of records the rules looked at, with the features, weights,
-- score, reasons and rules version behind the outcome, and the status a person (or the engine) gave
-- it. `business_merges` is the history of who joined which cluster, when and why, and when that was
-- undone. `canonical_values` records which observation represents a cluster for each field, chosen by
-- policy or by a person, superseded rather than overwritten.

INSERT INTO entity_type VALUES ('match_candidate');

CREATE TABLE match_outcome (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO match_outcome VALUES ('auto_merge'), ('manual_review'), ('distinct');

CREATE TABLE match_status (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO match_status VALUES ('pending'), ('deferred'), ('merged'), ('separate');

CREATE TABLE match_risk (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO match_risk VALUES ('low'), ('medium'), ('high');

CREATE TABLE canonical_field (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO canonical_field VALUES
    ('name'), ('location'), ('website'), ('phone'), ('email'), ('category');

CREATE TABLE selection_method (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO selection_method VALUES ('policy'), ('manual');

INSERT INTO status_transitions VALUES
    -- an unmerged record goes back to the status it had before the merge
    ('business_status', 'merged', 'candidate'),
    ('business_status', 'merged', 'closed'),
    ('match_status', 'pending', 'merged'),
    ('match_status', 'pending', 'separate'),
    ('match_status', 'pending', 'deferred'),
    ('match_status', 'deferred', 'pending'),
    ('match_status', 'deferred', 'merged'),
    ('match_status', 'deferred', 'separate'),
    ('match_status', 'merged', 'pending'),
    ('match_status', 'merged', 'separate'),
    ('match_status', 'separate', 'pending'),
    ('match_status', 'separate', 'merged');

CREATE INDEX businesses_by_merged_into ON businesses (merged_into_id)
    WHERE merged_into_id IS NOT NULL;

-- ============================================================================ records

CREATE TABLE entity_records (
    candidate_id TEXT PRIMARY KEY NOT NULL REFERENCES discovery_candidates (id),
    business_id TEXT NOT NULL UNIQUE REFERENCES businesses (id),
    project_id TEXT NOT NULL REFERENCES projects (id),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at))
) STRICT, WITHOUT ROWID;
CREATE INDEX entity_records_by_project ON entity_records (project_id);

CREATE TRIGGER entity_records_immutable BEFORE UPDATE ON entity_records
BEGIN SELECT RAISE(ABORT, 'entity_records are immutable'); END;
CREATE TRIGGER entity_records_permanent BEFORE DELETE ON entity_records
BEGIN SELECT RAISE(ABORT, 'entity_records are immutable'); END;

-- ============================================================================ pairs

CREATE TABLE match_candidates (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    project_id TEXT NOT NULL REFERENCES projects (id),
    left_business_id TEXT NOT NULL REFERENCES businesses (id),
    right_business_id TEXT NOT NULL REFERENCES businesses (id),
    rules_version TEXT NOT NULL CHECK (length(rules_version) >= 1),
    score REAL NOT NULL CHECK (score BETWEEN 0 AND 1),
    outcome TEXT NOT NULL REFERENCES match_outcome (value),
    risk TEXT NOT NULL REFERENCES match_risk (value),
    hard_conflict INTEGER NOT NULL CHECK (hard_conflict IN (0, 1)),
    status TEXT NOT NULL REFERENCES match_status (value),
    is_manual INTEGER NOT NULL CHECK (is_manual IN (0, 1)),
    features_json TEXT NOT NULL CHECK (json_valid(features_json)),
    reasons_json TEXT NOT NULL CHECK (json_valid(reasons_json)),
    job_id TEXT REFERENCES jobs (id),
    decided_by TEXT NOT NULL CHECK (length(decided_by) >= 1),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    CHECK (left_business_id < right_business_id),
    UNIQUE (left_business_id, right_business_id)
) STRICT;
CREATE INDEX match_candidates_queue ON match_candidates (project_id, status, score, id);
CREATE INDEX match_candidates_by_status ON match_candidates (status, score, id);
CREATE INDEX match_candidates_by_right ON match_candidates (right_business_id);

CREATE TRIGGER match_candidates_status_transition BEFORE UPDATE OF status ON match_candidates
WHEN NEW.status IS NOT OLD.status AND NOT EXISTS (
    SELECT 1 FROM status_transitions
    WHERE enum_name = 'match_status' AND from_value = OLD.status AND to_value = NEW.status)
BEGIN SELECT RAISE(ABORT, 'invalid match_status transition'); END;

-- the pair itself never changes; its evidence and its status may
CREATE TRIGGER match_candidates_pair_immutable BEFORE UPDATE OF
    id, project_id, left_business_id, right_business_id, created_at ON match_candidates
BEGIN SELECT RAISE(ABORT, 'a match candidate always describes the same pair'); END;
CREATE TRIGGER match_candidates_permanent BEFORE DELETE ON match_candidates
BEGIN SELECT RAISE(ABORT, 'match_candidates are permanent'); END;

-- ============================================================================ merge history

CREATE TABLE business_merges (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    project_id TEXT NOT NULL REFERENCES projects (id),
    business_id TEXT NOT NULL REFERENCES businesses (id),
    into_business_id TEXT NOT NULL REFERENCES businesses (id),
    match_id TEXT REFERENCES match_candidates (id),
    is_manual INTEGER NOT NULL CHECK (is_manual IN (0, 1)),
    score REAL CHECK (score BETWEEN 0 AND 1),
    rules_version TEXT,
    explanation_json TEXT NOT NULL CHECK (json_valid(explanation_json)),
    actor TEXT NOT NULL CHECK (length(actor) >= 1),
    merged_at TEXT NOT NULL CHECK (merged_at IS strftime('%Y-%m-%dT%H:%M:%fZ', merged_at)),
    unmerged_at TEXT CHECK (unmerged_at IS strftime('%Y-%m-%dT%H:%M:%fZ', unmerged_at)),
    unmerged_by TEXT,
    unmerge_reason TEXT,
    CHECK (business_id <> into_business_id),
    CHECK ((unmerged_at IS NULL) = (unmerged_by IS NULL)),
    CHECK ((unmerged_at IS NULL) = (unmerge_reason IS NULL)),
    CHECK (unmerged_at IS NULL OR unmerged_at >= merged_at)
) STRICT;
CREATE UNIQUE INDEX business_merges_open ON business_merges (business_id) WHERE unmerged_at IS NULL;
CREATE INDEX business_merges_by_into ON business_merges (into_business_id, merged_at);
CREATE INDEX business_merges_by_business ON business_merges (business_id, merged_at);

-- history: the only change ever allowed is recording the unmerge, once
CREATE TRIGGER business_merges_history BEFORE UPDATE ON business_merges
WHEN OLD.unmerged_at IS NOT NULL
    OR NEW.id IS NOT OLD.id OR NEW.project_id IS NOT OLD.project_id
    OR NEW.business_id IS NOT OLD.business_id OR NEW.into_business_id IS NOT OLD.into_business_id
    OR NEW.match_id IS NOT OLD.match_id OR NEW.is_manual IS NOT OLD.is_manual
    OR NEW.score IS NOT OLD.score OR NEW.rules_version IS NOT OLD.rules_version
    OR NEW.explanation_json IS NOT OLD.explanation_json OR NEW.actor IS NOT OLD.actor
    OR NEW.merged_at IS NOT OLD.merged_at
BEGIN SELECT RAISE(ABORT, 'business_merges are history: only an unmerge can be recorded, once'); END;
CREATE TRIGGER business_merges_permanent BEFORE DELETE ON business_merges
BEGIN SELECT RAISE(ABORT, 'business_merges are permanent'); END;

-- ============================================================================ canonical values

CREATE TABLE canonical_values (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    business_id TEXT NOT NULL REFERENCES businesses (id),
    field TEXT NOT NULL REFERENCES canonical_field (value),
    -- polymorphic: business_names, business_locations, websites, contact_points or
    -- business_classifications, depending on the field
    observation_id TEXT NOT NULL CHECK (length(observation_id) = 36),
    value TEXT NOT NULL,
    method TEXT NOT NULL REFERENCES selection_method (value),
    policy_version TEXT NOT NULL CHECK (length(policy_version) >= 1),
    alternative_count INTEGER NOT NULL CHECK (alternative_count >= 1),
    reason TEXT NOT NULL CHECK (length(trim(reason)) >= 1),
    actor TEXT NOT NULL CHECK (length(actor) >= 1),
    chosen_at TEXT NOT NULL CHECK (chosen_at IS strftime('%Y-%m-%dT%H:%M:%fZ', chosen_at)),
    superseded_at TEXT CHECK (superseded_at IS strftime('%Y-%m-%dT%H:%M:%fZ', superseded_at)),
    CHECK (superseded_at IS NULL OR superseded_at >= chosen_at)
) STRICT;
CREATE UNIQUE INDEX canonical_values_current ON canonical_values (business_id, field)
    WHERE superseded_at IS NULL;
CREATE INDEX canonical_values_by_business ON canonical_values (business_id, chosen_at);

CREATE TRIGGER canonical_values_history BEFORE UPDATE ON canonical_values
WHEN OLD.superseded_at IS NOT NULL
    OR NEW.id IS NOT OLD.id OR NEW.business_id IS NOT OLD.business_id
    OR NEW.field IS NOT OLD.field OR NEW.observation_id IS NOT OLD.observation_id
    OR NEW.value IS NOT OLD.value OR NEW.method IS NOT OLD.method
    OR NEW.policy_version IS NOT OLD.policy_version
    OR NEW.alternative_count IS NOT OLD.alternative_count OR NEW.reason IS NOT OLD.reason
    OR NEW.actor IS NOT OLD.actor OR NEW.chosen_at IS NOT OLD.chosen_at
BEGIN SELECT RAISE(ABORT, 'canonical_values are history: supersede them instead'); END;
CREATE TRIGGER canonical_values_permanent BEFORE DELETE ON canonical_values
BEGIN SELECT RAISE(ABORT, 'canonical_values are permanent'); END;
