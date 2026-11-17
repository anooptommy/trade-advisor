"""Persistent conditional trade plans and closed-candle assumption checks."""
from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo
import pandas as pd

PLAN_STATE_PATH = Path(__file__).resolve().parents[1] / "runtime" / "trade_plans.json"
LOCAL_TIMEZONE = ZoneInfo("Asia/Kolkata")
TIMEFRAME_KEYS = {"1d": "daily", "4h": "four_hour", "1h": "one_hour"}


def local_date(now: datetime | None = None) -> str:
    return (now or datetime.now(LOCAL_TIMEZONE)).astimezone(LOCAL_TIMEZONE).date().isoformat()


def load_plan_state(path: str | Path = PLAN_STATE_PATH) -> dict:
    target = Path(path)
    if not target.exists():
        return {"version": 1, "last_daily_summary_date": None, "assets": {}}
    value = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("assets", {}), dict):
        raise ValueError(f"Invalid plan-state document: {target}")
    value.setdefault("version", 1)
    value.setdefault("last_daily_summary_date", None)
    value.setdefault("assets", {})
    return value


def save_plan_state(state: dict, path: str | Path = PLAN_STATE_PATH) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
        handle.write(rendered)
        temporary = Path(handle.name)
    os.replace(temporary, target)


def _closed_rows(asset: dict, timeframe: str) -> list[dict]:
    frame = (asset.get("chart_data") or {}).get(timeframe)
    if frame is None or frame.empty or "timestamp" not in frame:
        return []
    snapshot = asset.get("snapshot") or {}
    timeframe_snapshot = snapshot.get(TIMEFRAME_KEYS[timeframe]) or {}
    cutoff = timeframe_snapshot.get("timestamp")
    rows = frame
    if cutoff:
        stamps = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        cutoff_stamp = pd.to_datetime(cutoff, utc=True, errors="coerce")
        rows = frame.loc[stamps <= cutoff_stamp]
    return rows.tail(2).to_dict("records")


def check_price_condition(asset: dict, condition: dict) -> dict:
    """Evaluate a structured condition on completed provider OHLC candles only."""
    timeframe = condition.get("timeframe")
    operator = condition.get("operator")
    level = condition.get("reference_price")
    base = {"timeframe": timeframe, "operator": operator, "reference_price": level,
            "description": condition.get("description", "")}
    if timeframe not in TIMEFRAME_KEYS or operator not in {
        "above", "below", "touches", "crosses_above", "crosses_below",
    } or not isinstance(level, (int, float)) or not math.isfinite(float(level)):
        return {**base, "state": "unavailable", "observed_close": None}
    rows = _closed_rows(asset, timeframe)
    if not rows:
        return {**base, "state": "unavailable", "observed_close": None}
    current = rows[-1]
    close = float(current["close"])
    if operator == "above":
        met = close > level
    elif operator == "below":
        met = close < level
    elif operator == "touches":
        met = float(current["low"]) <= level <= float(current["high"])
    elif len(rows) < 2:
        return {**base, "state": "unavailable", "observed_close": close}
    elif operator == "crosses_above":
        met = float(rows[-2]["close"]) <= level < close
    else:
        met = float(rows[-2]["close"]) >= level > close
    return {**base, "state": "met" if met else "waiting", "observed_close": close,
            "candle_utc": str(current["timestamp"])}


def evaluate_plan(asset: dict, plan: dict) -> dict:
    """Return wait/actionable/revise evidence for all conditional scenarios."""
    if asset.get("data_status") == "stale_cache_fallback":
        return {"state": "waiting", "scenarios": [], "reason": "stale_market_data"}
    results = []
    current_setup = (asset.get("snapshot") or {}).get("setup")
    for scenario in plan.get("scenarios") or []:
        assumptions = scenario.get("assumptions") or []
        invalidations = scenario.get("invalidation_conditions") or []
        checks = [check_price_condition(asset, item) for item in assumptions]
        invalidation_checks = [check_price_condition(asset, item) for item in invalidations]
        invalidated = any(item["state"] == "met" for item in invalidation_checks)
        direction = scenario.get("direction")
        setup_matches = current_setup == ("potential_long" if direction == "long" else "potential_short")
        ready = direction in {"long", "short"} and setup_matches and bool(checks) and all(item["state"] == "met" for item in checks)
        results.append({"name": scenario.get("name", "scenario"),
                        "state": "invalidated" if invalidated else "ready" if ready else "waiting",
                        "setup_confirmed": setup_matches,
                        "assumptions": checks, "invalidation_checks": invalidation_checks})
    if results and all(item["state"] == "invalidated" for item in results):
        overall = "revise"
    elif any(item["state"] == "ready" for item in results):
        overall = "actionable"
    else:
        overall = "waiting"
    reason = "closed_candle_conditions_and_deterministic_setup"
    return {"state": overall, "scenarios": results, "reason": reason}


def assumptions_summary(plan: dict, *, limit: int = 2) -> str:
    """Short human-readable plan summary for the daily Telegram digest."""
    scenarios = plan.get("scenarios") or []
    if not scenarios:
        return "No supported conditional plan yet; waiting for a clearer setup."
    parts = []
    for scenario in scenarios[:limit]:
        conditions = [item.get("description") or
                      f"{item.get('timeframe')} close {item.get('operator')} {item.get('reference_price')}"
                      for item in (scenario.get("assumptions") or [])[:2]]
        direction = str(scenario.get("direction", "watch")).upper()
        parts.append(f"{direction}: " + ("; ".join(conditions) if conditions else scenario.get("trigger", "conditional trigger unavailable")))
    return " | ".join(parts)
