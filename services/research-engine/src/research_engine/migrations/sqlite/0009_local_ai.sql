-- 0009 · Local AI, data contract v1 (phase 10).
--
-- Phase 03 reserved ai_inference_runs, ai_inference_evidence and ai_inference_outputs; nothing ever
-- wrote them. This migration rebuilds them for what running a local model auditably needs:
--
--   * ai_inference_runs: the task and its version, the business, project and job it served, the
--     runtime, model, digest, profile and parameters, the prompt template and output schema
--     versions, the exact input (minimal evidence, never HTML) and its hash, a deterministic cache
--     key, the device, tokens, duration, errors and a person's review. A valid result is unique per
--     cache key, so a restart or a repeated run never stores it twice;
--   * ai_inference_outputs: every raw answer next to what it validated to, or why it was refused;
--     immutable. The cited inputs live in the run's input_json (fragments, findings, subscores are
--     not all evidence_items, so ai_inference_evidence is dropped);
--   * ai_profile_settings, ai_task_settings, ai_resource_limits: which model each profile runs,
--     which profile each task uses, and the free memory below which crawling waits for AI work;
--   * ai_benchmark_results: accuracy, latency, throughput and memory of a model on a task, measured
--     on this machine over the synthetic labelled corpus.

CREATE TABLE ai_task_type (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO ai_task_type VALUES
    ('evidence_extraction'), ('business_classification'), ('finding_synthesis'),
    ('opportunity_reasoning'), ('language_detection'), ('visual_review');

CREATE TABLE ai_profile (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO ai_profile VALUES ('fast'), ('balanced'), ('quality');

CREATE TABLE ai_device (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO ai_device VALUES ('gpu'), ('partial'), ('cpu'), ('unknown');

CREATE TABLE ai_review_status (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO ai_review_status VALUES ('unreviewed'), ('accepted'), ('rejected');

-- ============================================================================ inferences

DROP TABLE ai_inference_outputs;
DROP TABLE ai_inference_evidence;
DROP TABLE ai_inference_runs;

CREATE TABLE ai_inference_runs (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    task_type TEXT NOT NULL REFERENCES ai_task_type (value),
    task_version TEXT NOT NULL CHECK (length(task_version) BETWEEN 1 AND 300),
    project_id TEXT REFERENCES projects (id),
    business_id TEXT REFERENCES businesses (id),
    job_id TEXT REFERENCES jobs (id),
    provider TEXT NOT NULL CHECK (length(provider) BETWEEN 1 AND 64),
    model TEXT NOT NULL CHECK (length(model) BETWEEN 1 AND 300),
    model_digest TEXT,
    profile TEXT NOT NULL REFERENCES ai_profile (value),
    parameters_json TEXT NOT NULL CHECK (json_valid(parameters_json)),
    prompt_template_id TEXT NOT NULL CHECK (length(prompt_template_id) BETWEEN 1 AND 64),
    prompt_template_version TEXT NOT NULL CHECK (length(prompt_template_version) >= 1),
    output_schema_version TEXT NOT NULL CHECK (length(output_schema_version) >= 1),
    input_sha256 TEXT NOT NULL
        CHECK (length(input_sha256) = 64 AND input_sha256 NOT GLOB '*[^0-9a-f]*'),
    input_json TEXT NOT NULL CHECK (json_valid(input_json)),
    cache_key TEXT NOT NULL CHECK (length(cache_key) = 64 AND cache_key NOT GLOB '*[^0-9a-f]*'),
    status TEXT NOT NULL REFERENCES inference_status (value),
    device TEXT NOT NULL REFERENCES ai_device (value),
    attempts INTEGER NOT NULL CHECK (attempts BETWEEN 0 AND 10),
    prompt_tokens INTEGER CHECK (prompt_tokens >= 0),
    completion_tokens INTEGER CHECK (completion_tokens >= 0),
    duration_ms INTEGER CHECK (duration_ms >= 0),
    error_code TEXT CHECK (error_code IS NULL OR length(error_code) BETWEEN 1 AND 64),
    error_message TEXT CHECK (error_message IS NULL OR length(error_message) <= 2000),
    review_status TEXT NOT NULL REFERENCES ai_review_status (value),
    started_at TEXT CHECK (started_at IS strftime('%Y-%m-%dT%H:%M:%fZ', started_at)),
    finished_at TEXT CHECK (finished_at IS strftime('%Y-%m-%dT%H:%M:%fZ', finished_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    CHECK (finished_at IS NULL OR (started_at IS NOT NULL AND finished_at >= started_at)),
    CHECK (review_status = 'unreviewed' OR status IN ('succeeded', 'insufficient_evidence')),
    CHECK (business_id IS NULL OR project_id IS NOT NULL)
) STRICT;
CREATE INDEX ai_inference_runs_by_task ON ai_inference_runs (task_type, created_at);
CREATE INDEX ai_inference_runs_by_date ON ai_inference_runs (created_at, id);
CREATE INDEX ai_inference_runs_by_business ON ai_inference_runs (business_id, created_at)
    WHERE business_id IS NOT NULL;
CREATE INDEX ai_inference_runs_by_job ON ai_inference_runs (job_id) WHERE job_id IS NOT NULL;
CREATE INDEX ai_inference_runs_open ON ai_inference_runs (status)
    WHERE status IN ('queued', 'running');
-- one valid, not rejected result per identical request
CREATE UNIQUE INDEX ai_inference_runs_one_result ON ai_inference_runs (cache_key)
    WHERE status IN ('succeeded', 'insufficient_evidence') AND review_status <> 'rejected';

CREATE TRIGGER ai_inference_runs_status_transition BEFORE UPDATE OF status ON ai_inference_runs
WHEN OLD.status IS NOT NEW.status AND NOT EXISTS (
    SELECT 1 FROM status_transitions
    WHERE enum_name = 'inference_status' AND from_value = OLD.status AND to_value = NEW.status)
BEGIN SELECT RAISE(ABORT, 'invalid inference_status transition'); END;

CREATE TRIGGER ai_inference_runs_fixed_request BEFORE UPDATE ON ai_inference_runs
WHEN NEW.id IS NOT OLD.id OR NEW.task_type IS NOT OLD.task_type
    OR NEW.task_version IS NOT OLD.task_version OR NEW.project_id IS NOT OLD.project_id
    OR NEW.business_id IS NOT OLD.business_id OR NEW.job_id IS NOT OLD.job_id
    OR NEW.provider IS NOT OLD.provider OR NEW.model IS NOT OLD.model
    OR NEW.model_digest IS NOT OLD.model_digest OR NEW.profile IS NOT OLD.profile
    OR NEW.prompt_template_id IS NOT OLD.prompt_template_id
    OR NEW.prompt_template_version IS NOT OLD.prompt_template_version
    OR NEW.output_schema_version IS NOT OLD.output_schema_version
    OR NEW.input_sha256 IS NOT OLD.input_sha256 OR NEW.input_json IS NOT OLD.input_json
    OR NEW.cache_key IS NOT OLD.cache_key OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT, 'an inference request is fixed once stored'); END;

CREATE TRIGGER ai_inference_runs_permanent BEFORE DELETE ON ai_inference_runs
BEGIN SELECT RAISE(ABORT, 'ai_inference_runs are history: they are never deleted'); END;

CREATE TABLE ai_inference_outputs (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    inference_run_id TEXT NOT NULL REFERENCES ai_inference_runs (id),
    attempt INTEGER NOT NULL CHECK (attempt BETWEEN 1 AND 10),
    raw_output TEXT NOT NULL CHECK (length(raw_output) <= 200000),
    validated_output_json TEXT CHECK (json_valid(validated_output_json)),
    validation_errors_json TEXT CHECK (json_valid(validation_errors_json)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    CHECK ((validated_output_json IS NULL) <> (validation_errors_json IS NULL)),
    UNIQUE (inference_run_id, attempt)
) STRICT;

CREATE TRIGGER ai_inference_outputs_immutable BEFORE UPDATE ON ai_inference_outputs
BEGIN SELECT RAISE(ABORT, 'ai_inference_outputs are immutable'); END;
CREATE TRIGGER ai_inference_outputs_permanent BEFORE DELETE ON ai_inference_outputs
BEGIN SELECT RAISE(ABORT, 'ai_inference_outputs are immutable'); END;

-- ============================================================================ settings

CREATE TABLE ai_profile_settings (
    profile TEXT PRIMARY KEY NOT NULL REFERENCES ai_profile (value),
    model TEXT CHECK (model IS NULL OR length(model) BETWEEN 1 AND 300),
    num_ctx INTEGER NOT NULL CHECK (num_ctx BETWEEN 512 AND 32768),
    allow_cpu INTEGER NOT NULL CHECK (allow_cpu IN (0, 1)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at))
) STRICT, WITHOUT ROWID;
-- no model is assigned by default: nothing is assumed to be installed
INSERT INTO ai_profile_settings VALUES
    ('fast', NULL, 4096, 1, '2026-01-01T00:00:00.000Z'),
    ('balanced', NULL, 4096, 1, '2026-01-01T00:00:00.000Z'),
    ('quality', NULL, 8192, 0, '2026-01-01T00:00:00.000Z');

CREATE TABLE ai_task_settings (
    task_type TEXT PRIMARY KEY NOT NULL REFERENCES ai_task_type (value),
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    profile TEXT NOT NULL REFERENCES ai_profile (value),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at))
) STRICT, WITHOUT ROWID;
INSERT INTO ai_task_settings VALUES
    ('evidence_extraction', 1, 'balanced', '2026-01-01T00:00:00.000Z'),
    ('business_classification', 1, 'fast', '2026-01-01T00:00:00.000Z'),
    ('finding_synthesis', 1, 'balanced', '2026-01-01T00:00:00.000Z'),
    ('opportunity_reasoning', 1, 'quality', '2026-01-01T00:00:00.000Z'),
    ('language_detection', 1, 'fast', '2026-01-01T00:00:00.000Z'),
    ('visual_review', 0, 'balanced', '2026-01-01T00:00:00.000Z');

CREATE TABLE ai_resource_limits (
    scope TEXT PRIMARY KEY NOT NULL CHECK (scope = 'machine'),
    min_free_ram_mib INTEGER NOT NULL CHECK (min_free_ram_mib BETWEEN 256 AND 65536),
    min_free_vram_mib INTEGER NOT NULL CHECK (min_free_vram_mib BETWEEN 0 AND 65536),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at))
) STRICT, WITHOUT ROWID;
INSERT INTO ai_resource_limits VALUES ('machine', 1024, 512, '2026-01-01T00:00:00.000Z');

-- ============================================================================ benchmarks

CREATE TABLE ai_benchmark_results (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    provider TEXT NOT NULL CHECK (length(provider) BETWEEN 1 AND 64),
    model TEXT NOT NULL CHECK (length(model) BETWEEN 1 AND 300),
    model_digest TEXT,
    task_type TEXT NOT NULL REFERENCES ai_task_type (value),
    task_version TEXT NOT NULL CHECK (length(task_version) >= 1),
    corpus_version TEXT NOT NULL CHECK (length(corpus_version) >= 1),
    num_ctx INTEGER NOT NULL CHECK (num_ctx BETWEEN 512 AND 32768),
    cases INTEGER NOT NULL CHECK (cases >= 1),
    valid_outputs INTEGER NOT NULL CHECK (valid_outputs BETWEEN 0 AND cases),
    correct INTEGER NOT NULL CHECK (correct BETWEEN 0 AND valid_outputs),
    failures INTEGER NOT NULL CHECK (failures BETWEEN 0 AND cases),
    latency_p50_ms INTEGER CHECK (latency_p50_ms >= 0),
    latency_p95_ms INTEGER CHECK (latency_p95_ms >= latency_p50_ms),
    tokens_per_second REAL CHECK (tokens_per_second >= 0),
    peak_vram_used_mib INTEGER CHECK (peak_vram_used_mib >= 0),
    min_free_ram_mib INTEGER CHECK (min_free_ram_mib >= 0),
    device TEXT NOT NULL REFERENCES ai_device (value),
    started_at TEXT NOT NULL CHECK (started_at IS strftime('%Y-%m-%dT%H:%M:%fZ', started_at)),
    finished_at TEXT NOT NULL CHECK (finished_at IS strftime('%Y-%m-%dT%H:%M:%fZ', finished_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    CHECK (finished_at >= started_at)
) STRICT;
CREATE INDEX ai_benchmark_results_by_model ON ai_benchmark_results (model, task_type, created_at);

CREATE TRIGGER ai_benchmark_results_immutable BEFORE UPDATE ON ai_benchmark_results
BEGIN SELECT RAISE(ABORT, 'ai_benchmark_results are immutable'); END;
