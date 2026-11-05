import asyncio
import copy
import pandas as pd
import pytest
from app.visualization.market_infographic import build_factor_statuses, proximity, render_market_infographic


def _snapshot():
    return {
        "symbol": "BTC/USD", "timestamp": "2026-10-12T12:00:00Z", "price": 100,
        "setup": "potential_long", "analysis_only": True,
        "daily": {"trend": {"trend": "bullish"}},
        "four_hour": {"trend": {"trend": "bullish"}},
        "one_hour": {"trend": {"trend": "bullish"}, "ema9": 99, "ema21": 98, "sma50": 95,
                     "momentum": {"rsi": 58}, "macd": 2, "macd_signal": 1},
        "daily_structure": {"structure": "bullish", "support": [{"price": 99}], "resistance": [{"price": 110}]},
        "four_hour_structure": {"structure": "bullish", "support": [], "resistance": []},
        "entry_confirmation": {"confirmations": {
            "liquidity_sweep": {"available": True, "confirmed": True},
            "structure_break": {"available": True, "confirmed": False},
        }},
        "level_quality": {"rating_available": True, "rating": "strong", "observed_points": 8, "coverage_pct": 75},
        "confidence": {"score_fraction": .8, "observed_points": 8, "assessed_points": 10, "coverage_pct": 80},
    }


def test_all_eight_factors_map_conflicts_and_missing_evidence():
    snap = _snapshot()
    snap["four_hour"]["trend"]["trend"] = "bearish"
    snap["four_hour_structure"]["structure"] = "bearish"
    snap["entry_confirmation"]["confirmations"]["liquidity_sweep"]["available"] = False
    factors = build_factor_statuses(snap)
    assert len(factors) == 8
    assert factors[0]["status"] == "BULLISH" and factors[1]["status"] == "BEARISH"
    assert factors[3]["status"] == "N/A" and factors[7]["status"] == "MIXED STRUCTURE"
    assert all(f["change"] == "CHANGE UNKNOWN" for f in factors)


@pytest.mark.parametrize(("level", "expected"), [(100, "AT LEVEL"), (101.5, "CLOSE"), (105, "APPROACHING"), (110, "FAR")])
def test_proximity_thresholds(level, expected):
    assert proximity(100, level)["label"] == expected


def test_unavailable_and_stale_data_are_explicit():
    snap = _snapshot()
    snap["one_hour"]["macd"] = None
    factors = build_factor_statuses(snap, stale=True)
    assert factors[6]["status"] == "N/A"
    assert all("stale" in factor["detail"] for factor in factors)
    assert proximity(None, 10)["label"] == "N/A"


def test_renderer_creates_png_without_changing_snapshot(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    timestamps = pd.date_range("2026-10-10", periods=70, freq="h", tz="UTC")
    frame = pd.DataFrame({"timestamp": timestamps, "close": range(100, 170),
                          "ema9": range(99, 169), "ema21": range(98, 168),
                          "sma50": range(97, 167)})
    snap = _snapshot()
    before = copy.deepcopy(snap)
    path = render_market_infographic(snap, {"1d": frame, "4h": frame, "1h": frame}, output_dir=tmp_path)
    assert path.is_file() and path.suffix == ".png"
    with Image.open(path) as image:
        assert image.format == "PNG" and image.size == (1200, 1600)
    assert snap == before


def test_telegram_photo_delivery_uses_environment_credentials(monkeypatch, tmp_path):
    from app import telegram
    photo = tmp_path / "dashboard.png"
    photo.write_bytes(b"png")
    observed = {}
    async def fake_to_thread(fn, *args):
        observed["args"] = args
        return 37
    monkeypatch.setattr(telegram.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    assert asyncio.run(telegram.send_telegram_photo(photo, "BTC analysis")) == 37
    assert str(observed["args"][0]) == "test-token" and observed["args"][1] == "test-chat"
