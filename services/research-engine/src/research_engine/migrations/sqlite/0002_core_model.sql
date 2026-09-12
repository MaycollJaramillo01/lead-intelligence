-- 0002 · Core data model, data contract v1 (research_engine/domain.py is the source of truth).
--
-- Conventions (docs/data-model.md):
--   * ids: UUIDv7 text (36 chars). Names, domains and phones are never keys.
--   * timestamps: UTC text 'YYYY-MM-DDTHH:MM:SS.sssZ'; the CHECK rejects any other form.
--   * enumerations: one lookup table per enum; columns reference it.
--   * status columns: changes must appear in status_transitions (enforced by triggers).
--   * *_json columns hold valid JSON; paths are relative to the data directory.
--   * raw_records, extractions, audit_log and other history rows are immutable.
--   * important rows are soft-deleted first (deleted_at + deleted_reason); purge needs both steps.

-- ============================================================================ enumerations

CREATE TABLE business_status (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO business_status VALUES ('candidate'), ('active'), ('closed'), ('merged');

CREATE TABLE source_run_status (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO source_run_status VALUES
    ('queued'), ('running'), ('paused'), ('succeeded'), ('failed'), ('cancelled');

CREATE TABLE job_status (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO job_status VALUES
    ('queued'), ('running'), ('paused'), ('succeeded'), ('failed'), ('cancelled');

CREATE TABLE evidence_type (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO evidence_type VALUES
    ('identity'), ('location'), ('contact'), ('ownership_declaration'), ('certification'),
    ('directory_membership'), ('market_language'), ('website_signal'), ('technology'),
    ('reputation'), ('operating_status');

CREATE TABLE evidence_strength (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO evidence_strength VALUES ('strong'), ('moderate'), ('weak');

CREATE TABLE evidence_status (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO evidence_status VALUES ('active'), ('stale'), ('contradicted'), ('revoked');

CREATE TABLE latino_status (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO latino_status VALUES
    ('verified_latino_owned'), ('high_confidence_latino_owned'), ('probable_latino_owned'),
    ('spanish_market_affinity'), ('unknown');

CREATE TABLE contact_type (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO contact_type VALUES
    ('phone'), ('email'), ('contact_form'), ('booking_form'), ('social_profile');

CREATE TABLE validation_status (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO validation_status VALUES
    ('observed'), ('structurally_valid'), ('domain_valid'), ('likely_valid'), ('invalid'),
    ('unknown'), ('stale'), ('suppressed');

CREATE TABLE website_finding_severity (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO website_finding_severity VALUES
    ('critical'), ('high'), ('medium'), ('low'), ('info');

CREATE TABLE website_finding_result (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO website_finding_result VALUES
    ('pass'), ('fail'), ('warning'), ('unknown'), ('not_applicable');

CREATE TABLE inference_status (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO inference_status VALUES
    ('queued'), ('running'), ('succeeded'), ('insufficient_evidence'), ('invalid_output'),
    ('failed'), ('cancelled');

CREATE TABLE lead_stage (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO lead_stage VALUES
    ('not_contacted'), ('attempted'), ('connected'), ('qualified'), ('meeting_booked'),
    ('proposal_sent'), ('won'), ('lost'), ('invalid_contact'), ('not_interested');

CREATE TABLE export_status (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO export_status VALUES ('pending'), ('running'), ('succeeded'), ('failed'), ('cancelled');

CREATE TABLE source_kind (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO source_kind VALUES
    ('official_api'), ('public_dataset'), ('directory'), ('public_registry'), ('web_search'),
    ('platform_api'), ('file_import'), ('manual_seed'), ('synthetic');

CREATE TABLE acquisition_method (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO acquisition_method VALUES
    ('api_request'), ('http_fetch'), ('browser_render'), ('file_import'), ('manual_entry'),
    ('synthetic');

CREATE TABLE entity_type (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO entity_type VALUES
    ('project'), ('campaign'), ('icp_profile'), ('source_connector'), ('source_run'),
    ('raw_record'), ('business'), ('contact_point'), ('decision_maker'), ('evidence_item'),
    ('latino_assessment'), ('website_audit'), ('website_finding'), ('inference_run'),
    ('scoring_model'), ('score_run'), ('job'), ('dataset'), ('dataset_snapshot'), ('export'),
    ('tag'), ('note');

CREATE TABLE audit_action (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO audit_action VALUES
    ('create'), ('update'), ('status_change'), ('soft_delete'), ('restore'), ('purge'),
    ('merge'), ('unmerge');

CREATE TABLE name_kind (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO name_kind VALUES ('legal'), ('trade'), ('dba'), ('alternate');

CREATE TABLE service_area_kind (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO service_area_kind VALUES
    ('city'), ('county'), ('state'), ('postal_code'), ('metro'), ('radius');

CREATE TABLE classification_kind (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO classification_kind VALUES ('category'), ('service');

CREATE TABLE page_kind (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO page_kind VALUES ('home'), ('about'), ('contact'), ('services'), ('other');

CREATE TABLE contact_origin (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO contact_origin VALUES ('published'), ('user_import'), ('hypothesis');

CREATE TABLE conversion_element_kind (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO conversion_element_kind VALUES
    ('primary_cta'), ('secondary_cta'), ('contact_form'), ('booking_widget'), ('chat_widget'),
    ('click_to_call'), ('messaging_link'), ('other');

CREATE TABLE assessment_method (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO assessment_method VALUES ('rules'), ('manual_review');

CREATE TABLE evidence_role (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO evidence_role VALUES ('supports'), ('contradicts'), ('context');

CREATE TABLE explanation_kind (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO explanation_kind VALUES ('reason'), ('gate'), ('penalty'), ('missing_data');

CREATE TABLE event_level (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO event_level VALUES ('debug'), ('info'), ('warning'), ('error');

CREATE TABLE export_format (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO export_format VALUES ('csv'), ('xlsx'), ('json'), ('jsonl'), ('parquet');

-- ============================================================================ state machines

CREATE TABLE status_transitions (
    enum_name TEXT NOT NULL,
    from_value TEXT NOT NULL,
    to_value TEXT NOT NULL,
    PRIMARY KEY (enum_name, from_value, to_value),
    CHECK (from_value <> to_value)
) STRICT, WITHOUT ROWID;

INSERT INTO status_transitions VALUES
    ('business_status', 'candidate', 'active'),
    ('business_status', 'candidate', 'closed'),
    ('business_status', 'candidate', 'merged'),
    ('business_status', 'active', 'closed'),
    ('business_status', 'active', 'merged'),
    ('business_status', 'closed', 'active'),
    ('business_status', 'closed', 'merged'),
    ('business_status', 'merged', 'active'),
    ('evidence_status', 'active', 'stale'),
    ('evidence_status', 'active', 'contradicted'),
    ('evidence_status', 'active', 'revoked'),
    ('evidence_status', 'stale', 'active'),
    ('evidence_status', 'stale', 'revoked'),
    ('evidence_status', 'contradicted', 'active'),
    ('evidence_status', 'contradicted', 'revoked'),
    ('inference_status', 'queued', 'running'),
    ('inference_status', 'queued', 'cancelled'),
    ('inference_status', 'running', 'succeeded'),
    ('inference_status', 'running', 'insufficient_evidence'),
    ('inference_status', 'running', 'invalid_output'),
    ('inference_status', 'running', 'failed'),
    ('inference_status', 'running', 'cancelled'),
    ('inference_status', 'failed', 'queued'),
    ('inference_status', 'invalid_output', 'queued'),
    ('lead_stage', 'not_contacted', 'attempted'),
    ('lead_stage', 'not_contacted', 'invalid_contact'),
    ('lead_stage', 'not_contacted', 'not_interested'),
    ('lead_stage', 'attempted', 'connected'),
    ('lead_stage', 'attempted', 'invalid_contact'),
    ('lead_stage', 'attempted', 'not_interested'),
    ('lead_stage', 'attempted', 'lost'),
    ('lead_stage', 'connected', 'qualified'),
    ('lead_stage', 'connected', 'not_interested'),
    ('lead_stage', 'connected', 'lost'),
    ('lead_stage', 'qualified', 'meeting_booked'),
    ('lead_stage', 'qualified', 'proposal_sent'),
    ('lead_stage', 'qualified', 'not_interested'),
    ('lead_stage', 'qualified', 'lost'),
    ('lead_stage', 'meeting_booked', 'proposal_sent'),
    ('lead_stage', 'meeting_booked', 'won'),
    ('lead_stage', 'meeting_booked', 'lost'),
    ('lead_stage', 'proposal_sent', 'won'),
    ('lead_stage', 'proposal_sent', 'lost'),
    ('lead_stage', 'lost', 'not_contacted'),
    ('lead_stage', 'invalid_contact', 'not_contacted'),
    ('lead_stage', 'not_interested', 'not_contacted'),
    ('export_status', 'pending', 'running'),
    ('export_status', 'pending', 'cancelled'),
    ('export_status', 'running', 'succeeded'),
    ('export_status', 'running', 'failed'),
    ('export_status', 'running', 'cancelled'),
    ('export_status', 'failed', 'pending');

-- source runs and jobs share one lifecycle
WITH machine (enum_name) AS (VALUES ('source_run_status'), ('job_status')),
    edges (from_value, to_value) AS (
        VALUES ('queued', 'running'), ('queued', 'cancelled'), ('running', 'paused'),
            ('running', 'succeeded'), ('running', 'failed'), ('running', 'cancelled'),
            ('paused', 'running'), ('paused', 'cancelled'), ('failed', 'queued'))
INSERT INTO status_transitions SELECT machine.enum_name, edges.from_value, edges.to_value
FROM machine, edges;

-- any re-validation result is allowed, but a suppression is only lifted back to 'observed'
INSERT INTO status_transitions
SELECT 'validation_status', a.value, b.value
FROM validation_status AS a, validation_status AS b
WHERE a.value <> b.value AND (a.value <> 'suppressed' OR b.value = 'observed');

-- ============================================================================ workspace

CREATE TABLE projects (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 300),
    description TEXT,
    is_synthetic INTEGER NOT NULL CHECK (is_synthetic IN (0, 1)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    deleted_at TEXT CHECK (deleted_at IS strftime('%Y-%m-%dT%H:%M:%fZ', deleted_at)),
    deleted_reason TEXT,
    CHECK ((deleted_at IS NULL) = (deleted_reason IS NULL))
) STRICT;
CREATE UNIQUE INDEX projects_live_name ON projects (name) WHERE deleted_at IS NULL;

CREATE TABLE icp_profiles (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    project_id TEXT NOT NULL REFERENCES projects (id),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 300),
    description TEXT,
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    deleted_at TEXT CHECK (deleted_at IS strftime('%Y-%m-%dT%H:%M:%fZ', deleted_at)),
    deleted_reason TEXT,
    CHECK ((deleted_at IS NULL) = (deleted_reason IS NULL))
) STRICT;
CREATE UNIQUE INDEX icp_profiles_live_name ON icp_profiles (project_id, name)
    WHERE deleted_at IS NULL;

CREATE TABLE icp_profile_versions (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    icp_profile_id TEXT NOT NULL REFERENCES icp_profiles (id),
    version INTEGER NOT NULL CHECK (version >= 1),
    definition_json TEXT NOT NULL CHECK (json_valid(definition_json)),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    created_by TEXT NOT NULL CHECK (length(created_by) >= 1),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    UNIQUE (icp_profile_id, version)
) STRICT;

CREATE TABLE campaigns (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    project_id TEXT NOT NULL REFERENCES projects (id),
    icp_profile_version_id TEXT REFERENCES icp_profile_versions (id),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 300),
    objective TEXT,
    criteria_json TEXT NOT NULL CHECK (json_valid(criteria_json)),
    criteria_schema_version INTEGER NOT NULL CHECK (criteria_schema_version >= 1),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    deleted_at TEXT CHECK (deleted_at IS strftime('%Y-%m-%dT%H:%M:%fZ', deleted_at)),
    deleted_reason TEXT,
    CHECK ((deleted_at IS NULL) = (deleted_reason IS NULL))
) STRICT;
CREATE UNIQUE INDEX campaigns_live_name ON campaigns (project_id, name) WHERE deleted_at IS NULL;

-- ============================================================================ sources and provenance

CREATE TABLE source_connectors (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    key TEXT NOT NULL UNIQUE
        CHECK (length(key) <= 64 AND key GLOB '[a-z]*' AND key NOT GLOB '*[^a-z0-9_.]*'),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 300),
    kind TEXT NOT NULL REFERENCES source_kind (value),
    connector_version TEXT NOT NULL CHECK (length(connector_version) >= 1),
    terms_url TEXT,
    requires_credentials INTEGER NOT NULL CHECK (requires_credentials IN (0, 1)),
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at))
) STRICT;

CREATE TABLE source_connector_configs (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    connector_id TEXT NOT NULL REFERENCES source_connectors (id),
    project_id TEXT REFERENCES projects (id),
    config_json TEXT NOT NULL CHECK (json_valid(config_json)),
    requests_per_minute INTEGER CHECK (requests_per_minute > 0),
    daily_request_budget INTEGER CHECK (daily_request_budget > 0),
    max_concurrency INTEGER CHECK (max_concurrency > 0),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    superseded_at TEXT CHECK (superseded_at IS strftime('%Y-%m-%dT%H:%M:%fZ', superseded_at))
) STRICT;
CREATE UNIQUE INDEX source_connector_configs_current
    ON source_connector_configs (connector_id, ifnull(project_id, ''))
    WHERE superseded_at IS NULL;

CREATE TABLE source_runs (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    project_id TEXT NOT NULL REFERENCES projects (id),
    campaign_id TEXT REFERENCES campaigns (id),
    connector_id TEXT NOT NULL REFERENCES source_connectors (id),
    connector_version TEXT NOT NULL CHECK (length(connector_version) >= 1),
    config_id TEXT REFERENCES source_connector_configs (id),
    job_id TEXT REFERENCES jobs (id),
    status TEXT NOT NULL REFERENCES source_run_status (value),
    effective_config_json TEXT NOT NULL CHECK (json_valid(effective_config_json)),
    started_at TEXT CHECK (started_at IS strftime('%Y-%m-%dT%H:%M:%fZ', started_at)),
    finished_at TEXT CHECK (finished_at IS strftime('%Y-%m-%dT%H:%M:%fZ', finished_at)),
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    CHECK (finished_at IS NULL OR (started_at IS NOT NULL AND finished_at >= started_at))
) STRICT;
CREATE INDEX source_runs_by_project ON source_runs (project_id, created_at);
CREATE INDEX source_runs_by_campaign ON source_runs (campaign_id) WHERE campaign_id IS NOT NULL;

CREATE TABLE source_queries (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    source_run_id TEXT NOT NULL REFERENCES source_runs (id),
    query_json TEXT NOT NULL CHECK (json_valid(query_json)),
    cursor TEXT,
    issued_at TEXT NOT NULL CHECK (issued_at IS strftime('%Y-%m-%dT%H:%M:%fZ', issued_at)),
    result_count INTEGER CHECK (result_count >= 0)
) STRICT;
CREATE INDEX source_queries_by_run ON source_queries (source_run_id);

CREATE TABLE acquired_pages (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    source_run_id TEXT NOT NULL REFERENCES source_runs (id),
    source_query_id TEXT REFERENCES source_queries (id),
    url TEXT NOT NULL CHECK (length(url) >= 1),
    final_url TEXT,
    http_status INTEGER CHECK (http_status BETWEEN 100 AND 599),
    content_type TEXT,
    content_sha256 TEXT
        CHECK (length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
    byte_size INTEGER CHECK (byte_size >= 0),
    artifact_path TEXT CHECK (
        artifact_path NOT GLOB '/*' AND artifact_path NOT GLOB '*\*' AND artifact_path NOT GLOB '*:*'
        AND '/' || artifact_path || '/' NOT GLOB '*/../*'),
    fetched_at TEXT NOT NULL CHECK (fetched_at IS strftime('%Y-%m-%dT%H:%M:%fZ', fetched_at))
) STRICT;
CREATE INDEX acquired_pages_by_run ON acquired_pages (source_run_id);
CREATE INDEX acquired_pages_by_content ON acquired_pages (content_sha256)
    WHERE content_sha256 IS NOT NULL;

CREATE TABLE raw_records (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    source_run_id TEXT NOT NULL REFERENCES source_runs (id),
    acquired_page_id TEXT REFERENCES acquired_pages (id),
    connector_id TEXT NOT NULL REFERENCES source_connectors (id),
    method TEXT NOT NULL REFERENCES acquisition_method (value),
    external_id TEXT,
    source_url TEXT,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    payload_sha256 TEXT NOT NULL
        CHECK (length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'),
    acquired_at TEXT NOT NULL CHECK (acquired_at IS strftime('%Y-%m-%dT%H:%M:%fZ', acquired_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    CHECK (source_url IS NOT NULL OR external_id IS NOT NULL),
    UNIQUE (source_run_id, payload_sha256)
) STRICT;
CREATE INDEX raw_records_by_external_id ON raw_records (connector_id, external_id)
    WHERE external_id IS NOT NULL;
CREATE INDEX raw_records_by_page ON raw_records (acquired_page_id)
    WHERE acquired_page_id IS NOT NULL;

CREATE TABLE extractions (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    raw_record_id TEXT NOT NULL REFERENCES raw_records (id),
    extractor TEXT NOT NULL CHECK (length(extractor) BETWEEN 1 AND 64),
    extractor_version TEXT NOT NULL CHECK (length(extractor_version) >= 1),
    extracted_at TEXT NOT NULL CHECK (extracted_at IS strftime('%Y-%m-%dT%H:%M:%fZ', extracted_at)),
    UNIQUE (raw_record_id, extractor, extractor_version)
) STRICT;

-- ============================================================================ businesses

CREATE TABLE businesses (
    -- rowid alias, only used as the stable key of the full-text index (survives VACUUM)
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE CHECK (length(id) = 36),
    project_id TEXT NOT NULL REFERENCES projects (id),
    display_name TEXT NOT NULL CHECK (length(trim(display_name)) BETWEEN 1 AND 300),
    name_sort_key TEXT NOT NULL,
    status TEXT NOT NULL REFERENCES business_status (value),
    lead_stage TEXT NOT NULL REFERENCES lead_stage (value),
    merged_into_id TEXT REFERENCES businesses (id),
    first_seen_at TEXT NOT NULL
        CHECK (first_seen_at IS strftime('%Y-%m-%dT%H:%M:%fZ', first_seen_at)),
    last_seen_at TEXT NOT NULL CHECK (last_seen_at IS strftime('%Y-%m-%dT%H:%M:%fZ', last_seen_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    deleted_at TEXT CHECK (deleted_at IS strftime('%Y-%m-%dT%H:%M:%fZ', deleted_at)),
    deleted_reason TEXT,
    CHECK (last_seen_at >= first_seen_at),
    CHECK ((status = 'merged') = (merged_into_id IS NOT NULL)),
    CHECK (merged_into_id IS NOT id),
    CHECK ((deleted_at IS NULL) = (deleted_reason IS NULL))
) STRICT;
CREATE INDEX businesses_by_name ON businesses (name_sort_key, id) WHERE deleted_at IS NULL;
CREATE INDEX businesses_by_last_seen ON businesses (last_seen_at, id) WHERE deleted_at IS NULL;
CREATE INDEX businesses_by_project ON businesses (project_id, status);

CREATE TABLE business_names (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    business_id TEXT NOT NULL REFERENCES businesses (id),
    extraction_id TEXT NOT NULL REFERENCES extractions (id),
    name_raw TEXT NOT NULL CHECK (length(name_raw) >= 1),
    name_normalized TEXT NOT NULL,
    name_kind TEXT NOT NULL REFERENCES name_kind (value),
    is_primary INTEGER NOT NULL CHECK (is_primary IN (0, 1)),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    observed_at TEXT NOT NULL CHECK (observed_at IS strftime('%Y-%m-%dT%H:%M:%fZ', observed_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at))
) STRICT;
CREATE INDEX business_names_by_business ON business_names (business_id);
CREATE INDEX business_names_by_extraction ON business_names (extraction_id);
CREATE INDEX business_names_by_value ON business_names (name_normalized);
CREATE UNIQUE INDEX business_names_primary ON business_names (business_id) WHERE is_primary = 1;

CREATE TABLE business_locations (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    business_id TEXT NOT NULL REFERENCES businesses (id),
    extraction_id TEXT NOT NULL REFERENCES extractions (id),
    address_raw TEXT,
    street TEXT,
    unit TEXT,
    locality TEXT,
    region TEXT,
    postal_code TEXT,
    country_code TEXT CHECK (length(country_code) = 2 AND country_code NOT GLOB '*[^A-Z]*'),
    latitude REAL CHECK (latitude BETWEEN -90 AND 90),
    longitude REAL CHECK (longitude BETWEEN -180 AND 180),
    is_primary INTEGER NOT NULL CHECK (is_primary IN (0, 1)),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    observed_at TEXT NOT NULL CHECK (observed_at IS strftime('%Y-%m-%dT%H:%M:%fZ', observed_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    CHECK ((latitude IS NULL) = (longitude IS NULL)),
    CHECK (address_raw IS NOT NULL OR locality IS NOT NULL OR postal_code IS NOT NULL
        OR latitude IS NOT NULL)
) STRICT;
CREATE INDEX business_locations_by_business ON business_locations (business_id);
CREATE INDEX business_locations_by_place ON business_locations (region, locality);
CREATE INDEX business_locations_by_postal_code ON business_locations (postal_code)
    WHERE postal_code IS NOT NULL;
CREATE UNIQUE INDEX business_locations_primary ON business_locations (business_id)
    WHERE is_primary = 1;

CREATE TABLE service_areas (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    business_id TEXT NOT NULL REFERENCES businesses (id),
    extraction_id TEXT NOT NULL REFERENCES extractions (id),
    area_raw TEXT NOT NULL CHECK (length(area_raw) >= 1),
    area_kind TEXT NOT NULL REFERENCES service_area_kind (value),
    area_normalized TEXT,
    radius_km REAL CHECK (radius_km > 0),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    observed_at TEXT NOT NULL CHECK (observed_at IS strftime('%Y-%m-%dT%H:%M:%fZ', observed_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    CHECK ((area_kind = 'radius') = (radius_km IS NOT NULL))
) STRICT;
CREATE INDEX service_areas_by_business ON service_areas (business_id);

CREATE TABLE business_classifications (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    business_id TEXT NOT NULL REFERENCES businesses (id),
    extraction_id TEXT NOT NULL REFERENCES extractions (id),
    kind TEXT NOT NULL REFERENCES classification_kind (value),
    label_raw TEXT NOT NULL CHECK (length(label_raw) >= 1),
    taxonomy_key TEXT,
    taxonomy_version TEXT,
    is_primary INTEGER NOT NULL CHECK (is_primary IN (0, 1)),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    observed_at TEXT NOT NULL CHECK (observed_at IS strftime('%Y-%m-%dT%H:%M:%fZ', observed_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    CHECK ((taxonomy_key IS NULL) = (taxonomy_version IS NULL))
) STRICT;
CREATE INDEX business_classifications_by_business ON business_classifications (business_id);
CREATE INDEX business_classifications_by_taxonomy ON business_classifications (kind, taxonomy_key)
    WHERE taxonomy_key IS NOT NULL;
CREATE UNIQUE INDEX business_classifications_primary ON business_classifications (business_id, kind)
    WHERE is_primary = 1;

-- ============================================================================ websites

CREATE TABLE websites (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    business_id TEXT NOT NULL REFERENCES businesses (id),
    extraction_id TEXT NOT NULL REFERENCES extractions (id),
    url_raw TEXT NOT NULL CHECK (length(url_raw) >= 1),
    url_normalized TEXT NOT NULL CHECK (length(url_normalized) >= 1),
    domain TEXT NOT NULL CHECK (length(domain) >= 1 AND domain = lower(domain)),
    is_primary INTEGER NOT NULL CHECK (is_primary IN (0, 1)),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    observed_at TEXT NOT NULL CHECK (observed_at IS strftime('%Y-%m-%dT%H:%M:%fZ', observed_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at))
) STRICT;
CREATE INDEX websites_by_business ON websites (business_id);
CREATE INDEX websites_by_domain ON websites (domain);
CREATE UNIQUE INDEX websites_primary ON websites (business_id) WHERE is_primary = 1;

CREATE TABLE web_pages (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    website_id TEXT NOT NULL REFERENCES websites (id),
    acquired_page_id TEXT REFERENCES acquired_pages (id),
    url_raw TEXT NOT NULL CHECK (length(url_raw) >= 1),
    url_normalized TEXT NOT NULL CHECK (length(url_normalized) >= 1),
    page_kind TEXT NOT NULL REFERENCES page_kind (value),
    title TEXT,
    observed_at TEXT NOT NULL CHECK (observed_at IS strftime('%Y-%m-%dT%H:%M:%fZ', observed_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    UNIQUE (website_id, url_normalized)
) STRICT;

-- ============================================================================ contacts

CREATE TABLE decision_makers (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    business_id TEXT NOT NULL REFERENCES businesses (id),
    extraction_id TEXT NOT NULL REFERENCES extractions (id),
    full_name_raw TEXT NOT NULL CHECK (length(full_name_raw) >= 1),
    full_name_normalized TEXT NOT NULL,
    role_raw TEXT,
    role_normalized TEXT,
    published_context TEXT,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    observed_at TEXT NOT NULL CHECK (observed_at IS strftime('%Y-%m-%dT%H:%M:%fZ', observed_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    deleted_at TEXT CHECK (deleted_at IS strftime('%Y-%m-%dT%H:%M:%fZ', deleted_at)),
    deleted_reason TEXT,
    CHECK ((deleted_at IS NULL) = (deleted_reason IS NULL))
) STRICT;
CREATE INDEX decision_makers_by_business ON decision_makers (business_id);

CREATE TABLE contact_points (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    business_id TEXT NOT NULL REFERENCES businesses (id),
    location_id TEXT REFERENCES business_locations (id),
    decision_maker_id TEXT REFERENCES decision_makers (id),
    contact_type TEXT NOT NULL REFERENCES contact_type (value),
    value_raw TEXT NOT NULL CHECK (length(value_raw) >= 1),
    value_normalized TEXT,
    origin TEXT NOT NULL REFERENCES contact_origin (value),
    platform TEXT,
    web_page_id TEXT REFERENCES web_pages (id),
    role_or_department TEXT,
    published_context TEXT,
    is_primary INTEGER NOT NULL CHECK (is_primary IN (0, 1)),
    validation_status TEXT NOT NULL REFERENCES validation_status (value),
    validated_at TEXT CHECK (validated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', validated_at)),
    do_not_contact INTEGER NOT NULL CHECK (do_not_contact IN (0, 1)),
    suppression_reason TEXT,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    extraction_id TEXT NOT NULL REFERENCES extractions (id),
    first_seen_at TEXT NOT NULL
        CHECK (first_seen_at IS strftime('%Y-%m-%dT%H:%M:%fZ', first_seen_at)),
    last_seen_at TEXT NOT NULL CHECK (last_seen_at IS strftime('%Y-%m-%dT%H:%M:%fZ', last_seen_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    deleted_at TEXT CHECK (deleted_at IS strftime('%Y-%m-%dT%H:%M:%fZ', deleted_at)),
    deleted_reason TEXT,
    CHECK (last_seen_at >= first_seen_at),
    CHECK ((contact_type = 'social_profile') = (platform IS NOT NULL)),
    CHECK (do_not_contact = 0 OR suppression_reason IS NOT NULL),
    CHECK ((deleted_at IS NULL) = (deleted_reason IS NULL))
) STRICT;
CREATE INDEX contact_points_by_business ON contact_points (business_id);
CREATE INDEX contact_points_by_value ON contact_points (contact_type, value_normalized);
CREATE UNIQUE INDEX contact_points_primary ON contact_points (business_id, contact_type)
    WHERE is_primary = 1 AND deleted_at IS NULL;

CREATE TABLE contact_validations (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    contact_point_id TEXT NOT NULL REFERENCES contact_points (id),
    status TEXT NOT NULL REFERENCES validation_status (value),
    method TEXT NOT NULL CHECK (length(method) BETWEEN 1 AND 64),
    validator_version TEXT NOT NULL CHECK (length(validator_version) >= 1),
    detail_json TEXT CHECK (json_valid(detail_json)),
    validated_at TEXT NOT NULL CHECK (validated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', validated_at)),
    expires_at TEXT CHECK (expires_at IS strftime('%Y-%m-%dT%H:%M:%fZ', expires_at))
) STRICT;
CREATE INDEX contact_validations_by_contact ON contact_validations (contact_point_id, validated_at);

-- ============================================================================ evidence

CREATE TABLE evidence_items (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    business_id TEXT NOT NULL REFERENCES businesses (id),
    evidence_type TEXT NOT NULL REFERENCES evidence_type (value),
    claim TEXT NOT NULL CHECK (length(claim) BETWEEN 1 AND 500),
    value_json TEXT CHECK (json_valid(value_json)),
    strength TEXT NOT NULL REFERENCES evidence_strength (value),
    status TEXT NOT NULL REFERENCES evidence_status (value),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    extraction_id TEXT NOT NULL REFERENCES extractions (id),
    web_page_id TEXT REFERENCES web_pages (id),
    field_path TEXT,
    snippet TEXT CHECK (length(snippet) <= 2000),
    snippet_sha256 TEXT
        CHECK (length(snippet_sha256) = 64 AND snippet_sha256 NOT GLOB '*[^0-9a-f]*'),
    observed_at TEXT NOT NULL CHECK (observed_at IS strftime('%Y-%m-%dT%H:%M:%fZ', observed_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    CHECK ((snippet IS NULL) = (snippet_sha256 IS NULL))
) STRICT;
CREATE INDEX evidence_items_by_business ON evidence_items (business_id, evidence_type, status);
CREATE INDEX evidence_items_by_extraction ON evidence_items (extraction_id);

CREATE TABLE latino_assessments (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    business_id TEXT NOT NULL REFERENCES businesses (id),
    status TEXT NOT NULL REFERENCES latino_status (value),
    assessment_confidence REAL NOT NULL CHECK (assessment_confidence BETWEEN 0 AND 1),
    method TEXT NOT NULL REFERENCES assessment_method (value),
    rules_version TEXT NOT NULL CHECK (length(rules_version) >= 1),
    inference_run_id TEXT REFERENCES ai_inference_runs (id),
    rationale TEXT NOT NULL CHECK (length(rationale) >= 1),
    limitations TEXT,
    assessed_at TEXT NOT NULL CHECK (assessed_at IS strftime('%Y-%m-%dT%H:%M:%fZ', assessed_at)),
    superseded_at TEXT CHECK (superseded_at IS strftime('%Y-%m-%dT%H:%M:%fZ', superseded_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at))
) STRICT;
CREATE UNIQUE INDEX latino_assessments_current ON latino_assessments (business_id)
    WHERE superseded_at IS NULL;
CREATE INDEX latino_assessments_by_business ON latino_assessments (business_id, assessed_at);

CREATE TABLE latino_assessment_evidence (
    assessment_id TEXT NOT NULL REFERENCES latino_assessments (id),
    evidence_item_id TEXT NOT NULL REFERENCES evidence_items (id),
    role TEXT NOT NULL REFERENCES evidence_role (value),
    PRIMARY KEY (assessment_id, evidence_item_id)
) STRICT, WITHOUT ROWID;
CREATE INDEX latino_assessment_evidence_by_evidence
    ON latino_assessment_evidence (evidence_item_id);

-- ============================================================================ website intelligence

CREATE TABLE website_audits (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    website_id TEXT NOT NULL REFERENCES websites (id),
    ruleset_version TEXT NOT NULL CHECK (length(ruleset_version) >= 1),
    started_at TEXT NOT NULL CHECK (started_at IS strftime('%Y-%m-%dT%H:%M:%fZ', started_at)),
    finished_at TEXT CHECK (finished_at IS strftime('%Y-%m-%dT%H:%M:%fZ', finished_at)),
    pages_analyzed INTEGER NOT NULL CHECK (pages_analyzed >= 0),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    CHECK (finished_at IS NULL OR finished_at >= started_at)
) STRICT;
CREATE INDEX website_audits_by_website ON website_audits (website_id, started_at);

CREATE TABLE website_technologies (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    audit_id TEXT NOT NULL REFERENCES website_audits (id),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 300),
    category TEXT,
    version TEXT,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence_item_id TEXT REFERENCES evidence_items (id)
) STRICT;
CREATE INDEX website_technologies_by_audit ON website_technologies (audit_id);

CREATE TABLE conversion_elements (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    audit_id TEXT NOT NULL REFERENCES website_audits (id),
    web_page_id TEXT REFERENCES web_pages (id),
    element_kind TEXT NOT NULL REFERENCES conversion_element_kind (value),
    label TEXT,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence_item_id TEXT REFERENCES evidence_items (id)
) STRICT;
CREATE INDEX conversion_elements_by_audit ON conversion_elements (audit_id);

CREATE TABLE website_findings (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    audit_id TEXT NOT NULL REFERENCES website_audits (id),
    rule_id TEXT NOT NULL CHECK (length(rule_id) BETWEEN 1 AND 64),
    rule_version TEXT NOT NULL CHECK (length(rule_version) >= 1),
    category TEXT NOT NULL CHECK (length(category) BETWEEN 1 AND 64),
    result TEXT NOT NULL REFERENCES website_finding_result (value),
    severity TEXT NOT NULL REFERENCES website_finding_severity (value),
    observed_value TEXT,
    expected_condition TEXT,
    remediation TEXT,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    web_page_id TEXT REFERENCES web_pages (id),
    evidence_item_id TEXT REFERENCES evidence_items (id),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at))
) STRICT;
CREATE INDEX website_findings_by_audit ON website_findings (audit_id, severity);

-- ============================================================================ local AI

CREATE TABLE ai_inference_runs (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    task_type TEXT NOT NULL CHECK (length(task_type) BETWEEN 1 AND 64),
    task_version TEXT NOT NULL CHECK (length(task_version) >= 1),
    provider TEXT NOT NULL CHECK (length(provider) BETWEEN 1 AND 64),
    model TEXT NOT NULL CHECK (length(model) >= 1),
    model_digest TEXT,
    parameters_json TEXT NOT NULL CHECK (json_valid(parameters_json)),
    prompt_template_id TEXT NOT NULL CHECK (length(prompt_template_id) BETWEEN 1 AND 64),
    prompt_template_version TEXT NOT NULL CHECK (length(prompt_template_version) >= 1),
    output_schema_version TEXT NOT NULL CHECK (length(output_schema_version) >= 1),
    input_sha256 TEXT NOT NULL
        CHECK (length(input_sha256) = 64 AND input_sha256 NOT GLOB '*[^0-9a-f]*'),
    status TEXT NOT NULL REFERENCES inference_status (value),
    started_at TEXT CHECK (started_at IS strftime('%Y-%m-%dT%H:%M:%fZ', started_at)),
    finished_at TEXT CHECK (finished_at IS strftime('%Y-%m-%dT%H:%M:%fZ', finished_at)),
    error_message TEXT,
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at))
) STRICT;
CREATE INDEX ai_inference_runs_by_task ON ai_inference_runs (task_type, created_at);
CREATE INDEX ai_inference_runs_by_input ON ai_inference_runs (input_sha256);

CREATE TABLE ai_inference_evidence (
    inference_run_id TEXT NOT NULL REFERENCES ai_inference_runs (id),
    evidence_item_id TEXT NOT NULL REFERENCES evidence_items (id),
    PRIMARY KEY (inference_run_id, evidence_item_id)
) STRICT, WITHOUT ROWID;

CREATE TABLE ai_inference_outputs (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    inference_run_id TEXT NOT NULL REFERENCES ai_inference_runs (id),
    business_id TEXT REFERENCES businesses (id),
    raw_output TEXT NOT NULL,
    validated_output_json TEXT CHECK (json_valid(validated_output_json)),
    confidence REAL CHECK (confidence BETWEEN 0 AND 1),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at))
) STRICT;
CREATE INDEX ai_inference_outputs_by_run ON ai_inference_outputs (inference_run_id);
CREATE INDEX ai_inference_outputs_by_business ON ai_inference_outputs (business_id)
    WHERE business_id IS NOT NULL;

-- ============================================================================ scoring

CREATE TABLE scoring_models (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    key TEXT NOT NULL UNIQUE
        CHECK (length(key) <= 64 AND key GLOB '[a-z]*' AND key NOT GLOB '*[^a-z0-9_.]*'),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 300),
    description TEXT,
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    deleted_at TEXT CHECK (deleted_at IS strftime('%Y-%m-%dT%H:%M:%fZ', deleted_at)),
    deleted_reason TEXT,
    CHECK ((deleted_at IS NULL) = (deleted_reason IS NULL))
) STRICT;

CREATE TABLE scoring_model_versions (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    scoring_model_id TEXT NOT NULL REFERENCES scoring_models (id),
    version INTEGER NOT NULL CHECK (version >= 1),
    definition_json TEXT NOT NULL CHECK (json_valid(definition_json)),
    is_heuristic INTEGER NOT NULL CHECK (is_heuristic IN (0, 1)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    UNIQUE (scoring_model_id, version)
) STRICT;

CREATE TABLE score_runs (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    scoring_model_version_id TEXT NOT NULL REFERENCES scoring_model_versions (id),
    icp_profile_version_id TEXT REFERENCES icp_profile_versions (id),
    dataset_snapshot_id TEXT REFERENCES dataset_snapshots (id),
    job_id TEXT REFERENCES jobs (id),
    started_at TEXT NOT NULL CHECK (started_at IS strftime('%Y-%m-%dT%H:%M:%fZ', started_at)),
    finished_at TEXT CHECK (finished_at IS strftime('%Y-%m-%dT%H:%M:%fZ', finished_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    CHECK (finished_at IS NULL OR finished_at >= started_at)
) STRICT;
CREATE INDEX score_runs_by_model ON score_runs (scoring_model_version_id, started_at);

CREATE TABLE business_scores (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    score_run_id TEXT NOT NULL REFERENCES score_runs (id),
    business_id TEXT NOT NULL REFERENCES businesses (id),
    total_score REAL CHECK (total_score BETWEEN 0 AND 100),
    confidence REAL CHECK (confidence BETWEEN 0 AND 1),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    UNIQUE (score_run_id, business_id)
) STRICT;
CREATE INDEX business_scores_by_business ON business_scores (business_id);

CREATE TABLE component_scores (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    business_score_id TEXT NOT NULL REFERENCES business_scores (id),
    component TEXT NOT NULL
        CHECK (length(component) <= 64 AND component GLOB '[A-Z]*' AND component NOT GLOB '*[^A-Z_]*'),
    value REAL CHECK (value BETWEEN 0 AND 100),
    weight REAL NOT NULL CHECK (weight >= 0),
    contribution REAL,
    confidence REAL CHECK (confidence BETWEEN 0 AND 1),
    UNIQUE (business_score_id, component)
) STRICT;

CREATE TABLE score_explanations (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    business_score_id TEXT NOT NULL REFERENCES business_scores (id),
    component TEXT,
    kind TEXT NOT NULL REFERENCES explanation_kind (value),
    message TEXT NOT NULL CHECK (length(message) >= 1),
    evidence_item_id TEXT REFERENCES evidence_items (id),
    sort_order INTEGER NOT NULL CHECK (sort_order >= 0)
) STRICT;
CREATE INDEX score_explanations_by_score ON score_explanations (business_score_id, sort_order);

-- ============================================================================ jobs

CREATE TABLE jobs (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    project_id TEXT NOT NULL REFERENCES projects (id),
    kind TEXT NOT NULL CHECK (length(kind) BETWEEN 1 AND 64),
    status TEXT NOT NULL REFERENCES job_status (value),
    priority INTEGER NOT NULL,
    params_json TEXT NOT NULL CHECK (json_valid(params_json)),
    idempotency_key TEXT UNIQUE,
    progress_done INTEGER NOT NULL CHECK (progress_done >= 0),
    progress_total INTEGER CHECK (progress_total >= 0),
    requested_at TEXT NOT NULL CHECK (requested_at IS strftime('%Y-%m-%dT%H:%M:%fZ', requested_at)),
    started_at TEXT CHECK (started_at IS strftime('%Y-%m-%dT%H:%M:%fZ', started_at)),
    finished_at TEXT CHECK (finished_at IS strftime('%Y-%m-%dT%H:%M:%fZ', finished_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    CHECK (progress_total IS NULL OR progress_done <= progress_total),
    CHECK (finished_at IS NULL OR (started_at IS NOT NULL AND finished_at >= started_at))
) STRICT;
CREATE INDEX jobs_by_status ON jobs (status, priority DESC, requested_at);
CREATE INDEX jobs_by_project ON jobs (project_id, requested_at);

CREATE TABLE job_tasks (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    job_id TEXT NOT NULL REFERENCES jobs (id),
    stage TEXT NOT NULL CHECK (length(stage) BETWEEN 1 AND 64),
    task_key TEXT NOT NULL CHECK (length(task_key) BETWEEN 1 AND 300),
    status TEXT NOT NULL REFERENCES job_status (value),
    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 0),
    max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    UNIQUE (job_id, task_key)
) STRICT;
CREATE INDEX job_tasks_by_status ON job_tasks (job_id, status);

CREATE TABLE task_attempts (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    task_id TEXT NOT NULL REFERENCES job_tasks (id),
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    status TEXT NOT NULL REFERENCES job_status (value),
    started_at TEXT NOT NULL CHECK (started_at IS strftime('%Y-%m-%dT%H:%M:%fZ', started_at)),
    finished_at TEXT CHECK (finished_at IS strftime('%Y-%m-%dT%H:%M:%fZ', finished_at)),
    error_code TEXT,
    error_message TEXT,
    retryable INTEGER CHECK (retryable IN (0, 1)),
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    UNIQUE (task_id, attempt)
) STRICT;

CREATE TABLE job_checkpoints (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    job_id TEXT NOT NULL REFERENCES jobs (id),
    task_id TEXT REFERENCES job_tasks (id),
    state_json TEXT NOT NULL CHECK (json_valid(state_json)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at))
) STRICT;
CREATE INDEX job_checkpoints_by_job ON job_checkpoints (job_id, created_at);

CREATE TABLE job_events (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    job_id TEXT NOT NULL REFERENCES jobs (id),
    task_id TEXT REFERENCES job_tasks (id),
    level TEXT NOT NULL REFERENCES event_level (value),
    event TEXT NOT NULL CHECK (length(event) BETWEEN 1 AND 64),
    error_code TEXT,
    retryable INTEGER CHECK (retryable IN (0, 1)),
    detail_json TEXT CHECK (json_valid(detail_json)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    CHECK ((level = 'error') = (error_code IS NOT NULL))
) STRICT;
CREATE INDEX job_events_by_job ON job_events (job_id, created_at);

CREATE TABLE job_metrics (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    job_id TEXT NOT NULL REFERENCES jobs (id),
    task_id TEXT REFERENCES job_tasks (id),
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 64),
    value REAL NOT NULL,
    unit TEXT,
    recorded_at TEXT NOT NULL CHECK (recorded_at IS strftime('%Y-%m-%dT%H:%M:%fZ', recorded_at))
) STRICT;
CREATE INDEX job_metrics_by_job ON job_metrics (job_id, name, recorded_at);

-- ============================================================================ datasets and exports

CREATE TABLE datasets (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    project_id TEXT NOT NULL REFERENCES projects (id),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 300),
    description TEXT,
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    deleted_at TEXT CHECK (deleted_at IS strftime('%Y-%m-%dT%H:%M:%fZ', deleted_at)),
    deleted_reason TEXT,
    CHECK ((deleted_at IS NULL) = (deleted_reason IS NULL))
) STRICT;
CREATE UNIQUE INDEX datasets_live_name ON datasets (project_id, name) WHERE deleted_at IS NULL;
CREATE INDEX datasets_by_created ON datasets (created_at, id) WHERE deleted_at IS NULL;

CREATE TABLE dataset_snapshots (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    dataset_id TEXT NOT NULL REFERENCES datasets (id),
    version INTEGER NOT NULL CHECK (version >= 1),
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    duckdb_table TEXT NOT NULL CHECK (length(duckdb_table) BETWEEN 1 AND 64),
    parquet_path TEXT NOT NULL CHECK (
        parquet_path NOT GLOB '/*' AND parquet_path NOT GLOB '*\*' AND parquet_path NOT GLOB '*:*'
        AND '/' || parquet_path || '/' NOT GLOB '*/../*'),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    filters_json TEXT NOT NULL CHECK (json_valid(filters_json)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    UNIQUE (dataset_id, version)
) STRICT;

CREATE TABLE dataset_snapshot_sources (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    snapshot_id TEXT NOT NULL REFERENCES dataset_snapshots (id),
    source_run_id TEXT REFERENCES source_runs (id),
    score_run_id TEXT REFERENCES score_runs (id),
    parent_snapshot_id TEXT REFERENCES dataset_snapshots (id),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    CHECK ((source_run_id IS NOT NULL) + (score_run_id IS NOT NULL)
        + (parent_snapshot_id IS NOT NULL) = 1)
) STRICT;
CREATE INDEX dataset_snapshot_sources_by_snapshot ON dataset_snapshot_sources (snapshot_id);

CREATE TABLE exports (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    project_id TEXT NOT NULL REFERENCES projects (id),
    dataset_snapshot_id TEXT REFERENCES dataset_snapshots (id),
    status TEXT NOT NULL REFERENCES export_status (value),
    format TEXT NOT NULL REFERENCES export_format (value),
    file_name TEXT,
    row_count INTEGER CHECK (row_count >= 0),
    file_sha256 TEXT CHECK (length(file_sha256) = 64 AND file_sha256 NOT GLOB '*[^0-9a-f]*'),
    manifest_json TEXT CHECK (json_valid(manifest_json)),
    requested_at TEXT NOT NULL CHECK (requested_at IS strftime('%Y-%m-%dT%H:%M:%fZ', requested_at)),
    started_at TEXT CHECK (started_at IS strftime('%Y-%m-%dT%H:%M:%fZ', started_at)),
    finished_at TEXT CHECK (finished_at IS strftime('%Y-%m-%dT%H:%M:%fZ', finished_at)),
    error_message TEXT,
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at))
) STRICT;
CREATE INDEX exports_by_project ON exports (project_id, requested_at);

-- ============================================================================ review and audit

CREATE TABLE tags (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    project_id TEXT NOT NULL REFERENCES projects (id),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 64),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    deleted_at TEXT CHECK (deleted_at IS strftime('%Y-%m-%dT%H:%M:%fZ', deleted_at)),
    deleted_reason TEXT,
    CHECK ((deleted_at IS NULL) = (deleted_reason IS NULL))
) STRICT;
CREATE UNIQUE INDEX tags_live_name ON tags (project_id, name) WHERE deleted_at IS NULL;

-- entity_id is polymorphic (entity_type says which table); ids are globally unique UUIDs
CREATE TABLE entity_tags (
    tag_id TEXT NOT NULL REFERENCES tags (id),
    entity_type TEXT NOT NULL REFERENCES entity_type (value),
    entity_id TEXT NOT NULL CHECK (length(entity_id) = 36),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    PRIMARY KEY (tag_id, entity_type, entity_id)
) STRICT, WITHOUT ROWID;
CREATE INDEX entity_tags_by_entity ON entity_tags (entity_type, entity_id);

CREATE TABLE notes (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    project_id TEXT NOT NULL REFERENCES projects (id),
    entity_type TEXT NOT NULL REFERENCES entity_type (value),
    entity_id TEXT NOT NULL CHECK (length(entity_id) = 36),
    body TEXT NOT NULL CHECK (length(body) BETWEEN 1 AND 20000),
    author TEXT NOT NULL CHECK (length(author) >= 1),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    deleted_at TEXT CHECK (deleted_at IS strftime('%Y-%m-%dT%H:%M:%fZ', deleted_at)),
    deleted_reason TEXT,
    CHECK ((deleted_at IS NULL) = (deleted_reason IS NULL))
) STRICT;
CREATE INDEX notes_by_entity ON notes (entity_type, entity_id);

CREATE TABLE review_decisions (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    entity_type TEXT NOT NULL REFERENCES entity_type (value),
    entity_id TEXT NOT NULL CHECK (length(entity_id) = 36),
    decision TEXT NOT NULL CHECK (length(decision) BETWEEN 1 AND 64),
    previous_value TEXT,
    new_value TEXT,
    reason TEXT NOT NULL CHECK (length(trim(reason)) >= 1),
    actor TEXT NOT NULL CHECK (length(actor) >= 1),
    decided_at TEXT NOT NULL CHECK (decided_at IS strftime('%Y-%m-%dT%H:%M:%fZ', decided_at))
) STRICT;
CREATE INDEX review_decisions_by_entity ON review_decisions (entity_type, entity_id, decided_at);

CREATE TABLE audit_log (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    occurred_at TEXT NOT NULL CHECK (occurred_at IS strftime('%Y-%m-%dT%H:%M:%fZ', occurred_at)),
    actor TEXT NOT NULL CHECK (length(actor) >= 1),
    action TEXT NOT NULL REFERENCES audit_action (value),
    entity_type TEXT NOT NULL REFERENCES entity_type (value),
    entity_id TEXT NOT NULL CHECK (length(entity_id) = 36),
    detail_json TEXT CHECK (json_valid(detail_json))
) STRICT;
CREATE INDEX audit_log_by_entity ON audit_log (entity_type, entity_id, occurred_at);

-- ============================================================================ full-text search

-- rowid = businesses.seq. Kept in sync by the triggers below; soft-deleted rows are filtered by
-- the query, so a soft delete does not need to touch the index.
CREATE VIRTUAL TABLE business_search USING fts5 (
    name, other_names, place, tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER business_search_insert AFTER INSERT ON businesses BEGIN
    INSERT INTO business_search (rowid, name, other_names, place)
    VALUES (NEW.seq, NEW.display_name, '', '');
END;

CREATE TRIGGER business_search_rename AFTER UPDATE OF display_name ON businesses BEGIN
    UPDATE business_search SET name = NEW.display_name WHERE rowid = NEW.seq;
END;

CREATE TRIGGER business_search_names AFTER INSERT ON business_names BEGIN
    UPDATE business_search
    SET other_names = (
        SELECT group_concat(name_raw, ' ') FROM business_names WHERE business_id = NEW.business_id)
    WHERE rowid = (SELECT seq FROM businesses WHERE id = NEW.business_id);
END;

CREATE TRIGGER business_search_places AFTER INSERT ON business_locations BEGIN
    UPDATE business_search
    SET place = (
        SELECT group_concat(concat_ws(' ', locality, region, postal_code), ' ')
        FROM business_locations WHERE business_id = NEW.business_id)
    WHERE rowid = (SELECT seq FROM businesses WHERE id = NEW.business_id);
END;

-- ============================================================================ status transitions

CREATE TRIGGER businesses_status_transition BEFORE UPDATE OF status ON businesses
WHEN NEW.status IS NOT OLD.status AND NOT EXISTS (
    SELECT 1 FROM status_transitions
    WHERE enum_name = 'business_status' AND from_value = OLD.status AND to_value = NEW.status)
BEGIN SELECT RAISE(ABORT, 'invalid business_status transition'); END;

CREATE TRIGGER businesses_lead_stage_transition BEFORE UPDATE OF lead_stage ON businesses
WHEN NEW.lead_stage IS NOT OLD.lead_stage AND NOT EXISTS (
    SELECT 1 FROM status_transitions
    WHERE enum_name = 'lead_stage' AND from_value = OLD.lead_stage AND to_value = NEW.lead_stage)
BEGIN SELECT RAISE(ABORT, 'invalid lead_stage transition'); END;

CREATE TRIGGER source_runs_status_transition BEFORE UPDATE OF status ON source_runs
WHEN NEW.status IS NOT OLD.status AND NOT EXISTS (
    SELECT 1 FROM status_transitions
    WHERE enum_name = 'source_run_status' AND from_value = OLD.status AND to_value = NEW.status)
BEGIN SELECT RAISE(ABORT, 'invalid source_run_status transition'); END;

CREATE TRIGGER jobs_status_transition BEFORE UPDATE OF status ON jobs
WHEN NEW.status IS NOT OLD.status AND NOT EXISTS (
    SELECT 1 FROM status_transitions
    WHERE enum_name = 'job_status' AND from_value = OLD.status AND to_value = NEW.status)
BEGIN SELECT RAISE(ABORT, 'invalid job_status transition'); END;

CREATE TRIGGER job_tasks_status_transition BEFORE UPDATE OF status ON job_tasks
WHEN NEW.status IS NOT OLD.status AND NOT EXISTS (
    SELECT 1 FROM status_transitions
    WHERE enum_name = 'job_status' AND from_value = OLD.status AND to_value = NEW.status)
BEGIN SELECT RAISE(ABORT, 'invalid job_status transition'); END;

CREATE TRIGGER task_attempts_status_transition BEFORE UPDATE OF status ON task_attempts
WHEN NEW.status IS NOT OLD.status AND NOT EXISTS (
    SELECT 1 FROM status_transitions
    WHERE enum_name = 'job_status' AND from_value = OLD.status AND to_value = NEW.status)
BEGIN SELECT RAISE(ABORT, 'invalid job_status transition'); END;

CREATE TRIGGER evidence_items_status_transition BEFORE UPDATE OF status ON evidence_items
WHEN NEW.status IS NOT OLD.status AND NOT EXISTS (
    SELECT 1 FROM status_transitions
    WHERE enum_name = 'evidence_status' AND from_value = OLD.status AND to_value = NEW.status)
BEGIN SELECT RAISE(ABORT, 'invalid evidence_status transition'); END;

CREATE TRIGGER contact_points_validation_transition BEFORE UPDATE OF validation_status
ON contact_points
WHEN NEW.validation_status IS NOT OLD.validation_status AND NOT EXISTS (
    SELECT 1 FROM status_transitions
    WHERE enum_name = 'validation_status' AND from_value = OLD.validation_status
        AND to_value = NEW.validation_status)
BEGIN SELECT RAISE(ABORT, 'invalid validation_status transition'); END;

CREATE TRIGGER ai_inference_runs_status_transition BEFORE UPDATE OF status ON ai_inference_runs
WHEN NEW.status IS NOT OLD.status AND NOT EXISTS (
    SELECT 1 FROM status_transitions
    WHERE enum_name = 'inference_status' AND from_value = OLD.status AND to_value = NEW.status)
BEGIN SELECT RAISE(ABORT, 'invalid inference_status transition'); END;

CREATE TRIGGER exports_status_transition BEFORE UPDATE OF status ON exports
WHEN NEW.status IS NOT OLD.status AND NOT EXISTS (
    SELECT 1 FROM status_transitions
    WHERE enum_name = 'export_status' AND from_value = OLD.status AND to_value = NEW.status)
BEGIN SELECT RAISE(ABORT, 'invalid export_status transition'); END;

-- ============================================================================ immutability

CREATE TRIGGER raw_records_immutable BEFORE UPDATE ON raw_records
BEGIN SELECT RAISE(ABORT, 'raw_records are immutable'); END;
CREATE TRIGGER raw_records_permanent BEFORE DELETE ON raw_records
BEGIN SELECT RAISE(ABORT, 'raw_records are immutable'); END;

CREATE TRIGGER extractions_immutable BEFORE UPDATE ON extractions
BEGIN SELECT RAISE(ABORT, 'extractions are immutable'); END;
CREATE TRIGGER extractions_permanent BEFORE DELETE ON extractions
BEGIN SELECT RAISE(ABORT, 'extractions are immutable'); END;

CREATE TRIGGER audit_log_immutable BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER audit_log_permanent BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TRIGGER review_decisions_immutable BEFORE UPDATE ON review_decisions
BEGIN SELECT RAISE(ABORT, 'review_decisions are immutable'); END;
CREATE TRIGGER review_decisions_permanent BEFORE DELETE ON review_decisions
BEGIN SELECT RAISE(ABORT, 'review_decisions are immutable'); END;

CREATE TRIGGER icp_profile_versions_immutable BEFORE UPDATE ON icp_profile_versions
BEGIN SELECT RAISE(ABORT, 'icp_profile_versions are immutable'); END;

CREATE TRIGGER scoring_model_versions_immutable BEFORE UPDATE ON scoring_model_versions
BEGIN SELECT RAISE(ABORT, 'scoring_model_versions are immutable'); END;

CREATE TRIGGER contact_validations_immutable BEFORE UPDATE ON contact_validations
BEGIN SELECT RAISE(ABORT, 'contact_validations are immutable'); END;

CREATE TRIGGER ai_inference_outputs_immutable BEFORE UPDATE ON ai_inference_outputs
BEGIN SELECT RAISE(ABORT, 'ai_inference_outputs are immutable'); END;

CREATE TRIGGER business_scores_immutable BEFORE UPDATE ON business_scores
BEGIN SELECT RAISE(ABORT, 'business_scores are immutable'); END;
CREATE TRIGGER component_scores_immutable BEFORE UPDATE ON component_scores
BEGIN SELECT RAISE(ABORT, 'component_scores are immutable'); END;
CREATE TRIGGER score_explanations_immutable BEFORE UPDATE ON score_explanations
BEGIN SELECT RAISE(ABORT, 'score_explanations are immutable'); END;

CREATE TRIGGER job_events_immutable BEFORE UPDATE ON job_events
BEGIN SELECT RAISE(ABORT, 'job_events are immutable'); END;

CREATE TRIGGER dataset_snapshots_immutable BEFORE UPDATE ON dataset_snapshots
BEGIN SELECT RAISE(ABORT, 'dataset_snapshots are immutable'); END;
CREATE TRIGGER dataset_snapshot_sources_immutable BEFORE UPDATE ON dataset_snapshot_sources
BEGIN SELECT RAISE(ABORT, 'dataset_snapshot_sources are immutable'); END;

-- evidence: only its status (and the matching updated_at) may change
CREATE TRIGGER evidence_items_immutable_claim BEFORE UPDATE OF
    business_id, evidence_type, claim, value_json, strength, confidence, extraction_id,
    web_page_id, field_path, snippet, snippet_sha256, observed_at, created_at
ON evidence_items
BEGIN SELECT RAISE(ABORT, 'evidence_items are immutable except their status'); END;

-- assessments: only superseded_at may be set, once
CREATE TRIGGER latino_assessments_immutable BEFORE UPDATE ON latino_assessments
WHEN OLD.superseded_at IS NOT NULL
    OR NEW.id IS NOT OLD.id OR NEW.business_id IS NOT OLD.business_id
    OR NEW.status IS NOT OLD.status OR NEW.assessment_confidence IS NOT OLD.assessment_confidence
    OR NEW.method IS NOT OLD.method OR NEW.rules_version IS NOT OLD.rules_version
    OR NEW.inference_run_id IS NOT OLD.inference_run_id OR NEW.rationale IS NOT OLD.rationale
    OR NEW.limitations IS NOT OLD.limitations OR NEW.assessed_at IS NOT OLD.assessed_at
    OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT, 'latino_assessments are immutable; supersede them instead'); END;

-- purge = soft delete first (audited), then delete; a live row cannot be deleted directly
CREATE TRIGGER projects_soft_delete_first BEFORE DELETE ON projects WHEN OLD.deleted_at IS NULL
BEGIN SELECT RAISE(ABORT, 'soft-delete projects before purging them'); END;
CREATE TRIGGER campaigns_soft_delete_first BEFORE DELETE ON campaigns WHEN OLD.deleted_at IS NULL
BEGIN SELECT RAISE(ABORT, 'soft-delete campaigns before purging them'); END;
CREATE TRIGGER icp_profiles_soft_delete_first BEFORE DELETE ON icp_profiles
WHEN OLD.deleted_at IS NULL
BEGIN SELECT RAISE(ABORT, 'soft-delete icp_profiles before purging them'); END;
CREATE TRIGGER businesses_soft_delete_first BEFORE DELETE ON businesses
WHEN OLD.deleted_at IS NULL
BEGIN SELECT RAISE(ABORT, 'soft-delete businesses before purging them'); END;
CREATE TRIGGER decision_makers_soft_delete_first BEFORE DELETE ON decision_makers
WHEN OLD.deleted_at IS NULL
BEGIN SELECT RAISE(ABORT, 'soft-delete decision_makers before purging them'); END;
CREATE TRIGGER contact_points_soft_delete_first BEFORE DELETE ON contact_points
WHEN OLD.deleted_at IS NULL
BEGIN SELECT RAISE(ABORT, 'soft-delete contact_points before purging them'); END;
CREATE TRIGGER scoring_models_soft_delete_first BEFORE DELETE ON scoring_models
WHEN OLD.deleted_at IS NULL
BEGIN SELECT RAISE(ABORT, 'soft-delete scoring_models before purging them'); END;
CREATE TRIGGER datasets_soft_delete_first BEFORE DELETE ON datasets WHEN OLD.deleted_at IS NULL
BEGIN SELECT RAISE(ABORT, 'soft-delete datasets before purging them'); END;
CREATE TRIGGER tags_soft_delete_first BEFORE DELETE ON tags WHEN OLD.deleted_at IS NULL
BEGIN SELECT RAISE(ABORT, 'soft-delete tags before purging them'); END;
CREATE TRIGGER notes_soft_delete_first BEFORE DELETE ON notes WHEN OLD.deleted_at IS NULL
BEGIN SELECT RAISE(ABORT, 'soft-delete notes before purging them'); END;

INSERT INTO store_metadata (key, value, updated_at)
VALUES ('data_contract_version', '1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
