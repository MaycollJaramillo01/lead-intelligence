-- 0011 · Lead explorer, research workspace, sales briefs and the funnel, data contract v1 (phase 12).
--
-- Phase 12 integrates the analyst's work over what the engines stored; it computes no new facts:
--
--   * lead_index: one row per business per score run, projected from business_scores when a run
--     writes them (and on first read for older runs), with the business's rank once the run
--     finished. The Lead Explorer filters, sorts and pages it without reading scorecards;
--   * lead_research: the research status of a canonical business (sales_ready, needs_research,
--     disqualified, always with a reason) and whom it is assigned to. Every change is also a
--     review_decisions row, so the status has a history and can be undone;
--   * saved_views and saved_view_versions: explorer views, versioned and never rewritten;
--   * research_checklists, research_checklist_versions and research_checks: what an analyst verifies
--     per vertical or campaign, and the history of every manual item;
--   * research_tasks: work left on a lead (missing data, checklist items, refresh requests);
--   * evidence_pins: evidence an analyst marked as important, unpinned with a reason;
--   * lead_outcomes gains how the lead was worked: channel, attempt result, value, contact quality
--     and notes. A row may keep the stage (an attempt), and stays immutable;
--   * ai_task_type gains sales_brief: a model's wording of a brief that may only cite its claims.

