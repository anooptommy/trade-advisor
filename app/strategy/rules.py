import json
import logging
import math

import pandas as pd

from app.strategy.market_structure import find_swing_points, _find_swing_records

logger = logging.getLogger(__name__)

CONFIDENCE_WEIGHTS = {
    "daily_trend": 15,
    "four_hour_trend": 15,
    "htf_level_quality": 16,
    "liquidity_sweep": 10,
    "one_hour_structure": 15,
    "ema_sma_confluence": 10,
    # Future optional component: "volume_oi_confirmation": 8,
    "rsi_macd_confirmation": 5,
    "market_regime": 5,
}

HTF_LEVEL_QUALITY_WEIGHTS = {
    "daily_level": 3,
    "four_hour_level": 2,
    "multiple_touches": 2,
    "strong_rejection": 2,
    "prior_liquidity_sweep": 2,
    "major_swing": 2,
    # Future optional factor: "volume_confirmation": 1,
    "ema_sma_confluence": 1,
    "fresh_zone": 2,
}

RSI_MACD_CONFIRMATION_WEIGHTS = {"rsi": 2, "macd": 3}


def check_trend(row: pd.Series) -> dict:
    """
    Determine trend using EMA9, EMA21 and SMA50.
    """

    price = row["close"]
    ema9 = row["ema9"]
    ema21 = row["ema21"]
    sma50 = row["sma50"]

    if pd.isna(ema9) or pd.isna(ema21) or pd.isna(sma50):
        return {
            "trend": "unknown",
            "trend_confirmed": False,
            "ema_alignment": False,
            "price_above_sma50": False,
        }

    bullish = (
        ema9 > ema21
        and ema21 > sma50
        and price > sma50
    )

    bearish = (
        ema9 < ema21
        and ema21 < sma50
        and price < sma50
    )

    if bullish:
        trend = "bullish"
    elif bearish:
        trend = "bearish"
    else:
        trend = "neutral"

    return {
        "trend": trend,
        "trend_confirmed": (
            bullish or bearish
        ),
        "ema_alignment": (
            bullish or bearish
        ),
        "price_above_sma50": (
            price > sma50
        ),
    }


def check_crossover(
    row: pd.Series,
) -> str:
    """
    Return the most recent EMA9/EMA21 crossover.
    """

    return row.get(
        "ema9_21_cross",
        "no_cross",
    )


def check_momentum(
    row: pd.Series,
) -> dict:

    rsi = row["rsi14"]

    if pd.isna(rsi):
        return {
            "momentum": "unknown",
            "momentum_confirmed": False,
            "rsi": None,
        }

    if rsi >= 50:
        momentum = "bullish"
    else:
        momentum = "bearish"

    return {
        "momentum": momentum,
        "momentum_confirmed": True,
        "rsi": round(float(rsi), 2),
    }


def evaluate_timeframe(
    df: pd.DataFrame,
) -> dict:
    """
    Evaluate the most recent candle for one timeframe.
    """

    if df.empty:
        raise ValueError(
            "DataFrame is empty."
        )

    row = df.iloc[-1]
    previous_row = df.iloc[-2] if len(df) > 1 else None

    trend = check_trend(row)
    momentum = check_momentum(row)

    return {
        "timestamp": str(
            row["timestamp"]
        ),
        "price": float(
            row["close"]
        ),
        "trend": trend,
        "momentum": momentum,
        "ema9": round(
            float(row["ema9"]), 2,
        ) if pd.notna(row["ema9"]) else None,
        "ema21": round(
            float(row["ema21"]), 2,
        ) if pd.notna(row["ema21"]) else None,
        "sma50": (
            None
            if pd.isna(row["sma50"])
            else round(
                float(row["sma50"]),
                2,
            )
        ),
        "ema_crossover": check_crossover(
            row
        ),
        "atr14": (
            None
            if pd.isna(row["atr14"])
            else round(
                float(row["atr14"]),
                2,
            )
        ),
        "macd": round(float(row["macd"]), 6) if "macd" in row and pd.notna(row["macd"]) else None,
        "macd_signal": round(float(row["macd_signal"]), 6) if "macd_signal" in row and pd.notna(row["macd_signal"]) else None,
        "macd_histogram": round(float(row["macd_histogram"]), 6) if "macd_histogram" in row and pd.notna(row["macd_histogram"]) else None,
        "macd_histogram_previous": (
            round(float(previous_row["macd_histogram"]), 6)
            if previous_row is not None and "macd_histogram" in previous_row and pd.notna(previous_row["macd_histogram"])
            else None
        ),
    }


def determine_setup(
    daily: dict,
    four_hour: dict,
    one_hour: dict,
) -> str:
    """
    HTF-first setup classification.

    This is NOT an execution signal.
    """

    daily_trend = daily["trend"]["trend"]
    four_hour_trend = four_hour["trend"]["trend"]
    one_hour_trend = one_hour["trend"]["trend"]

    one_hour_cross = (
        one_hour["ema_crossover"]
    )

    if (
        daily_trend == "bullish"
        and four_hour_trend == "bullish"
        and one_hour_trend == "bullish"
        and one_hour_cross == "bullish_cross"
    ):
        return "potential_long"

    if (
        daily_trend == "bearish"
        and four_hour_trend == "bearish"
        and one_hour_trend == "bearish"
        and one_hour_cross == "bearish_cross"
    ):
        return "potential_short"

    return "no_setup"


def determine_htf_context(daily: dict, four_hour: dict, one_hour: dict) -> dict:
    """Apply Daily context, 4H alignment, then 1H timing confirmation.

    The result is analysis only; it is not an execution instruction.
    """
    daily_trend = daily["trend"]["trend"]
    four_hour_trend = four_hour["trend"]["trend"]
    one_hour_trend = one_hour["trend"]["trend"]
    one_hour_cross = one_hour["ema_crossover"]
    if daily_trend == "bullish" and four_hour_trend == "bullish":
        direction = "bullish"
        context_aligned = True
    elif daily_trend == "bearish" and four_hour_trend == "bearish":
        direction = "bearish"
        context_aligned = True
    else:
        direction = "neutral"
        context_aligned = False
    timing_confirmed = (
        (direction == "bullish" and one_hour_trend == "bullish" and one_hour_cross == "bullish_cross")
        or (direction == "bearish" and one_hour_trend == "bearish" and one_hour_cross == "bearish_cross")
    )
    setup = "potential_long" if direction == "bullish" and timing_confirmed else "potential_short" if direction == "bearish" and timing_confirmed else "no_setup"
    return {
        "daily_context": daily_trend,
        "four_hour_alignment": four_hour_trend,
        "one_hour_timing": one_hour_trend,
        "context_aligned": context_aligned,
        "timing_confirmed": timing_confirmed,
        "setup": setup,
        "execution_authorized": False,
    }


