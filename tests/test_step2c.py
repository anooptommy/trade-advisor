import asyncio
from datetime import datetime, timedelta, timezone

import certifi
import pandas as pd

from app.market import data
from app.market.data import _ohlc_to_dataframe, resample_ohlc


def test_normalized_ohlc_has_stable_columns_utc_and_source_metadata():
    raw = {"result": [
        [1_700_000_000_000, "10", "12", "9", "11"],
        [1_700_003_600_000, "11", "13", "10", "12"],
    ]}
    candles = _ohlc_to_dataframe(
        raw, coin_id="bitcoin", vs_currency="USD", timeframe="1h",
    )
    assert list(candles.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert str(candles["timestamp"].dt.tz) == "UTC"
    assert candles["volume"].isna().all()
    assert candles.attrs["market_data"]["volume_available"] is False
    assert candles.attrs["market_data"]["candle_origin"] == "provider_ohlc"


def test_resample_derives_ohlc_and_drops_incomplete_buckets_without_filling_volume():
    timestamps = pd.date_range("2026-01-01", periods=6, freq="h", tz="UTC")
    hourly = pd.DataFrame({
        "timestamp": timestamps,
        "open": [10, 11, 12, 11, 13, 14],
        "high": [12, 13, 14, 13, 15, 16],
        "low": [9, 10, 11, 10, 12, 13],
        "close": [11, 12, 11, 13, 14, 15],
        "volume": [float("nan")] * 6,
    })
    hourly.attrs["market_data"] = {
        "source": "coingecko_mcp",
        "coin_id": "bitcoin",
        "quote_currency": "usd",
        "timeframe": "1h",
        "volume_available": False,
    }

    four_hour = resample_ohlc(hourly, "4h")
    assert len(four_hour) == 1
    candle = four_hour.iloc[0]
    assert (candle["open"], candle["high"], candle["low"], candle["close"]) == (10, 14, 9, 13)
    assert pd.isna(candle["volume"])
    assert four_hour.attrs["market_data"]["candle_origin"] == "resampled_provider_ohlc"
    assert four_hour.attrs["market_data"]["resampling"]["incomplete_buckets"] == "dropped"


def test_ohlc_parser_rejects_rows_with_unknown_shape():
    try:
        _ohlc_to_dataframe([[1, 2, 3]], coin_id="bitcoin")
    except ValueError as error:
        assert "exactly timestamp" in str(error)
    else:
        raise AssertionError("malformed OHLC row was accepted")


def test_hourly_range_end_includes_latest_completed_utc_hour():
    now = datetime(2026, 11, 5, 12, 37, tzinfo=timezone.utc)
    assert data._range_end(now, "hourly") == "2026-11-05T12:00"
    assert data._range_end(now, "daily") == "2026-11-05"


def test_resampler_sums_volume_when_source_volume_is_available():
    timestamps = pd.date_range("2026-01-01", periods=4, freq="h", tz="UTC")
    hourly = pd.DataFrame({
        "timestamp": timestamps,
        "open": [10, 11, 12, 11],
        "high": [12, 13, 14, 13],
        "low": [9, 10, 11, 10],
        "close": [11, 12, 11, 13],
        "volume": [2, 3, 4, 5],
    })
    hourly.attrs["market_data"] = {"timeframe": "1h", "volume_available": True}
    four_hour = resample_ohlc(hourly, "4h")
    assert four_hour.iloc[0]["volume"] == 14
    assert four_hour.attrs["market_data"]["volume_available"] is True


def test_tls_context_keeps_platform_roots_and_adds_configured_roots(monkeypatch):
    class FakeContext:
        def __init__(self):
            self.loaded = []

        def load_verify_locations(self, **kwargs):
            self.loaded.append(kwargs)

    context = FakeContext()
    monkeypatch.setattr(data.ssl, "create_default_context", lambda: context)
    monkeypatch.setenv("SSL_CERT_FILE", "custom-ca.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "custom-ca-dir")

    result = data._build_tls_context()

    assert result is context
    assert {tuple(item.items()) for item in context.loaded} == {
        (("cafile", certifi.where()),),
        (("cafile", "custom-ca.pem"),),
        (("capath", "custom-ca-dir"),),
    }


def test_market_data_marks_cache_fallback_with_latest_candle_and_last_success(monkeypatch, tmp_path):
    monkeypatch.setattr(data, "CACHE_DIR", tmp_path)
    now = datetime.now(timezone.utc)
    old_success = (now - timedelta(days=2)).isoformat()
    daily = pd.DataFrame({
        "timestamp": [now - timedelta(days=1)],
        "open": [10.0], "high": [12.0], "low": [9.0], "close": [11.0],
        "volume": [float("nan")],
    })
    hourly = pd.DataFrame({
        "timestamp": [now - timedelta(hours=2), now - timedelta(hours=1)],
        "open": [10.0, 11.0], "high": [12.0, 13.0], "low": [9.0, 10.0],
        "close": [11.0, 12.0], "volume": [float("nan"), float("nan")],
    })
    data._write_cache(daily, "bitcoin", "usd", "daily", {"last_success_utc": old_success})
    data._write_cache(hourly, "bitcoin", "usd", "hourly", {"last_success_utc": old_success})

    async def fail_refresh(_requests):
        raise RuntimeError("refresh unavailable")

    monkeypatch.setattr(data, "_fetch_ohlc_batch", fail_refresh)
    result = asyncio.run(data.get_market_data(
        coin_id="bitcoin", daily_days=5, hourly_days=5,
    ))

    freshness = result["metadata"]["data_freshness"]
    assert freshness["daily"]["refresh_status"] == "stale_cache_fallback"
    assert freshness["hourly"]["refresh_status"] == "stale_cache_fallback"
    assert freshness["daily"]["last_success_utc"] == old_success
    assert freshness["daily"]["latest_candle_utc"].endswith("Z")
    assert freshness["hourly"]["latest_candle_age_hours"] is not None
