-- 0003 · Discovery candidates, data contract v1 (phase 04).
--
-- A candidate is the light, listable projection of one immutable `raw_record`: enough to show it,
-- group it and spot an obvious repeat, and nothing more. It is NOT a business and not a lead: the
-- entity is resolved later (phase 06), so this table carries no canonical id and no score.
--
-- `dedup_key` is the connector's cheap identity guess (normalised name + place, or a stable source
-- id). Rows that repeat a key already seen in the same campaign point at the first one through
-- `duplicate_of_id`; nothing is merged or discarded. Candidates never cross campaigns: every read
-- filters by campaign or by run, and lineage stays on `source_run_id` -> `raw_record_id`.

CREATE TABLE discovery_candidates (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    project_id TEXT NOT NULL REFERENCES projects (id),
    campaign_id TEXT REFERENCES campaigns (id),
    source_run_id TEXT NOT NULL REFERENCES source_runs (id),
    raw_record_id TEXT NOT NULL REFERENCES raw_records (id),
    connector_id TEXT NOT NULL REFERENCES source_connectors (id),
    dedup_key TEXT NOT NULL CHECK (length(dedup_key) BETWEEN 1 AND 300),
    duplicate_of_id TEXT REFERENCES discovery_candidates (id),
    name_raw TEXT NOT NULL CHECK (length(trim(name_raw)) BETWEEN 1 AND 300),
    name_normalized TEXT NOT NULL CHECK (length(name_normalized) >= 1),
    locality TEXT,
    region TEXT,
    postal_code TEXT,
    country_code TEXT CHECK (country_code IS NULL OR length(country_code) = 2),
    domain TEXT,
    website_url TEXT,
    phone_raw TEXT,
    category_raw TEXT,
    external_id TEXT,
    source_url TEXT,
    discovered_at TEXT NOT NULL
        CHECK (discovered_at IS strftime('%Y-%m-%dT%H:%M:%fZ', discovered_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    CHECK (duplicate_of_id IS NOT id),
    UNIQUE (source_run_id, raw_record_id)
) STRICT;
CREATE INDEX discovery_candidates_by_run ON discovery_candidates (source_run_id, created_at);
CREATE INDEX discovery_candidates_by_project ON discovery_candidates (project_id, created_at);
CREATE INDEX discovery_candidates_by_key ON discovery_candidates (campaign_id, dedup_key)
    WHERE campaign_id IS NOT NULL;
CREATE INDEX discovery_candidates_duplicates ON discovery_candidates (duplicate_of_id)
    WHERE duplicate_of_id IS NOT NULL;

-- Same rule as the raw record it projects: written once, never edited, never removed.
CREATE TRIGGER discovery_candidates_immutable BEFORE UPDATE ON discovery_candidates
BEGIN SELECT RAISE(ABORT, 'discovery_candidates are immutable'); END;
CREATE TRIGGER discovery_candidates_permanent BEFORE DELETE ON discovery_candidates
BEGIN SELECT RAISE(ABORT, 'discovery_candidates are immutable'); END;