CREATE TABLE research_status (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO research_status VALUES ('sales_ready'), ('needs_research'), ('disqualified');

CREATE TABLE research_task_kind (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO research_task_kind VALUES ('missing_data'), ('checklist'), ('refresh'), ('custom');

CREATE TABLE research_task_status (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO research_task_status VALUES ('open'), ('done'), ('dismissed');
INSERT INTO status_transitions VALUES
    ('research_task_status', 'open', 'done'),
    ('research_task_status', 'open', 'dismissed'),
    ('research_task_status', 'done', 'open'),
    ('research_task_status', 'dismissed', 'open');

CREATE TABLE evidence_ref_kind (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO evidence_ref_kind VALUES
    ('business'), ('evidence_item'), ('website_audit'), ('website_finding'), ('website_technology'),
    ('contact_channel'), ('decision_maker'), ('latino_assessment'), ('inference_run'), ('score_run');

CREATE TABLE attempt_result (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO attempt_result VALUES
    ('no_answer'), ('left_message'), ('reached'), ('replied'), ('bounced'), ('wrong_contact'),
    ('call_back_later'), ('meeting_set'), ('proposal_sent'), ('won'), ('lost');

CREATE TABLE contact_quality (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO contact_quality VALUES
    ('decision_maker'), ('gatekeeper'), ('wrong_person'), ('unreachable'), ('unknown');

INSERT INTO entity_type VALUES ('saved_view'), ('research_checklist');

INSERT INTO ai_task_type VALUES ('sales_brief');
INSERT INTO ai_task_settings VALUES ('sales_brief', 1, 'quality', '2026-01-01T00:00:00.000Z');

-- ============================================================================ lead index

CREATE TABLE lead_index (
    score_run_id TEXT NOT NULL REFERENCES score_runs (id),
    business_id TEXT NOT NULL REFERENCES businesses (id),
    project_id TEXT NOT NULL REFERENCES projects (id),
    -- the business's place in the finished run (tier, total, confidence, business id)
    rank INTEGER CHECK (rank >= 1),
    tier TEXT NOT NULL REFERENCES lead_tier (value),
    tier_order INTEGER NOT NULL CHECK (tier_order BETWEEN 0 AND 4),
    total_score REAL CHECK (total_score BETWEEN 0 AND 100),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    data_coverage REAL NOT NULL CHECK (data_coverage BETWEEN 0 AND 1),
    icp_match REAL CHECK (icp_match BETWEEN 0 AND 100),
    opportunity_score REAL CHECK (opportunity_score BETWEEN 0 AND 100),
    digital_gap REAL CHECK (digital_gap BETWEEN 0 AND 100),
    business_health REAL CHECK (business_health BETWEEN 0 AND 100),
    buying_capacity REAL CHECK (buying_capacity BETWEEN 0 AND 100),
    contactability REAL CHECK (contactability BETWEEN 0 AND 100),
    commercial_intent REAL CHECK (commercial_intent BETWEEN 0 AND 100),
    data_confidence REAL CHECK (data_confidence BETWEEN 0 AND 100),
    display_name TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 300),
    name_sort_key TEXT NOT NULL,
    locality TEXT,
    region TEXT,
    primary_category TEXT,
    primary_domain TEXT,
    latino_status TEXT NOT NULL REFERENCES latino_status (value),
    latino_confidence REAL CHECK (latino_confidence BETWEEN 0 AND 1),
    -- the best usable published channel when the facts were read
    main_channel TEXT REFERENCES contact_type (value),
    channel_status TEXT REFERENCES validation_status (value),
    usable_channels INTEGER NOT NULL CHECK (usable_channels >= 0),
    -- one bit per observed problem type, in ProblemType order
    problem_mask INTEGER NOT NULL CHECK (problem_mask BETWEEN 0 AND 255),
    top_problem TEXT CHECK (length(top_problem) BETWEEN 1 AND 64),
    top_reason TEXT CHECK (length(top_reason) BETWEEN 1 AND 300),
    offer_key TEXT CHECK (length(offer_key) BETWEEN 1 AND 64),
    facts_collected_at TEXT NOT NULL
        CHECK (facts_collected_at IS strftime('%Y-%m-%dT%H:%M:%fZ', facts_collected_at)),
    audit_status TEXT REFERENCES website_audit_status (value),
    conflicts INTEGER NOT NULL CHECK (conflicts >= 0),
    suppressed_when_scored INTEGER NOT NULL CHECK (suppressed_when_scored IN (0, 1)),
    is_synthetic INTEGER NOT NULL CHECK (is_synthetic IN (0, 1)),
    UNIQUE (score_run_id, business_id),
    CHECK ((main_channel IS NULL) = (channel_status IS NULL))
) STRICT;
-- the run's rank order, and the two orders analysts sort by most, walked instead of sorted
CREATE INDEX lead_index_by_rank ON lead_index (score_run_id, rank);
CREATE INDEX lead_index_by_total ON lead_index (score_run_id, total_score, business_id);
CREATE INDEX lead_index_by_name ON lead_index (score_run_id, name_sort_key, business_id);
CREATE INDEX lead_index_by_business ON lead_index (business_id);
-- every column a filter or a sort reads: a query with conditions finds its window in this index
-- alone, and only the window's rows are read from the table (leads.explorer)
CREATE INDEX lead_index_scan ON lead_index (
    score_run_id, tier, tier_order, rank, total_score, confidence, data_coverage, icp_match,
    opportunity_score, digital_gap, business_health, buying_capacity, contactability,
    commercial_intent, data_confidence, latino_status, latino_confidence, main_channel,
    channel_status, usable_channels, problem_mask, top_problem, offer_key, audit_status, conflicts,
    facts_collected_at, region, locality, primary_category, primary_domain, name_sort_key,
    business_id
);

-- a soft-deleted business leaves the explorer; restored, its rows are projected again on read
CREATE TRIGGER lead_index_forgets_deleted AFTER UPDATE OF deleted_at ON businesses
WHEN NEW.deleted_at IS NOT NULL
BEGIN DELETE FROM lead_index WHERE business_id = NEW.id; END;

-- ============================================================================ research state

CREATE TABLE lead_research (
    business_id TEXT PRIMARY KEY NOT NULL REFERENCES businesses (id),
    project_id TEXT NOT NULL REFERENCES projects (id),
    status TEXT REFERENCES research_status (value),
    status_reason TEXT CHECK (length(trim(status_reason)) BETWEEN 1 AND 2000),
    status_by TEXT CHECK (length(status_by) >= 1),
    status_at TEXT CHECK (status_at IS strftime('%Y-%m-%dT%H:%M:%fZ', status_at)),
    assignee TEXT CHECK (length(trim(assignee)) BETWEEN 1 AND 120),
    assigned_at TEXT CHECK (assigned_at IS strftime('%Y-%m-%dT%H:%M:%fZ', assigned_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    CHECK ((status IS NULL) = (status_reason IS NULL)),
    CHECK ((status IS NULL) = (status_by IS NULL)),
    CHECK ((status IS NULL) = (status_at IS NULL)),
    CHECK ((assignee IS NULL) = (assigned_at IS NULL))
) STRICT;
CREATE INDEX lead_research_by_status ON lead_research (status);

CREATE TRIGGER lead_research_permanent BEFORE DELETE ON lead_research
BEGIN SELECT RAISE(ABORT, 'lead_research is history: rows are never deleted'); END;

-- ============================================================================ saved views

CREATE TABLE saved_views (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    scope TEXT NOT NULL CHECK (scope IN ('lead_explorer')),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 120),
    description TEXT CHECK (length(description) <= 2000),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    archived_at TEXT CHECK (archived_at IS strftime('%Y-%m-%dT%H:%M:%fZ', archived_at))
) STRICT;
CREATE UNIQUE INDEX saved_views_live_name ON saved_views (scope, name) WHERE archived_at IS NULL;

CREATE TABLE saved_view_versions (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    view_id TEXT NOT NULL REFERENCES saved_views (id),
    version INTEGER NOT NULL CHECK (version >= 1),
    definition_json TEXT NOT NULL CHECK (json_valid(definition_json)),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    created_by TEXT NOT NULL CHECK (length(created_by) >= 1),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    UNIQUE (view_id, version)
) STRICT;

CREATE TRIGGER saved_views_permanent BEFORE DELETE ON saved_views
BEGIN SELECT RAISE(ABORT, 'saved_views are history: archive them instead'); END;
CREATE TRIGGER saved_view_versions_immutable BEFORE UPDATE ON saved_view_versions
BEGIN SELECT RAISE(ABORT, 'saved_view_versions are immutable'); END;
CREATE TRIGGER saved_view_versions_permanent BEFORE DELETE ON saved_view_versions
BEGIN SELECT RAISE(ABORT, 'saved_view_versions are history: they are never deleted'); END;

-- ============================================================================ checklists

CREATE TABLE research_checklists (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 120),
    description TEXT CHECK (length(description) <= 2000),
    active_version_id TEXT REFERENCES research_checklist_versions (id),
    archived_at TEXT CHECK (archived_at IS strftime('%Y-%m-%dT%H:%M:%fZ', archived_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at))
) STRICT;
CREATE UNIQUE INDEX research_checklists_live_name ON research_checklists (name)
    WHERE archived_at IS NULL;

CREATE TABLE research_checklist_versions (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    checklist_id TEXT NOT NULL REFERENCES research_checklists (id),
    version INTEGER NOT NULL CHECK (version >= 1),
    definition_json TEXT NOT NULL CHECK (json_valid(definition_json)),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    created_by TEXT NOT NULL CHECK (length(created_by) >= 1),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    UNIQUE (checklist_id, version)
) STRICT;

CREATE TRIGGER research_checklists_permanent BEFORE DELETE ON research_checklists
BEGIN SELECT RAISE(ABORT, 'research_checklists are history: archive them instead'); END;
CREATE TRIGGER research_checklists_active_version
BEFORE UPDATE OF active_version_id ON research_checklists
WHEN NEW.active_version_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM research_checklist_versions v
    WHERE v.id = NEW.active_version_id AND v.checklist_id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'the active version must belong to its checklist'); END;
CREATE TRIGGER research_checklist_versions_immutable BEFORE UPDATE ON research_checklist_versions
BEGIN SELECT RAISE(ABORT, 'research_checklist_versions are immutable'); END;
CREATE TRIGGER research_checklist_versions_permanent BEFORE DELETE ON research_checklist_versions
BEGIN SELECT RAISE(ABORT, 'research_checklist_versions are history: they are never deleted'); END;

CREATE TABLE research_checks (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    business_id TEXT NOT NULL REFERENCES businesses (id),
    checklist_version_id TEXT NOT NULL REFERENCES research_checklist_versions (id),
    item_key TEXT NOT NULL CHECK (length(item_key) BETWEEN 1 AND 64),
    done INTEGER NOT NULL CHECK (done IN (0, 1)),
    note TEXT CHECK (length(trim(note)) BETWEEN 1 AND 1000),
    actor TEXT NOT NULL CHECK (length(actor) >= 1),
    decided_at TEXT NOT NULL CHECK (decided_at IS strftime('%Y-%m-%dT%H:%M:%fZ', decided_at))
) STRICT;
CREATE INDEX research_checks_by_business ON research_checks (business_id, item_key, decided_at);

CREATE TRIGGER research_checks_immutable BEFORE UPDATE ON research_checks
BEGIN SELECT RAISE(ABORT, 'research_checks are immutable'); END;
CREATE TRIGGER research_checks_permanent BEFORE DELETE ON research_checks
BEGIN SELECT RAISE(ABORT, 'research_checks are history: they are never deleted'); END;

-- ============================================================================ tasks and pins

CREATE TABLE research_tasks (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    business_id TEXT NOT NULL REFERENCES businesses (id),
    project_id TEXT NOT NULL REFERENCES projects (id),
    kind TEXT NOT NULL REFERENCES research_task_kind (value),
    -- what the task is about: `missing:<feature>`, `checklist:<item>`, `refresh:<scope>`, `custom:<id>`
    key TEXT NOT NULL CHECK (length(key) BETWEEN 1 AND 160),
    title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 300),
    detail_json TEXT CHECK (json_valid(detail_json)),
    status TEXT NOT NULL REFERENCES research_task_status (value),
    created_by TEXT NOT NULL CHECK (length(created_by) >= 1),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    closed_by TEXT CHECK (length(closed_by) >= 1),
    closed_at TEXT CHECK (closed_at IS strftime('%Y-%m-%dT%H:%M:%fZ', closed_at)),
    close_reason TEXT CHECK (length(trim(close_reason)) BETWEEN 1 AND 2000),
    CHECK ((status = 'open') = (closed_at IS NULL)),
    CHECK ((closed_at IS NULL) = (closed_by IS NULL))
) STRICT;
CREATE UNIQUE INDEX research_tasks_open_key ON research_tasks (business_id, key)
    WHERE status = 'open';
CREATE INDEX research_tasks_by_business ON research_tasks (business_id, status, created_at);

CREATE TRIGGER research_tasks_status_transition BEFORE UPDATE OF status ON research_tasks
WHEN NEW.status IS NOT OLD.status AND NOT EXISTS (
    SELECT 1 FROM status_transitions
    WHERE enum_name = 'research_task_status' AND from_value = OLD.status AND to_value = NEW.status)
BEGIN SELECT RAISE(ABORT, 'invalid research_task_status transition'); END;
CREATE TRIGGER research_tasks_permanent BEFORE DELETE ON research_tasks
BEGIN SELECT RAISE(ABORT, 'research_tasks are history: they are never deleted'); END;

CREATE TABLE evidence_pins (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    business_id TEXT NOT NULL REFERENCES businesses (id),
    ref_kind TEXT NOT NULL REFERENCES evidence_ref_kind (value),
    -- a row id, or the key of a consolidated contact channel
    ref TEXT NOT NULL CHECK (length(ref) BETWEEN 1 AND 400),
    label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 300),
    note TEXT CHECK (length(trim(note)) BETWEEN 1 AND 1000),
    pinned_by TEXT NOT NULL CHECK (length(pinned_by) >= 1),
    pinned_at TEXT NOT NULL CHECK (pinned_at IS strftime('%Y-%m-%dT%H:%M:%fZ', pinned_at)),
    unpinned_by TEXT CHECK (length(unpinned_by) >= 1),
    unpinned_at TEXT CHECK (unpinned_at IS strftime('%Y-%m-%dT%H:%M:%fZ', unpinned_at)),
    unpin_reason TEXT CHECK (length(trim(unpin_reason)) BETWEEN 1 AND 2000),
    CHECK ((unpinned_at IS NULL) = (unpinned_by IS NULL)),
    CHECK ((unpinned_at IS NULL) = (unpin_reason IS NULL))
) STRICT;
CREATE UNIQUE INDEX evidence_pins_live ON evidence_pins (business_id, ref_kind, ref)
    WHERE unpinned_at IS NULL;

CREATE TRIGGER evidence_pins_unpin_once BEFORE UPDATE ON evidence_pins
WHEN NEW.id IS NOT OLD.id OR NEW.business_id IS NOT OLD.business_id
    OR NEW.ref_kind IS NOT OLD.ref_kind OR NEW.ref IS NOT OLD.ref OR NEW.label IS NOT OLD.label
    OR NEW.note IS NOT OLD.note OR NEW.pinned_by IS NOT OLD.pinned_by
    OR NEW.pinned_at IS NOT OLD.pinned_at OR OLD.unpinned_at IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'a pin only records when it was unpinned, once'); END;
CREATE TRIGGER evidence_pins_permanent BEFORE DELETE ON evidence_pins
BEGIN SELECT RAISE(ABORT, 'evidence_pins are history: they are never deleted'); END;

-- ============================================================================ funnel

ALTER TABLE lead_outcomes ADD COLUMN channel TEXT REFERENCES contact_type (value);
ALTER TABLE lead_outcomes ADD COLUMN attempt_result TEXT REFERENCES attempt_result (value);
ALTER TABLE lead_outcomes ADD COLUMN value_amount REAL
    CHECK (value_amount BETWEEN 0 AND 1000000000);
ALTER TABLE lead_outcomes ADD COLUMN value_currency TEXT
    CHECK (value_currency GLOB '[A-Z][A-Z][A-Z]' AND (value_currency IS NULL) = (value_amount IS NULL));
ALTER TABLE lead_outcomes ADD COLUMN contact_quality TEXT REFERENCES contact_quality (value);
ALTER TABLE lead_outcomes ADD COLUMN notes TEXT CHECK (length(trim(notes)) BETWEEN 1 AND 4000);
