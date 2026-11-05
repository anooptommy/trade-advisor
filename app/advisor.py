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

def format_advisor_message(asset: dict, assessment: dict) -> str:
    """Render current context, plan scenarios, and the user's decision role."""
    snapshot = asset.get("snapshot") or {}
    confidence = snapshot.get("confidence") or {}
    fraction = confidence.get("score_fraction")
    confidence_line = "Confidence score: unavailable"
    if isinstance(fraction, (int, float)):
        points = confidence.get("observed_points", confidence.get("score"))
        total = confidence.get("assessed_points", confidence.get("score_out_of"))
        coverage = confidence.get("coverage_pct")
        confidence_line = f"Confidence score: {float(fraction) * 10:.1f}/10 (not a probability"
        if points is not None and total is not None:
            confidence_line += f"; {points}/{total} assessable points"
        if coverage is not None:
            confidence_line += f"; {coverage}% coverage"
        confidence_line += ")"
    status = assessment.get("status", "no_plan")
    context = snapshot.get("context") or {}
    location = snapshot.get("htf_location") or context.get("location") or {}
    quality = snapshot.get("level_quality") or context.get("level_quality") or {}
    daily_trend = (snapshot.get("daily") or {}).get("trend", {}).get("trend", "unknown")
    four_hour_trend = (snapshot.get("four_hour") or {}).get("trend", {}).get("trend", "unknown")
    daily_structure = (snapshot.get("daily_structure") or {}).get("structure", "unknown")
    four_hour_structure = (snapshot.get("four_hour_structure") or {}).get("structure", "unknown")
    directional_side = context.get("daily_context")
    proximity = location.get("proximity_by_side") or {}
    nearest = proximity.get(directional_side, []) if directional_side in {"bullish", "bearish"} else []
    if not nearest:
        nearest = sorted(
            proximity.get("bullish", []) + proximity.get("bearish", []),
            key=lambda item: item.get("distance", float("inf")),
        )
    location_state = (
        "not evaluated (no directional HTF context or completed 1H candle)"
        if not location.get("available")
        else "confirmed by the strict 1H interaction rule"
        if location.get("confirmed")
        else "not confirmed by the strict 1H interaction rule"
    )
    trend_detail = context.get("trend_alignment_detail") or {}
    structure_detail = context.get("structure_alignment_detail") or {}
    trend_alignment_text = (
        "aligned" if trend_detail.get("aligned")
        else "not aligned" if trend_detail.get("available")
        else "not evaluated (one or both HTF trends are neutral/unknown)"
    )
    structure_alignment_text = (
        "aligned" if structure_detail.get("aligned")
        else "not aligned" if structure_detail.get("available")
        else "not evaluated (no directional Daily bias)"
    )
    nearest_items = []
    for item in nearest[:2]:
        if "low" in item and "high" in item:
            reference = f"{item['low']:g}–{item['high']:g}"
        elif isinstance(item.get("price"), (int, float)):
            reference = f"{item['price']:g}"
        else:
            continue
        gap = f"{item['distance_pct']:.2f}% from close" if isinstance(item.get("distance_pct"), (int, float)) else "distance unavailable"
        if isinstance(item.get("distance_atr"), (int, float)):
            gap += f", {item['distance_atr']:.2f} ATR"
        nearest_items.append(f"{item.get('timeframe')} {item.get('kind')} @ {reference} ({gap})")
    nearest_text = ", ".join(nearest_items)
    quality_rating = quality.get("rating")
    quality_text = (
        f"{quality_rating} ({quality.get('observed_points', 0)}/{quality.get('assessed_weight', 0)} assessed points; "
        f"{quality.get('coverage_pct', 0)}% factor coverage)"
        if quality.get("rating_available")
        else f"not fully rated ({quality.get('observed_points', 0)}/{quality.get('assessed_weight', 0)} assessed points; "
             f"{quality.get('coverage_pct', 0)}% factor coverage)"
    )
    market_data = snapshot.get("market_data") or {}
    volume_line = (
        "Volume: unavailable from CoinGecko OHLC; this is not a negative volume signal and is not a deterministic setup gate."
        if market_data.get("volume_available") is False
        else ""
    )
    risk = asset.get("risk_plan") or {}
    lines = [
        f"🧭 {asset.get('symbol', 'unknown')} — Market update",
        f"As of {assessment.get('assessed_at_utc', 'unknown')}", "",
        f"Situation: {assessment.get('market_situation', 'Unavailable')}",
        f"Trend: {assessment.get('trend', 'Unavailable')}",
        f"Bias: {assessment.get('bias', 'Unavailable')}",
        f"Momentum: {assessment.get('momentum', 'Unavailable')}",
        f"Key levels: {assessment.get('key_levels', 'Unavailable')}", confidence_line,
        f"Data: {asset.get('data_status', 'unknown')}; {assessment.get('data_gaps', 'No additional data notes.')}",
        f"Deterministic setup: {snapshot.get('setup', 'unknown')} (current confirmation only).",
        f"HTF trend alignment: Daily {daily_trend}; 4H {four_hour_trend}; {trend_alignment_text}.",
        f"HTF structure alignment: Daily {daily_structure}; 4H {four_hour_structure}; {structure_alignment_text}.",
        f"HTF location: {location_state}" + (f"; nearest by close: {nearest_text}." if nearest_text else "."),
        f"HTF level quality: {quality_text}.",
        *([volume_line] if volume_line else []),
        *([f"Candidate risk context: {risk.get('status')} ({risk.get('reason', 'see supplied candidate geometry')}); unavailable candidate geometry does not prevent a conditional watch scenario."] if risk else []),
        "", f"Plan status: {STATUS_LABELS.get(status, status)}",
    ]
    if assessment.get("plan_id"):
        lines.append(f"Plan: {assessment['plan_id'][:10]} · revision {assessment.get('plan_revision')}")
    if assessment.get("plan_thesis"):
        lines.append(f"Thesis: {assessment['plan_thesis']}")
    for scenario in assessment.get("scenarios", []):
        direction = scenario.get("direction", "stand_aside").replace("_", " ").upper()
        lines.extend(["", f"{scenario.get('name', 'Scenario')} — {direction}",
                      f"Trigger: {scenario.get('trigger', 'Unavailable')}",
                      f"Response: {scenario.get('action', 'Unavailable')}",
                      f"Entry reference: {scenario.get('entry_reference', 'unavailable')}",
                      f"Stop / invalidation: {scenario.get('stop_or_invalidation', 'unavailable')}",
                      f"Target reference: {scenario.get('target_reference', 'unavailable')}",
                      f"R:R: {scenario.get('risk_reward', 'unavailable')}"])
    if assessment.get("plan_invalidation"):
        lines.extend(["", f"Plan invalidation: {assessment['plan_invalidation']}"])
    if assessment.get("wait_conditions"):
        lines.append(f"Wait for: {assessment['wait_conditions']}")
    lines.extend(["", "Analysis only. You decide whether to trade; no order is placed and no position is managed."])
    message = "\n".join(lines)
    if len(message) <= 3900:
        return message
    footer = lines[-1]
    budget = 3900 - len(footer) - len("\n[Message shortened]\n\n")
    return message[:budget].rsplit("\n", 1)[0] + "\n[Message shortened]\n\n" + footer