def _directional_location(
    direction: str,
    latest_hour: pd.Series,
    four_hour: dict,
    daily_structure: dict,
    four_hour_structure: dict,
) -> dict:
    """Check 1H interaction with HTF areas and report nearest references.

    Proximity is descriptive only. The deterministic location confirmation
    remains the stricter completed-1H interaction rule below.
    """
    low, high = float(latest_hour["low"]), float(latest_hour["high"])
    close = float(latest_hour["close"])
    atr = float(latest_hour.get("atr14")) if pd.notna(latest_hour.get("atr14")) else None
    atr = atr if atr is not None and math.isfinite(atr) and atr > 0 else None
    touched: list[str] = []
    structures = (("1d", daily_structure), ("4h", four_hour_structure))
    proximity_by_side: dict[str, list[dict]] = {}
    for side, level_key, zone_key in (
        ("bullish", "support", "demand"),
        ("bearish", "resistance", "supply"),
    ):
        references = []
        for label, structure in structures:
            for level in structure.get(level_key, []):
                price = float(level["price"])
                distance = abs(close - price)
                references.append({
                    "timeframe": label, "kind": level_key, "price": price,
                    "touches": level.get("touches"), "distance": distance,
                    "relation_to_close": "above" if price > close else "below" if price < close else "at_close",
                    "distance_pct": distance / abs(close) * 100 if close else None,
                    "distance_atr": distance / atr if atr else None,
                })
                if direction == side and low <= price <= high:
                    touched.append(f"{label}_{level_key}")
            for zone in structure.get(zone_key, []):
                zone_low, zone_high = float(zone["low"]), float(zone["high"])
                distance = 0.0 if zone_low <= close <= zone_high else min(abs(close - zone_low), abs(close - zone_high))
                references.append({
                    "timeframe": label, "kind": f"{zone_key}_zone",
                    "low": zone_low, "high": zone_high,
                    "touches": zone.get("touches"), "distance": distance,
                    "relation_to_close": "above" if close < zone_low else "below" if close > zone_high else "inside",
                    "distance_pct": distance / abs(close) * 100 if close else None,
                    "distance_atr": distance / atr if atr else None,
                })
                if direction == side and low <= zone_high and high >= zone_low:
                    touched.append(f"{label}_{zone_key}_zone")
        proximity_by_side[side] = sorted(references, key=lambda item: item["distance"])[:3]

    # A pullback is observable when the 1H range crosses a 4H 9/21 EMA and
    # closes back on the directional side. This is evidence, not an entry.
    ema_pullback = False
    ema_references = []
    for key in ("ema9", "ema21"):
        value = four_hour.get(key)
        if value is not None:
            distance = abs(close - float(value))
            ema_references.append({
                "timeframe": "4h", "kind": key, "price": float(value),
                "distance": distance,
                "distance_pct": distance / abs(close) * 100 if close else None,
                "distance_atr": distance / atr if atr else None,
            })
        if value is not None and direction in ("bullish", "bearish") and low <= value <= high:
            ema_pullback = close >= value if direction == "bullish" else close <= value
            if ema_pullback:
                touched.append(f"4h_{key}_pullback")
                break
    if direction in ("bullish", "bearish"):
        proximity_by_side[direction] = sorted(
            proximity_by_side[direction] + ema_references,
            key=lambda item: item["distance"],
        )[:3]
    return {
        "confirmed": bool(touched),
        "interaction_confirmed": bool(touched),
        "evidence": sorted(set(touched)),
        "available": direction in ("bullish", "bearish"),
        "evaluated_timestamp": str(latest_hour["timestamp"]),
        "evaluated_close": close,
        "atr14_1h": atr,
        "proximity_by_side": proximity_by_side,
        "confirmation_rule": "completed 1H range overlaps directional HTF support/resistance or zone, or crosses a 4H EMA9/21 and closes back on its directional side",
    }


def _closed_hourly_candles(hourly_df: pd.DataFrame) -> pd.DataFrame:
    """Return completed UTC hourly candles, excluding the current forming bar."""
    if hourly_df.empty:
        return hourly_df.copy()
    if hourly_df.attrs.get("all_candles_closed"):
        return hourly_df
    frame = hourly_df.sort_values("timestamp").reset_index(drop=True)
    timestamps = pd.to_datetime(frame["timestamp"], utc=True)
    closed_mask = timestamps + pd.Timedelta(hours=1) <= pd.Timestamp.now(tz="UTC")
    return frame.loc[closed_mask].reset_index(drop=True)


