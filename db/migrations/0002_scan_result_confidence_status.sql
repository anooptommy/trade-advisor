ALTER TABLE scan_results ADD COLUMN confidence_status TEXT;

UPDATE scan_results
SET direction = CASE
        WHEN json_extract(snapshot_json, '$.result.snapshot.setup') = 'potential_long' THEN 'long'
        WHEN json_extract(snapshot_json, '$.result.snapshot.setup') = 'potential_short' THEN 'short'
        WHEN json_extract(snapshot_json, '$.result.snapshot.context.daily_context') = 'bullish' THEN 'long'
        WHEN json_extract(snapshot_json, '$.result.snapshot.context.daily_context') = 'bearish' THEN 'short'
        ELSE NULL
    END,
    confidence_status = json_extract(snapshot_json, '$.result.snapshot.confidence.status')
WHERE status = 'analyzed';
