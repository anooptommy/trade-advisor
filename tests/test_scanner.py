import asyncio
import json
import logging

import pandas as pd

from app.agents.gemini import review_candidate
from app import scanner
from app.scanner import load_watchlist, qualifies_for_gemini


def _snapshot(setup="potential_long", daily="bullish", four_hour="bullish"):
    return {
        "setup": setup,
        "daily_structure": {"structure": daily},
        "four_hour_structure": {"structure": four_hour},
        "analysis_only": True,
    }


def test_watchlist_is_explicit_and_contains_coin_ids():
    config = load_watchlist()
    assert config["assets"]
    assert all(asset["symbol"] and asset["coin_id"] for asset in config["assets"])
    assert len({asset["coin_id"] for asset in config["assets"]}) == len(config["assets"])


def test_only_setup_and_daily_four_hour_structure_alignment_qualify():
    assert qualifies_for_gemini(_snapshot())
    assert qualifies_for_gemini(_snapshot("potential_short", "bearish", "bearish"))
    assert not qualifies_for_gemini(_snapshot("potential_long", "mixed", "bullish"))
    assert not qualifies_for_gemini(_snapshot("no_setup", "bullish", "bullish"))


def test_gemini_receives_only_snapshot_and_is_prompted_not_to_invent_facts():
    class FakeInteractions:
        def create(self, **kwargs):
            self.kwargs = kwargs
            return type("Result", (), {"output_text": "Review complete."})()

    class FakeClient:
        def __init__(self):
            self.interactions = FakeInteractions()

    client = FakeClient()
    result = review_candidate(_snapshot(), client=client, model="test-model")
    assert result == "Review complete."
    assert json.loads(client.interactions.kwargs["input"]) == _snapshot()
    assert client.interactions.kwargs["model"] == "test-model"
    assert "Do not invent" in client.interactions.kwargs["system_instruction"]
    assert "score_fraction" in client.interactions.kwargs["system_instruction"]
    assert "coverage_pct" in client.interactions.kwargs["system_instruction"]


def test_watchlist_waits_before_each_asset_after_the_first(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="app.scanner")
    waits = []
    scanned = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    async def fake_analyze_asset(asset, **kwargs):
        scanned.append(asset["symbol"])
        return {"symbol": asset["symbol"], "status": "analyzed"}

    monkeypatch.setattr(scanner.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(scanner, "analyze_asset", fake_analyze_asset)
    config = {
        "assets": [
            {"symbol": "BTC", "coin_id": "bitcoin"},
            {"symbol": "ETH", "coin_id": "ethereum"},
            {"symbol": "SOL", "coin_id": "solana"},
        ],
        "quote_currency": "usd",
        "daily_days": 180,
        "hourly_days": 31,
    }

    results = asyncio.run(scanner.scan_watchlist(config))

    assert scanned == ["BTC", "ETH", "SOL"]
    assert waits == [20, 20]
    assert len(results) == 3
    assert "Waiting 20 seconds before next CoinGecko MCP request" in caplog.text


def test_analyze_asset_carries_stale_data_status_into_scan_result(monkeypatch):
    timestamp = pd.date_range("2026-01-01", periods=4, freq="h", tz="UTC")
    hourly = pd.DataFrame({
        "timestamp": timestamp,
        "open": [10.0] * 4, "high": [12.0] * 4, "low": [9.0] * 4,
        "close": [11.0] * 4, "volume": [float("nan")] * 4,
    })
    daily = hourly.iloc[[0]].copy()
    freshness = {
        "daily": {"refresh_status": "stale_cache_fallback"},
        "hourly": {"refresh_status": "stale_cache_fallback"},
    }
    metadata = {
        "symbol": "BTC/USD",
        "data_freshness": freshness,
        "timeframes": {},
    }
    structures = {
        "1d": {"structure": "bullish"},
        "4h": {"structure": "bullish"},
    }

    async def fake_get_market_data(**_kwargs):
        return {"daily": daily, "hourly": hourly, "metadata": metadata}

    monkeypatch.setattr(scanner, "get_market_data", fake_get_market_data)
    monkeypatch.setattr(scanner, "persist_derived_4h_candles", lambda *_args, **_kwargs: 4)
    monkeypatch.setattr(scanner, "calculate_indicators", lambda frame: frame)
    monkeypatch.setattr(scanner, "analyze_market_structure", lambda _frame, timeframe: structures[timeframe])
    monkeypatch.setattr(
        scanner,
        "evaluate_market",
        lambda **_kwargs: {
            "setup": "no_setup",
            "daily_structure": structures["1d"],
            "four_hour_structure": structures["4h"],
        },
    )

    result = asyncio.run(scanner.analyze_asset({"symbol": "BTC", "coin_id": "bitcoin"}))

    assert result["status"] == "analyzed"
    assert result["data_status"] == "stale_cache_fallback"
    assert result["data_freshness"] == freshness