def evaluate_htf_level_quality(
    direction: str,
    candle: pd.Series,
    daily_structure: dict,
    four_hour_structure: dict,
    entry_confirmation: dict,
    daily_df: pd.DataFrame | None = None,
    four_hour_df: pd.DataFrame | None = None,
    hourly_df: pd.DataFrame | None = None,
) -> dict:
    """Report observable level-quality points and explicitly incomplete factors."""
    low, high = float(candle["low"]), float(candle["high"])
    level_key, zone_key = ("support", "demand") if direction == "bullish" else ("resistance", "supply")
    weights = HTF_LEVEL_QUALITY_WEIGHTS
    touched_by_tf: dict[str, list[dict]] = {}
    level_data_available: dict[str, bool] = {}
    for label, structure in (("daily", daily_structure), ("four_hour", four_hour_structure)):
        level_key, zone_key = ("support", "demand") if direction == "bullish" else ("resistance", "supply")
        level_data_available[label] = (
            isinstance(structure.get(level_key), list)
            and isinstance(structure.get(zone_key), list)
        )
        hits = []
        for level in structure.get(level_key, []):
            if low <= float(level["price"]) <= high:
                hits.append({"type": level_key, "price": level["price"], "touches": level.get("touches")})
        for zone in structure.get(zone_key, []):
            if low <= float(zone["high"]) and high >= float(zone["low"]):
                hits.append({"type": zone_key, "low": zone["low"], "high": zone["high"], "touches": zone.get("touches")})
        touched_by_tf[label] = hits

    daily_available = level_data_available["daily"]
    four_hour_available = level_data_available["four_hour"]
    touch_candidates = touched_by_tf["daily"] + touched_by_tf["four_hour"]
    sweep = entry_confirmation.get("confirmations", {}).get("liquidity_sweep", {})
    strong_rejection = _has_strong_htf_rejection(direction, candle, daily_structure, four_hour_structure)
    major_swing = _interacts_with_major_swing(
        direction, candle, daily_df, four_hour_df,
        daily_structure, four_hour_structure,
    )
    ma_confluence = _has_htf_ma_confluence(
        direction, candle, daily_df, four_hour_df, daily_structure, four_hour_structure,
    )
    fresh_zone = _has_fresh_zone_retest(
        direction, candle, daily_df, four_hour_df, hourly_df,
        daily_structure, four_hour_structure,
    )
    factors = {
        "daily_level": {"points": weights["daily_level"] if touched_by_tf["daily"] else 0, "available": daily_available, "confirmed": bool(touched_by_tf["daily"])},
        "four_hour_level": {"points": weights["four_hour_level"] if touched_by_tf["four_hour"] else 0, "available": four_hour_available, "confirmed": bool(touched_by_tf["four_hour"])},
        "multiple_touches": {
            "points": weights["multiple_touches"] if any((item.get("touches") or 0) > 1 for item in touch_candidates) else 0,
            "available": daily_available and four_hour_available,
            "confirmed": any((item.get("touches") or 0) > 1 for item in touch_candidates),
            "evidence_basis": "count of clustered swing pivots, not candle-by-candle retests",
        },
        "strong_rejection": {"points": weights["strong_rejection"] if strong_rejection["confirmed"] else 0, **strong_rejection},
        "prior_liquidity_sweep": {
            "points": weights["prior_liquidity_sweep"] if sweep.get("confirmed") else 0,
            "available": bool(sweep.get("available", False)),
            "confirmed": bool(sweep.get("confirmed", False)),
        },
        "major_swing": {"points": weights["major_swing"] if major_swing["confirmed"] else 0, **major_swing},
        # Future optional factor; keep excluded until a genuine volume source and
        # an approved scoring rule are available: "volume_confirmation": {...}
        "ema_sma_confluence": {"points": weights["ema_sma_confluence"] if ma_confluence["confirmed"] else 0, **ma_confluence},
        "fresh_zone": {"points": weights["fresh_zone"] if fresh_zone["confirmed"] else 0, **fresh_zone},
    }
    earned = sum(item["points"] for item in factors.values() if item["available"])
    assessed_weight = sum(weights[key] for key, item in factors.items() if item["available"])
    total_weight = sum(weights.values())
    rating_available = assessed_weight == total_weight
    rating = None
    if rating_available:
        rating = "very_strong" if earned >= 8 else "strong" if earned >= 6 else "unrated"
    return {
        "direction": direction,
        "factors": factors,
        "touched_levels": touched_by_tf,
        "scoring_basis": "candidate_htf_factor_points",
        "observed_points": earned,
        "assessed_weight": assessed_weight,
        "maximum_weight": total_weight,
        "coverage_pct": round(assessed_weight / total_weight * 100, 1),
        "rating": rating,
        "rating_available": rating_available,
        "analysis_only": True,
    }


def _area_interaction(candle: pd.Series, structure: dict, level_key: str, zone_key: str) -> tuple[bool, bool]:
    """Return (area touched, directional rejection close) for a relevant HTF area."""
    low, high, close = (float(candle[k]) for k in ("low", "high", "close"))
    touched = rejected = False
    for level in structure.get(level_key, []):
        price = float(level["price"])
        if low <= price <= high:
            touched = True
            rejected |= close > price if level_key == "support" else close < price
    for zone in structure.get(zone_key, []):
        zone_low, zone_high = float(zone["low"]), float(zone["high"])
        if low <= zone_high and high >= zone_low:
            touched = True
            rejected |= close > zone_high if zone_key == "demand" else close < zone_low
    return touched, rejected


def _completed_bars(frame: pd.DataFrame, duration: pd.Timedelta) -> pd.DataFrame:
    if frame.empty or "timestamp" not in frame:
        return frame.iloc[0:0].copy()
    if frame.attrs.get("all_candles_closed"):
        return frame
    timestamps = pd.to_datetime(frame["timestamp"], utc=True)
    return frame.loc[timestamps + duration <= pd.Timestamp.now(tz="UTC")].reset_index(drop=True)


def _has_strong_htf_rejection(direction, candle, daily_structure, four_hour_structure) -> dict:
    long_side = direction == "bullish"
    level_key, zone_key = ("support", "demand") if long_side else ("resistance", "supply")
    touched = rejected = False
    for structure in (daily_structure, four_hour_structure):
        area_touched, area_rejected = _area_interaction(candle, structure, level_key, zone_key)
        touched |= area_touched
        rejected |= area_rejected
    o, h, l, c = (float(candle[k]) for k in ("open", "high", "low", "close"))
    span = h - l
    body = abs(c - o)
    wick = min(o, c) - l if long_side else h - max(o, c)
    close_position = (c - l) / span if span > 0 else None
    candle_directional = c > o if long_side else c < o
    confirmed = bool(
        span > 0 and body > 0 and touched and rejected and candle_directional
        and wick >= 1.5 * body
        and (close_position >= 0.65 if long_side else close_position <= 0.35)
    )
    return {
        "available": all(isinstance(s.get(level_key), list) and isinstance(s.get(zone_key), list) for s in (daily_structure, four_hour_structure)),
        "confirmed": confirmed,
        "reason": "1H wick/body, close-location, and HTF-area rejection candidate rule",
        "evidence": {
            "area_touched": touched,
            "close_rejected_area": rejected,
            "directional_candle": candle_directional,
            "wick_to_body": round(wick / body, 3) if body else None,
            "close_position": round(close_position, 3) if close_position is not None else None,
        },
    }


