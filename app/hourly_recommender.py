"""Run the crypto market advisor hourly and deliver Gemini plan updates."""
from __future__ import annotations
import argparse
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv
from app.advisor import attach_plan_identity, format_advisor_message
from app.advisor_persistence import get_latest_advisor_assessment, persist_advisor_assessment
from app.agents.gemini import assess_market
from app.logging_config import configure_logging
from app.scan_persistence import begin_scan_run, mark_scan_run_failed, persist_scan_results
from app.scanner import load_watchlist, scan_watchlist
from app.telegram import send_telegram_message

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def _metrics_payload(asset: dict) -> dict:
    """Bundle all scanner evidence plus data quality for Gemini."""
    return {
        "symbol": asset.get("symbol"), "coin_id": asset.get("coin_id"),
        "data_status": asset.get("data_status"),
        "data_freshness": asset.get("data_freshness", {}),
        "market_metrics": asset.get("snapshot") or {},
        "candidate_risk_context": asset.get("risk_plan", {}),
    }


async def run_once(*, sender=send_telegram_message, advisor=assess_market) -> dict:
    """Scan every asset and save/send one Gemini assessment per successful scan."""
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment.")
    config = load_watchlist()
    started_at = datetime.now(timezone.utc)
    scan_id = begin_scan_run(config, started_at=started_at)
    try:
        results = await scan_watchlist(config)
        persist_scan_results(scan_id, config, results)
    except Exception as exc:
        mark_scan_run_failed(scan_id, str(exc))
        raise
    analyzed = sent = 0
    failed = sum(result.get("status") != "analyzed" for result in results)
    statuses: dict[str, int] = {}
    for asset in results:
        if asset.get("status") != "analyzed":
            continue
        analyzed += 1
        try:
            metrics = _metrics_payload(asset)
            previous = get_latest_advisor_assessment(asset["coin_id"])
            assessment = attach_plan_identity(advisor(metrics, previous), previous)
            history_id = persist_advisor_assessment(
                scan_id, asset["coin_id"], metrics=metrics,
                previous_assessment=previous, assessment=assessment,
            )
            message_id = await sender(format_advisor_message(asset, assessment))
            sent += 1
            statuses[assessment["status"]] = statuses.get(assessment["status"], 0) + 1
            logger.info("Advisor update sent symbol=%s status=%s history_id=%s message_id=%s",
                        asset.get("symbol"), assessment["status"], history_id, message_id)
        except Exception as exc:
            failed += 1
            logger.exception("Advisor update failed symbol=%s error_type=%s",
                             asset.get("symbol"), type(exc).__name__)
    summary = {"scan_run_id": scan_id, "assets": len(results), "analyzed": analyzed,
               "failed": failed, "sent": sent, "plan_status_counts": statuses}
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
