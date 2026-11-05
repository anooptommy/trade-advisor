import pandas as pd

from app.market.indicators import calculate_indicators, calculate_macd
from app.strategy.rules import (
    CONFIDENCE_WEIGHTS,
    _directional_location,
    evaluate_confidence,
    evaluate_entry_confirmations,
    evaluate_htf_level_quality,
    evaluate_market,
    _has_htf_ma_confluence,
)


def _hourly_fixture():
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=9, freq="h", tz="UTC"),
        "open": [9, 10, 11, 10, 9, 10, 11, 10, 12.8],
        "high": [10, 11, 12, 11, 10, 11, 12, 11, 14],
        "low": [8, 9, 10, 9, 8, 9, 10, 9, 11.5],
        "close": [9.5, 10.5, 11.5, 10.5, 9.5, 10.5, 11.5, 10.5, 13.5],
        "ema9": [9, 10, 11, 10, 9, 10, 11, 10, 13],
        "ema21": [8.5, 9.5, 10.5, 9.5, 8.5, 9.5, 10.5, 9.5, 12],
    })


def test_macd_is_calculated_from_close_with_standard_warmup():
    close = pd.Series([float(value) for value in range(1, 60)])
    macd = calculate_macd(close)
    assert macd["macd"].iloc[:25].isna().all()
    assert macd["macd_signal"].iloc[:33].isna().all()
    assert pd.notna(macd["macd_histogram"].iloc[-1])
    assert abs(macd["macd_histogram"].iloc[-1] - (macd["macd"].iloc[-1] - macd["macd_signal"].iloc[-1])) < 1e-12


def test_indicator_pipeline_adds_macd_without_volume_values():
    count = 60
    frame = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=count, freq="h", tz="UTC"),
        "open": [float(x) for x in range(1, count + 1)],
        "high": [float(x) + 1 for x in range(1, count + 1)],
        "low": [float(x) - 1 for x in range(1, count + 1)],
        "close": [float(x) for x in range(1, count + 1)],
        "volume": [float("nan")] * count,
    })
    result = calculate_indicators(frame)
    assert {"macd", "macd_signal", "macd_histogram"}.issubset(result.columns)
    assert result["macd_histogram"].notna().any()
    assert result["volume"].isna().all()


def test_rsi_macd_confidence_component_scores_and_assesses_each_input_separately():
    def timeframe(rsi, macd=1.0, signal=0.0, histogram=0.5, previous=0.3):
        return {
            "trend": {"trend": "bullish", "ema_alignment": True},
            "ema9": 12.0, "ema21": 11.0, "sma50": 10.0,
            "momentum": {"momentum_confirmed": True, "momentum": "bullish", "rsi": rsi},
            "macd": macd, "macd_signal": signal,
            "macd_histogram": histogram, "macd_histogram_previous": previous,
        }
    structures = {"structure": "bullish"}
    quality = {"observed_points": 0, "assessed_weight": 0, "coverage_pct": 0, "rating": None, "factors": {}}
    entry = {"confirmations": {
        "liquidity_sweep": {"available": True, "confirmed": False},
        "structure_break": {"available": True, "confirmed": False},
    }}
    result = evaluate_confidence(
        "potential_long", timeframe(58), timeframe(54, macd=0.5, signal=0.1, histogram=0.4, previous=0.2),
        structures, structures, entry, quality,
    )
    component = result["components"]["rsi_macd_confirmation"]
    assert component["points"] == 5
    assert component["assessed_points"] == 5
    assert component["confirmed"] is True

    partial = evaluate_confidence(
        "potential_long", timeframe(58), timeframe(54, macd=None, signal=None, histogram=None, previous=None),
        structures, structures, entry, quality,
    )["components"]["rsi_macd_confirmation"]
    assert partial["points"] == 2
    assert partial["assessed_points"] == 2
    assert partial["available"] is False

    macd_only = evaluate_confidence(
        "potential_long", timeframe(None), timeframe(54, macd=0.5, signal=0.1, histogram=0.4, previous=0.2),
        structures, structures, entry, quality,
    )["components"]["rsi_macd_confirmation"]
    assert macd_only["points"] == 3
    assert macd_only["assessed_points"] == 3