def _interacts_with_major_swing(direction, candle, daily_df, four_hour_df, daily_structure, four_hour_structure) -> dict:
    if daily_df is None or four_hour_df is None:
        return {"available": False, "confirmed": False, "reason": "Daily and 4H OHLC with ATR(14) are required."}
    long_side = direction == "bullish"
    candidates = []
    evaluable_swing_count = 0
    for timeframe, source_frame, structure, duration in (("1d", daily_df, daily_structure, pd.Timedelta(days=1)), ("4h", four_hour_df, four_hour_structure, pd.Timedelta(hours=4))):
        frame = _completed_bars(source_frame, duration)
        if frame.empty or not {"high", "low", "close", "atr14"}.issubset(frame.columns) or not isinstance(structure.get("support" if long_side else "resistance"), list):
            return {"available": False, "confirmed": False, "reason": f"{timeframe} pivot or ATR evidence is unavailable."}
        highs, lows = _find_swing_records(frame, lookback=5)
        wanted, opposite = (lows, highs) if long_side else (highs, lows)
        for pivot in wanted:
            atr = pd.to_numeric(pd.Series([frame.iloc[pivot["index"]]["atr14"]]), errors="coerce").iloc[0]
            later = [other for other in opposite if other["index"] > pivot["index"]]
            if not later or pd.isna(atr) or float(atr) <= 0:
                continue
            evaluable_swing_count += 1
            next_pivot = min(later, key=lambda item: item["index"])
            excursion = pivot["price"] - next_pivot["price"] if not long_side else next_pivot["price"] - pivot["price"]
            candidates.append({"timeframe": timeframe, "price": pivot["price"], "atr14": float(atr), "excursion_atr": round(excursion / float(atr), 3)})
    if evaluable_swing_count == 0:
        return {"available": False, "confirmed": False, "reason": "No confirmed directional pivot pair has usable ATR(14) evidence."}
    low, high = float(candle["low"]), float(candle["high"])
    level_key, zone_key = ("support", "demand") if long_side else ("resistance", "supply")
    interacted = []
    for item in candidates:
        price = item["price"]
        if low <= price <= high:
            interacted.append(item)
            continue
        structure = daily_structure if item["timeframe"] == "1d" else four_hour_structure
        associated_level_hit = any(
            low <= float(level["price"]) <= high
            and (abs(float(level["price"]) - price) / abs(price) <= 0.005 if price else float(level["price"]) == price)
            for level in structure.get(level_key, [])
        )
        if associated_level_hit:
            interacted.append(item)
            continue
        if any(low <= float(z["high"]) and high >= float(z["low"]) and float(z["low"]) <= price <= float(z["high"]) for z in structure.get(zone_key, [])):
            interacted.append(item)
    qualified = [item for item in interacted if item["excursion_atr"] >= 2.0]
    return {
        "available": evaluable_swing_count > 0,
        "confirmed": bool(qualified),
        "evidence": qualified,
        "candidate_interactions": interacted,
        "threshold_atr": 2.0,
        "reason": "confirmed pivot followed by >=2 ATR opposite swing and current 1H interaction",
    }


def _has_htf_ma_confluence(direction, candle, daily_df, four_hour_df, daily_structure, four_hour_structure) -> dict:
    if daily_df is None or four_hour_df is None:
        return {"available": False, "confirmed": False, "reason": "Daily/4H EMA, SMA, and ATR data are required."}
    long_side = direction == "bullish"
    level_key, zone_key = ("support", "demand") if long_side else ("resistance", "supply")
    evidence = []
    proximity_evidence = []
    assessed = 0
    for timeframe, source_frame, structure, duration in (("1d", daily_df, daily_structure, pd.Timedelta(days=1)), ("4h", four_hour_df, four_hour_structure, pd.Timedelta(hours=4))):
        frame = _completed_bars(source_frame, duration)
        if frame.empty or not all(key in frame for key in ("ema9", "ema21", "sma50", "atr14")):
            return {"available": False, "confirmed": False, "reason": f"{timeframe} EMA/SMA/ATR data are unavailable."}
        if not isinstance(structure.get(level_key), list) or not isinstance(structure.get(zone_key), list):
            return {"available": False, "confirmed": False, "reason": f"{timeframe} level data are unavailable."}
        row = frame.iloc[-1]
        atr = row["atr14"]
        if pd.isna(atr) or float(atr) <= 0:
            continue
        averages = {average: row[average] for average in ("ema9", "ema21", "sma50") if pd.notna(row[average])}
        if not averages:
            continue
        areas = [(float(item["price"]), float(item["price"])) for item in structure[level_key]]
        areas += [(float(item["low"]), float(item["high"])) for item in structure[zone_key]]
        candle_low, candle_high = float(candle["low"]), float(candle["high"])
        interacted_areas = [
            area for area in areas
            if candle_low <= area[1] and candle_high >= area[0]
        ]
        assessed += 1
        for average, value in averages.items():
            if not interacted_areas:
                continue
            distance = min(max(area_low - float(value), 0.0, float(value) - area_high) for area_low, area_high in interacted_areas)
            candidate = {"timeframe": timeframe, "average": average, "value": float(value), "distance_atr": round(distance / float(atr), 4)}
            proximity_evidence.append(candidate)
            if distance <= 0.25 * float(atr):
                evidence.append(candidate)
    return {
        "available": assessed == 2,
        "confirmed": bool(evidence),
        "evidence": evidence,
        "proximity_evidence": proximity_evidence,
        "threshold_atr": 0.25,
        "reason": "same-timeframe Daily/4H average proximity to an HTF area interacted with by the latest closed 1H candle",
    }


