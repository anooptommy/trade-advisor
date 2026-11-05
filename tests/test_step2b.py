import json

import pandas as pd

from app.strategy.market_structure import (
    _merge_zones,
    classify_swings,
    cluster_levels,
    find_swing_points,
)
from app.strategy.rules import determine_htf_context, market_snapshot_json


def test_swing_points_use_real_high_low_and_return_chronological_prices():
    df = pd.DataFrame({
        "high": [1, 2, 5, 2, 1, 2, 6, 2, 1],
        "low": [4, 3, 2, 3, 4, 3, 1, 3, 4],
        "close": [2] * 9,
    })
    assert find_swing_points(df, lookback=2) == ([5.0, 6.0], [2.0, 1.0])


def test_level_clustering_groups_nearby_prices_and_handles_zero():
    assert cluster_levels([100.0, 100.2, 105.0], tolerance_pct=0.005) == [100.1, 105.0]
    assert cluster_levels([0.0, 0.0], tolerance_pct=0.01) == [0.0]


def test_overlapping_zones_merge_and_accumulate_touches():
    zones = _merge_zones([
        {"low": 10.0, "high": 12.0, "mid": 11.0, "touches": 1},
        {"low": 11.5, "high": 13.0, "mid": 12.25, "touches": 1},
        {"low": 15.0, "high": 16.0, "mid": 15.5, "touches": 1},
    ])
    assert zones == [
        {"low": 10.0, "high": 13.0, "mid": 11.5, "touches": 2},
        {"low": 15.0, "high": 16.0, "mid": 15.5, "touches": 1},
    ]


def test_market_structure_classifies_direction_and_transition():
    assert classify_swings([10, 12], [5, 7])["structure"] == "bullish"
    assert classify_swings([12, 10], [7, 5])["structure"] == "bearish"
    assert classify_swings([10, 12], [5, 4])["structure"] == "mixed"
    assert classify_swings([10], [5])["structure"] == "unknown"


def test_snapshot_serializes_strict_json_and_marks_analysis_only():
    snapshot = {
        "setup": "no_setup",
        "analysis_only": True,
        "missing_indicator": float("nan"),
        "items": [1.0, float("inf")],
    }
    encoded = market_snapshot_json(snapshot)
    decoded = json.loads(encoded)
    assert decoded["analysis_only"] is True
    assert decoded["missing_indicator"] is None
    assert decoded["items"] == [1.0, None]


def test_htf_context_requires_daily_four_hour_and_hourly_confirmation():
    def timeframe(trend, crossover="no_cross"):
        return {"trend": {"trend": trend}, "ema_crossover": crossover}

    bullish = determine_htf_context(
        timeframe("bullish"), timeframe("bullish"),
        timeframe("bullish", "bullish_cross"),
    )
    assert bullish["setup"] == "potential_long"
    assert bullish["execution_authorized"] is False

    blocked = determine_htf_context(
        timeframe("bullish"), timeframe("neutral"),
        timeframe("bullish", "bullish_cross"),
    )
    assert blocked["setup"] == "no_setup"
    assert blocked["context_aligned"] is False