def test_entry_confirmation_counts_only_observed_ohlc_conditions():
    result = evaluate_entry_confirmations(
        _hourly_fixture(),
        {"support": [{"price": 12.0}]},
        "bullish",
    )
    assert result["available"] is True
    assert result["confirmed_count"] == 4
    assert result["confirmations"]["liquidity_sweep"]["confirmed"] is True
    assert result["confirmations"]["structure_break"]["confirmed"] is True
    assert result["confirmations"]["rejection_candle"]["confirmed"] is True
    assert result["confirmations"]["ema_reclaim_or_structure"]["confirmed"] is True


def test_missing_four_hour_level_list_is_an_assessable_zero_sweep():
    result = evaluate_entry_confirmations(_hourly_fixture(), {"support": []}, "bullish")
    sweep = result["confirmations"]["liquidity_sweep"]
    assert sweep["available"] is True
    assert sweep["confirmed"] is False


def test_location_requires_interaction_with_htf_level_or_zone():
    candle = _hourly_fixture().iloc[-1]
    support_location = _directional_location(
        "bullish", candle, {"ema9": None, "ema21": None},
        {"support": [{"price": 12.0}], "demand": []},
        {"support": [], "demand": []},
    )
    no_location = _directional_location(
        "bullish", candle, {"ema9": None, "ema21": None},
        {"support": [{"price": 8.0}], "demand": []},
        {"support": [], "demand": []},
    )
    assert support_location["confirmed"] is True
    assert no_location["confirmed"] is False


def test_missing_ema_is_reported_unavailable_not_as_confirmation():
    frame = _hourly_fixture().drop(columns=["ema9", "ema21"])
    result = evaluate_entry_confirmations(frame, {}, "bullish")
    assert result["confirmations"]["ema_reclaim_or_structure"] == {
        "confirmed": False,
        "available": False,
    }


def test_candidate_uses_two_confirmations_instead_of_requiring_latest_ema_cross():
    hourly = _hourly_fixture()
    hourly["sma50"] = [8.0] * len(hourly)
    hourly["rsi14"] = [55.0] * len(hourly)
    hourly["atr14"] = [1.0] * len(hourly)
    hourly["ema9_21_cross"] = ["no_cross"] * len(hourly)
    daily = pd.DataFrame([{
        "timestamp": pd.Timestamp("2026-01-01", tz="UTC"), "open": 10, "high": 12,
        "low": 9, "close": 11, "ema9": 10.8, "ema21": 10.5, "sma50": 10,
        "rsi14": 55, "atr14": 1, "ema9_21_cross": "no_cross",
    }])
    four_hour = daily.copy()
    four_hour["timestamp"] = pd.Timestamp("2026-01-01 04:00", tz="UTC")
    four_hour["open"] = 12
    four_hour["high"] = 14
    four_hour["low"] = 11
    four_hour["close"] = 13
    four_hour["ema9"] = 12.8
    four_hour["ema21"] = 12.5
    four_hour["sma50"] = 12
    structures = {
        "structure": "bullish", "support": [{"price": 12.0}], "resistance": [],
        "demand": [], "supply": [],
    }
    snapshot = evaluate_market(daily, four_hour, hourly, structures, structures, symbol="TEST")
    assert snapshot["setup"] == "potential_long"
    assert snapshot["one_hour"]["ema_crossover"] == "no_cross"
    assert snapshot["entry_confirmation"]["confirmed_count"] >= 2
    assert snapshot["confidence"]["status"] == "partial_candidate_score_missing_inputs"
    assert snapshot["confidence"]["score"] is not None
    assert snapshot["confidence"]["band"] is None