def _has_fresh_zone_retest(direction, candle, daily_df, four_hour_df, hourly_df, daily_structure, four_hour_structure) -> dict:
    if daily_df is None or four_hour_df is None or hourly_df is None:
        return {"available": False, "confirmed": False, "reason": "Daily, 4H, and hourly OHLC history are required for zone lifecycle."}
    if any(frame.empty or "timestamp" not in frame for frame in (daily_df, four_hour_df, hourly_df)):
        return {"available": False, "confirmed": False, "reason": "Required OHLC history is empty or lacks timestamps."}
    long_side = direction == "bullish"
    zone_key = "demand" if long_side else "supply"
    current_low, current_high = float(candle["low"]), float(candle["high"])
    before = hourly_df[pd.to_datetime(hourly_df["timestamp"], utc=True) < pd.to_datetime(candle["timestamp"], utc=True)]
    fresh = []
    lifecycle_history_complete = True
    for timeframe, source_frame, structure, duration in (("1d", daily_df, daily_structure, pd.Timedelta(days=1)), ("4h", four_hour_df, four_hour_structure, pd.Timedelta(hours=4))):
        frame = _completed_bars(source_frame, duration)
        if frame.empty or not {"high", "low", "close"}.issubset(frame.columns):
            return {"available": False, "confirmed": False, "reason": f"{timeframe} OHLC pivot evidence is unavailable."}
        zones = structure.get(zone_key)
        if not isinstance(zones, list):
            return {"available": False, "confirmed": False, "reason": f"{timeframe} zone data is unavailable."}
        highs, lows = _find_swing_records(frame, lookback=5)
        pivots = lows if long_side else highs
        timestamps = pd.to_datetime(frame["timestamp"], utc=True).reset_index(drop=True)
        for zone in zones:
            zl, zh = float(zone["low"]), float(zone["high"])
            if current_low > zh or current_high < zl:
                continue
            origins = [p for p in pivots if zl <= p["price"] <= zh and p["index"] + 5 < len(frame)]
            if not origins:
                lifecycle_history_complete = False
                continue
            # A merged swing zone is treated as newly defined by its latest
            # confirmed contributing pivot; older sub-pivots do not reset it.
            pivot = max(origins, key=lambda item: item["index"])
            active_after = timestamps.iloc[pivot["index"] + 5] + duration
            if pd.to_datetime(hourly_df["timestamp"], utc=True).min() > active_after:
                lifecycle_history_complete = False
                continue
            lifecycle = before[pd.to_datetime(before["timestamp"], utc=True) >= active_after]
            departed = False
            mitigated = False
            for _, prior in lifecycle.iterrows():
                pl, ph = float(prior["low"]), float(prior["high"])
                if not departed:
                    departed = ph < zl or pl > zh
                elif pl <= zh and ph >= zl:
                    mitigated = True
                    break
            if departed and not mitigated:
                origin = timestamps.iloc[pivot["index"]].isoformat()
                zone_id = f"{timeframe}:{origin}:{zl:.12g}:{zh:.12g}"
                fresh.append({
                    "zone_id": zone_id,
                    "timeframe": timeframe,
                    "low": zl,
                    "high": zh,
                    "pivot_price": pivot["price"],
                    "origin_timestamp": origin,
                })
    if not lifecycle_history_complete:
        return {"available": False, "confirmed": False, "evidence": fresh, "reason": "Hourly history does not reach the zone's confirmed origin, so prior mitigation cannot be ruled out."}
    return {"available": True, "confirmed": bool(fresh), "evidence": fresh, "reason": "zone activated after confirmed pivot; no earlier post-departure retest"}


