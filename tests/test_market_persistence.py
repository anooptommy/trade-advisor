from contextlib import closing
from datetime import datetime, timezone

import pandas as pd

from app.database import connect_database
from app.market.persistence import persist_derived_4h_candles, persist_provider_candles
from app.scan_persistence import begin_scan_run


def test_provider_refresh_is_upserted_and_refresh_run_is_audited(tmp_path):
    database = tmp_path / "market.sqlite3"
    timestamp = pd.Timestamp("2026-10-07T10:00:00Z")
    frame = pd.DataFrame([{
        "timestamp": timestamp,
        "open": 100.0,
        "high": 110.0,
        "low": 90.0,
        "close": 105.0,
        "volume": float("nan"),
    }])
    common = {
        "coin_id": "bitcoin",
        "symbol": "BTC",
        "quote_currency": "usd",
        "timeframe": "1h",
        "requested_start": "2026-10-06",
        "requested_end": "2026-10-07",
        "started_at": datetime.now(timezone.utc),
        "database_path": database,
    }

    assert persist_provider_candles(frame, **common) == 1
    frame.loc[0, "close"] = 106.0
    assert persist_provider_candles(frame, **common) == 1

    with closing(connect_database(database)) as connection:
        candles = connection.execute("SELECT * FROM candles").fetchall()
        runs = connection.execute("SELECT * FROM market_data_runs ORDER BY id").fetchall()

    assert len(candles) == 1
    assert candles[0]["close_price"] == "106.0"
    assert candles[0]["volume"] is None
    assert candles[0]["volume_available"] == 0
    assert len(runs) == 2
    assert runs[-1]["status"] == "succeeded"
    assert runs[-1]["rows_received"] == 1
    assert candles[0]["first_ingestion_run_id"] == runs[0]["id"]
    assert candles[0]["last_ingestion_run_id"] == runs[-1]["id"]


def test_derived_4h_candles_use_same_instrument_without_provider_run(tmp_path):
    database = tmp_path / "derived.sqlite3"
    begin_scan_run({
        "quote_currency": "usd", "daily_days": 180, "hourly_days": 90,
        "assets": [{"symbol": "BTC", "coin_id": "bitcoin"}],
    }, database_path=database)
    frame = pd.DataFrame([{
        "timestamp": pd.Timestamp("2026-10-07T08:00:00Z"),
        "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0,
        "volume": float("nan"),
    }])

    assert persist_derived_4h_candles(
        frame, coin_id="bitcoin", symbol="BTC", quote_currency="usd", database_path=database,
    ) == 1
    with closing(connect_database(database)) as connection:
        candle = connection.execute("SELECT * FROM candles").fetchone()
        instruments = connection.execute("SELECT COUNT(*) FROM instruments").fetchone()[0]
        market_runs = connection.execute("SELECT COUNT(*) FROM market_data_runs").fetchone()[0]

    assert candle["timeframe"] == "4h"
    assert candle["origin_kind"] == "derived"
    assert candle["derivation_version"] == "1h_to_4h_v1"
    assert candle["volume"] is None and candle["volume_available"] == 0
    assert instruments == 1
    assert market_runs == 0