def test_forming_one_hour_candle_is_not_used_for_confirmation():
    now = pd.Timestamp.now(tz="UTC").floor("h")
    forming = pd.DataFrame([{
        "timestamp": now, "open": 10, "high": 20, "low": 1, "close": 19,
        "ema9": 15, "ema21": 14,
    }])
    result = evaluate_entry_confirmations(forming, {"support": [{"price": 10}]}, "bullish")
    assert result["available"] is False
    assert result["confirmed_count"] == 0


def test_level_quality_reports_partial_points_and_does_not_issue_incomplete_rating():
    candle = _hourly_fixture().iloc[-1]
    daily = {"support": [{"price": 12.0, "touches": 2}], "demand": []}
    four_hour = {"support": [{"price": 13.0, "touches": 1}], "demand": []}
    entry = {"confirmations": {"liquidity_sweep": {"available": True, "confirmed": True}}}
    result = evaluate_htf_level_quality("bullish", candle, daily, four_hour, entry)
    assert result["observed_points"] == 11
    assert result["maximum_weight"] == 16
    assert result["rating"] is None
    assert result["rating_available"] is False
    assert "volume_confirmation" not in result["factors"]


def test_strong_rejection_uses_wick_close_location_and_htf_test():
    candle = pd.Series({"timestamp": pd.Timestamp("2026-01-01 08:00", tz="UTC"), "open": 13.0, "high": 14.0, "low": 9.0, "close": 13.5})
    daily = {"support": [{"price": 10.0}], "demand": []}
    four_hour = {"support": [], "demand": []}
    result = evaluate_htf_level_quality("bullish", candle, daily, four_hour, {},)
    assert result["factors"]["strong_rejection"]["available"] is True
    assert result["factors"]["strong_rejection"]["confirmed"] is True
    assert result["factors"]["strong_rejection"]["points"] == 2


def test_major_swing_requires_two_atr_reversal_and_current_interaction():
    def frame():
        lows = [8, 7, 6, 7, 8, 5, 8, 9, 10, 11, 12, 11, 10, 9, 8, 9]
        highs = [10, 11, 12, 11, 10, 9, 11, 12, 13, 14, 15, 14, 13, 12, 13, 14]
        return pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01", periods=len(lows), freq="D", tz="UTC"),
            "low": lows, "high": highs, "close": [(l + h) / 2 for l, h in zip(lows, highs)],
            "atr14": [2.0] * len(lows),
        })
    candle = pd.Series({"timestamp": pd.Timestamp("2026-01-20", tz="UTC"), "open": 5.2, "high": 5.4, "low": 4.9, "close": 5.3})
    daily = {"support": [{"price": 5.0}], "demand": [{"low": 4.9, "high": 5.1}]}
    four_hour = {"support": [], "demand": []}
    result = evaluate_htf_level_quality("bullish", candle, daily, four_hour, {}, daily_df=frame(), four_hour_df=frame())
    assert result["factors"]["major_swing"]["available"] is True
    assert result["factors"]["major_swing"]["confirmed"] is True


def test_ema_sma_confluence_uses_same_timeframe_atr_and_level():
    candle = pd.Series({"timestamp": pd.Timestamp("2026-01-01", tz="UTC"), "open": 10, "high": 11, "low": 9, "close": 10})
    frame = pd.DataFrame([{"timestamp": pd.Timestamp("2026-01-01", tz="UTC"), "open": 10, "high": 11, "low": 9, "close": 10, "ema9": 10.1, "ema21": 12, "sma50": 14, "atr14": 1.0}])
    daily = {"support": [{"price": 10.0}], "demand": []}
    four_hour = {"support": [{"price": 10.0}], "demand": []}
    result = evaluate_htf_level_quality("bullish", candle, daily, four_hour, {}, daily_df=frame, four_hour_df=frame)
    assert result["factors"]["ema_sma_confluence"]["available"] is True
    assert result["factors"]["ema_sma_confluence"]["confirmed"] is True
    assert result["factors"]["ema_sma_confluence"]["points"] == 1