def evaluate_confidence(
    setup: str,
    daily: dict,
    four_hour: dict,
    daily_structure: dict,
    four_hour_structure: dict,
    entry_confirmation: dict,
    level_quality: dict,
) -> dict:
    """Score assessable candidate points; suppress the confidence band if incomplete."""
    if setup not in ("potential_long", "potential_short"):
        return {
            "status": "not_applicable_no_qualifying_setup",
            "direction": None,
            "observed_points": 0,
            "assessed_points": 0,
            "total_points": sum(CONFIDENCE_WEIGHTS.values()),
            "coverage_pct": 0.0,
            "missing_inputs": [],
            "score": None,
            "score_out_of": 0,
            "score_fraction": None,
            "band": None,
            "calibrated_probability": False,
            "execution_authorized": False,
        }
    daily_trend = daily["trend"]["trend"]
    four_hour_trend = four_hour["trend"]["trend"]
    direction = daily_trend if daily_trend == four_hour_trend and daily_trend in ("bullish", "bearish") else None
    if direction is None:
        return {
            "status": "not_applicable_no_aligned_direction",
            "direction": None,
            "observed_points": 0,
            "assessed_points": 0,
            "total_points": sum(CONFIDENCE_WEIGHTS.values()),
            "coverage_pct": 0.0,
            "missing_inputs": ["aligned_daily_four_hour_direction"],
            "score": None,
            "score_out_of": 0,
            "score_fraction": None,
            "band": None,
            "calibrated_probability": False,
            "execution_authorized": False,
        }

    components: dict[str, dict] = {}

    def add_component(name: str, *, points: int, assessed: int, confirmed: bool | None, missing: list[str] | None = None, evidence: dict | None = None) -> None:
        weight = CONFIDENCE_WEIGHTS[name]
        components[name] = {
            "weight": weight,
            "points": points if assessed > 0 else 0,
            "assessed_points": assessed,
            "available": assessed == weight,
            "confirmed": confirmed,
            "missing_inputs": missing or [],
            "evidence": evidence or {},
        }

    for name, snapshot, trend in (
        ("daily_trend", daily, daily_trend),
        ("four_hour_trend", four_hour, four_hour_trend),
    ):
        available = trend != "unknown"
        add_component(
            name,
            points=CONFIDENCE_WEIGHTS[name] if available and trend == direction else 0,
            assessed=CONFIDENCE_WEIGHTS[name] if available else 0,
            confirmed=trend == direction if available else None,
            missing=[] if available else [f"{name}.trend"],
            evidence={"trend": trend},
        )

    level_factors = level_quality.get("factors", {})
    level_weight = CONFIDENCE_WEIGHTS["htf_level_quality"]
    if level_factors:
        level_assessed = sum(
            HTF_LEVEL_QUALITY_WEIGHTS.get(key, 0)
            for key, value in level_factors.items()
            if value.get("available", False)
        )
        level_points = sum(
            int(value.get("points", 0))
            for value in level_factors.values()
            if value.get("available", False)
        )
    else:
        level_assessed = int(level_quality.get("assessed_weight", 0))
        level_points = int(level_quality.get("observed_points", 0)) if level_assessed > 0 else 0
    level_missing = [f"htf_level_quality.{key}" for key, value in level_factors.items() if not value.get("available", False)]
    add_component(
        "htf_level_quality",
        points=level_points,
        assessed=min(level_assessed, level_weight),
        confirmed=bool(level_points) if level_assessed == level_weight else None,
        missing=level_missing or ([] if level_assessed == level_weight else ["htf_level_quality.factors"]),
        evidence={"rating": level_quality.get("rating"), "coverage_pct": level_quality.get("coverage_pct")},
    )

    confirmations = entry_confirmation.get("confirmations", {})
    sweep = confirmations.get("liquidity_sweep", {})
    sweep_weight = CONFIDENCE_WEIGHTS["liquidity_sweep"]
    add_component(
        "liquidity_sweep",
        points=sweep_weight if sweep.get("available") and sweep.get("confirmed") else 0,
        assessed=sweep_weight if sweep.get("available") else 0,
        confirmed=sweep.get("confirmed") if sweep.get("available") else None,
        missing=[] if sweep.get("available") else ["4h_directional_support_or_resistance_level"],
    )

    structure_break = confirmations.get("structure_break", {})
    structure_weight = CONFIDENCE_WEIGHTS["one_hour_structure"]
    add_component(
        "one_hour_structure",
        points=structure_weight if structure_break.get("available") and structure_break.get("confirmed") else 0,
        assessed=structure_weight if structure_break.get("available") else 0,
        confirmed=structure_break.get("confirmed") if structure_break.get("available") else None,
        missing=[] if structure_break.get("available") else ["confirmed_1h_swing_pivot"],
    )

    ma_weight = CONFIDENCE_WEIGHTS["ema_sma_confluence"]
    ma_available = all(snapshot.get(key) is not None for snapshot in (daily, four_hour) for key in ("ema9", "ema21", "sma50"))
    ma_confirmed = ma_available and all(
        snapshot["trend"]["trend"] == direction and snapshot["trend"]["ema_alignment"]
        for snapshot in (daily, four_hour)
    )
    add_component(
        "ema_sma_confluence",
        points=ma_weight if ma_confirmed else 0,
        assessed=ma_weight if ma_available else 0,
        confirmed=ma_confirmed if ma_available else None,
        missing=[] if ma_available else ["daily_or_4h_ema9_ema21_sma50"],
    )

    # Future optional input: volume/OI remains outside the active confidence
    # budget until real data sources and an approved scoring rule exist.

    rsi_weight = RSI_MACD_CONFIRMATION_WEIGHTS["rsi"]
    macd_weight = RSI_MACD_CONFIRMATION_WEIGHTS["macd"]
    rsi_values = [snapshot.get("momentum", {}).get("rsi") for snapshot in (daily, four_hour)]
    rsi_available = all(value is not None and math.isfinite(float(value)) for value in rsi_values)
    rsi_confirmed = rsi_available and all(
        float(value) >= 50 if direction == "bullish" else float(value) < 50
        for value in rsi_values
    )
    macd_available = all(
        snapshot.get(key) is not None and math.isfinite(float(snapshot[key]))
        for snapshot in (daily, four_hour)
        for key in ("macd", "macd_signal")
    ) and four_hour.get("macd_histogram") is not None and four_hour.get("macd_histogram_previous") is not None
    if macd_available:
        macd_aligned = all(
            float(snapshot["macd"]) > float(snapshot["macd_signal"])
            if direction == "bullish" else float(snapshot["macd"]) < float(snapshot["macd_signal"])
            for snapshot in (daily, four_hour)
        )
        histogram_now = float(four_hour["macd_histogram"])
        histogram_before = float(four_hour["macd_histogram_previous"])
        histogram_confirms = (
            histogram_now > 0 and histogram_now > histogram_before
            if direction == "bullish" else
            histogram_now < 0 and histogram_now < histogram_before
        )
        macd_confirmed = macd_aligned and histogram_confirms
    else:
        macd_confirmed = False
    rsi_macd_missing = []
    if not rsi_available:
        rsi_macd_missing.append("rsi_daily_or_4h")
    if not macd_available:
        rsi_macd_missing.append("macd_indicator")
    add_component(
        "rsi_macd_confirmation",
        points=(rsi_weight if rsi_confirmed else 0) + (macd_weight if macd_confirmed else 0),
        assessed=(rsi_weight if rsi_available else 0) + (macd_weight if macd_available else 0),
        confirmed=(rsi_confirmed and macd_confirmed) if rsi_available and macd_available else None,
        missing=rsi_macd_missing,
        evidence={
            "rsi_weight": rsi_weight, "rsi_points": rsi_weight if rsi_confirmed else 0,
            "rsi_available": rsi_available, "rsi_confirmed": rsi_confirmed if rsi_available else None,
            "rsi_daily": rsi_values[0], "rsi_4h": rsi_values[1],
            "macd_weight": macd_weight, "macd_points": macd_weight if macd_confirmed else 0,
            "macd_available": macd_available, "macd_confirmed": macd_confirmed if macd_available else None,
            "macd_line_signal_daily": "bullish" if daily.get("macd") is not None and daily.get("macd_signal") is not None and daily["macd"] > daily["macd_signal"] else "bearish" if daily.get("macd") is not None and daily.get("macd_signal") is not None else None,
            "macd_line_signal_4h": "bullish" if four_hour.get("macd") is not None and four_hour.get("macd_signal") is not None and four_hour["macd"] > four_hour["macd_signal"] else "bearish" if four_hour.get("macd") is not None and four_hour.get("macd_signal") is not None else None,
            "four_hour_histogram": four_hour.get("macd_histogram"),
            "four_hour_histogram_previous": four_hour.get("macd_histogram_previous"),
        },
    )

    regime_weight = CONFIDENCE_WEIGHTS["market_regime"]
    structures_available = daily_structure.get("structure") != "unknown" and four_hour_structure.get("structure") != "unknown"
    regime_confirmed = structures_available and daily_structure.get("structure") == direction and four_hour_structure.get("structure") == direction
    add_component(
        "market_regime",
        points=regime_weight if regime_confirmed else 0,
        assessed=regime_weight if structures_available else 0,
        confirmed=regime_confirmed if structures_available else None,
        missing=[] if structures_available else ["daily_or_4h_swing_structure"],
        evidence={"daily_structure": daily_structure.get("structure"), "four_hour_structure": four_hour_structure.get("structure")},
    )

    total_points = sum(CONFIDENCE_WEIGHTS.values())
    observed_points = sum(item["points"] for item in components.values())
    assessed_points = sum(item["assessed_points"] for item in components.values())
    missing_inputs = sorted({value for item in components.values() for value in item["missing_inputs"]})
    complete = assessed_points == total_points
    score = observed_points if assessed_points else None
    score_fraction = round(observed_points / assessed_points, 4) if assessed_points else None
    band = None
    score_pct = round(score_fraction * 100, 2) if score_fraction is not None else None
    if complete and score_pct is not None:
        band = "no_trade" if score_pct < 55 else "watch" if score_pct < 65 else "small" if score_pct < 75 else "normal" if score_pct < 85 else "maximum_permitted"
    return {
        "status": "complete_candidate_score" if complete else "partial_candidate_score_missing_inputs",
        "direction": direction,
        "components": components,
        "observed_points": observed_points,
        "assessed_points": assessed_points,
        "total_points": total_points,
        "coverage_pct": round(assessed_points / total_points * 100, 1),
        "missing_inputs": missing_inputs,
        "score": score,
        "score_out_of": assessed_points,
        "score_fraction": score_fraction,
        "score_pct": score_pct,
        "score_basis": "observed_points / assessed_points; interpret score_fraction together with coverage_pct",
        "band": band,
        "calibrated_probability": False,
        "execution_authorized": False,
    }


