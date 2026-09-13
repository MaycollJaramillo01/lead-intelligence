-- 0006 · Latino evidence engine, data contract v1 (phase 07).
--
-- Phase 03 already modelled evidence (`evidence_items`, only its status changes) and evaluations
-- (`latino_assessments`, superseded rather than edited, citing evidence through
-- `latino_assessment_evidence`). This migration adds what the rules need to explain themselves:
--
--   * one current assessment per business AND method: a person's review and the automatic
--     calculation coexist. The business shows the review; the calculation is never deleted and
--     keeps being recalculated when evidence or rules change;
--   * `details_json` on an assessment: warnings, what the evidence cannot conclude and, for a
--     review, which calculation the person reviewed;
--   * for every piece of evidence an assessment cites: the source reliability, freshness and score
--     the rules gave it at that moment, whether it counted, and whether it describes an
--     organization several locations share.
--
-- The claim of each piece of evidence (its taxonomy code) lives in `evidence_items.value_json`.

DROP INDEX latino_assessments_current;
CREATE UNIQUE INDEX latino_assessments_current ON latino_assessments (business_id, method)
    WHERE superseded_at IS NULL;

ALTER TABLE latino_assessments ADD COLUMN details_json TEXT CHECK (json_valid(details_json));

DROP TRIGGER latino_assessments_immutable;
CREATE TRIGGER latino_assessments_immutable BEFORE UPDATE ON latino_assessments
WHEN OLD.superseded_at IS NOT NULL
    OR NEW.id IS NOT OLD.id OR NEW.business_id IS NOT OLD.business_id
    OR NEW.status IS NOT OLD.status OR NEW.assessment_confidence IS NOT OLD.assessment_confidence
    OR NEW.method IS NOT OLD.method OR NEW.rules_version IS NOT OLD.rules_version
    OR NEW.inference_run_id IS NOT OLD.inference_run_id OR NEW.rationale IS NOT OLD.rationale
    OR NEW.limitations IS NOT OLD.limitations OR NEW.assessed_at IS NOT OLD.assessed_at
    OR NEW.created_at IS NOT OLD.created_at OR NEW.details_json IS NOT OLD.details_json
BEGIN SELECT RAISE(ABORT, 'latino_assessments are immutable; supersede them instead'); END;

ALTER TABLE latino_assessment_evidence ADD COLUMN source_reliability REAL NOT NULL DEFAULT 0
    CHECK (source_reliability BETWEEN 0 AND 1);
ALTER TABLE latino_assessment_evidence ADD COLUMN freshness REAL NOT NULL DEFAULT 0
    CHECK (freshness BETWEEN 0 AND 1);
ALTER TABLE latino_assessment_evidence ADD COLUMN score REAL NOT NULL DEFAULT 0
    CHECK (score BETWEEN 0 AND 1);
ALTER TABLE latino_assessment_evidence ADD COLUMN counted INTEGER NOT NULL DEFAULT 0
    CHECK (counted IN (0, 1));
ALTER TABLE latino_assessment_evidence ADD COLUMN organizational INTEGER NOT NULL DEFAULT 0
    CHECK (organizational IN (0, 1));

-- what an assessment cited is part of its history
CREATE TRIGGER latino_assessment_evidence_immutable BEFORE UPDATE ON latino_assessment_evidence
BEGIN SELECT RAISE(ABORT, 'latino_assessment_evidence is history'); END;
