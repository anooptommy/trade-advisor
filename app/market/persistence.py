"""Persist provider OHLC refreshes to the local SQLite market-data ledger."""

from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

from app.database import DEFAULT_DATABASE_PATH, connect_database, initialize_database


SOURCE_KEY = "coingecko_mcp"
TIMEFRAME_DURATION = {"1d": timedelta(days=1), "1h": timedelta(hours=1)}


class DatabasePersistenceError(RuntimeError):
    """A provider response could not be durably recorded in SQLite."""


def _decimal_text(value) -> str | None:
    if pd.isna(value):
        return None
    return format(Decimal(str(value)), "f")


def persist_provider_candles(
    frame: pd.DataFrame,
    *,
    coin_id: str,
    symbol: str,
    quote_currency: str,
    timeframe: str,
    requested_start: str,
    requested_end: str,
    started_at: datetime,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> int:
    """Upsert one successful provider response and record its ingestion run."""
    if timeframe not in TIMEFRAME_DURATION:
        raise ValueError(f"Unsupported provider timeframe: {timeframe}")
    try:
        database_path = initialize_database(database_path)
    except Exception as exc:
        raise DatabasePersistenceError(f"Unable to initialize market-data database: {exc}") from exc
    now = datetime.now(timezone.utc)
    started = started_at.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    completed = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    duration_ms = int(TIMEFRAME_DURATION[timeframe].total_seconds() * 1000)

    candles = []
    for row in frame.itertuples(index=False):
        opened = pd.Timestamp(row.timestamp).to_pydatetime().astimezone(timezone.utc)
        open_ms = int(opened.timestamp() * 1000)
        candles.append((
            open_ms, open_ms + duration_ms,
            _decimal_text(row.open), _decimal_text(row.high),
            _decimal_text(row.low), _decimal_text(row.close),
            _decimal_text(row.volume),
            int(open_ms + duration_ms <= int(now.timestamp() * 1000)),
        ))

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
                mapping = connection.execute(
                    "SELECT instrument_id FROM instrument_source_mappings "
                    "WHERE data_source_id = ? AND external_asset_id = ?",
                    (source_id, coin_id),
                ).fetchone()
                if mapping:
                    instrument_id = mapping["instrument_id"]
                else:
                    instrument_id = connection.execute(
                        "INSERT INTO instruments(canonical_symbol, display_name, base_currency) "
                        "VALUES (?, ?, ?) RETURNING id",
                        (symbol.upper(), symbol.upper(), symbol.upper()),
                    ).fetchone()["id"]
                    connection.execute(
                        "INSERT INTO instrument_source_mappings(" 
                        "instrument_id, data_source_id, external_asset_id, external_symbol, "
                        "quote_currency, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
                        (instrument_id, source_id, coin_id, symbol.upper(), quote_currency.upper(),
                         json.dumps({"provider": SOURCE_KEY}, separators=(",", ":"))),
                    )

                start_ms = int(datetime.fromisoformat(requested_start).replace(tzinfo=timezone.utc).timestamp() * 1000)
                end_ms = int((datetime.fromisoformat(requested_end).replace(tzinfo=timezone.utc) + timedelta(days=1)).timestamp() * 1000)
                run_id = connection.execute(
                    "INSERT INTO market_data_runs(data_source_id, instrument_id, timeframe, "
                    "requested_start_ms, requested_end_ms, started_at_utc, completed_at_utc, "
                    "status, rows_received, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, 'succeeded', ?, ?) "
                    "RETURNING id",
                    (source_id, instrument_id, timeframe, start_ms, end_ms, started, completed,
                     len(candles), json.dumps({
                         "operation": "incremental_cache_refresh",
                         "coin_id": coin_id,
                         "quote_currency": quote_currency.lower(),
                         "volume_available": False,
                         "rows_upserted": len(candles),
                     }, separators=(",", ":"))),
                ).fetchone()["id"]
                connection.executemany(
                    "INSERT INTO candles(instrument_id, data_source_id, first_ingestion_run_id, "
                    "last_ingestion_run_id, timeframe, open_time_ms, close_time_ms, open_price, "
                    "high_price, low_price, close_price, volume, volume_available, is_closed, "
                    "origin_kind, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'provider', ?) "
                    "ON CONFLICT(instrument_id, data_source_id, timeframe, open_time_ms) DO UPDATE SET "
                    "last_ingestion_run_id = excluded.last_ingestion_run_id, "
                    "close_time_ms = excluded.close_time_ms, open_price = excluded.open_price, "
                    "high_price = excluded.high_price, low_price = excluded.low_price, "
                    "close_price = excluded.close_price, volume = excluded.volume, "
                    "volume_available = excluded.volume_available, is_closed = excluded.is_closed, "
                    "metadata_json = excluded.metadata_json, "
                    "last_seen_at_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
                    [(instrument_id, source_id, run_id, run_id, timeframe, *candle,
                      json.dumps({"timestamp_timezone": "UTC", "origin": "provider_ohlc"}, separators=(",", ":")))
                     for candle in candles],
                )
    except Exception as exc:
        raise DatabasePersistenceError(f"Unable to persist {coin_id} {timeframe} candles: {exc}") from exc
    return len(candles)


def persist_derived_4h_candles(
    frame: pd.DataFrame,
    *,
    coin_id: str,
    symbol: str,
    quote_currency: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> int:
    """Upsert locally resampled complete 4H candles without claiming provider ingestion."""
    database_path = initialize_database(database_path)
    now = datetime.now(timezone.utc)
    duration_ms = int(timedelta(hours=4).total_seconds() * 1000)
    rows = []
    for row in frame.itertuples(index=False):
        opened = pd.Timestamp(row.timestamp).to_pydatetime().astimezone(timezone.utc)
        open_ms = int(opened.timestamp() * 1000)
        rows.append((
            open_ms, open_ms + duration_ms,
            _decimal_text(row.open), _decimal_text(row.high),
            _decimal_text(row.low), _decimal_text(row.close),
            int(open_ms + duration_ms <= int(now.timestamp() * 1000)),
        ))

    try:
        with closing(connect_database(database_path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO data_sources(source_key, display_name, source_type) "
                    "VALUES (?, 'Trading Agent Derived Data', 'internal') "
                    "ON CONFLICT(source_key) DO NOTHING",
                    ("trading_agent_derived",),
                )
                source_id = connection.execute(
                    "SELECT id FROM data_sources WHERE source_key = 'trading_agent_derived'"
                ).fetchone()["id"]
                mapping = connection.execute(
                    "SELECT instrument_id FROM instrument_source_mappings "
                    "WHERE data_source_id = ? AND external_asset_id = ?",
                    (source_id, coin_id),
                ).fetchone()
                if mapping:
                    instrument_id = mapping["instrument_id"]
                else:
                    provider_mapping = connection.execute(
                        "SELECT m.instrument_id FROM instrument_source_mappings m "
                        "JOIN data_sources s ON s.id = m.data_source_id "
                        "WHERE s.source_key = ? AND m.external_asset_id = ?",
                        (SOURCE_KEY, coin_id),
                    ).fetchone()
                    symbol = symbol.upper()
                    instrument_id = (
                        provider_mapping["instrument_id"] if provider_mapping else connection.execute(
                            "INSERT INTO instruments(canonical_symbol, display_name, base_currency) "
                            "VALUES (?, ?, ?) RETURNING id",
                            (symbol, symbol, symbol),
                        ).fetchone()["id"]
                    )
                    connection.execute(
                        "INSERT INTO instrument_source_mappings(instrument_id, data_source_id, "
                        "external_asset_id, external_symbol, quote_currency, metadata_json) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (instrument_id, source_id, coin_id, symbol, quote_currency.upper(),
                         json.dumps({"derivation": "1h_to_4h"}, separators=(",", ":"))),
                    )
                connection.executemany(
                    "INSERT INTO candles(instrument_id, data_source_id, timeframe, open_time_ms, close_time_ms, "
                    "open_price, high_price, low_price, close_price, volume, volume_available, is_closed, "
                    "origin_kind, derivation_version, metadata_json) "
                    "VALUES (?, ?, '4h', ?, ?, ?, ?, ?, ?, NULL, 0, ?, 'derived', '1h_to_4h_v1', ?) "
                    "ON CONFLICT(instrument_id, data_source_id, timeframe, open_time_ms) DO UPDATE SET "
                    "close_time_ms = excluded.close_time_ms, open_price = excluded.open_price, "
                    "high_price = excluded.high_price, low_price = excluded.low_price, "
                    "close_price = excluded.close_price, volume = NULL, volume_available = 0, "
                    "is_closed = excluded.is_closed, derivation_version = excluded.derivation_version, "
                    "metadata_json = excluded.metadata_json, last_seen_at_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
                    [(instrument_id, source_id, *row,
                      json.dumps({"source_timeframe": "1h", "volume_available": False}, separators=(",", ":")))
                     for row in rows],
                )
    except Exception as exc:
        raise DatabasePersistenceError(f"Unable to persist derived 4H candles for {coin_id}: {exc}") from exc
    return len(rows)
