CREATE TABLE advisor_assessments (
    id INTEGER PRIMARY KEY,
    scan_run_id INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE RESTRICT,
    instrument_id INTEGER NOT NULL REFERENCES instruments(id) ON DELETE RESTRICT,
    plan_id TEXT,
    plan_revision INTEGER,
    status TEXT NOT NULL CHECK (status IN ('new_plan', 'actionable', 'wait', 'revise', 'no_plan')),
    metrics_snapshot_json TEXT NOT NULL,
    previous_assessment_json TEXT,
    response_json TEXT NOT NULL,
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (scan_run_id, instrument_id),
    CHECK ((plan_id IS NULL AND plan_revision IS NULL) OR (plan_id IS NOT NULL AND plan_revision >= 1))
);
CREATE INDEX idx_advisor_assessments_instrument_created
    ON advisor_assessments(instrument_id, created_at_utc DESC);
CREATE INDEX idx_advisor_assessments_plan ON advisor_assessments(plan_id, id DESC);
