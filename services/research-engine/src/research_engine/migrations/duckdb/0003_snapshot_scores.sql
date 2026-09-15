-- Snapshot schema 2 (phase 13): each row also carries the score run it was read with, the lead's
-- tier and total in that run, and whether the business was on the do-not-contact list at the
-- cut-off. Rows of schema 1 snapshots keep NULL here; research_engine/datasets.py lists the columns.
ALTER TABLE business_snapshot_rows ADD COLUMN score_run_id VARCHAR;
ALTER TABLE business_snapshot_rows ADD COLUMN tier VARCHAR;
ALTER TABLE business_snapshot_rows ADD COLUMN total_score DOUBLE;
ALTER TABLE business_snapshot_rows ADD COLUMN suppressed BOOLEAN;
