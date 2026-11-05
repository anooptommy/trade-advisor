import csv
import json
from contextlib import closing

from app.database import connect_database
from app.import_cached_ohlc import import_cached_ohlc


def test_cached_ohlc_import_is_idempotent_and_keeps_volume_missing(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    candle_path = cache / "bitcoin_usd_hourly.csv"
    with candle_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerow({
            "timestamp": "2026-10-07T00:00:00+00:00",
            "open": "100.10", "high": "102.00", "low": "99.50", "close": "101.25", "volume": "",
        })
    candle_path.with_suffix(".json").write_text(
        json.dumps({"last_success_utc": "2026-10-07T00:05:00+00:00"}), encoding="utf-8"
    )
    database = tmp_path / "paper.sqlite3"

    first = import_cached_ohlc(cache, database)
    second = import_cached_ohlc(cache, database)

    assert first["total_rows_imported_or_updated"] == 1
    assert second["total_rows_imported_or_updated"] == 1
    with closing(connect_database(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 1
        candle = connection.execute("SELECT * FROM candles").fetchone()
        run_count = connection.execute("SELECT COUNT(*) FROM market_data_runs").fetchone()[0]
        instrument = connection.execute("SELECT canonical_symbol, active FROM instruments").fetchone()

    assert run_count == 2
    assert candle["timeframe"] == "1h"
    assert candle["open_price"] == "100.10"
    assert candle["volume"] is None
    assert candle["volume_available"] == 0
    assert instrument["canonical_symbol"] == "BTC"
    assert instrument["active"] == 1
