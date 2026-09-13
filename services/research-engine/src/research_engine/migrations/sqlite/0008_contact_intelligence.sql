-- 0008 · Contact intelligence, data contract v1 (phase 09).
--
-- Phase 03 created contact_points, contact_validations and decision_makers; phase 06 fills
-- contact_points with the phones, emails and profiles its sources and crawled pages publish. This
-- migration adds what validating, reviewing and suppressing them needs:
--
--   * contact_points.validation_method: how the current validation_status was reached;
--   * a hypothesis (an email built from a published address pattern) is never primary and never
--     carries a status that reads as validated, whatever writes the row;
--   * decision_makers is rebuilt (nothing ever wrote it) with the relationship its published role
--     states and the review status of the queue of ambiguous people;
--   * contact_suppressions: the local do-not-contact list (email, phone, domain, business) with its
--     reason, source and date; lifting an entry keeps it as history;
--   * mail_domain_checks: the cache of mail-domain lookups and when each answer expires.

CREATE TABLE suppression_kind (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO suppression_kind VALUES ('email'), ('phone'), ('domain'), ('business');

CREATE TABLE decision_maker_review (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO decision_maker_review VALUES ('unreviewed'), ('ambiguous'), ('confirmed'), ('rejected');

CREATE TABLE decision_maker_relationship (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO decision_maker_relationship VALUES
    ('owner'), ('founder'), ('executive'), ('partner'), ('manager'), ('unknown');

CREATE TABLE mail_domain_result (value TEXT PRIMARY KEY NOT NULL) STRICT, WITHOUT ROWID;
INSERT INTO mail_domain_result VALUES
    ('mx'), ('implicit_mx'), ('null_mx'), ('no_mail'), ('nxdomain'), ('timeout'), ('error');

INSERT INTO entity_type VALUES ('contact_suppression');

-- ============================================================================ contact points

ALTER TABLE contact_points ADD COLUMN validation_method TEXT
    CHECK (validation_method IS NULL OR length(validation_method) BETWEEN 1 AND 64);
-- the extraction stage looks observations up by extraction, and the freshness of crawled channels
-- by the extractions of one extractor
CREATE INDEX contact_points_by_extraction ON contact_points (extraction_id);
CREATE INDEX extractions_by_extractor ON extractions (extractor, extractor_version);

CREATE TRIGGER contact_points_hypothesis_insert BEFORE INSERT ON contact_points
WHEN NEW.origin = 'hypothesis'
    AND (NEW.is_primary = 1 OR NEW.validation_status NOT IN ('unknown', 'invalid', 'stale'))
BEGIN SELECT RAISE(ABORT, 'a hypothesis is never primary and never validated'); END;
CREATE TRIGGER contact_points_hypothesis_update
BEFORE UPDATE OF origin, is_primary, validation_status ON contact_points
WHEN NEW.origin = 'hypothesis'
    AND (NEW.is_primary = 1 OR NEW.validation_status NOT IN ('unknown', 'invalid', 'stale'))
BEGIN SELECT RAISE(ABORT, 'a hypothesis is never primary and never validated'); END;

-- ============================================================================ decision makers

DROP TABLE decision_makers;

CREATE TABLE decision_makers (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    business_id TEXT NOT NULL REFERENCES businesses (id),
    extraction_id TEXT NOT NULL REFERENCES extractions (id),
    full_name_raw TEXT NOT NULL CHECK (length(full_name_raw) BETWEEN 1 AND 300),
    full_name_normalized TEXT NOT NULL CHECK (length(full_name_normalized) >= 1),
    role_raw TEXT,
    role_normalized TEXT,
    relationship TEXT NOT NULL REFERENCES decision_maker_relationship (value),
    published_context TEXT,
    review_status TEXT NOT NULL REFERENCES decision_maker_review (value),
    review_reasons_json TEXT CHECK (json_valid(review_reasons_json)),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    observed_at TEXT NOT NULL CHECK (observed_at IS strftime('%Y-%m-%dT%H:%M:%fZ', observed_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    deleted_at TEXT CHECK (deleted_at IS strftime('%Y-%m-%dT%H:%M:%fZ', deleted_at)),
    deleted_reason TEXT,
    CHECK ((deleted_at IS NULL) = (deleted_reason IS NULL))
) STRICT;
CREATE INDEX decision_makers_by_business ON decision_makers (business_id);
CREATE INDEX decision_makers_by_review ON decision_makers (review_status) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX decision_makers_once
    ON decision_makers (extraction_id, business_id, full_name_normalized);

CREATE TRIGGER decision_makers_soft_delete_first BEFORE DELETE ON decision_makers
WHEN OLD.deleted_at IS NULL
BEGIN SELECT RAISE(ABORT, 'soft-delete decision_makers before purging them'); END;

-- ============================================================================ suppressions

CREATE TABLE contact_suppressions (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 36),
    kind TEXT NOT NULL REFERENCES suppression_kind (value),
    value TEXT NOT NULL CHECK (length(value) BETWEEN 1 AND 320),
    business_id TEXT REFERENCES businesses (id),
    reason TEXT NOT NULL CHECK (length(trim(reason)) BETWEEN 1 AND 2000),
    source TEXT NOT NULL CHECK (length(trim(source)) BETWEEN 1 AND 300),
    actor TEXT NOT NULL CHECK (length(trim(actor)) BETWEEN 1 AND 300),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    lifted_at TEXT CHECK (lifted_at IS strftime('%Y-%m-%dT%H:%M:%fZ', lifted_at)),
    lifted_by TEXT,
    lift_reason TEXT,
    CHECK ((kind = 'business') = (business_id IS NOT NULL)),
    CHECK (kind <> 'business' OR value = business_id),
    CHECK ((lifted_at IS NULL) = (lifted_by IS NULL)),
    CHECK ((lifted_at IS NULL) = (lift_reason IS NULL)),
    CHECK (lifted_at IS NULL OR lifted_at >= created_at)
) STRICT;
CREATE UNIQUE INDEX contact_suppressions_live ON contact_suppressions (kind, value)
    WHERE lifted_at IS NULL;
CREATE INDEX contact_suppressions_by_date ON contact_suppressions (created_at, id);

CREATE TRIGGER contact_suppressions_lift_once BEFORE UPDATE ON contact_suppressions
WHEN OLD.lifted_at IS NOT NULL OR NEW.id IS NOT OLD.id OR NEW.kind IS NOT OLD.kind
    OR NEW.value IS NOT OLD.value OR NEW.business_id IS NOT OLD.business_id
    OR NEW.reason IS NOT OLD.reason OR NEW.source IS NOT OLD.source OR NEW.actor IS NOT OLD.actor
    OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT, 'a suppression is only ever lifted, once'); END;
CREATE TRIGGER contact_suppressions_permanent BEFORE DELETE ON contact_suppressions
BEGIN SELECT RAISE(ABORT, 'contact_suppressions are permanent: lift one instead'); END;

-- ============================================================================ mail domains

CREATE TABLE mail_domain_checks (
    domain TEXT PRIMARY KEY NOT NULL CHECK (length(domain) BETWEEN 3 AND 253 AND domain = lower(domain)),
    result TEXT NOT NULL REFERENCES mail_domain_result (value),
    exchanges_json TEXT NOT NULL CHECK (json_valid(exchanges_json)),
    checked_at TEXT NOT NULL CHECK (checked_at IS strftime('%Y-%m-%dT%H:%M:%fZ', checked_at)),
    expires_at TEXT NOT NULL CHECK (expires_at IS strftime('%Y-%m-%dT%H:%M:%fZ', expires_at)),
    created_at TEXT NOT NULL CHECK (created_at IS strftime('%Y-%m-%dT%H:%M:%fZ', created_at)),
    updated_at TEXT NOT NULL CHECK (updated_at IS strftime('%Y-%m-%dT%H:%M:%fZ', updated_at)),
    CHECK (expires_at > checked_at)
) STRICT, WITHOUT ROWID;