def test_ema_sma_confluence_requires_latest_hourly_interaction_with_the_area():
    candle = pd.Series({"low": 19.0, "high": 21.0})
    frame = pd.DataFrame([{
        "timestamp": pd.Timestamp("2026-01-01", tz="UTC"), "open": 10, "high": 11,
        "low": 9, "close": 10, "ema9": 10.1, "ema21": 12, "sma50": 14, "atr14": 1.0,
    }])
    daily = {"support": [{"price": 10.0}], "demand": []}
    four_hour = {"support": [{"price": 10.0}], "demand": []}

    result = _has_htf_ma_confluence("bullish", candle, frame, frame, daily, four_hour)

    assert result["available"] is True
    assert result["confirmed"] is False
    assert result["evidence"] == []


def test_fresh_zone_is_first_retest_after_price_departed():
    lows = [8, 7, 6, 7, 8, 5, 8, 9, 10, 11, 12, 11, 10]
    highs = [10, 11, 12, 11, 10, 9, 11, 12, 13, 14, 15, 14, 13]
    daily_frame = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=len(lows), freq="D", tz="UTC"),
        "low": lows, "high": highs, "close": [(l + h) / 2 for l, h in zip(lows, highs)],
    })
    hourly = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-12", periods=5, freq="h", tz="UTC"),
        "low": [6.0, 6.1, 6.0, 6.2, 5.0], "high": [6.5, 6.6, 6.5, 6.7, 5.2],
    })
    current = pd.Series({"timestamp": hourly.iloc[-1]["timestamp"], "open": 5.1, "high": 5.2, "low": 5.0, "close": 5.15})
    daily = {"support": [], "demand": [{"low": 4.95, "high": 5.05}]}
    four_hour = {"support": [], "demand": []}
    result = evaluate_htf_level_quality("bullish", current, daily, four_hour, {}, daily_df=daily_frame, four_hour_df=daily_frame, hourly_df=hourly)
    assert result["factors"]["fresh_zone"]["available"] is True
    assert result["factors"]["fresh_zone"]["confirmed"] is True
    assert result["factors"]["fresh_zone"]["points"] == 2
    zone = result["factors"]["fresh_zone"]["evidence"][0]
    assert zone["zone_id"].startswith(f"{zone['timeframe']}:")
    assert zone["origin_timestamp"] in zone["zone_id"]


def test_no_detected_level_touch_or_sweep_is_zero_when_data_is_available():
    candle = _hourly_fixture().iloc[-1]
    daily = {"support": [], "resistance": [], "demand": [], "supply": []}
    four_hour = {"support": [], "resistance": [], "demand": [], "supply": []}
    entry = {"confirmations": {"liquidity_sweep": {"available": True, "confirmed": False}}}

    result = evaluate_htf_level_quality("bullish", candle, daily, four_hour, entry)

    for name in ("daily_level", "four_hour_level", "multiple_touches", "prior_liquidity_sweep"):
        assert result["factors"][name]["available"] is True
        assert result["factors"][name]["points"] == 0
    assert result["assessed_weight"] == 11
    assert result["observed_points"] == 0


