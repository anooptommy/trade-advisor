"""Run the crypto market advisor hourly and deliver Gemini plan updates."""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
import os
import time
from tempfile import TemporaryDirectory
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv
from app.advisor import attach_plan_identity, deterministic_watch_update, format_advisor_message
from app.advisor_persistence import get_latest_advisor_assessment, persist_advisor_assessment
from app.agents.gemini import assess_market
from app.logging_config import configure_logging
from app.plan_state import (
    PLAN_STATE_PATH,
    assumptions_summary,
    evaluate_plan,
    load_plan_state,
    local_date,
    save_plan_state,
)
from app.scan_persistence import begin_scan_run, mark_scan_run_failed, persist_scan_results
from app.scanner import COINGECKO_INTER_ASSET_DELAY_SECONDS, analyze_asset, load_watchlist
from app.telegram import send_telegram_message, send_telegram_photo

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
GEMINI_REQUEST_DELAY_SECONDS = max(0.0, float(os.getenv("GEMINI_REQUEST_DELAY_SECONDS", "30")))
GEMINI_RATE_LIMIT_COOLDOWN_SECONDS = max(0.0, float(os.getenv("GEMINI_RATE_LIMIT_COOLDOWN_SECONDS", "60")))


def _metrics_payload(asset: dict) -> dict:
    """Bundle all scanner evidence plus data quality for Gemini."""
    return {
        "symbol": asset.get("symbol"), "coin_id": asset.get("coin_id"),
        "data_status": asset.get("data_status"),
        "data_freshness": asset.get("data_freshness", {}),
        "market_metrics": asset.get("snapshot") or {},
        "candidate_risk_context": asset.get("risk_plan", {}),
    }


async def _send_visual_update(asset: dict, assessment: dict, *, sender, photo_sender) -> tuple[int | None, bool]:
    """Attach the chart when rendering works, otherwise deliver the caption."""
    caption = format_advisor_message(asset, assessment)
    try:
        from app.visualization.market_infographic import render_market_infographic
        with TemporaryDirectory(prefix="trade-advisor-chart-") as output_dir:
            image_path = await asyncio.to_thread(
                render_market_infographic,
                asset.get("snapshot") or {},
                asset.get("chart_data") or {},
                output_dir=output_dir,
                width=1600,
                height=1200,
                stale=asset.get("data_status") == "stale_cache_fallback",
                assessment=assessment,
                watch_conditions=[assessment.get("wait_conditions", "")],
            )
            message_id = await photo_sender(image_path, caption)
        return message_id, True
    except Exception as exc:
        logger.warning("Chart delivery unavailable symbol=%s error_type=%s",
                       asset.get("symbol"), type(exc).__name__)
        return await sender(caption), False


def _is_rate_limited(error: Exception) -> bool:
    response = getattr(error, "response", None)
    code = getattr(error, "code", None) or getattr(response, "status_code", None)
    return code == 429 or "429" in str(error) or "rate limit" in str(error).lower()


def _fallback_after_gemini_error(asset: dict, previous: dict | None) -> dict:
    """Do not imply a fresh plan decision when Gemini did not answer."""
    fallback = deterministic_watch_update(asset)
    if previous and previous.get("plan_active"):
        fallback = dict(previous)
        fallback.update({
            "status": "wait",
            "data_gaps": "Gemini could not refresh this assessment; the prior plan remains unchanged and unconfirmed.",
            "wait_conditions": "Do not treat this cycle as an actionability check. Continue waiting for the prior plan's stated trigger.",
        })
    else:
        fallback["data_gaps"] = "Gemini did not return a fresh assessment; no new plan was created."
    return fallback


