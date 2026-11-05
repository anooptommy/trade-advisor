"""Append-only persistence for Gemini plan assessments."""
from __future__ import annotations
import json
from contextlib import closing
from pathlib import Path
from typing import Any
from app.database import DEFAULT_DATABASE_PATH, connect_database, initialize_database

def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

def get_latest_advisor_assessment(coin_id: str, *, database_path: str | Path = DEFAULT_DATABASE_PATH) -> dict | None:
    """Load the most recent assessment so Gemini can continue the same plan."""
    initialize_database(database_path)
    with closing(connect_database(database_path)) as connection:
        row = connection.execute(
            """SELECT aa.id, aa.plan_id, aa.plan_revision, aa.response_json
               FROM advisor_assessments aa
               JOIN instrument_source_mappings ism ON ism.instrument_id = aa.instrument_id
               JOIN data_sources ds ON ds.id = ism.data_source_id
               WHERE ism.external_asset_id = ? AND ds.source_key = 'coingecko_mcp'
               ORDER BY aa.id DESC LIMIT 1""", (coin_id,),
        ).fetchone()
    if row is None:
        return None
    result = json.loads(row["response_json"])
    result.update(history_id=row["id"], plan_id=row["plan_id"], plan_revision=row["plan_revision"])
    return result

def persist_advisor_assessment(
    scan_run_id: int, coin_id: str, *, metrics: dict, previous_assessment: dict | None,
    assessment: dict, database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> int:
    """Save metrics, prior assessment, and Gemini response in one immutable record."""
    initialize_database(database_path)
    with closing(connect_database(database_path)) as connection:
        with connection:
            instrument = connection.execute(
                """SELECT ism.instrument_id FROM instrument_source_mappings ism
                   JOIN data_sources ds ON ds.id = ism.data_source_id
                   WHERE ism.external_asset_id = ? AND ds.source_key = 'coingecko_mcp'""",
                (coin_id,),
            ).fetchone()
            if instrument is None:
                raise ValueError(f"CoinGecko asset {coin_id!r} is not registered for advisor history.")
            cursor = connection.execute(
                """INSERT INTO advisor_assessments(
                       scan_run_id, instrument_id, plan_id, plan_revision, status,
                       metrics_snapshot_json, previous_assessment_json, response_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (scan_run_id, instrument["instrument_id"], assessment.get("plan_id"),
                 assessment.get("plan_revision"), assessment["status"], _json(metrics),
                 _json(previous_assessment) if previous_assessment is not None else None, _json(assessment)),
            )
            return cursor.lastrowid
