-- 0012 · Campaign pipelines, reproducible datasets and local exports, data contract v1 (phase 13).
--
-- Phase 13 closes the operational loop without a second scheduler: a pipeline job chains the
-- engines' own resumable jobs, a dataset snapshot is a fixed point-in-time copy, and an export is
-- a local file with a manifest.
--
--   * pipeline_stage_specs, pipeline_stage_dependencies: the DAG a pipeline was queued with (inputs,
--     outputs, version, retry policy and resources per stage), fixed from then on;
--   * task_attempt_jobs: the engine jobs each attempt of a stage started or reused, so a pipeline
--     resumes that work after a restart instead of starting it twice;
--   * dead_letter_tasks: stages that failed for good, until a person retries or skips them;
--   * dataset_snapshots gain what makes them reproducible (query, cut-off, pipeline versions and
--     a quality report) and refuse updates: a snapshot is read, compared or cleaned up, never edited;
--   * exports gain their job, destination, columns, configuration, actor, preset and error code;
--   * export_presets: versioned presets, built in or cloned, never rewritten.

INSERT INTO entity_type VALUES ('export_preset');

-- an export may fail before it starts writing (a destination refused, no row to write)
INSERT INTO status_transitions VALUES ('export_status', 'pending', 'failed');

-- ============================================================================ pipelines

CREATE TABLE pipeline_stage_specs (
    job_id TEXT NOT NULL REFERENCES jobs (id),
    stage TEXT NOT NULL CHECK (length(stage) BETWEEN 1 AND 64),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    version TEXT NOT NULL CHECK (length(version) BETWEEN 1 AND 64),
    inputs_json TEXT NOT NULL CHECK (json_valid(inputs_json) AND json_type(inputs_json) = 'array'),
    outputs_json TEXT NOT NULL CHECK (json_valid(outputs_json) AND json_type(outputs_json) = 'array'),
    retry_json TEXT NOT NULL CHECK (json_valid(retry_json) AND json_type(retry_json) = 'object'),
    resources_json TEXT NOT NULL
        CHECK (json_valid(resources_json) AND json_type(resources_json) = 'array'),
    PRIMARY KEY (job_id, stage),
    UNIQUE (job_id, ordinal)
) STRICT, WITHOUT ROWID;

CREATE TRIGGER pipeline_stage_specs_fixed BEFORE UPDATE ON pipeline_stage_specs
BEGIN SELECT RAISE(ABORT, 'a pipeline stage is fixed when the pipeline is queued'); END;

CREATE TABLE pipeline_stage_dependencies (
    job_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    depends_on TEXT NOT NULL,
    PRIMARY KEY (job_id, stage, depends_on),
    FOREIGN KEY (job_id, stage) REFERENCES pipeline_stage_specs (job_id, stage),
    FOREIGN KEY (job_id, depends_on) REFERENCES pipeline_stage_specs (job_id, stage),
    CHECK (stage <> depends_on)
) STRICT, WITHOUT ROWID;

CREATE TRIGGER pipeline_stage_dependencies_fixed BEFORE UPDATE ON pipeline_stage_dependencies
BEGIN SELECT RAISE(ABORT, 'a pipeline stage is fixed when the pipeline is queued'); END;

CREATE TABLE task_attempt_jobs (
    attempt_id TEXT NOT NULL REFERENCES task_attempts (id),
    job_id TEXT NOT NULL REFERENCES jobs (id),
    -- the engine job was already open for the project and was attached instead of started
    reused INTEGER NOT NULL CHECK (reused IN (0, 1)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    PRIMARY KEY (attempt_id, job_id)
) STRICT, WITHOUT ROWID;
CREATE INDEX task_attempt_jobs_by_job ON task_attempt_jobs (job_id);

CREATE TABLE dead_letter_tasks (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    task_id TEXT NOT NULL REFERENCES job_tasks (id),
    job_id TEXT NOT NULL REFERENCES jobs (id),
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    error_code TEXT NOT NULL CHECK (length(error_code) BETWEEN 1 AND 64),
    error_message TEXT NOT NULL CHECK (length(error_message) BETWEEN 1 AND 4000),
    retryable INTEGER NOT NULL CHECK (retryable IN (0, 1)),
    review_status TEXT NOT NULL CHECK (review_status IN ('open', 'retried', 'skipped')),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    resolved_at TEXT CHECK (resolved_at IS strftime('%Y-%m-%dT%H:%M:%fZ', resolved_at)),
    resolved_by TEXT CHECK (resolved_by IS NULL OR length(resolved_by) BETWEEN 1 AND 120),
    resolution_reason TEXT
        CHECK (resolution_reason IS NULL OR length(resolution_reason) BETWEEN 1 AND 2000),
    CHECK ((review_status = 'open') = (resolved_at IS NULL)),
    CHECK ((resolved_at IS NULL) = (resolved_by IS NULL)),
    CHECK ((resolved_at IS NULL) = (resolution_reason IS NULL))
) STRICT;
CREATE UNIQUE INDEX dead_letter_tasks_open ON dead_letter_tasks (task_id)
    WHERE review_status = 'open';
CREATE INDEX dead_letter_tasks_by_job ON dead_letter_tasks (job_id, created_at);

-- a resolved entry is history: only its review moves, once
CREATE TRIGGER dead_letter_tasks_fixed BEFORE UPDATE ON dead_letter_tasks
WHEN OLD.review_status <> 'open' OR NEW.id IS NOT OLD.id OR NEW.task_id IS NOT OLD.task_id
    OR NEW.job_id IS NOT OLD.job_id OR NEW.attempt IS NOT OLD.attempt
    OR NEW.error_code IS NOT OLD.error_code OR NEW.error_message IS NOT OLD.error_message
    OR NEW.retryable IS NOT OLD.retryable OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT, 'a dead letter keeps its failure; only an open one is resolved'); END;

