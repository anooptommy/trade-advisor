"""Deterministic market structure derived from genuine OHLC candles."""

from __future__ import annotations

import math
import logging

import pandas as pd

logger = logging.getLogger(__name__)


def _validate_ohlc(df: pd.DataFrame) -> None:
    missing = {"high", "low", "close"}.difference(df.columns)
    if missing:
        raise ValueError(f"Market structure requires OHLC columns: {sorted(missing)}")


def _find_swing_records(df: pd.DataFrame, lookback: int = 5) -> tuple[list[dict], list[dict]]:
    if lookback < 1:
        raise ValueError("lookback must be at least 1")
    _validate_ohlc(df)
    if len(df) < lookback * 2 + 1:
        return [], []

    highs: list[dict] = []
    lows: list[dict] = []
    high_values = pd.to_numeric(df["high"], errors="coerce").to_numpy()
    low_values = pd.to_numeric(df["low"], errors="coerce").to_numpy()
    for i in range(lookback, len(df) - lookback):
        h, lo = high_values[i], low_values[i]
        if not math.isfinite(h) or not math.isfinite(lo):
            continue
        left_h, right_h = high_values[i-lookback:i], high_values[i+1:i+lookback+1]
        left_l, right_l = low_values[i-lookback:i], low_values[i+1:i+lookback+1]
        if all(math.isfinite(v) for v in (*left_h, *right_h)) and h >= max(*left_h, *right_h):
            highs.append({"index": i, "price": float(h)})
        if all(math.isfinite(v) for v in (*left_l, *right_l)) and lo <= min(*left_l, *right_l):
            lows.append({"index": i, "price": float(lo)})
    return highs, lows


def find_swing_points(df: pd.DataFrame, lookback: int = 5) -> tuple[list[float], list[float]]:
    """Return chronologically ordered swing high and low prices."""
    highs, lows = _find_swing_records(df, lookback)
    return [p["price"] for p in highs], [p["price"] for p in lows]


def _cluster_records(records: list[dict], tolerance_pct: float, max_levels: int) -> list[dict]:
    if tolerance_pct < 0 or max_levels < 0:
        raise ValueError("tolerance_pct and max_levels must be non-negative")
    if not records or max_levels == 0:
        return []
    ordered = sorted(records, key=lambda item: item["price"])
    clusters: list[list[dict]] = []
    for record in ordered:
        if not clusters:
            clusters.append([record])
            continue
        cluster = clusters[-1]
        center = sum(x["price"] for x in cluster) / len(cluster)
        denominator = abs(center)
        distance = abs(record["price"] - center) / denominator if denominator else (0 if record["price"] == center else math.inf)
        if distance <= tolerance_pct:
            cluster.append(record)
        else:
            clusters.append([record])
    # Keep strongest clusters, then return them in price order.
    clusters = sorted(clusters, key=lambda group: (-len(group), group[0]["price"]))[:max_levels]
    result = [{"price": sum(x["price"] for x in group) / len(group), "touches": len(group)} for group in clusters]
    return sorted(result, key=lambda item: item["price"])


def cluster_levels(levels: list[float], tolerance_pct: float = 0.005, max_levels: int = 5) -> list[float]:
    """Cluster nearby prices; cluster strength is the number of swing touches."""
    groups = _cluster_records([{"price": float(x)} for x in levels if math.isfinite(float(x))], tolerance_pct, max_levels)
    return [round(group["price"], 2) for group in groups]


def find_htf_levels(df: pd.DataFrame, lookback: int = 5, tolerance_pct: float = 0.005) -> dict:
    """Find nearest clustered support and resistance with touch counts."""
    if df.empty:
        return {"support": [], "resistance": []}
    _validate_ohlc(df)
    highs, lows = _find_swing_records(df, lookback)
    close = float(df.iloc[-1]["close"])
    resistance = [x for x in _cluster_records(highs, tolerance_pct, 100) if x["price"] > close]
    support = [x for x in _cluster_records(lows, tolerance_pct, 100) if x["price"] < close]
    support.sort(key=lambda x: close - x["price"])
    resistance.sort(key=lambda x: x["price"] - close)
    def present(items):
        return [{"price": round(x["price"], 2), "touches": x["touches"]} for x in items[:3]]
    return {"support": present(support), "resistance": present(resistance)}


