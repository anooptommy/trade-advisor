"""Import existing CoinGecko CSV OHLC caches into the local SQLite database."""

from __future__ import annotations

import argparse
import csv
import json
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.database import DEFAULT_DATABASE_PATH, PROJECT_ROOT, connect_database, initialize_database


DEFAULT_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "coingecko"
SOURCE_KEY = "coingecko_mcp"
ASSETS = {
    "bitcoin": ("BTC", "Bitcoin"),
    "ethereum": ("ETH", "Ethereum"),
    "solana": ("SOL", "Solana"),
    "hyperliquid": ("HYPE", "Hyperliquid"),
    "binancecoin": ("BNB", "BNB"),
    "aerodrome-finance": ("AERO", "Aerodrome Finance"),
}
TIMEFRAMES = {"daily": ("1d", timedelta(days=1)), "hourly": ("1h", timedelta(hours=1))}


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _epoch_ms(value: datetime) -> int:
    delta = value - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


def _decimal_text(value: str, field: str, *, allow_empty: bool = False) -> str | None:
    value = value.strip()
    if not value and allow_empty:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid {field} value {value!r}") from exc
    if not number.is_finite():
        raise ValueError(f"Non-finite {field} value {value!r}")
    return format(number, "f")


def _read_candles(path: Path, duration: timedelta, now: datetime) -> list[dict]:
    rows = []
    with path.open("r", newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"{path.name} missing required columns: {sorted(required)}")
        for row_number, row in enumerate(reader, start=2):
            opened_at = _parse_timestamp(row["timestamp"])
            ohlc = {
                key: _decimal_text(row[key], key)
                for key in ("open", "high", "low", "close")
            }
            high, low = Decimal(ohlc["high"]), Decimal(ohlc["low"])
            open_price, close_price = Decimal(ohlc["open"]), Decimal(ohlc["close"])
            if low > high or low > min(open_price, close_price) or high < max(open_price, close_price):
                raise ValueError(f"Invalid OHLC range in {path.name}:{row_number}")
            # CoinGecko OHLC does not provide volume; keep blanks as NULL.
            volume = _decimal_text(row.get("volume", ""), "volume", allow_empty=True)
            if volume is not None:
                raise ValueError(
                    f"Unexpected volume value in CoinGecko OHLC cache {path.name}:{row_number}; "
                    "volume availability must be separately validated before import."
                )
            close_at = opened_at + duration
            rows.append({
                "open_time_ms": _epoch_ms(opened_at),
                "close_time_ms": _epoch_ms(close_at),
                **ohlc,
                "volume": volume,
                "is_closed": int(close_at <= now),
            })
    return rows