CREATE INDEX jobs_by_kind_status ON jobs (kind, status, requested_at);
CREATE INDEX job_events_by_level ON job_events (level, created_at);

-- ============================================================================ datasets

ALTER TABLE dataset_snapshots ADD COLUMN query_json TEXT NOT NULL DEFAULT '{}'
    CHECK (json_valid(query_json));
ALTER TABLE dataset_snapshots ADD COLUMN cutoff_at TEXT
    CHECK (cutoff_at IS strftime('%Y-%m-%dT%H:%M:%fZ', cutoff_at));
ALTER TABLE dataset_snapshots ADD COLUMN pipeline_versions_json TEXT NOT NULL DEFAULT '{}'
    CHECK (json_valid(pipeline_versions_json));
ALTER TABLE dataset_snapshots ADD COLUMN quality_json TEXT
    CHECK (quality_json IS NULL OR json_valid(quality_json));

-- a snapshot and its lineage are never edited (0002 triggers); retention may delete a snapshot
-- nothing depends on, and finds what depends on one through this index
CREATE INDEX dataset_snapshot_sources_by_parent ON dataset_snapshot_sources (parent_snapshot_id)
    WHERE parent_snapshot_id IS NOT NULL;

-- ============================================================================ exports

ALTER TABLE exports ADD COLUMN job_id TEXT REFERENCES jobs (id);
-- where the file was written, as the save dialog returned it: history only, never read back
ALTER TABLE exports ADD COLUMN destination TEXT
    CHECK (destination IS NULL OR length(destination) BETWEEN 3 AND 1024);
ALTER TABLE exports ADD COLUMN columns_json TEXT NOT NULL DEFAULT '[]'
    CHECK (json_valid(columns_json) AND json_type(columns_json) = 'array');
ALTER TABLE exports ADD COLUMN config_json TEXT NOT NULL DEFAULT '{}'
    CHECK (json_valid(config_json) AND json_type(config_json) = 'object');
ALTER TABLE exports ADD COLUMN actor TEXT NOT NULL DEFAULT 'system'
    CHECK (length(actor) BETWEEN 1 AND 120);
ALTER TABLE exports ADD COLUMN preset_key TEXT CHECK (preset_key IS NULL OR length(preset_key) <= 64);
ALTER TABLE exports ADD COLUMN preset_version INTEGER
    CHECK (preset_version IS NULL OR preset_version >= 1);
ALTER TABLE exports ADD COLUMN error_code TEXT
    CHECK (error_code IS NULL OR length(error_code) BETWEEN 1 AND 64);
CREATE UNIQUE INDEX exports_one_per_job ON exports (job_id) WHERE job_id IS NOT NULL;
CREATE INDEX exports_by_requested ON exports (requested_at, id);

CREATE TABLE export_presets (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    key TEXT NOT NULL CHECK (length(key) <= 64 AND key GLOB '[a-z]*' AND key NOT GLOB '*[^a-z0-9_]*'),
    version INTEGER NOT NULL CHECK (version >= 1),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 120),
    description TEXT CHECK (description IS NULL OR length(description) <= 2000),
    definition_json TEXT NOT NULL CHECK (json_valid(definition_json)),
    built_in INTEGER NOT NULL CHECK (built_in IN (0, 1)),
    cloned_from_id TEXT REFERENCES export_presets (id),
    created_by TEXT NOT NULL CHECK (length(created_by) BETWEEN 1 AND 120),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    superseded_at TEXT CHECK (superseded_at IS strftime('%Y-%m-%dT%H:%M:%fZ', superseded_at)),
    UNIQUE (key, version)
) STRICT;
CREATE UNIQUE INDEX export_presets_current ON export_presets (key) WHERE superseded_at IS NULL;

-- a version is immutable: saving makes the next one and only marks this one superseded
CREATE TRIGGER export_presets_fixed BEFORE UPDATE ON export_presets
WHEN OLD.superseded_at IS NOT NULL OR NEW.id IS NOT OLD.id OR NEW.key IS NOT OLD.key
    OR NEW.version IS NOT OLD.version OR NEW.name IS NOT OLD.name
    OR NEW.description IS NOT OLD.description OR NEW.definition_json IS NOT OLD.definition_json
    OR NEW.built_in IS NOT OLD.built_in OR NEW.cloned_from_id IS NOT OLD.cloned_from_id
    OR NEW.created_by IS NOT OLD.created_by OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT, 'an export preset version is immutable: save a new version'); END;
CREATE TRIGGER export_presets_permanent BEFORE DELETE ON export_presets
BEGIN SELECT RAISE(ABORT, 'export preset versions are permanent'); END;
