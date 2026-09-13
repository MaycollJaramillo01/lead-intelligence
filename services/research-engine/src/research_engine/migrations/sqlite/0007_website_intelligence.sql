-- 0007 · Website intelligence, data contract v1 (phase 08).
--
-- Phase 03 sketched the audit tables around `web_pages` and `evidence_items`, which nothing fills
-- for a crawled site, and no engine ever wrote them: they are empty in every store. They are rebuilt
-- here around what an audit actually reads:
--
--   * an audit belongs to a project and a crawled site, cites the crawl source run it read, the
--     versions of the extractor and of the rules, and the previous audit of the same site;
--   * `snapshot_json` holds every fact the rules read, so an audit can be recalculated with newer
--     rules without touching it; `summary_json` the formula, subscores, coverage and the pages with
--     their lab measurements; `diff_json` what changed since the previous audit;
--   * findings, technologies and conversion elements carry their evidence references in
--     `evidence_json`: the crawled URL, the field read and the fragment.
--
-- An audit and everything in it is history: never updated, never deleted.

CREATE TABLE website_category (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO website_category VALUES
    ('technical'), ('seo'), ('local_seo'), ('content'), ('conversion'), ('mobile'),
    ('accessibility'), ('measurement'), ('aeo_geo_readiness');

CREATE TABLE website_audit_status (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO website_audit_status VALUES ('complete'), ('partial'), ('empty');

DROP TABLE website_findings;
DROP TABLE conversion_elements;
DROP TABLE website_technologies;
DROP TABLE website_audits;

CREATE TABLE website_audits (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    project_id TEXT NOT NULL REFERENCES projects (id),
    site TEXT NOT NULL CHECK (length(site) BETWEEN 1 AND 300),
    root_url TEXT NOT NULL CHECK (length(root_url) BETWEEN 1 AND 2048),
    final_url TEXT CHECK (length(final_url) BETWEEN 1 AND 2048),
    source_run_id TEXT NOT NULL REFERENCES source_runs (id),
    job_id TEXT REFERENCES jobs (id),
    previous_audit_id TEXT REFERENCES website_audits (id),
    status TEXT NOT NULL REFERENCES website_audit_status (value),
    ruleset_version TEXT NOT NULL CHECK (length(ruleset_version) BETWEEN 1 AND 300),
    extractor_version TEXT NOT NULL CHECK (length(extractor_version) BETWEEN 1 AND 300),
    pages_expected INTEGER NOT NULL CHECK (pages_expected >= 0),
    pages_analyzed INTEGER NOT NULL CHECK (pages_analyzed BETWEEN 0 AND pages_expected),
    coverage REAL NOT NULL CHECK (coverage BETWEEN 0 AND 1),
    summary_json TEXT NOT NULL CHECK (json_valid(summary_json)),
    snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json)),
    diff_json TEXT CHECK (json_valid(diff_json)),
    crawled_at TEXT NOT NULL CHECK (crawled_at IS strftime('%Y-%m-%dT%H:%M:%fZ', crawled_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    CHECK (previous_audit_id IS NOT id),
    CHECK ((status = 'empty') = (pages_analyzed = 0))
) STRICT;
CREATE INDEX website_audits_by_site ON website_audits (project_id, site, created_at);
CREATE INDEX website_audits_by_run ON website_audits (source_run_id);

CREATE TABLE website_findings (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    audit_id TEXT NOT NULL REFERENCES website_audits (id),
    rule_id TEXT NOT NULL CHECK (length(rule_id) BETWEEN 1 AND 64),
    rule_version TEXT NOT NULL CHECK (length(rule_version) BETWEEN 1 AND 300),
    title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 300),
    category TEXT NOT NULL REFERENCES website_category (value),
    result TEXT NOT NULL REFERENCES website_finding_result (value),
    severity TEXT NOT NULL REFERENCES website_finding_severity (value),
    observed_value TEXT,
    expected_condition TEXT NOT NULL CHECK (length(expected_condition) >= 1),
    remediation TEXT,
    commercial_relevance TEXT,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    UNIQUE (audit_id, rule_id)
) STRICT;
CREATE INDEX website_findings_by_rule ON website_findings (rule_id, result);

CREATE TABLE website_technologies (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    audit_id TEXT NOT NULL REFERENCES website_audits (id),
    technology_key TEXT NOT NULL CHECK (length(technology_key) BETWEEN 1 AND 64),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 300),
    category TEXT NOT NULL CHECK (length(category) BETWEEN 1 AND 64),
    version TEXT,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
    UNIQUE (audit_id, technology_key)
) STRICT;
CREATE INDEX website_technologies_by_key ON website_technologies (technology_key);

CREATE TABLE conversion_elements (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    audit_id TEXT NOT NULL REFERENCES website_audits (id),
    element_kind TEXT NOT NULL REFERENCES conversion_element_kind (value),
    label TEXT,
    page_url TEXT NOT NULL CHECK (length(page_url) BETWEEN 1 AND 2048),
    target TEXT,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json))
) STRICT;
CREATE INDEX conversion_elements_by_audit ON conversion_elements (audit_id, element_kind);

CREATE TRIGGER website_audits_immutable BEFORE UPDATE ON website_audits
BEGIN SELECT RAISE(ABORT, 'website_audits are history; audit again instead'); END;
CREATE TRIGGER website_audits_permanent BEFORE DELETE ON website_audits
BEGIN SELECT RAISE(ABORT, 'website_audits are permanent'); END;

CREATE TRIGGER website_findings_immutable BEFORE UPDATE ON website_findings
BEGIN SELECT RAISE(ABORT, 'website_findings are history'); END;
CREATE TRIGGER website_findings_permanent BEFORE DELETE ON website_findings
BEGIN SELECT RAISE(ABORT, 'website_findings are permanent'); END;

CREATE TRIGGER website_technologies_immutable BEFORE UPDATE ON website_technologies
BEGIN SELECT RAISE(ABORT, 'website_technologies are history'); END;
CREATE TRIGGER website_technologies_permanent BEFORE DELETE ON website_technologies
BEGIN SELECT RAISE(ABORT, 'website_technologies are permanent'); END;

CREATE TRIGGER conversion_elements_immutable BEFORE UPDATE ON conversion_elements
BEGIN SELECT RAISE(ABORT, 'conversion_elements are history'); END;
CREATE TRIGGER conversion_elements_permanent BEFORE DELETE ON conversion_elements
BEGIN SELECT RAISE(ABORT, 'conversion_elements are permanent'); END;
