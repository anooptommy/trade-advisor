"""Analysis-only structural stop, target, and reward/risk candidates."""

from __future__ import annotations

import math
from typing import Any

from risk.eligibility import evaluate_rr_eligibility


# Evaluate the documented buffer range without promoting either endpoint.
CANDIDATE_ATR_BUFFER_SCENARIOS = (0.5, 1.0)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _unavailable(reason: str) -> dict:
    return {
        "status": "unavailable",
        "analysis_only": True,
        "execution_authorized": False,
        "reason": reason,
        "buffer_scenarios": [],
    }


def _collect_stop_anchors(structures: dict[str, dict], long_side: bool) -> list[dict]:
    level_key, zone_key = ("support", "demand") if long_side else ("resistance", "supply")
    edge = "low" if long_side else "high"
    anchors = []
    for timeframe in ("1d", "4h"):
        structure = structures.get(timeframe) or {}
        if not isinstance(structure, dict):
            continue
        for level in structure.get(level_key, []):
            if not isinstance(level, dict):
                continue
            price = _number(level.get("price"))
            if price is not None:
                anchors.append({
                    "timeframe": timeframe,
                    "kind": f"{level_key}_level",
                    "price": price,
                    "touches": level.get("touches"),
                })
        for zone in structure.get(zone_key, []):
            if not isinstance(zone, dict):
                continue
            price = _number(zone.get(edge))
            if price is not None:
                anchors.append({
                    "timeframe": timeframe,
                    "kind": f"{zone_key}_zone_{edge}_edge",
                    "price": price,
                    "touches": zone.get("touches"),
                })
    return anchors


def _collect_targets(structures: dict[str, dict], long_side: bool, entry: float) -> list[dict]:
    level_key, zone_key = ("resistance", "supply") if long_side else ("support", "demand")
    targets = []
    for timeframe in ("1d", "4h"):
        structure = structures.get(timeframe) or {}
        if not isinstance(structure, dict):
            continue
        for level in structure.get(level_key, []):
            if not isinstance(level, dict):
                continue
            price = _number(level.get("price"))
            is_forward = price is not None and (price > entry if long_side else price < entry)
            if is_forward:
                targets.append({
                    "timeframe": timeframe,
                    "kind": f"{level_key}_level",
                    "price": price,
                    "touches": level.get("touches"),
                })
        for zone in structure.get(zone_key, []):
            if not isinstance(zone, dict):
                continue
            low, high = _number(zone.get("low")), _number(zone.get("high"))
            if low is None or high is None or low > high:
                continue
            # Aim for the near edge; if entry is already inside the zone, use
            # the far edge so the target remains beyond the reference entry.
            price = (low if low > entry else high) if long_side else (high if high < entry else low)
            is_forward = price > entry if long_side else price < entry
            if is_forward:
                targets.append({
                    "timeframe": timeframe,
                    "kind": f"{zone_key}_zone_target_edge",
                    "price": price,
                    "touches": zone.get("touches"),
                })
    return targets


def build_candidate_risk_plan(
    snapshot: dict,
    *,
    atr14_1h: float | None = None,
    market_data_stale: bool = False,
) -> dict:
    """Build stop/target/R:R candidates from a deterministic scan snapshot.

    This does not size a position, model fees/slippage, approve risk, or execute.
    The 0.5x and 1.0x ATR buffers are reported as separate unvalidated scenarios.
    """
    setup = snapshot.get("setup")
    if setup not in ("potential_long", "potential_short"):
        return {
            "status": "not_applicable",
            "analysis_only": True,
            "execution_authorized": False,
            "reason": "no_deterministic_candidate_setup",
            "buffer_scenarios": [],
        }
    if market_data_stale:
        return _unavailable("stale_market_data_not_eligible_for_risk_review")

    long_side = setup == "potential_long"
    entry = _number(snapshot.get("price"))
    one_hour = snapshot.get("one_hour", {})
    atr = _number(atr14_1h if atr14_1h is not None else one_hour.get("atr14"))
    candle_time = snapshot.get("timestamp")
    confidence = snapshot.get("confidence", {})
    if entry is None or entry <= 0:
        return _unavailable("positive_closed_hourly_entry_reference_required")
    if atr is None or atr <= 0:
        return _unavailable("positive_1h_atr14_required")

    structures = {
        "1d": snapshot.get("daily_structure", {}),
        "4h": snapshot.get("four_hour_structure", {}),
    }
    anchors = _collect_stop_anchors(structures, long_side)
    valid_anchors = [item for item in anchors if item["price"] < entry] if long_side else [item for item in anchors if item["price"] > entry]
    if not valid_anchors:
        return _unavailable("no_structural_invalidation_level_beyond_entry")
    stop_anchor = max(valid_anchors, key=lambda item: item["price"]) if long_side else min(valid_anchors, key=lambda item: item["price"])

    targets = _collect_targets(structures, long_side, entry)
    if not targets:
        return _unavailable("no_forward_daily_or_4h_target_level_or_zone")
    target = min(targets, key=lambda item: item["price"]) if long_side else max(targets, key=lambda item: item["price"])

    scenarios = []
    for buffer_multiple in CANDIDATE_ATR_BUFFER_SCENARIOS:
        stop = stop_anchor["price"] - buffer_multiple * atr if long_side else stop_anchor["price"] + buffer_multiple * atr
        risk_distance = entry - stop if long_side else stop - entry
        reward_distance = target["price"] - entry if long_side else entry - target["price"]
        if stop <= 0 or risk_distance <= 0 or reward_distance <= 0:
            scenarios.append({
                "atr_buffer_multiple": buffer_multiple,
                "status": "unavailable",
                "reason": "stop_or_target_is_not_on_the_valid_side_of_entry",
            })
            continue
        risk_reward = reward_distance / risk_distance
        scenarios.append({
            "atr_buffer_multiple": buffer_multiple,
            "status": "assessed",
            "stop_price": stop,
            "stop_distance": risk_distance,
            "target_price": target["price"],
            "reward_distance": reward_distance,
            "gross_risk_reward": risk_reward,
            "eligibility": evaluate_rr_eligibility(confidence, risk_reward),
        })

    return {
        "status": "assessed" if any(item["status"] == "assessed" for item in scenarios) else "unavailable",
        "analysis_only": True,
        "execution_authorized": False,
        "setup": setup,
        "entry_reference": {
            "price": entry,
            "basis": "latest_closed_1h_candle_close; not an assumed fill",
            "candle_timestamp": candle_time,
        },
        "atr14_1h": atr,
        "stop_anchor": stop_anchor,
        "target_reference": target,
        "buffer_scenarios": scenarios,
        "limitations": [
            "Structural levels are candidate invalidation/target proxies, not validated trade levels.",
            "ATR buffers are unvalidated 0.5x and 1.0x scenarios; neither is selected as a default.",
            "R:R is gross and excludes fees, slippage, fill uncertainty, and portfolio risk controls.",
            "Eligible means eligible for later risk review only; it does not authorize an order.",
        ],
    }