def _merge_zones(zones: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for zone in sorted(zones, key=lambda z: z["low"]):
        if merged and zone["low"] <= merged[-1]["high"]:
            prior = merged[-1]
            prior["low"] = min(prior["low"], zone["low"])
            prior["high"] = max(prior["high"], zone["high"])
            prior["touches"] += zone["touches"]
            prior["mid"] = (prior["low"] + prior["high"]) / 2
        else:
            merged.append(dict(zone))
    return merged


def find_supply_demand_zones(df: pd.DataFrame, lookback: int = 5, zone_width_pct: float = 0.01, max_zones: int = 3) -> dict:
    """Build swing-centered zones and merge overlapping zones of each type.

    These are simple swing-price zones, not displacement/base-candle zones.
    """
    if zone_width_pct < 0 or max_zones < 0:
        raise ValueError("zone_width_pct and max_zones must be non-negative")
    if df.empty:
        return {"supply": [], "demand": []}
    _validate_ohlc(df)
    highs, lows = _find_swing_records(df, lookback)
    current = float(df.iloc[-1]["close"])
    def make_zone(item):
        price = item["price"]
        return {"low": price * (1-zone_width_pct), "high": price * (1+zone_width_pct), "mid": price, "touches": 1}
    supply = _merge_zones([make_zone(x) for x in highs if x["price"] > current])
    demand = _merge_zones([make_zone(x) for x in lows if x["price"] < current])
    supply.sort(key=lambda z: z["low"] - current)
    demand.sort(key=lambda z: current - z["high"])
    def present(items):
        return [{k: round(float(z[k]), 2) if k != "touches" else z[k] for k in ("low", "high", "mid", "touches")} for z in items[:max_zones]]
    return {"supply": present(supply), "demand": present(demand)}


def classify_swings(swing_highs: list[float], swing_lows: list[float]) -> dict:
    """Classify latest swing pairs without treating one-sided evidence as trend."""
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return {"structure": "unknown", "high_sequence": None, "low_sequence": None}
    high_sequence = "HH" if swing_highs[-1] > swing_highs[-2] else "LH" if swing_highs[-1] < swing_highs[-2] else "EH"
    low_sequence = "HL" if swing_lows[-1] > swing_lows[-2] else "LL" if swing_lows[-1] < swing_lows[-2] else "EL"
    state = "bullish" if high_sequence == "HH" and low_sequence == "HL" else "bearish" if high_sequence == "LH" and low_sequence == "LL" else "mixed"
    return {"structure": state, "high_sequence": high_sequence, "low_sequence": low_sequence}


def determine_market_structure(df: pd.DataFrame) -> str:
    """Return bullish, bearish, mixed, or unknown from chronological pivots."""
    highs, lows = find_swing_points(df, lookback=5)
    return classify_swings(highs, lows)["structure"]


def analyze_market_structure(df: pd.DataFrame, timeframe: str | None = None) -> dict:
    """Generate one-timeframe structure snapshot from actual OHLC data."""
    logger.debug("Market structure analysis started timeframe=%s rows=%d", timeframe, len(df))
    levels = find_htf_levels(df)
    zones = find_supply_demand_zones(df)
    highs, lows = find_swing_points(df)
    classification = classify_swings(highs, lows)
    result = {
        **classification,
        "timeframe": timeframe,
        "swing_highs": highs[-4:],
        "swing_lows": lows[-4:],
        **levels,
        **zones,
    }
    logger.info("Market structure analysis completed timeframe=%s state=%s", timeframe, result["structure"])
    return result
