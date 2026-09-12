-- 0004 · Web acquisition, data contract v1 (phase 05).
--
-- `crawl_urls` is at once the frontier of a site crawl and its fetch log. Every URL the crawler
-- decided something about gets one row per site run: queued until it is fetched, skipped (with the
-- policy that stopped it: robots, budget, trap, scope...) or failed (with the error that stopped it).
-- A row is final once it leaves `queued`, so the log cannot be rewritten after the fact, and
-- `UNIQUE (source_run_id, url)` is what makes a resumed crawl never fetch or store a URL twice.
--
-- The bytes stay where phase 03 put them: `acquired_pages` (status, content type, sha256, the body
-- stored as a content-addressed artifact) and `raw_records` (the parsed page as raw findings). This
-- table adds what a crawl needs on top: method, timings, redirects, allowed headers, the reason for a
-- browser fallback and its cost, and the content hash that spots identical pages.

INSERT INTO source_kind VALUES ('business_website');

CREATE TABLE crawl_url_status (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO crawl_url_status VALUES ('queued'), ('fetched'), ('skipped'), ('failed');

CREATE TABLE crawl_target (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO crawl_target VALUES ('page'), ('robots'), ('sitemap');

INSERT INTO status_transitions VALUES
    ('crawl_url_status', 'queued', 'fetched'),
    ('crawl_url_status', 'queued', 'skipped'),
    ('crawl_url_status', 'queued', 'failed');

CREATE TABLE crawl_urls (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    source_run_id TEXT NOT NULL REFERENCES source_runs (id),
    candidate_id TEXT REFERENCES discovery_candidates (id),
    discovered_from_id TEXT REFERENCES crawl_urls (id),
    url TEXT NOT NULL CHECK (length(url) BETWEEN 1 AND 2048),
    target TEXT NOT NULL REFERENCES crawl_target (value),
    page_kind TEXT NOT NULL REFERENCES page_kind (value),
    depth INTEGER NOT NULL CHECK (depth >= 0),
    priority INTEGER NOT NULL,
    status TEXT NOT NULL REFERENCES crawl_url_status (value),
    reason TEXT CHECK (reason IS NULL OR length(reason) BETWEEN 1 AND 64),
    method TEXT REFERENCES acquisition_method (value),
    final_url TEXT,
    http_status INTEGER CHECK (http_status BETWEEN 100 AND 599),
    content_type TEXT,
    byte_size INTEGER CHECK (byte_size >= 0),
    body_sha256 TEXT CHECK (length(body_sha256) = 64 AND body_sha256 NOT GLOB '*[^0-9a-f]*'),
    content_sha256 TEXT
        CHECK (length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
    duplicate_of_id TEXT REFERENCES crawl_urls (id),
    acquired_page_id TEXT REFERENCES acquired_pages (id),
    raw_record_id TEXT REFERENCES raw_records (id),
    request_count INTEGER NOT NULL CHECK (request_count >= 0),
    retry_count INTEGER NOT NULL CHECK (retry_count >= 0),
    fetch_ms INTEGER CHECK (fetch_ms >= 0),
    browser_ms INTEGER CHECK (browser_ms >= 0),
    fallback_reason TEXT CHECK (fallback_reason IS NULL OR length(fallback_reason) BETWEEN 1 AND 64),
    redirects_json TEXT CHECK (json_valid(redirects_json)),
    headers_json TEXT CHECK (json_valid(headers_json)),
    screenshot_path TEXT CHECK (
        screenshot_path NOT GLOB '/*' AND screenshot_path NOT GLOB '*\*'
        AND screenshot_path NOT GLOB '*:*' AND '/' || screenshot_path || '/' NOT GLOB '*/../*'),
    error_message TEXT,
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    CHECK (status IN ('queued', 'fetched') OR reason IS NOT NULL),
    CHECK (duplicate_of_id IS NOT id),
    UNIQUE (source_run_id, url)
) STRICT;
CREATE INDEX crawl_urls_frontier ON crawl_urls (source_run_id, status, priority DESC);
CREATE INDEX crawl_urls_fetched_by_url ON crawl_urls (url) WHERE status = 'fetched';

CREATE TRIGGER crawl_urls_status_transition BEFORE UPDATE OF status ON crawl_urls
WHEN NEW.status IS NOT OLD.status AND NOT EXISTS (
    SELECT 1 FROM status_transitions
    WHERE enum_name = 'crawl_url_status' AND from_value = OLD.status AND to_value = NEW.status)
BEGIN SELECT RAISE(ABORT, 'invalid crawl_url_status transition'); END;

-- a decision is final: what was fetched, skipped or failed is never rewritten, only read
CREATE TRIGGER crawl_urls_final BEFORE UPDATE ON crawl_urls WHEN OLD.status <> 'queued'
BEGIN SELECT RAISE(ABORT, 'crawl_urls are final once they leave queued'); END;
CREATE TRIGGER crawl_urls_permanent BEFORE DELETE ON crawl_urls
BEGIN SELECT RAISE(ABORT, 'crawl_urls are permanent'); END;
