import argparse
import asyncio
import logging
import os
from datetime import datetime, timezone

from app.logging_config import configure_logging
from app.scanner import load_watchlist, scan_watchlist
from app.scan_persistence import (
    begin_scan_run,
    mark_scan_run_failed,
    persist_agent_review,
    persist_scan_results,
)
from app.strategy.rules import market_snapshot_json

logger = logging.getLogger(__name__)


async def run() -> None:
    config = load_watchlist()
    logger.info(
        "Loaded watchlist assets=%s quote_currency=%s daily_days=%s hourly_days=%s",
        [asset["symbol"] for asset in config["assets"]],
        config["quote_currency"],
        config["daily_days"],
        config["hourly_days"],
    )
    print("TRADING AGENT — MULTI-ASSET MARKET SCAN")
    print(f"Assets: {', '.join(asset['symbol'] for asset in config['assets'])}")
    print("Scanning configured CoinGecko assets sequentially...\n")

    scan_started_at = datetime.now(timezone.utc)
    scan_run_id = begin_scan_run(config, started_at=scan_started_at)
    try:
        results = await scan_watchlist(config)
        persistence_ids = persist_scan_results(scan_run_id, config, results)
    except Exception as exc:
        mark_scan_run_failed(scan_run_id, str(exc))
        raise
    candidates = [result for result in results if result["qualifies_for_gemini"]]
    logger.info(
        "Watchlist scan complete analyzed=%d failed=%d stale_data=%d candidates=%d",
        sum(result["status"] == "analyzed" for result in results),
        sum(result["status"] == "failed" for result in results),
        sum(result.get("data_status") == "stale_cache_fallback" for result in results),
        len(candidates),
    )

    for result in results:
        print(f"{result['symbol']} — {result['status']}")
        if result["status"] == "failed":
            print(f"  Data/analysis error: {result['error']}")
            logger.error("Asset failed symbol=%s error=%s", result["symbol"], result["error"])
            continue
        freshness = result.get("data_freshness", {})
        if freshness:
            details = []
            for timeframe, item in freshness.items():
                status = item.get("refresh_status", "unknown")
                latest = item.get("latest_candle_utc") or "unknown"
                last_success = item.get("last_success_utc") or "unknown"
                age_hours = item.get("latest_candle_age_hours")
                age = f" age={age_hours}h" if age_hours is not None else ""
                details.append(
                    f"{timeframe}={status} latest={latest}{age} last_success={last_success}"
                )
            print(f"  Data freshness: {'; '.join(details)}")
            log_method = (
                logger.warning
                if result.get("data_status") == "stale_cache_fallback"
                else logger.info
            )
            log_method(
                "Scan data freshness symbol=%s status=%s details=%s",
                result["symbol"], result.get("data_status", "unknown"), freshness,
            )
        snapshot = result["snapshot"]
        print(
            f"  Setup: {snapshot['setup']} | "
            f"Daily/4H structure: "
            f"{snapshot['daily_structure']['structure']}/"
            f"{snapshot['four_hour_structure']['structure']} | "
            f"Gemini review: {'yes' if result['qualifies_for_gemini'] else 'no'}"
        )
        confidence = snapshot.get("confidence", {})
        if confidence.get("score") is not None:
            score_line = (
                f"  Confidence evidence: {confidence['score']}/{confidence['score_out_of']} "
                f"assessable points | fraction: {confidence['score_fraction']:.4f} | "
                f"coverage: {confidence['coverage_pct']}% | "
                f"band: {confidence['band'] or 'unavailable'}"
            )
            print(score_line)
            if confidence.get("missing_inputs"):
                print(f"  Missing confidence inputs: {', '.join(confidence['missing_inputs'])}")
            logger.info(
                "Confidence assessment symbol=%s score=%s/%s score_fraction=%s coverage_pct=%s band=%s missing_inputs=%s",
                result["symbol"], confidence["score"], confidence["score_out_of"],
                confidence["score_fraction"], confidence["coverage_pct"], confidence["band"], confidence["missing_inputs"],
            )
        else:
            print("  Confidence: not applicable (no qualifying setup)")
        risk_plan = result.get("risk_plan", {})
        if snapshot.get("setup") in ("potential_long", "potential_short"):
            print(f"  Risk planning: {risk_plan.get('status', 'unavailable')} (analysis only)")
            if risk_plan.get("status") == "assessed":
                anchor = risk_plan["stop_anchor"]
                target = risk_plan["target_reference"]
                print(
                    f"    Stop anchor: {anchor['timeframe']} {anchor['kind']} at {anchor['price']:.8g}; "
                    f"target: {target['timeframe']} {target['kind']} at {target['price']:.8g}"
                )
                for scenario in risk_plan["buffer_scenarios"]:
                    if scenario.get("status") != "assessed":
                        print(f"    {scenario['atr_buffer_multiple']}x ATR buffer: unavailable ({scenario['reason']})")
                        continue
                    matrix = scenario["eligibility"]
                    print(
                        f"    {scenario['atr_buffer_multiple']}x ATR buffer: stop={scenario['stop_price']:.8g} "
                        f"gross R:R={scenario['gross_risk_reward']:.3f} | "
                        f"matrix={matrix['matrix_result']} | risk review eligible="
                        f"{matrix['eligible_for_risk_review']}"
                    )
            elif risk_plan.get("reason"):
                print(f"    Reason: {risk_plan['reason']}")
            logger.info("Risk planning analysis symbol=%s risk_plan=%s", result["symbol"], risk_plan)
        if result["qualifies_for_gemini"]:
            print("  Snapshot:")
            print(market_snapshot_json(snapshot))
            logger.info("Candidate snapshot symbol=%s snapshot=%s", result["symbol"], market_snapshot_json(snapshot, indent=None))

    print(f"\nQualifying candidates: {len(candidates)} of {len(results)}")
    if candidates:
        from app.agents.gemini import review_candidate

        for candidate in candidates:
            review_error = None
            review_text = None
            try:
                review_text = review_candidate(candidate["snapshot"])
                candidate["gemini_review"] = review_text
                print(f"\nGEMINI REVIEW — {candidate['symbol']}\n{review_text}")
                logger.info("Gemini review completed symbol=%s review=%s", candidate["symbol"], review_text)
            except Exception as exc:
                review_error = f"{type(exc).__name__}: {exc}"
                print(f"\nGEMINI REVIEW FAILED — {candidate['symbol']}: {exc}")
                logger.exception("Gemini review failed symbol=%s", candidate["symbol"])
            suggestion_id = persistence_ids["trade_suggestion_ids"].get(candidate["coin_id"])
            if suggestion_id is not None:
                persist_agent_review(
                    suggestion_id,
                    model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
                    input_snapshot=candidate["snapshot"],
                    status="failed" if review_error else "succeeded",
                    response_text=review_text,
                    error_message=review_error,
                )


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram crypto trade recommender")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--scan-only",
        action="store_true",
        help="Run the legacy console scan without Telegram delivery.",
    )
    modes.add_argument(
        "--hourly",
        action="store_true",
        help="Keep the Telegram recommender running at UTC hour boundaries.",
    )
    args = parser.parse_args()

    mode = "scan-only" if args.scan_only else "hourly" if args.hourly else "recommend-once"
    log_path = configure_logging()
    logger.info("Application run started mode=%s log_file=%s", mode, log_path)
    try:
        if args.scan_only:
            asyncio.run(run())
        else:
            from app.hourly_recommender import run_hourly, run_once

            asyncio.run(run_hourly() if args.hourly else run_once())
    except KeyboardInterrupt:
        logger.info("Application run stopped by user")
    except Exception:
        logger.exception("Application run terminated with an unhandled error")
        raise
    finally:
        logger.info("Application run finished mode=%s", mode)


if __name__ == "__main__":
    main()

