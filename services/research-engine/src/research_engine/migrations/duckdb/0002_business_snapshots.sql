-- Analytical copy of canonical businesses: one immutable set of rows per dataset snapshot.
-- The snapshot itself (version, lineage, Parquet location) is registered in SQLite; the column
-- list here must match research_engine/datasets.py::BUSINESS_SNAPSHOT_SCHEMA (tested).
-- Timestamps are UTC (TIMESTAMP without zone, by convention of the data contract).
CREATE TABLE business_snapshot_rows (
    snapshot_id VARCHAR NOT NULL,
    business_id VARCHAR NOT NULL,
    project_id VARCHAR NOT NULL,
    display_name VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    lead_stage VARCHAR NOT NULL,
    locality VARCHAR,
    region VARCHAR,
    postal_code VARCHAR,
    primary_domain VARCHAR,
    primary_phone VARCHAR,
    primary_category VARCHAR,
    source_count INTEGER NOT NULL,
    evidence_count INTEGER NOT NULL,
    first_seen_at TIMESTAMP NOT NULL,
    last_seen_at TIMESTAMP NOT NULL,
    PRIMARY KEY (snapshot_id, business_id)
);