def _cache_metadata(path: Path) -> dict:
    metadata_path = path.with_suffix(".json")
    if not metadata_path.exists():
        return {}
    value = json.loads(metadata_path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _cache_is_stale(last_success: str | None, interval: str, now: datetime) -> bool:
    if not last_success:
        return True
    try:
        checked = _parse_timestamp(last_success)
    except (TypeError, ValueError):
        return True
    if interval == "daily":
        return checked.date() < now.date()
    return checked.replace(minute=0, second=0, microsecond=0) < now.replace(minute=0, second=0, microsecond=0)


def import_cached_ohlc(
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict:
    """Idempotently import cached provider OHLC rows; performs no network calls."""
    cache_dir = Path(cache_dir)
    cache_files = sorted(
        path for path in cache_dir.glob("*_usd_*.csv")
        if path.stem.rsplit("_", 1)[-1] in TIMEFRAMES
    )
    if not cache_files:
        raise FileNotFoundError(f"No daily/hourly OHLC cache CSV files found under {cache_dir}")

    initialize_database(database_path)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    summaries = []

    with closing(connect_database(database_path)) as connection:
        with connection:
            connection.execute(
                "INSERT INTO data_sources(source_key, display_name, source_type) VALUES (?, ?, ?) "
                "ON CONFLICT(source_key) DO NOTHING",
                (SOURCE_KEY, "CoinGecko MCP", "market_data"),
            )
            source_id = connection.execute(
                "SELECT id FROM data_sources WHERE source_key = ?", (SOURCE_KEY,)
            ).fetchone()["id"]
            active_ids = {
                item["coin_id"] for item in json.loads(
                    (PROJECT_ROOT / "config" / "watchlist.json").read_text(encoding="utf-8")
                )["assets"]
            }

            for path in cache_files:
                coin_id, quote_currency, interval = path.stem.rsplit("_", 2)
                symbol, display_name = ASSETS.get(
                    coin_id, (coin_id.upper().replace("-", ""), coin_id.replace("-", " ").title())
                )
                timeframe, duration = TIMEFRAMES[interval]
                rows = _read_candles(path, duration, now)
                metadata = _cache_metadata(path)
                stale = _cache_is_stale(metadata.get("last_success_utc"), interval, now)

                mapping = connection.execute(
                    "SELECT instrument_id FROM instrument_source_mappings "
                    "WHERE data_source_id = ? AND external_asset_id = ?",
                    (source_id, coin_id),
                ).fetchone()
                if mapping:
                    instrument_id = mapping["instrument_id"]
                    connection.execute(
                        "UPDATE instruments SET active = ? WHERE id = ?",
                        (int(coin_id in active_ids), instrument_id),
                    )
                else:
                    instrument_id = connection.execute(
                        "INSERT INTO instruments(canonical_symbol, display_name, base_currency, active) "
                        "VALUES (?, ?, ?, ?) RETURNING id",
                        (symbol, display_name, symbol, int(coin_id in active_ids)),
                    ).fetchone()["id"]
                    connection.execute(
                        """INSERT INTO instrument_source_mappings(
                               instrument_id, data_source_id, external_asset_id,
                               external_symbol, quote_currency, metadata_json
                           ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (instrument_id, source_id, coin_id, symbol, quote_currency.upper(),
                         json.dumps({"provider": "coingecko_mcp"}, separators=(",", ":"))),
                    )

                start_ms = min((row["open_time_ms"] for row in rows), default=None)
                end_ms = max((row["close_time_ms"] for row in rows), default=None)
                run_metadata = {
                    "operation": "local_cache_import",
                    "cache_file": path.name,
                    "cache_file_modified_at_utc": datetime.fromtimestamp(
                        path.stat().st_mtime, timezone.utc
                    ).isoformat(timespec="seconds"),
                    "provider_last_success_utc": metadata.get("last_success_utc"),
                    "provider_last_attempt_utc": metadata.get("last_attempt_utc"),
                    "provider_volume_available": False,
                }
                run_id = connection.execute(
                    """INSERT INTO market_data_runs(
                           data_source_id, instrument_id, timeframe, requested_start_ms,
                           requested_end_ms, started_at_utc, completed_at_utc, status,
                           stale_cache_used, rows_received, metadata_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, 'succeeded', ?, ?, ?) RETURNING id""",
                    (source_id, instrument_id, timeframe, start_ms, end_ms, now_iso, now_iso,
                     int(stale), len(rows), json.dumps(run_metadata, separators=(",", ":"))),
                ).fetchone()["id"]

                connection.executemany(
                    """INSERT INTO candles(
                           instrument_id, data_source_id, first_ingestion_run_id,
                           last_ingestion_run_id, timeframe, open_time_ms, close_time_ms,
                           open_price, high_price, low_price, close_price, volume,
                           volume_available, is_closed, origin_kind, metadata_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'provider', ?)
                       ON CONFLICT(instrument_id, data_source_id, timeframe, open_time_ms)
                       DO UPDATE SET
                           last_ingestion_run_id = excluded.last_ingestion_run_id,
                           close_time_ms = excluded.close_time_ms,
                           open_price = excluded.open_price,
                           high_price = excluded.high_price,
                           low_price = excluded.low_price,
                           close_price = excluded.close_price,
                           volume = excluded.volume,
                           volume_available = excluded.volume_available,
                           is_closed = excluded.is_closed,
                           metadata_json = excluded.metadata_json,
                           last_seen_at_utc = excluded.last_seen_at_utc""",
                    [(
                        instrument_id, source_id, run_id, run_id, timeframe,
                        row["open_time_ms"], row["close_time_ms"], row["open"],
                        row["high"], row["low"], row["close"], row["volume"],
                        int(row["volume"] is not None), row["is_closed"],
                        json.dumps({
                            "timestamp_timezone": "UTC",
                            "origin": "cached_provider_ohlc",
                            "cache_file": path.name,
                        }, separators=(",", ":")),
                    ) for row in rows],
                )
                summaries.append({
                    "coin_id": coin_id,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "rows_imported_or_updated": len(rows),
                    "stale_at_import": stale,
                    "latest_candle_ms": max((row["open_time_ms"] for row in rows), default=None),
                    "instrument_active": coin_id in active_ids,
                })

    return {
        "database_path": str(Path(database_path)),
        "source": SOURCE_KEY,
        "operation": "local_cache_import_no_network",
        "files_processed": len(summaries),
        "total_rows_imported_or_updated": sum(item["rows_imported_or_updated"] for item in summaries),
        "summaries": summaries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import local CoinGecko OHLC CSV caches into SQLite (offline).")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    args = parser.parse_args()
    result = import_cached_ohlc(args.cache_dir, args.database)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