def evaluate_entry_confirmations(
    hourly_df: pd.DataFrame,
    four_hour: dict,
    direction: str,
    swing_point_state: dict | None = None,
) -> dict:
    """Evaluate the four strategy-defined 1H confirmations from actual OHLC."""
    if hourly_df.empty:
        return {"confirmations": {}, "confirmed_count": 0, "required_count": 2, "available": False}
    frame = _closed_hourly_candles(hourly_df)
    if frame.empty:
        return {"confirmations": {}, "confirmed_count": 0, "required_count": 2, "available": False}
    latest = frame.iloc[-1]
    previous = frame.iloc[-2] if len(frame) > 1 else None
    long_side = direction == "bullish"

    level_type = "support" if long_side else "resistance"
    sweep_levels = four_hour.get(level_type, [])

    def directional_sweep() -> bool:
        for level in sweep_levels:
            price = float(level["price"])
            if long_side and latest["low"] < price and latest["close"] > price:
                return True
            if not long_side and latest["high"] > price and latest["close"] < price:
                return True
        return False

    if swing_point_state is None:
        swing_highs, swing_lows = find_swing_points(frame, lookback=2)
        latest_swing_high = swing_highs[-1] if swing_highs else None
        latest_swing_low = swing_lows[-1] if swing_lows else None
    else:
        latest_swing_high = swing_point_state.get("latest_high")
        latest_swing_low = swing_point_state.get("latest_low")
    structure_break = False
    if long_side and latest_swing_high is not None:
        structure_break = float(latest["close"]) > latest_swing_high
    elif not long_side and latest_swing_low is not None:
        structure_break = float(latest["close"]) < latest_swing_low

    body = abs(float(latest["close"]) - float(latest["open"]))
    lower_wick = min(float(latest["open"]), float(latest["close"])) - float(latest["low"])
    upper_wick = float(latest["high"]) - max(float(latest["open"]), float(latest["close"]))
    rejection = (
        float(latest["close"]) > float(latest["open"]) and lower_wick > body
        if long_side else
        float(latest["close"]) < float(latest["open"]) and upper_wick > body
    )

    ema9, ema21 = latest.get("ema9"), latest.get("ema21")
    ema_reclaim = False
    if pd.notna(ema9) and pd.notna(ema21):
        aligned = float(latest["close"]) > float(ema9) > float(ema21) if long_side else float(latest["close"]) < float(ema9) < float(ema21)
        crossed = False
        if previous is not None and pd.notna(previous.get("ema9")):
            crossed = (
                float(previous["close"]) <= float(previous["ema9"]) < float(latest["close"])
                if long_side else
                float(previous["close"]) >= float(previous["ema9"]) > float(latest["close"])
            )
        ema_reclaim = aligned or crossed

    structure_available = (
        latest_swing_high is not None if long_side else latest_swing_low is not None
    )
    confirmations = {
        "liquidity_sweep": {
            "confirmed": directional_sweep(),
            "available": isinstance(four_hour.get(level_type), list),
        },
        "structure_break": {"confirmed": structure_break, "available": structure_available},
        "rejection_candle": {"confirmed": bool(rejection), "available": True, "definition": "directional candle with rejection wick longer than its body"},
        "ema_reclaim_or_structure": {"confirmed": ema_reclaim, "available": pd.notna(ema9) and pd.notna(ema21)},
    }
    count = sum(item["confirmed"] for item in confirmations.values())
    return {
        "confirmations": confirmations,
        "confirmed_count": count,
        "required_count": 2,
        "available": True,
        "evaluated_timestamp": str(latest["timestamp"]),
    }