def test_unavailable_htf_factor_is_excluded_from_score_numerator_and_denominator():
    def timeframe():
        return {
            "trend": {"trend": "bullish", "ema_alignment": True},
            "ema9": 12.0, "ema21": 11.0, "sma50": 10.0,
            "momentum": {"momentum_confirmed": True, "momentum": "bullish"},
        }

    quality = {
        "observed_points": 5,
        "assessed_weight": 5,
        "coverage_pct": 29.4,
        "rating": None,
        "factors": {
            "daily_level": {"available": True, "points": 3},
            "strong_rejection": {"available": False, "points": 2},
        },
    }
    structures = {"structure": "bullish"}
    result = evaluate_confidence(
        "potential_long", timeframe(), timeframe(), structures, structures,
        {"confirmations": {}}, quality,
    )

    assert result["components"]["htf_level_quality"]["points"] == 3
    assert result["components"]["htf_level_quality"]["assessed_points"] == 3
    assert result["observed_points"] == 48
    assert result["assessed_points"] == 48
    assert result["score_fraction"] == 1.0
    assert result["coverage_pct"] == 52.7


def test_volume_oi_is_excluded_and_band_waits_only_for_active_inputs():
    def timeframe(trend):
        return {
            "trend": {"trend": trend, "ema_alignment": True},
            "ema9": 12.0, "ema21": 11.0, "sma50": 10.0,
            "momentum": {"momentum_confirmed": True, "momentum": "bullish"},
        }

    structures = {"structure": "bullish"}
    entry = {"confirmations": {
        "liquidity_sweep": {"available": True, "confirmed": True},
        "structure_break": {"available": True, "confirmed": True},
    }}
    quality = {"observed_points": 0, "assessed_weight": 0, "coverage_pct": 0, "rating": None, "factors": {}}
    result = evaluate_confidence(
        "potential_long", timeframe("bullish"), timeframe("bullish"), structures, structures,
        entry, quality,
    )
    assert result["status"] == "partial_candidate_score_missing_inputs"
    assert result["score"] == 70
    assert result["score_out_of"] == 70
    assert result["score_fraction"] == 1.0
    assert result["coverage_pct"] == round(70 / 91 * 100, 1)
    assert result["band"] is None
    assert result["total_points"] == 91
    assert result["observed_points"] == 70
    assert result["assessed_points"] == 70
    assert "volume" not in result["missing_inputs"]
    assert "open_interest" not in result["missing_inputs"]
    assert "volume_oi_confirmation" not in result["components"]
    assert "macd_indicator" in result["missing_inputs"]


def test_confidence_is_not_applicable_without_a_deterministic_setup():
    result = evaluate_confidence("no_setup", {}, {}, {}, {}, {}, {})
    assert result["status"] == "not_applicable_no_qualifying_setup"
    assert result["score"] is None
    assert result["score_fraction"] is None
    assert result["band"] is None


def test_confidence_weight_allocation_excludes_volume_oi():
    assert sum(CONFIDENCE_WEIGHTS.values()) == 91
    assert CONFIDENCE_WEIGHTS["htf_level_quality"] == 16
    assert "volume_oi_confirmation" not in CONFIDENCE_WEIGHTS


def test_complete_active_budget_emits_band_on_normalized_percentage():
    from app.strategy.rules import HTF_LEVEL_QUALITY_WEIGHTS

    def timeframe():
        return {
            "trend": {"trend": "bullish", "ema_alignment": True},
            "ema9": 12.0, "ema21": 11.0, "sma50": 10.0,
            "momentum": {"rsi": 60.0},
            "macd": 1.0, "macd_signal": 0.0,
            "macd_histogram": 0.5, "macd_histogram_previous": 0.3,
        }

    quality = {"factors": {
        name: {"available": True, "points": weight}
        for name, weight in HTF_LEVEL_QUALITY_WEIGHTS.items()
    }}
    structures = {"structure": "bullish"}
    entry = {"confirmations": {
        "liquidity_sweep": {"available": True, "confirmed": True},
        "structure_break": {"available": True, "confirmed": True},
    }}
    result = evaluate_confidence(
        "potential_long", timeframe(), timeframe(), structures, structures,
        entry, quality,
    )

    assert result["status"] == "complete_candidate_score"
    assert result["assessed_points"] == result["total_points"] == 91
    assert result["score_pct"] == 100.0
    assert result["band"] == "maximum_permitted"
