from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4

STATUS_LABELS = {"new_plan": "New conditional plan", "actionable": "Previous plan trigger is present", "wait": "Wait for the stated conditions", "revise": "Previous plan needs revision", "no_plan": "No active trade plan"}

def attach_plan_identity(assessment: dict, previous: dict | None) -> dict:
    """Assign an ID and enforce valid plan lifecycle transitions."""
    result = dict(assessment)
    status = result.get("status")
    active = bool(previous and previous.get("plan_active") and previous.get("plan_id"))
    scenarios = result.get("scenarios") or []
    if status == "new_plan":
        if active:
            raise ValueError("Gemini cannot create a second plan while a prior plan is active; use revise.")
        if not scenarios:
            raise ValueError("A new plan must contain at least one conditional scenario.")
        result["plan_id"], result["plan_revision"] = uuid4().hex, 1
    elif status in {"wait", "actionable"}:
        if not active:
            raise ValueError(f"Gemini returned {status!r} without an active prior plan.")
        if not scenarios:
            raise ValueError(f"A {status} assessment must restate the active plan scenarios.")
        result["plan_id"] = previous["plan_id"]
        result["plan_revision"] = previous.get("plan_revision", 1)
    elif status == "revise":
        if not active:
            raise ValueError("Gemini cannot revise a plan when there is no active prior plan.")
        result["superseded_plan"] = previous
        result["plan_id"] = previous["plan_id"]
        result["plan_revision"] = previous.get("plan_revision", 1) + int(bool(scenarios))
    elif status == "no_plan":
        if scenarios:
            raise ValueError("A no_plan assessment cannot contain trade scenarios.")
        if active:
            result["superseded_plan"] = previous
        result["plan_id"], result["plan_revision"] = None, None
    else:
        raise ValueError("Unsupported Gemini plan status.")
    result["plan_active"] = status in {"new_plan", "actionable", "wait", "revise"} and bool(scenarios)
    result["assessed_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return result


def deterministic_watch_update(asset: dict) -> dict:
    """Build a zero-Gemini watch note when no plan/candidate needs review."""
    snapshot = asset.get("snapshot") or {}
    context = snapshot.get("context") or {}
    location = snapshot.get("htf_location") or {}
    entry = snapshot.get("entry_confirmation") or {}
    blockers = []
    trend_alignment = context.get("trend_alignment_detail") or {}
    structure_alignment = context.get("structure_alignment_detail") or {}
    if not trend_alignment.get("available"):
        blockers.append("directional Daily and 4H trends")
    elif not trend_alignment.get("aligned"):
        blockers.append("Daily/4H trend alignment")
    if not structure_alignment.get("available"):
        blockers.append("a directional Daily bias")
    elif not structure_alignment.get("aligned"):
        blockers.append("matching Daily/4H swing structure")
    if location.get("available") and not location.get("confirmed"):
        blockers.append("a completed 1H interaction with the directional HTF area")
    confirmations = entry.get("confirmations") or {}
    confirmed_count = entry.get("confirmed_count", 0)
    required_count = entry.get("required_count", 2)
    if confirmed_count < required_count:
        blockers.append(f"at least {required_count} closed-1H confirmations (currently {confirmed_count})")
    wait = "Wait for " + "; ".join(blockers) + "." if blockers else "Wait for the next completed 1H candle and recheck the deterministic setup."
    market_data = snapshot.get("market_data") or {}
    gaps = []
    if market_data.get("volume_available") is False:
        gaps.append("volume unavailable from CoinGecko OHLC")
    if asset.get("data_status") == "stale_cache_fallback":
        gaps.append("market data is using stale cache")
    return {
        "status": "no_plan",
        "market_situation": "No deterministic candidate setup is confirmed on this scan.",
        "trend": "Daily/4H/1H conditions are shown on the attached chart.",
        "bias": "See Daily and 4H trend/structure alignment on the chart.",
        "momentum": "See 1H indicators on the chart.",
        "key_levels": "See plotted HTF levels and nearest-level distances on the chart.",
        "data_gaps": "; ".join(gaps),
        "plan_thesis": "No Gemini plan requested for an unqualified setup with no active prior plan.",
        "scenarios": [],
        "plan_invalidation": "",
        "wait_conditions": wait,
    }

def format_advisor_message(asset: dict, assessment: dict) -> str:
    """Render a compact Telegram caption; the attached chart carries detail."""
    snapshot = asset.get("snapshot") or {}
    status = assessment.get("status", "no_plan")
    price = snapshot.get("price")
    price_text = f"${float(price):,.6g}" if isinstance(price, (int, float)) else "price unavailable"
    timeframe_trends = []
    for label, key in (("D", "daily"), ("4H", "four_hour"), ("1H", "one_hour")):
        trend = ((snapshot.get(key) or {}).get("trend") or {}).get("trend")
        timeframe_trends.append(f"{label} {trend or 'unknown'}")
    short_status = STATUS_LABELS.get(status, status)
    lines = [
        f"{asset.get('symbol', 'unknown')} · {short_status}",
        f"{price_text}  |  {' · '.join(timeframe_trends)}  |  {snapshot.get('setup', 'unknown')}",
    ]
    grade = setup_grade(snapshot)
    if grade:
        lines[1] += f" · Grade {grade}"
    if assessment.get("plan_id"):
        lines[0] += f"  ·  rev {assessment.get('plan_revision')}"
    scenarios = assessment.get("scenarios") or []
    if scenarios:
        for scenario in scenarios[:2]:
            direction = scenario.get("direction", "stand_aside").replace("_", " ").upper()
            lines.append(f"Watch {direction}: {scenario.get('trigger', 'trigger unavailable')}")
            invalidation = scenario.get("stop_or_invalidation")
            if invalidation and invalidation.lower() != "unavailable":
                lines.append(f"Invalidation: {invalidation}")
    else:
        watch = assessment.get("wait_conditions") or assessment.get("plan_thesis")
        if watch:
            lines.append(f"Watch: {watch}")
    if assessment.get("data_gaps"):
        lines.append(f"Data: {assessment['data_gaps']}")
    lines.append("Analysis only · No order is placed.")
    message = "\n".join(lines)
    return message if len(message) <= 1000 else message[:997].rsplit("\n", 1)[0] + "…"


def setup_grade(snapshot: dict) -> str | None:
    """Map an existing candidate score to the requested display grade only."""
    if snapshot.get("setup") not in {"potential_long", "potential_short"}:
        return None
    fraction = ((snapshot.get("confidence") or {}).get("score_fraction"))
    if not isinstance(fraction, (int, float)):
        return "UNRATED"
    percent = float(fraction) * 100
    return "A+" if percent >= 85 else "A" if percent >= 75 else "B+" if percent >= 65 else "B"
