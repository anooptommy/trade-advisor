from contextlib import closing
from datetime import datetime, timezone
import json

import pandas as pd

from app.database import connect_database
from app.scan_persistence import (
    begin_scan_run,
    persist_agent_review,
    persist_portfolio_risk_review,
    persist_scan_results,
)


def test_scan_run_persists_asset_results_price_candidate_risk_and_review(tmp_path):
    database = tmp_path / "scan.sqlite3"
    config = {
        "quote_currency": "usd",
        "daily_days": 180,
        "hourly_days": 90,
        "assets": [
            {"symbol": "BTC", "coin_id": "bitcoin"},
            {"symbol": "HYPE", "coin_id": "hyperliquid"},
            {"symbol": "ETH", "coin_id": "ethereum"},
        ],
    }
    run_id = begin_scan_run(config, started_at=datetime.now(timezone.utc), database_path=database)
    hourly_time = "2026-10-07T10:00:00+00:00"

    with closing(connect_database(database)) as connection:
        with connection:
            source_id = connection.execute(
                "SELECT id FROM data_sources WHERE source_key = 'coingecko_mcp'"
            ).fetchone()["id"]
            instrument_id = connection.execute(
                "SELECT instrument_id FROM instrument_source_mappings WHERE external_asset_id = 'bitcoin'"
            ).fetchone()["instrument_id"]
            connection.execute(
                "INSERT INTO candles(instrument_id, data_source_id, timeframe, open_time_ms, close_time_ms, "
                "open_price, high_price, low_price, close_price, volume_available, is_closed) "
                "VALUES (?, ?, '1h', 1791367200000, 1791370800000, '100', '110', '90', '105', 0, 1)",
                (instrument_id, source_id),
            )

    snapshot = {
        "schema_version": 4,
        "symbol": "BTC/USD",
        "timestamp": hourly_time,
        "price": 105.0,
        "daily": {"timestamp": "2026-10-07T00:00:00+00:00"},
        "four_hour": {"timestamp": "2026-10-07T08:00:00+00:00"},
        "one_hour": {"timestamp": hourly_time},
        "setup": "potential_long",
        "confidence": {
            "status": "partial_candidate_score_missing_inputs",
            "direction": "bullish", "observed_points": 20, "assessed_points": 30,
            "total_points": 91, "score_fraction": 0.6667, "score_pct": 66.67,
            "coverage_pct": 33.0, "band": None,
        },
    }
    risk_plan = {
        "status": "assessed", "analysis_only": True, "execution_authorized": False,
        "entry_reference": {"price": 105.0},
        "buffer_scenarios": [{
            "atr_buffer_multiple": 0.5, "status": "assessed", "stop_price": 95.0,
            "target_price": 125.0, "gross_risk_reward": 2.0,
        }],
    }
    results = [
        {"coin_id": "bitcoin", "symbol": "BTC/USD", "status": "analyzed", "data_status": "current",
         "snapshot": snapshot, "risk_plan": risk_plan, "qualifies_for_gemini": True,
         "chart_data": {"1h": pd.DataFrame({"close": [105.0]})}},
        {"coin_id": "hyperliquid", "symbol": "HYPE/USD", "status": "failed",
         "error": "RuntimeError: provider unavailable", "qualifies_for_gemini": False},
        {"coin_id": "ethereum", "symbol": "ETH/USD", "status": "analyzed", "data_status": "current",
         "snapshot": {
             "setup": "no_setup",
             "context": {"daily_context": "bullish"},
             "confidence": {
                 "status": "not_applicable_no_qualifying_setup", "observed_points": 0,
                 "assessed_points": 0, "total_points": 91, "score_fraction": None,
                 "score_pct": None, "coverage_pct": 0.0, "band": None,
             },
         }, "risk_plan": {}, "qualifies_for_gemini": False},
    ]

    ids = persist_scan_results(run_id, config, results, database_path=database)
    review_id = persist_agent_review(
        ids["trade_suggestion_ids"]["bitcoin"], model="test-model", input_snapshot=snapshot,
        status="succeeded", response_text="Review completed.", database_path=database,
    )
    portfolio_review = {
        "status": "approved_for_later_risk_review", "analysis_only": True,
        "execution_authorized": False, "eligibility": {"risk_reward": 2.0},
        "position_size": {
            "status": "assessed", "quantity": 1.0, "entry_price": 105.0,
            "stop_price": 95.0, "notional": 105.0, "effective_risk_amount": 10.0,
            "liquidation_price": 85.0,
        },
    }
    portfolio_assessment_id = persist_portfolio_risk_review(
        ids["trade_suggestion_ids"]["bitcoin"], input_snapshot={"portfolio": "paper-test"},
        risk_review=portfolio_review, database_path=database,
    )

    with closing(connect_database(database)) as connection:
        scan_run = connection.execute("SELECT status, summary_json FROM scan_runs WHERE id = ?", (run_id,)).fetchone()
        rows = connection.execute(
            "SELECT status, setup, direction, confidence_fraction, confidence_status, "
            "confidence_observed_points, confidence_assessed_points FROM scan_results ORDER BY id"
        ).fetchall()
        persisted_snapshot = connection.execute(
            "SELECT snapshot_json FROM scan_results ORDER BY id LIMIT 1"
        ).fetchone()["snapshot_json"]
        observation = connection.execute("SELECT price, price_kind, candle_id FROM price_observations").fetchone()
        suggestion = connection.execute("SELECT status, direction FROM trade_suggestions").fetchone()
        assessment = connection.execute("SELECT status, result_json FROM risk_assessments").fetchone()
        scenario = connection.execute("SELECT scenario_key, status FROM risk_scenarios").fetchone()
        portfolio_assessment = connection.execute(
            "SELECT status, result_json FROM risk_assessments WHERE id = ?", (portfolio_assessment_id,)
        ).fetchone()
        portfolio_scenario = connection.execute(
            "SELECT scenario_key, risk_amount, gross_risk_reward, status FROM risk_scenarios "
            "WHERE risk_assessment_id = ?", (portfolio_assessment_id,)
        ).fetchone()
        review = connection.execute("SELECT status, response_text FROM agent_reviews WHERE id = ?", (review_id,)).fetchone()

    assert scan_run["status"] == "partial"
    assert json.loads(scan_run["summary_json"])["failed"] == 1
    assert [row["status"] for row in rows] == ["analyzed", "failed", "analyzed"]
    assert rows[0]["setup"] == "potential_long" and rows[0]["direction"] == "long"
    assert "chart_data" not in json.loads(persisted_snapshot)["result"]
    assert rows[0]["confidence_fraction"] == 0.6667
    assert rows[0]["confidence_status"] == "partial_candidate_score_missing_inputs"
    assert rows[2]["setup"] == "no_setup" and rows[2]["direction"] == "long"
    assert rows[2]["confidence_status"] == "not_applicable_no_qualifying_setup"
    assert rows[2]["confidence_observed_points"] == 0 and rows[2]["confidence_assessed_points"] == 0
    assert observation["price"] == "105.0" and observation["price_kind"] == "candle_close"
    assert observation["candle_id"] is not None
    assert suggestion["status"] == "suggested" and suggestion["direction"] == "long"
    assert assessment["status"] == "assessed"
    assert json.loads(assessment["result_json"])["execution_authorized"] is False
    assert scenario["scenario_key"] == "atr_0.5x" and scenario["status"] == "assessed"
    assert portfolio_assessment["status"] == "assessed"
    assert json.loads(portfolio_assessment["result_json"])["execution_authorized"] is False
    assert portfolio_scenario["scenario_key"] == "portfolio_review"
    assert portfolio_scenario["risk_amount"] == "10.0"
    assert portfolio_scenario["gross_risk_reward"] == 2.0
    assert portfolio_scenario["status"] == "assessed"
    assert review["status"] == "succeeded" and review["response_text"] == "Review completed."
