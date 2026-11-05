"""Seed longer CoinGecko hourly candle history for selected watchlist assets."""

import argparse
import asyncio
import logging

from app.logging_config import configure_logging
from app.scanner import load_watchlist
from app.market.data import backfill_hourly_history


async def run(symbols: set[str], days: int, delay_seconds: int) -> None:
    config = load_watchlist()
    assets = [asset for asset in config["assets"] if asset["symbol"].upper() in symbols]
    found = {asset["symbol"].upper() for asset in assets}
    if found != symbols:
        raise ValueError(f"Symbols not in watchlist: {', '.join(sorted(symbols - found))}")
    for index, asset in enumerate(assets):
        if index:
            logging.getLogger(__name__).info("Waiting %d seconds before next asset backfill", delay_seconds)
            await asyncio.sleep(delay_seconds)
        frame = await backfill_hourly_history(
            asset["coin_id"],
            hourly_days=days,
            vs_currency=config.get("quote_currency", "usd"),
            delay_seconds=delay_seconds,
        )
        print(f"{asset['symbol']}: cached {len(frame)} hourly candles")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill selected CoinGecko hourly history into the local cache.")
    parser.add_argument("--assets", default="BTC,ETH", help="Comma-separated watchlist symbols (default: BTC,ETH)")
    parser.add_argument("--days", type=int, default=90, help="Target history in days (default: 90)")
    parser.add_argument("--delay-seconds", type=int, default=20, help="Delay between provider requests and assets")
    args = parser.parse_args()
    symbols = {symbol.strip().upper() for symbol in args.assets.split(",") if symbol.strip()}
    if not symbols:
        parser.error("At least one --assets symbol is required")
    log_path = configure_logging()
    logging.getLogger(__name__).info("Hourly backfill started assets=%s days=%d log_file=%s", sorted(symbols), args.days, log_path)
    asyncio.run(run(symbols, args.days, args.delay_seconds))


if __name__ == "__main__":
    main()