def _json_safe(value):
    """Convert pandas/numpy scalars and non-finite floats to strict JSON values."""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def evaluate_market(
    daily_df: pd.DataFrame,
    four_hour_df: pd.DataFrame,
    one_hour_df: pd.DataFrame,
    daily_structure: dict,
    four_hour_structure: dict,
    symbol: str = "unknown",
    market_data_metadata: dict | None = None,
    swing_point_state: dict | None = None,
) -> dict:

    closed_daily = _completed_bars(daily_df, pd.Timedelta(days=1))
    closed_four_hour = _completed_bars(four_hour_df, pd.Timedelta(hours=4))
    closed_one_hour = _closed_hourly_candles(one_hour_df)
    daily = evaluate_timeframe(closed_daily if not closed_daily.empty else daily_df)
    four_hour = evaluate_timeframe(closed_four_hour if not closed_four_hour.empty else four_hour_df)
    one_hour = evaluate_timeframe(closed_one_hour if not closed_one_hour.empty else one_hour_df)

    context = determine_htf_context(
        daily,
        four_hour,
        one_hour,
    )

    direction = context["daily_context"]
    htf_structures_aligned = (
        (direction == "bullish" and daily_structure.get("structure") == "bullish" and four_hour_structure.get("structure") == "bullish")
        or (direction == "bearish" and daily_structure.get("structure") == "bearish" and four_hour_structure.get("structure") == "bearish")
    )
    closed_hourly = closed_one_hour
    location = (
        _directional_location(direction, closed_hourly.iloc[-1], four_hour, daily_structure, four_hour_structure)
        if not closed_hourly.empty
        else {"confirmed": False, "interaction_confirmed": False, "evidence": [], "available": False,
              "proximity_by_side": {"bullish": [], "bearish": []},
              "confirmation_rule": "completed 1H candle required"}
    )
    entry = evaluate_entry_confirmations(one_hour_df, four_hour_structure, direction, swing_point_state=swing_point_state) if direction in ("bullish", "bearish") else {"confirmations": {}, "confirmed_count": 0, "required_count": 2, "available": False}
    level_quality = evaluate_htf_level_quality(
        direction, closed_hourly.iloc[-1], daily_structure, four_hour_structure, entry,
        daily_df=daily_df, four_hour_df=four_hour_df, hourly_df=one_hour_df,
    ) if direction in ("bullish", "bearish") and not closed_hourly.empty else {"direction": direction, "factors": {}, "observed_points": 0, "assessed_weight": 0, "maximum_weight": sum(HTF_LEVEL_QUALITY_WEIGHTS.values()), "coverage_pct": 0.0, "rating": None, "rating_available": False, "analysis_only": True}
    setup = "no_setup"
    if context["context_aligned"] and htf_structures_aligned and location["confirmed"] and entry["confirmed_count"] >= entry["required_count"]:
        setup = "potential_long" if direction == "bullish" else "potential_short"

    def rule_flag(confirmed: bool, available: bool = True) -> str:
        return "SATISFIED" if confirmed else "NOT_SATISFIED" if available else "UNAVAILABLE"

    trend_alignment = context["context_aligned"]
    confirmation_flags = entry.get("confirmations", {})
    logger.info(
        "Setup rule flags symbol=%s direction=%s daily_directional=%s daily_4h_trend_alignment=%s "
        "daily_4h_structure_alignment=%s htf_location=%s liquidity_sweep=%s structure_break=%s "
        "rejection_candle=%s ema_reclaim_or_structure=%s one_hour_min_2_of_4=%s setup=%s",
        symbol,
        direction,
        rule_flag(direction in ("bullish", "bearish")),
        rule_flag(trend_alignment),
        rule_flag(htf_structures_aligned, direction in ("bullish", "bearish")),
        rule_flag(location.get("confirmed", False), location.get("available", False)),
        rule_flag(confirmation_flags.get("liquidity_sweep", {}).get("confirmed", False), confirmation_flags.get("liquidity_sweep", {}).get("available", False)),
        rule_flag(confirmation_flags.get("structure_break", {}).get("confirmed", False), confirmation_flags.get("structure_break", {}).get("available", False)),
        rule_flag(confirmation_flags.get("rejection_candle", {}).get("confirmed", False), confirmation_flags.get("rejection_candle", {}).get("available", False)),
        rule_flag(confirmation_flags.get("ema_reclaim_or_structure", {}).get("confirmed", False), confirmation_flags.get("ema_reclaim_or_structure", {}).get("available", False)),
        rule_flag(entry["confirmed_count"] >= entry["required_count"], entry.get("available", False)),
        setup,
    )
    confidence = evaluate_confidence(setup, daily, four_hour, daily_structure, four_hour_structure, entry, level_quality)
    context.update({
        "structure_aligned": htf_structures_aligned,
        "trend_alignment_detail": {
            "daily_trend": daily["trend"]["trend"],
            "four_hour_trend": four_hour["trend"]["trend"],
            "aligned": context["context_aligned"],
            "available": daily["trend"]["trend"] in ("bullish", "bearish") and four_hour["trend"]["trend"] in ("bullish", "bearish"),
        },
        "structure_alignment_detail": {
            "daily_structure": daily_structure.get("structure", "unknown"),
            "four_hour_structure": four_hour_structure.get("structure", "unknown"),
            "aligned": htf_structures_aligned,
            "available": direction in ("bullish", "bearish"),
        },
        "location": location,
        "entry_confirmation": entry,
        "level_quality": level_quality,
        "confidence": confidence,
        "setup": setup,
        "timing_confirmed": location["confirmed"] and entry["confirmed_count"] >= entry["required_count"],
    })

    result = _json_safe({
        "schema_version": 4,
        "symbol": symbol,
        "timeframes": ["1d", "4h", "1h"],
        "market_data": market_data_metadata or {},
        "timestamp": one_hour["timestamp"],
        "price": one_hour["price"],

        "daily": daily,
        "four_hour": four_hour,
        "one_hour": one_hour,

        "daily_structure": daily_structure,
        "four_hour_structure": four_hour_structure,

        "context": context,
        "htf_location": location,
        "entry_confirmation": entry,
        "level_quality": level_quality,
        "confidence": confidence,
        "setup": setup,
        "analysis_only": True,
    })
    logger.info("Deterministic rules evaluated symbol=%s setup=%s", symbol, result["setup"])
    return result


def market_snapshot_json(analysis: dict, *, indent: int | None = 2) -> str:
    """Serialize a market analysis snapshot as standards-compliant JSON."""
    return json.dumps(_json_safe(analysis), indent=indent, allow_nan=False)
