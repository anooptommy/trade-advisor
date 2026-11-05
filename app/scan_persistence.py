"""Persist deterministic scan outputs and their analysis-only follow-ups."""

from __future__ import annotations

import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.database import DEFAULT_DATABASE_PATH, connect_database, initialize_database


SOURCE_KEY = "coingecko_mcp"
STRATEGY_KEY = "deterministic_market_scan"
STRATEGY_VERSION = "snapshot-schema-4"


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _epoch_ms(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _ensure_instrument(connection, asset: dict, source_id: int, quote_currency: str) -> int:
    mapping = connection.execute(
        "SELECT instrument_id FROM instrument_source_mappings "
        "WHERE data_source_id = ? AND external_asset_id = ?",
        (source_id, asset["coin_id"]),
    ).fetchone()
    if mapping:
        instrument_id = mapping["instrument_id"]
        connection.execute("UPDATE instruments SET active = 1 WHERE id = ?", (instrument_id,))
        return instrument_id

    symbol = asset["symbol"].upper()
    instrument_id = connection.execute(
        "INSERT INTO instruments(canonical_symbol, display_name, base_currency, active) "
        "VALUES (?, ?, ?, 1) RETURNING id",
        (symbol, symbol, symbol),
    ).fetchone()["id"]
    connection.execute(
        "INSERT INTO instrument_source_mappings(instrument_id, data_source_id, external_asset_id, "
        "external_symbol, quote_currency, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
        (instrument_id, source_id, asset["coin_id"], symbol, quote_currency.upper(),
         _json({"provider": SOURCE_KEY})),
    )
    return instrument_id


def begin_scan_run(
    config: dict,
    *,
    started_at: datetime | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> int:
    """Create a running scan record and make sure all watchlist assets are known."""
    database_path = initialize_database(database_path)
    started_at = started_at or datetime.now(timezone.utc)
    strategy_config = {
        "snapshot_schema_version": 4,
        "timeframes": ["1d", "4h", "1h"],
        "quote_currency": config["quote_currency"].lower(),
        "daily_days": config["daily_days"],
        "hourly_days": config["hourly_days"],
    }
    config_text = _json(strategy_config)
    config_hash = hashlib.sha256(config_text.encode("utf-8")).hexdigest()

    try:
        with closing(connect_database(database_path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO data_sources(source_key, display_name, source_type) "
                    "VALUES (?, ?, 'market_data') ON CONFLICT(source_key) DO NOTHING",
                    (SOURCE_KEY, "CoinGecko MCP"),
                )
                source_id = connection.execute(
                    "SELECT id FROM data_sources WHERE source_key = ?", (SOURCE_KEY,)
                ).fetchone()["id"]
                strategy_id = connection.execute(
                    "INSERT INTO strategy_versions(strategy_key, version, config_hash, config_json) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(strategy_key, version, config_hash) DO NOTHING",
                    (STRATEGY_KEY, STRATEGY_VERSION, config_hash, config_text),
                )
                strategy_id = connection.execute(
                    "SELECT id FROM strategy_versions WHERE strategy_key = ? AND version = ? AND config_hash = ?",
                    (STRATEGY_KEY, STRATEGY_VERSION, config_hash),
                ).fetchone()["id"]
                for asset in config["assets"]:
                    _ensure_instrument(connection, asset, source_id, config["quote_currency"])
                return connection.execute(
                    "INSERT INTO scan_runs(strategy_version_id, started_at_utc, status, summary_json) "
                    "VALUES (?, ?, 'running', ?) RETURNING id",
                    (strategy_id, _utc_text(started_at), _json({"watchlist_assets": len(config["assets"])})),
                ).fetchone()["id"]
    except Exception as exc:
        raise RuntimeError(f"Unable to start persistent scan run: {exc}") from exc


def _persist_suggestion(connection, scan_result_id: int, instrument_id: int, result: dict) -> int | None:
    snapshot = result.get("snapshot") or {}
    setup = snapshot.get("setup")
    direction = {"potential_long": "long", "potential_short": "short"}.get(setup)
    candle_time = _epoch_ms(snapshot.get("timestamp"))
    if direction is None or candle_time is None:
        return None

    confidence = snapshot.get("confidence") or {}
    risk_plan = result.get("risk_plan") or {}
    setup_snapshot = {
        "snapshot": snapshot,
        "risk_plan": risk_plan,
        "qualifies_for_gemini": bool(result.get("qualifies_for_gemini")),
        "analysis_only": True,
        "execution_authorized": False,
    }
    suggestion_id = connection.execute(
        "INSERT INTO trade_suggestions(scan_result_id, instrument_id, direction, setup_candle_time_ms, "
        "status, entry_reference_price, confidence_snapshot_json, setup_snapshot_json) "
        "VALUES (?, ?, ?, ?, 'suggested', ?, ?, ?) RETURNING id",
        (scan_result_id, instrument_id, direction, candle_time,
         None if snapshot.get("price") is None else str(snapshot["price"]),
         _json(confidence), _json(setup_snapshot)),
    ).fetchone()["id"]

    # This is the current structural candidate risk plan, not portfolio risk
    # approval. The explicit JSON flags prevent it being mistaken for approval.
    assessment_status = "assessed" if risk_plan.get("status") == "assessed" else "unavailable"
    assessment_id = connection.execute(
        "INSERT INTO risk_assessments(trade_suggestion_id, status, input_snapshot_json, result_json) "
        "VALUES (?, ?, ?, ?) RETURNING id",
        (suggestion_id, assessment_status,
         _json({"market_snapshot": snapshot, "analysis_only": True, "execution_authorized": False}),
         _json(risk_plan)),
    ).fetchone()["id"]

    entry = risk_plan.get("entry_reference") or {}
    for scenario in risk_plan.get("buffer_scenarios", []):
        scenario_status = "assessed" if scenario.get("status") == "assessed" else "unavailable"
        connection.execute(
            "INSERT INTO risk_scenarios(risk_assessment_id, scenario_key, atr_buffer_multiple, "
            "entry_reference_price, stop_price, target_price, gross_risk_reward, status, result_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (assessment_id, f"atr_{scenario.get('atr_buffer_multiple', 'unknown')}x",
             scenario.get("atr_buffer_multiple"),
             None if entry.get("price") is None else str(entry["price"]),
             None if scenario.get("stop_price") is None else str(scenario["stop_price"]),
             None if scenario.get("target_price") is None else str(scenario["target_price"]),
             scenario.get("gross_risk_reward"), scenario_status, _json(scenario)),
        )
    return suggestion_id


def persist_scan_results(
    scan_run_id: int,
    config: dict,
    results: list[dict],
    *,
    completed_at: datetime | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict:
    """Write per-asset outcomes, candidate records, and the final run summary."""
    database_path = Path(database_path)
    completed_at = completed_at or datetime.now(timezone.utc)
    asset_by_coin = {asset["coin_id"]: asset for asset in config["assets"]}
    analyzed = sum(item.get("status") == "analyzed" for item in results)
    failed = sum(item.get("status") == "failed" for item in results)
    stale = sum(item.get("data_status") == "stale_cache_fallback" for item in results)
    candidates = sum(
        (item.get("snapshot") or {}).get("setup") in ("potential_long", "potential_short")
        for item in results
    )
    status = "failed" if results and failed == len(results) else "partial" if failed else "succeeded"
    summary = {
        "watchlist_assets": len(config["assets"]),
        "results_written": len(results),
        "analyzed": analyzed,
        "failed": failed,
        "stale_data": stale,
        "deterministic_candidates": candidates,
        "quote_currency": config["quote_currency"],
        "analysis_only": True,
    }
    ids: dict[str, Any] = {"scan_run_id": scan_run_id, "scan_result_ids": {}, "trade_suggestion_ids": {}}

    try:
        with closing(connect_database(database_path)) as connection:
            with connection:
                source_id = connection.execute(
                    "SELECT id FROM data_sources WHERE source_key = ?", (SOURCE_KEY,)
                ).fetchone()["id"]
                for result in results:
                    coin_id = result["coin_id"]
                    asset = asset_by_coin[coin_id]
                    instrument_id = _ensure_instrument(connection, asset, source_id, config["quote_currency"])
                    snapshot = result.get("snapshot") or {}
                    confidence = snapshot.get("confidence") or {}
                    setup = snapshot.get("setup")
                    direction = {"potential_long": "long", "potential_short": "short"}.get(setup)
                    if direction is None:
                        direction = {
                            "bullish": "long",
                            "bearish": "short",
                        }.get((snapshot.get("context") or {}).get("daily_context"))
                    market_freshness = (snapshot.get("market_data") or {}).get("data_freshness") or result.get("data_freshness") or {}
                    data_status = result.get("data_status")
                    if data_status not in ("current", "stale_cache_fallback", "unavailable"):
                        data_status = "unavailable" if result.get("status") != "analyzed" else "current"
                    # Renderer inputs can contain pandas DataFrames and are not
                    # part of the durable analysis snapshot stored in SQLite.
                    persisted_result = {
                        key: value for key, value in result.items()
                        if key != "chart_data"
                    }
                    scan_result_id = connection.execute(
                        "INSERT INTO scan_results(scan_run_id, instrument_id, status, data_status, "
                        "latest_daily_candle_ms, latest_four_hour_candle_ms, latest_hourly_candle_ms, "
                        "setup, direction, confidence_observed_points, confidence_assessed_points, "
                        "confidence_total_points, confidence_fraction, confidence_pct, confidence_coverage_pct, "
                        "confidence_band, confidence_status, snapshot_json, error_message) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                        (scan_run_id, instrument_id,
                         result.get("status") if result.get("status") in ("analyzed", "failed", "skipped") else "failed",
                         data_status,
                         _epoch_ms((snapshot.get("daily") or {}).get("timestamp")),
                         _epoch_ms((snapshot.get("four_hour") or {}).get("timestamp")),
                         _epoch_ms((snapshot.get("one_hour") or {}).get("timestamp")),
                         setup, direction, confidence.get("observed_points"), confidence.get("assessed_points"),
                         confidence.get("total_points"), confidence.get("score_fraction"),
                         confidence.get("score_pct"), confidence.get("coverage_pct"), confidence.get("band"),
                         confidence.get("status"),
                         _json({"result": persisted_result, "market_freshness": market_freshness}), result.get("error")),
                    ).fetchone()["id"]
                    ids["scan_result_ids"][coin_id] = scan_result_id

                    # Store the evaluated closed 1H close as an observation
                    # linked to its source candle; never synthesize one if the
                    # matching candle has not reached SQLite yet.
                    hourly_time = _epoch_ms((snapshot.get("one_hour") or {}).get("timestamp"))
                    observed_price = snapshot.get("price")
                    if hourly_time is not None and observed_price is not None:
                        candle = connection.execute(
                            "SELECT id, last_ingestion_run_id FROM candles WHERE instrument_id = ? "
                            "AND data_source_id = ? AND timeframe = '1h' AND open_time_ms = ?",
                            (instrument_id, source_id, hourly_time),
                        ).fetchone()
                        if candle:
                            observed_at = datetime.fromtimestamp(hourly_time / 1000, timezone.utc)
                            connection.execute(
                                "INSERT INTO price_observations(instrument_id, data_source_id, observed_at_utc, "
                                "price, quote_currency, price_kind, candle_id, ingestion_run_id, metadata_json) "
                                "VALUES (?, ?, ?, ?, ?, 'candle_close', ?, ?, ?) "
                                "ON CONFLICT(instrument_id, data_source_id, observed_at_utc, price_kind, quote_currency) "
                                "DO UPDATE SET price = excluded.price, candle_id = excluded.candle_id, "
                                "ingestion_run_id = excluded.ingestion_run_id, metadata_json = excluded.metadata_json",
                                (instrument_id, source_id, _utc_text(observed_at), str(observed_price),
                                 config["quote_currency"].upper(), candle["id"], candle["last_ingestion_run_id"],
                                 _json({"source": "scan_snapshot", "timeframe": "1h", "analysis_only": True})),
                            )
                    suggestion_id = _persist_suggestion(connection, scan_result_id, instrument_id, result)
                    if suggestion_id is not None:
                        ids["trade_suggestion_ids"][coin_id] = suggestion_id

                connection.execute(
                    "UPDATE scan_runs SET completed_at_utc = ?, status = ?, summary_json = ?, error_message = ? "
                    "WHERE id = ? AND status = 'running'",
                    (_utc_text(completed_at), status, _json(summary),
                     "all configured assets failed" if status == "failed" else None, scan_run_id),
                )
        return ids
    except Exception as exc:
        mark_scan_run_failed(scan_run_id, str(exc), database_path=database_path)
        raise RuntimeError(f"Unable to persist results for scan run {scan_run_id}: {exc}") from exc


def mark_scan_run_failed(
    scan_run_id: int,
    error: str,
    *,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> None:
    """Close a started scan run as failed after an unexpected application error."""
    try:
        with closing(connect_database(database_path)) as connection:
            with connection:
                connection.execute(
                    "UPDATE scan_runs SET completed_at_utc = ?, status = 'failed', error_message = ? "
                    "WHERE id = ? AND status = 'running'",
                    (_utc_text(datetime.now(timezone.utc)), error[:2000], scan_run_id),
                )
    except Exception:
        # Preserve the original scan failure if SQLite is unavailable too.
        pass


def persist_agent_review(
    trade_suggestion_id: int,
    *,
    model: str,
    input_snapshot: dict,
    status: str,
    response_text: str | None = None,
    error_message: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> int:
    """Persist a Gemini review outcome for a deterministic trade suggestion."""
    if status not in ("succeeded", "failed"):
        raise ValueError("Agent review status must be 'succeeded' or 'failed'.")
    with closing(connect_database(database_path)) as connection:
        with connection:
            return connection.execute(
                "INSERT INTO agent_reviews(trade_suggestion_id, provider, model, prompt_version, status, "
                "input_snapshot_json, response_text, error_message) VALUES (?, 'google_gemini', ?, ?, ?, ?, ?, ?) "
                "RETURNING id",
                (trade_suggestion_id, model, "candidate-review-v1", status, _json(input_snapshot),
                 response_text, error_message),
            ).fetchone()["id"]


def persist_portfolio_risk_review(
    trade_suggestion_id: int,
    *,
    input_snapshot: dict,
    risk_review: dict,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> int:
    """Persist one analysis-only portfolio sizing/governor review."""
    if not isinstance(input_snapshot, dict) or not isinstance(risk_review, dict):
        raise ValueError("Risk review inputs and result must be JSON objects.")
    status_map = {
        "approved_for_later_risk_review": "assessed",
        "vetoed": "vetoed",
        "unavailable": "unavailable",
    }
    review_status = status_map.get(risk_review.get("status"))
    if review_status is None:
        raise ValueError("Risk review must be assessed, vetoed, or unavailable.")
    if risk_review.get("execution_authorized") is not False:
        raise ValueError("Portfolio risk review must explicitly deny execution authorization.")

    size = risk_review.get("position_size") or {}
    eligibility = risk_review.get("eligibility") or {}
    with closing(connect_database(database_path)) as connection:
        with connection:
            assessment_id = connection.execute(
                """INSERT INTO risk_assessments(
                       trade_suggestion_id, status, input_snapshot_json, result_json
                   ) VALUES (?, ?, ?, ?) RETURNING id""",
                (trade_suggestion_id, review_status, _json(input_snapshot), _json(risk_review)),
            ).fetchone()["id"]
            connection.execute(
                """INSERT INTO risk_scenarios(
                       risk_assessment_id, scenario_key, entry_reference_price, stop_price,
                       gross_risk_reward, position_size, notional, risk_amount,
                       liquidation_price, status, result_json
                   ) VALUES (?, 'portfolio_review', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    assessment_id,
                    _optional_str(size.get("entry_price")),
                    _optional_str(size.get("stop_price")),
                    eligibility.get("risk_reward"),
                    _optional_str(size.get("quantity")),
                    _optional_str(size.get("notional")),
                    _optional_str(size.get("effective_risk_amount")),
                    _optional_str(size.get("liquidation_price")),
                    review_status,
                    _json(risk_review),
                ),
            )
    return assessment_id
