"""Watchlist market scan orchestration and deterministic Gemini screening."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from app.market.data import get_market_data, resample_ohlc
from app.market.indicators import calculate_indicators
from app.market.persistence import persist_derived_4h_candles
from app.strategy.market_structure import analyze_market_structure
from app.strategy.rules import evaluate_market
from risk.planning import build_candidate_risk_plan


DEFAULT_WATCHLIST = Path(__file__).resolve().parents[1] / "config" / "watchlist.json"
COINGECKO_INTER_ASSET_DELAY_SECONDS = 20
logger = logging.getLogger(__name__)


def load_watchlist(path: str | Path = DEFAULT_WATCHLIST) -> dict:
    """Load and validate the explicit CoinGecko asset list and scan settings."""
    with Path(path).open(encoding="utf-8") as stream:
        config = json.load(stream)
    assets = config.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("Watchlist must contain a non-empty 'assets' list.")
    seen = set()
    for asset in assets:
        if not isinstance(asset, dict) or not asset.get("symbol") or not asset.get("coin_id"):
            raise ValueError("Each watchlist asset requires non-empty 'symbol' and 'coin_id'.")
        coin_id = asset["coin_id"].strip()
        symbol = asset["symbol"].strip().upper()
        if not coin_id or not symbol:
            raise ValueError("Asset symbol and coin_id cannot be whitespace only.")
        if coin_id in seen:
            raise ValueError(f"Duplicate CoinGecko coin_id in watchlist: {coin_id}")
        asset["coin_id"] = coin_id
        asset["symbol"] = symbol
        seen.add(coin_id)
    config["assets"] = assets
    config.setdefault("quote_currency", "usd")
    config.setdefault("daily_days", 180)
    config.setdefault("hourly_days", 31)
    logger.info("Watchlist loaded path=%s assets=%d", path, len(assets))
    return config


def qualifies_for_gemini(snapshot: dict) -> bool:
    """Only send setups aligned by HTF trend and HTF market structure."""
    setup = snapshot.get("setup")
    daily = snapshot.get("daily_structure", {}).get("structure")
    four_hour = snapshot.get("four_hour_structure", {}).get("structure")
    return (
        (setup == "potential_long" and daily == "bullish" and four_hour == "bullish")
        or (setup == "potential_short" and daily == "bearish" and four_hour == "bearish")
    )


async def analyze_asset(
    asset: dict,
    *,
    quote_currency: str = "usd",
    daily_days: int = 180,
    hourly_days: int = 31,
) -> dict:
    """Fetch and deterministically analyze one configured token."""
    logger.info("Asset analysis started symbol=%s coin_id=%s", asset["symbol"], asset["coin_id"])
    market_data = await get_market_data(
        coin_id=asset["coin_id"],
        asset_symbol=asset["symbol"],
        daily_days=daily_days,
        hourly_days=hourly_days,
        vs_currency=quote_currency,
    )
    daily_df = market_data["daily"]
    hourly_df = market_data["hourly"]
    if daily_df.empty or hourly_df.empty:
        raise ValueError("CoinGecko returned no daily or hourly OHLC candles.")
    logger.info(
        "OHLC loaded symbol=%s daily_candles=%d hourly_candles=%d",
        asset["symbol"], len(daily_df), len(hourly_df),
    )

    four_hour_df = resample_ohlc(hourly_df, "4h")
    if four_hour_df.empty:
        raise ValueError("No complete 4H candles could be derived from hourly OHLC.")
    persist_derived_4h_candles(
        four_hour_df,
        coin_id=asset["coin_id"],
        symbol=asset["symbol"],
        quote_currency=quote_currency,
    )
    logger.info("4H resampling complete symbol=%s candles=%d", asset["symbol"], len(four_hour_df))
    one_hour_df = hourly_df.copy()

    daily_df = calculate_indicators(daily_df)
    four_hour_df = calculate_indicators(four_hour_df)
    one_hour_df = calculate_indicators(one_hour_df)

    daily_structure = analyze_market_structure(daily_df, timeframe="1d")
    four_hour_structure = analyze_market_structure(four_hour_df, timeframe="4h")
    metadata = market_data["metadata"]
    data_freshness = metadata.get("data_freshness", {})
    data_status = (
        "stale_cache_fallback"
        if any(item.get("refresh_status") == "stale_cache_fallback" for item in data_freshness.values())
        else "current"
    )
    snapshot = evaluate_market(
        daily_df=daily_df,
        four_hour_df=four_hour_df,
        one_hour_df=one_hour_df,
        daily_structure=daily_structure,
        four_hour_structure=four_hour_structure,
        symbol=metadata["symbol"],
        market_data_metadata={
            **metadata,
            "timeframes": {
                "1d": daily_df.attrs.get("market_data", {}),
                "4h": four_hour_df.attrs.get("market_data", {}),
                "1h": one_hour_df.attrs.get("market_data", {}),
            },
        },
    )
    risk_plan = build_candidate_risk_plan(
        snapshot,
        atr14_1h=one_hour_df.iloc[-1].get("atr14"),
        market_data_stale=data_status == "stale_cache_fallback",
    )
    result = {
        "symbol": metadata["symbol"],
        "coin_id": asset["coin_id"],
        "status": "analyzed",
        "data_status": data_status,
        "data_freshness": data_freshness,
        "qualifies_for_gemini": qualifies_for_gemini(snapshot),
        "snapshot": snapshot,
        "risk_plan": risk_plan,
        # Renderer-only inputs; persistence and rule decisions continue to use snapshot.
        "chart_data": {"1d": daily_df, "4h": four_hour_df, "1h": one_hour_df},
    }
    logger.info(
        "Asset analysis completed symbol=%s setup=%s daily_structure=%s four_hour_structure=%s "
        "data_status=%s data_freshness=%s qualifies_for_gemini=%s risk_plan_status=%s",
        result["symbol"],
        snapshot["setup"],
        daily_structure["structure"],
        four_hour_structure["structure"],
        data_status,
        data_freshness,
        result["qualifies_for_gemini"],
        risk_plan.get("status"),
    )
    return result


async def scan_watchlist(config: dict) -> list[dict]:
    """Scan assets sequentially to limit load on the shared public MCP."""
    results = []
    for index, asset in enumerate(config["assets"]):
        if index > 0:
            logger.info(
                "Waiting %d seconds before next CoinGecko MCP request symbol=%s coin_id=%s",
                COINGECKO_INTER_ASSET_DELAY_SECONDS,
                asset["symbol"],
                asset["coin_id"],
            )
            await asyncio.sleep(COINGECKO_INTER_ASSET_DELAY_SECONDS)
        logger.info("Scanning asset symbol=%s coin_id=%s", asset["symbol"], asset["coin_id"])
        try:
            result = await analyze_asset(
                asset,
                quote_currency=config["quote_currency"],
                daily_days=config["daily_days"],
                hourly_days=config["hourly_days"],
            )
        except Exception as exc:  # isolate a bad/unavailable asset from the rest
            logger.exception("Asset scan failed symbol=%s coin_id=%s", asset["symbol"], asset["coin_id"])
            result = {
                "symbol": f"{asset['symbol']}/{config['quote_currency'].upper()}",
                "coin_id": asset["coin_id"],
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "qualifies_for_gemini": False,
            }
        results.append(result)
    return results