def _record_plan_event(entry: dict, event: str, assessment: dict, details: dict | None = None) -> None:
    event_time = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    history = entry.setdefault("history", [])
    history.append({"at_utc": event_time, "event": event, "status": assessment.get("status"),
                    "plan_revision": assessment.get("plan_revision"), "details": details or {}})
    del history[:-50]
    if event in {"plan_created", "plan_revised", "plan_ended"}:
        version = dict(assessment)
        version.pop("superseded_plan", None)
        version.pop("history_id", None)
        versions = entry.setdefault("plan_history", [])
        versions.append({"at_utc": event_time, "event": event, "assessment": version})
        del versions[:-25]


def _daily_plan_digest(entries: list[tuple[str, dict | None, dict | None]], today: str) -> str:
    lines = [f"Trading plans · {today} · conditional analysis only"]
    for symbol, plan, evaluation in entries:
        if plan and plan.get("plan_active"):
            state = (evaluation or {}).get("state", "waiting").upper()
            lines.append(f"\n{symbol} · {state}\n{assumptions_summary(plan)}")
        else:
            lines.append(f"\n{symbol} · No supported conditional plan on this scan")
    lines.append("\nWait for the listed closed-candle conditions; no orders are placed.")
    return "\n".join(lines)[:4000]


async def run_once(*, sender=send_telegram_message, photo_sender=send_telegram_photo, advisor=assess_market) -> dict:
    """Scan sequentially, persist per-token plans, and alert only on plan milestones."""
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment.")
    config = load_watchlist()
    started_at = datetime.now(timezone.utc)
    today = local_date(started_at)
    plan_state = load_plan_state()
    first_run_today = plan_state.get("last_daily_summary_date") != today
    for watch_asset in config["assets"]:
        plan_state.setdefault("assets", {}).setdefault(
            watch_asset["coin_id"], {"symbol": watch_asset["symbol"], "history": []},
        )
    original_state = json.dumps(plan_state, ensure_ascii=False, sort_keys=True)
    scan_id = begin_scan_run(config, started_at=started_at)
    results: list[dict] = []
    analyzed = failed = gemini_calls = gemini_skipped = sent = images_sent = 0
    statuses: dict[str, int] = {}
    daily_entries: list[tuple[str, dict | None, dict | None]] = []
    actionable_alerts: list[tuple[dict, dict]] = []
    run_timer = time.perf_counter()
    last_provider_request_at: float | None = None
    try:
        for index, watch_asset in enumerate(config["assets"]):
            if last_provider_request_at is not None:
                remaining = COINGECKO_INTER_ASSET_DELAY_SECONDS - (time.perf_counter() - last_provider_request_at)
                if remaining > 0:
                    logger.info("Waiting %.1f seconds to space CoinGecko requests", remaining)
                    await asyncio.sleep(remaining)
            asset_timer = time.perf_counter()
            scan_timer = time.perf_counter()
            try:
                asset = await analyze_asset(watch_asset, quote_currency=config["quote_currency"],
                                            daily_days=config["daily_days"], hourly_days=config["hourly_days"])
                freshness = asset.get("data_freshness") or {}
                refreshed_provider = any(info.get("refresh_status") == "refreshed"
                                         for info in freshness.values() if isinstance(info, dict))
            except Exception as exc:
                failed += 1
                logger.exception("Asset scan failed symbol=%s coin_id=%s", watch_asset["symbol"], watch_asset["coin_id"])
                asset = {"symbol": f"{watch_asset['symbol']}/{config['quote_currency'].upper()}",
                         "coin_id": watch_asset["coin_id"], "status": "failed",
                         "error": f"{type(exc).__name__}: {exc}"}
                refreshed_provider = True
            results.append(asset)
            if refreshed_provider:
                last_provider_request_at = time.perf_counter()
            if asset.get("status") != "analyzed":
                daily_entries.append((watch_asset["symbol"], None, None))
                continue

            analyzed += 1
            coin_id = asset["coin_id"]
            entry = plan_state.setdefault("assets", {}).setdefault(coin_id, {"symbol": watch_asset["symbol"], "history": []})
            previous = entry.get("plan")
            if not previous:
                # Migrate an existing SQLite plan into the tracked runtime state file.
                legacy = get_latest_advisor_assessment(coin_id)
                if legacy and legacy.get("plan_active"):
                    previous = legacy
                    entry["plan"] = legacy
            previous = previous if previous and previous.get("plan_active") else None
            before_eval = evaluate_plan(asset, previous) if previous else {"state": "no_plan", "scenarios": []}
            prior_eval_state = entry.get("assumption_state")
            current_eval_state = before_eval["state"]
            if previous and current_eval_state != prior_eval_state:
                _record_plan_event(entry, f"assumptions_{current_eval_state}", previous, before_eval)
                entry["assumption_state"] = current_eval_state

            legacy_missing_assumptions = bool(previous) and any(
                not scenario.get("assumptions") for scenario in (previous.get("scenarios") or [])
            )
            no_plan_retry_due = entry.get("last_plan_attempt_date") != today
            needs_new_plan = not previous and no_plan_retry_due and (first_run_today or asset.get("qualifies_for_gemini"))
            needs_review = bool(previous) and (
                legacy_missing_assumptions or
                entry.get("revision_pending", False) or before_eval["state"] == "revise"
            )
            gemini_needed = needs_new_plan or needs_review
            assessment = previous or deterministic_watch_update(asset)
            gemini_delay = GEMINI_REQUEST_DELAY_SECONDS
            if gemini_needed:
                gemini_timer = time.perf_counter()
                if needs_new_plan:
                    entry["last_plan_attempt_date"] = today
                try:
                    gemini_calls += 1
                    assessment = attach_plan_identity(advisor(_metrics_payload(asset), previous), previous)
                    history_id = persist_advisor_assessment(scan_id, coin_id, metrics=_metrics_payload(asset),
                                                            previous_assessment=previous, assessment=assessment)
                    statuses[assessment["status"]] = statuses.get(assessment["status"], 0) + 1
                    old_id = (previous or {}).get("plan_id")
                    new_id = assessment.get("plan_id")
                    if assessment.get("status") == "no_plan":
                        event = "plan_ended" if previous else "no_plan_supported"
                        _record_plan_event(entry, event, assessment, {"history_id": history_id})
                    elif not previous:
                        _record_plan_event(entry, "plan_created", assessment, {"history_id": history_id})
                    elif old_id != new_id or assessment.get("plan_revision") != previous.get("plan_revision"):
                        _record_plan_event(entry, "plan_revised", assessment, {"history_id": history_id})
                    elif assessment.get("status") == "actionable":
                        _record_plan_event(entry, "execution_assumptions_met", assessment, before_eval)
                    plan_record = dict(assessment)
                    plan_record.pop("superseded_plan", None)
                    plan_record.pop("history_id", None)
                    entry["plan"] = plan_record
                    entry["revision_pending"] = False
                    logger.info("Plan assessment saved symbol=%s status=%s history_id=%s",
                                asset["symbol"], assessment["status"], history_id)
                except Exception as exc:
                    failed += 1
                    if previous and before_eval["state"] == "revise":
                        entry["revision_pending"] = True
                    logger.exception("Plan assessment failed symbol=%s error_type=%s", asset["symbol"], type(exc).__name__)
                    if _is_rate_limited(exc):
                        gemini_delay = max(gemini_delay, GEMINI_RATE_LIMIT_COOLDOWN_SECONDS)
                    assessment = _fallback_after_gemini_error(asset, previous)
                if gemini_delay and index < len(config["assets"]) - 1:
                    await asyncio.sleep(gemini_delay)
            else:
                gemini_skipped += 1

            active_plan = entry.get("plan") or assessment
            after_eval = evaluate_plan(asset, active_plan) if active_plan.get("plan_active") else {"state": "no_plan", "scenarios": []}
            if active_plan.get("plan_active"):
                new_state = after_eval["state"]
                if new_state != entry.get("assumption_state"):
                    _record_plan_event(entry, f"assumptions_{new_state}", active_plan, after_eval)
                    entry["assumption_state"] = new_state
                if new_state == "actionable":
                    actionable_assessment = dict(active_plan)
                    actionable_assessment["status"] = "actionable"
                    entry["plan"] = actionable_assessment
                    if not first_run_today and not entry.get("actionable_notified", False):
                        actionable_alerts.append((asset, actionable_assessment, coin_id))
                elif new_state == "waiting":
                    waiting_assessment = dict(active_plan)
                    waiting_assessment["status"] = "wait"
                    entry["plan"] = waiting_assessment
                    entry["actionable_notified"] = False
            daily_entries.append((watch_asset["symbol"], active_plan if active_plan.get("plan_active") else None, after_eval))
            logger.info("Asset plan check symbol=%s state=%s seconds=%.2f", watch_asset["symbol"],
                        after_eval["state"], time.perf_counter()-asset_timer)

        persist_scan_results(scan_id, config, results)
    except Exception as exc:
        mark_scan_run_failed(scan_id, str(exc))
        raise

    changed = json.dumps(plan_state, ensure_ascii=False, sort_keys=True) != original_state
    if changed:
        save_plan_state(plan_state)
    if first_run_today:
        message = _daily_plan_digest(daily_entries, today)
        await sender(message)
        plan_state["last_daily_summary_date"] = today
        save_plan_state(plan_state)
        sent += 1
    elif actionable_alerts:
        for asset, assessment, coin_id in actionable_alerts:
            message_id, image_sent = await _send_visual_update(asset, assessment, sender=sender, photo_sender=photo_sender)
            sent += 1
            images_sent += int(image_sent)
            plan_state["assets"][coin_id]["actionable_notified"] = True
            save_plan_state(plan_state)
            logger.info("Action threshold alert sent symbol=%s image=%s message_id=%s",
                        asset.get("symbol"), image_sent, message_id)
    else:
        await sender("None of the tokens' trading plans have reached the execution threshold on this scan.")
        sent += 1

    if first_run_today:
        # The daily digest itself communicates any plan already at its threshold.
        for _, plan, evaluation in daily_entries:
            if plan and evaluation and evaluation.get("state") == "actionable":
                for entry in plan_state["assets"].values():
                    if entry.get("plan", {}).get("plan_id") == plan.get("plan_id"):
                        entry["actionable_notified"] = True
        save_plan_state(plan_state)

    summary = {"scan_run_id": scan_id, "assets": len(results), "analyzed": analyzed,
               "failed": failed, "sent": sent, "images_sent": images_sent,
               "gemini_calls": gemini_calls, "gemini_skipped": gemini_skipped,
               "plan_status_counts": statuses, "first_run_today": first_run_today,
               "total_seconds": round(time.perf_counter()-run_timer, 2),
               "plan_state_path": str(PLAN_STATE_PATH)}
    logger.info("Hourly advisor run completed summary=%s", summary)
    return summary


def _seconds_until_next_utc_hour(now: datetime | None = None) -> float:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    next_hour = current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return max(0.0, (next_hour - current).total_seconds())


async def run_hourly() -> None:
    """Run at UTC hour boundaries and await each cycle so scans cannot overlap."""
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment.")
    logger.info("Hourly Telegram market advisor started; schedule timezone=UTC")
    while True:
        delay = _seconds_until_next_utc_hour()
        logger.info("Next advisor scan begins in %.1f seconds", delay)
        await asyncio.sleep(delay)
        try:
            await run_once()
        except Exception as exc:
            logger.exception("Hourly advisor run failed error_type=%s", type(exc).__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Hourly Telegram crypto market advisor")
    parser.add_argument("--once", action="store_true", help="Run one analysis and plan update, then exit.")
    args = parser.parse_args()
    log_path = configure_logging()
    logger.info("Market advisor process started log_file=%s", log_path)
    try:
        asyncio.run(run_once() if args.once else run_hourly())
    except KeyboardInterrupt:
        logger.info("Market advisor stopped by user")


if __name__ == "__main__":
    main()
