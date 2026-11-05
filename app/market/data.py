import asyncio
import json
import logging
import os
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app.market.persistence import DatabasePersistenceError, persist_provider_candles


COINGECKO_MCP_URL = "https://mcp.api.coingecko.com/mcp"
logger = logging.getLogger(__name__)
NORMALIZED_CANDLE_COLUMNS = [
    "timestamp", "open", "high", "low", "close", "volume",
]
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "coingecko"


def _build_tls_context():
    """Trust platform roots plus certifi and any explicitly configured roots."""
    import certifi

    context = ssl.create_default_context()
    # On Windows this includes the Windows certificate store. Add certifi as a
    # portable baseline without replacing system roots (including approved
    # enterprise proxy roots).
    context.load_verify_locations(cafile=certifi.where())
    ca_file = os.getenv("SSL_CERT_FILE")
    ca_dir = os.getenv("SSL_CERT_DIR")
    if ca_file:
        context.load_verify_locations(cafile=ca_file)
    if ca_dir:
        context.load_verify_locations(capath=ca_dir)
    return context


async def _call_coingecko(code: str) -> dict:
    """
    Execute TypeScript against the official CoinGecko MCP server.

    The MCP server exposes an `execute` tool which runs the supplied
    TypeScript against an initialized CoinGecko SDK client.
    """

    # Keep the provider SDK import at the network boundary so normalization
    # and resampling remain usable in isolated tests/offline analysis.
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    logger.debug("Opening CoinGecko MCP Streamable HTTP session url=%s", COINGECKO_MCP_URL)
    try:
        ssl_context = _build_tls_context()
        http_client = httpx2.AsyncClient(
            verify=ssl_context,
            # CoinGecko's MCP currently sends the tool result over POST but
            # rejects the optional long-lived GET event stream. Keep the read
            # timeout bounded so stream cleanup cannot stall scans for minutes.
            timeout=httpx2.Timeout(30.0, read=30.0),
        )
        logger.debug(
            "Using platform and supplemental CA roots for CoinGecko MCP TLS verification "
            "ssl_cert_file_configured=%s ssl_cert_dir_configured=%s",
            bool(os.getenv("SSL_CERT_FILE")), bool(os.getenv("SSL_CERT_DIR")),
        )
        async with http_client:
            async with streamable_http_client(
                COINGECKO_MCP_URL,
                http_client=http_client,
            ) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    logger.debug("Initializing CoinGecko MCP session")
                    await session.initialize()
                    logger.debug("Calling CoinGecko MCP execute tool")
                    result = await session.call_tool(
                        "execute",
                        arguments={
                            "code": code,
                            "intent": "Fetch historical OHLC market data for trading-agent analysis",
                        },
                    )
                    text_parts = [item.text for item in result.content if hasattr(item, "text")]
                    raw_text = "\n".join(text_parts).strip()
                    # Python MCP SDK models expose protocol `isError` as `is_error`.
                    if getattr(result, "is_error", getattr(result, "isError", False)):
                        detail = raw_text or "MCP execute returned an error without text details."
                        raise RuntimeError(f"CoinGecko MCP execute failed: {detail}")
                    if not result.content:
                        raise RuntimeError("CoinGecko MCP returned an empty response.")
                    if not text_parts:
                        raise RuntimeError("CoinGecko MCP returned no text content.")
                    try:
                        response = json.loads(raw_text)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(
                            f"Unable to parse CoinGecko MCP response as JSON: {exc}; "
                            f"response_prefix={raw_text[:240]!r}"
                        ) from exc
                    logger.debug("CoinGecko MCP execute response parsed")
                    return response
    except Exception:
        logger.exception("CoinGecko MCP session/tool request failed")
        raise


async def _fetch_ohlc(
    coin_id: str,
    start_date: str,
    end_date: str,
    interval: str,
    vs_currency: str = "usd",
) -> Any:
    """
    Fetch OHLC data from CoinGecko through MCP.
    """

    logger.info(
        "Requesting CoinGecko OHLC coin_id=%s interval=%s currency=%s from=%s to=%s",
        coin_id, interval, vs_currency, start_date, end_date,
    )

    coin_id_js = json.dumps(coin_id)
    currency_js = json.dumps(vs_currency)
    interval_js = json.dumps(interval)
    start_js = json.dumps(start_date)
    end_js = json.dumps(end_date)
    code = f"""
async function run(client) {{
    const response = await client.coins.ohlc.getRange(
        {coin_id_js},
        {{
            vs_currency: {currency_js},
            from: {start_js},
            to: {end_js},
            interval: {interval_js}
        }}
    );
    
    return response;
}}
"""

    response = await _call_coingecko(code)
    logger.info("CoinGecko OHLC response received coin_id=%s interval=%s", coin_id, interval)

    return response


async def _fetch_ohlc_batch(requests: list[dict[str, str]]) -> dict[str, Any]:
    """Fetch requested ranges in one MCP session and one execute call."""
    if not requests:
        return {}
    request_json = json.dumps(requests)
    code = f"""
async function run(client) {{
    const requests = {request_json};
    const results = {{}};
    for (const request of requests) {{
        const interval = request.interval as "daily" | "hourly";
        results[request.key] = await client.coins.ohlc.getRange(
            request.coin_id,
            {{ vs_currency: request.vs_currency, from: request.from,
               to: request.to, interval }}
        );
    }}
    return results;
}}
"""
    response = await _call_coingecko(code)
    # MCP execute responses wrap returned values in `result`.
    if isinstance(response, dict) and isinstance(response.get("result"), dict):
        response = response["result"]
    if not isinstance(response, dict):
        raise ValueError("CoinGecko MCP batch response was not an object.")
    return response


def _cache_paths(coin_id: str, currency: str, interval: str) -> tuple[Path, Path]:
    stem = f"{coin_id}_{currency.lower()}_{interval}"
    return CACHE_DIR / f"{stem}.csv", CACHE_DIR / f"{stem}.json"


def _read_cache(coin_id: str, currency: str, interval: str, timeframe: str):
    data_path, meta_path = _cache_paths(coin_id, currency, interval)
    if not data_path.exists():
        return _empty_candles(coin_id, currency, timeframe), {}
    try:
        frame = pd.read_csv(data_path, parse_dates=["timestamp"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        for column in NORMALIZED_CANDLE_COLUMNS[1:]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        frame = frame[NORMALIZED_CANDLE_COLUMNS].sort_values("timestamp").reset_index(drop=True)
        return _attach_candle_metadata(
            frame, coin_id=coin_id, vs_currency=currency, timeframe=timeframe,
            origin="cached_provider_ohlc", volume_available=False,
        ), metadata
    except Exception:
        logger.exception("Unable to read CoinGecko cache coin_id=%s interval=%s", coin_id, interval)
        return _empty_candles(coin_id, currency, timeframe), {}


def _write_cache(frame: pd.DataFrame, coin_id: str, currency: str, interval: str, metadata: dict) -> None:
    data_path, meta_path = _cache_paths(coin_id, currency, interval)
    data_path.parent.mkdir(parents=True, exist_ok=True)
    # Replace temporary files atomically so an interrupted write won't corrupt the cache.
    data_tmp, meta_tmp = data_path.with_suffix(".csv.tmp"), meta_path.with_suffix(".json.tmp")
    frame[NORMALIZED_CANDLE_COLUMNS].to_csv(data_tmp, index=False)
    meta_tmp.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    os.replace(data_tmp, data_path)
    os.replace(meta_tmp, meta_path)


def _refresh_due(metadata: dict, interval: str, now: datetime) -> bool:
    last_ok = metadata.get("last_success_utc")
    if not last_ok:
        return True
    try:
        checked = datetime.fromisoformat(last_ok)
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    if interval == "daily":
        return checked.astimezone(timezone.utc).date() < now.date()
    return checked.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) < now.replace(minute=0, second=0, microsecond=0)


def _range_end(now: datetime, interval: str) -> str:
    """Return an endpoint that includes the latest completed hourly candle."""
    if interval == "hourly":
        completed_hour = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        return completed_hour.strftime("%Y-%m-%dT%H:%M")
    return now.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _merge_candles(cached: pd.DataFrame, fetched: pd.DataFrame, max_days: int, now: datetime) -> pd.DataFrame:
    merged = pd.concat([cached, fetched], ignore_index=True)
    if merged.empty:
        return merged
    merged["timestamp"] = pd.to_datetime(merged["timestamp"], utc=True)
    merged = merged.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    cutoff = pd.Timestamp(now - timedelta(days=max_days))
    return merged.loc[merged["timestamp"] >= cutoff, NORMALIZED_CANDLE_COLUMNS].reset_index(drop=True)


def _date_chunks(start: datetime, end: datetime, max_days: int = 31) -> list[tuple[str, str]]:
    """Split an inclusive calendar-date request into bounded, non-overlapping ranges."""
    chunks = []
    cursor = start.date()
    final_day = end.date()
    while cursor <= final_day:
        chunk_end = min(cursor + timedelta(days=max_days - 1), final_day)
        chunks.append((cursor.isoformat(), chunk_end.isoformat()))
        cursor = chunk_end + timedelta(days=1)
    return chunks


async def backfill_hourly_history(
    coin_id: str,
    hourly_days: int = 90,
    vs_currency: str = "usd",
    delay_seconds: int = 20,
) -> pd.DataFrame:
    """Seed/extend one asset's hourly cache using bounded CoinGecko ranges."""
    if hourly_days < 1:
        raise ValueError("hourly_days must be at least 1")
    if delay_seconds < 0:
        raise ValueError("delay_seconds cannot be negative")
    now = datetime.now(timezone.utc)
    cached, metadata = _read_cache(coin_id, vs_currency, "hourly", "1h")
    target_start = now - timedelta(days=hourly_days)
    if not cached.empty:
        cached_start = pd.Timestamp(cached["timestamp"].min()).to_pydatetime()
        if cached_start <= target_start:
            logger.info("Hourly history already covers target coin_id=%s rows=%d", coin_id, len(cached))
            return cached
        # Overlap one date at the oldest cached boundary to avoid a gap.
        target_start = min(target_start, cached_start - timedelta(days=1))
    chunks = _date_chunks(target_start, now, max_days=31)
    logger.info("Hourly backfill started coin_id=%s chunks=%d days=%d", coin_id, len(chunks), hourly_days)
    frames = []
    for index, (from_date, to_date) in enumerate(chunks):
        if index:
            logger.info("Waiting %d seconds before next CoinGecko hourly range coin_id=%s", delay_seconds, coin_id)
            await asyncio.sleep(delay_seconds)
        raw = await _fetch_ohlc(coin_id, from_date, to_date, "hourly", vs_currency)
        frames.append(_ohlc_to_dataframe(raw, coin_id=coin_id, vs_currency=vs_currency, timeframe="1h"))
    fetched = pd.concat(frames, ignore_index=True) if frames else _empty_candles(coin_id, vs_currency, "1h")
    merged = _merge_candles(cached, fetched, max(hourly_days, 31), now)
    stamp = now.isoformat()
    _write_cache(merged, coin_id, vs_currency, "hourly", {
        **metadata,
        "last_success_utc": stamp,
        "last_attempt_utc": stamp,
        "hourly_backfill_days": hourly_days,
    })
    logger.info("Hourly backfill completed coin_id=%s rows=%d", coin_id, len(merged))
    return _attach_candle_metadata(
        merged, coin_id=coin_id, vs_currency=vs_currency, timeframe="1h",
        origin="cached_provider_ohlc", volume_available=False,
    )


def _ohlc_to_dataframe(
    data: Any,
    *,
    coin_id: str = "unknown",
    vs_currency: str = "usd",
    timeframe: str = "unknown",
) -> pd.DataFrame:
    """
    Convert CoinGecko MCP OHLC response into a pandas DataFrame.

    CoinGecko MCP returns:
    {
        "result": [
            [timestamp, open, high, low, close],
            ...
        ]
    }
    """

    if not data:
        return _empty_candles(coin_id, vs_currency, timeframe)

    # CoinGecko MCP wraps the actual candles inside "result"
    if isinstance(data, dict) and "result" in data:
        data = data["result"]

    if not isinstance(data, list):
        raise ValueError(
            f"Unexpected CoinGecko OHLC response type: {type(data)}"
        )

    if not data:
        return _empty_candles(coin_id, vs_currency, timeframe)

    if any(not isinstance(row, (list, tuple)) or len(row) != 5 for row in data):
        raise ValueError(
            "CoinGecko OHLC rows must contain exactly timestamp, open, high, low, close."
        )

    df = pd.DataFrame(
        data,
        columns=["timestamp", "open", "high", "low", "close"],
    )

    # CoinGecko timestamps are milliseconds since Unix epoch
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True, errors="coerce")

    # OHLC values should be numeric
    for column in ["open", "high", "low", "close"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    # OHLC endpoint does not provide volume
    # CoinGecko's OHLC range response does not include volume.
    df["volume"] = float("nan")

    df = df.dropna(
        subset=["timestamp", "open", "high", "low", "close"]
    )

    df = df.sort_values("timestamp")
    df = df.drop_duplicates(subset=["timestamp"])
    df = df.reset_index(drop=True)
    logger.info(
        "Normalized OHLC coin_id=%s timeframe=%s rows=%d dropped_rows=%d",
        coin_id, timeframe, len(df), len(data) - len(df),
    )

    return _attach_candle_metadata(
        df,
        coin_id=coin_id,
        vs_currency=vs_currency,
        timeframe=timeframe,
        origin="provider_ohlc",
        volume_available=False,
    )


def _attach_candle_metadata(
    df: pd.DataFrame,
    *,
    coin_id: str,
    vs_currency: str,
    timeframe: str,
    origin: str,
    volume_available: bool,
    resampling: dict | None = None,
) -> pd.DataFrame:
    """Attach source semantics without adding fabricated market columns."""
    df.attrs["market_data"] = {
        "source": "coingecko_mcp",
        "source_url": COINGECKO_MCP_URL,
        "coin_id": coin_id,
        "quote_currency": vs_currency.lower(),
        "timeframe": timeframe,
        "timestamp_timezone": "UTC",
        "candle_origin": origin,
        "volume_available": volume_available,
        "resampling": resampling,
    }
    return df


def _empty_candles(coin_id: str, vs_currency: str, timeframe: str) -> pd.DataFrame:
    return _attach_candle_metadata(
        pd.DataFrame(columns=NORMALIZED_CANDLE_COLUMNS),
        coin_id=coin_id,
        vs_currency=vs_currency,
        timeframe=timeframe,
        origin="provider_ohlc",
        volume_available=False,
    )


async def get_market_data(
    coin_id: str = "bitcoin",
    asset_symbol: str = "BTC",
    daily_days: int = 180,
    hourly_days: int = 31,
    vs_currency: str = "usd",
) -> dict:
    """Load cached OHLC and refresh only ranges that may have new candles.

    CoinGecko's range API accepts calendar dates, so incremental refreshes
    overlap the newest cached date and de-duplicate by candle timestamp.
    Daily data is checked once per UTC day and hourly data once per UTC hour.
    The 4H candles continue to be derived locally from the hourly cache.
    """
    now = datetime.now(timezone.utc)
    end_date = now.strftime("%Y-%m-%d")
    logger.info("Market data load started coin_id=%s currency=%s", coin_id, vs_currency)

    intervals = [
        ("daily", "1d", daily_days),
        ("hourly", "1h", hourly_days),
    ]
    states = {}
    requests = []
    for interval, timeframe, max_days in intervals:
        cached, cache_meta = _read_cache(coin_id, vs_currency, interval, timeframe)
        retry_until = cache_meta.get("retry_after_utc")
        in_cooldown = False
        if retry_until:
            try:
                in_cooldown = datetime.fromisoformat(retry_until) > now
            except ValueError:
                pass
        due = len(cached) == 0 or _refresh_due(cache_meta, interval, now)
        states[interval] = {
            "frame": cached, "metadata": cache_meta, "timeframe": timeframe,
            "max_days": max_days,
            "stale": in_cooldown and bool(cache_meta.get("last_error")),
            "refreshed": False,
        }
        if not due or in_cooldown:
            logger.info(
                "CoinGecko cache reused coin_id=%s interval=%s rows=%d reason=%s",
                coin_id, interval, len(cached), "retry_cooldown" if in_cooldown else "fresh",
            )
            continue

        if cached.empty:
            start_date = (now - timedelta(days=max_days)).strftime("%Y-%m-%d")
        else:
            # API range boundaries are date-based. A one-day overlap safely
            # captures updates to the latest candle while avoiding full history.
            newest = pd.Timestamp(cached["timestamp"].max()).to_pydatetime()
            start_date = (newest - timedelta(days=1)).strftime("%Y-%m-%d")
        requests.append({
            "key": interval, "coin_id": coin_id, "vs_currency": vs_currency,
            "from": start_date, "to": _range_end(now, interval), "interval": interval,
        })

    if requests:
        try:
            logger.info("Refreshing CoinGecko cache coin_id=%s intervals=%s", coin_id, [r["interval"] for r in requests])
            fetched_ranges = await _fetch_ohlc_batch(requests)
            for request in requests:
                interval = request["interval"]
                state = states[interval]
                raw = fetched_ranges.get(interval)
                if raw is None:
                    raise ValueError(f"CoinGecko batch omitted {interval} data for {coin_id}.")
                fresh = _ohlc_to_dataframe(
                    raw, coin_id=coin_id, vs_currency=vs_currency,
                    timeframe=state["timeframe"],
                )
                persist_provider_candles(
                    fresh,
                    coin_id=coin_id,
                    symbol=asset_symbol,
                    quote_currency=vs_currency,
                    timeframe=state["timeframe"],
                    requested_start=request["from"],
                    requested_end=request["to"],
                    started_at=now,
                )
                merged = _merge_candles(state["frame"], fresh, state["max_days"], now)
                success = now.isoformat()
                cache_meta = {"last_success_utc": success, "last_attempt_utc": success}
                _write_cache(merged, coin_id, vs_currency, interval, cache_meta)
                state["frame"], state["metadata"] = merged, cache_meta
                state["refreshed"] = True
                logger.info("CoinGecko cache updated coin_id=%s interval=%s rows=%d", coin_id, interval, len(merged))
        except Exception as exc:
            if isinstance(exc, DatabasePersistenceError):
                raise
            logger.exception("CoinGecko refresh failed; checking cached fallback coin_id=%s", coin_id)
            retry_after = (now + timedelta(seconds=60)).isoformat()
            for request in requests:
                state = states[request["interval"]]
                if state["frame"].empty:
                    raise
                state["stale"] = True
                state["metadata"]["retry_after_utc"] = retry_after
                state["metadata"]["last_error"] = str(exc)
                _write_cache(state["frame"], coin_id, vs_currency, request["interval"], state["metadata"])
                logger.warning("Using cached CoinGecko data coin_id=%s interval=%s after refresh failure", coin_id, request["interval"])

    daily_df = states["daily"]["frame"]
    hourly_df = states["hourly"]["frame"]
    _attach_candle_metadata(
        daily_df, coin_id=coin_id, vs_currency=vs_currency, timeframe="1d",
        origin="cached_provider_ohlc" if not states["daily"]["stale"] else "stale_cached_provider_ohlc",
        volume_available=False,
    )
    _attach_candle_metadata(
        hourly_df, coin_id=coin_id, vs_currency=vs_currency, timeframe="1h",
        origin="cached_provider_ohlc" if not states["hourly"]["stale"] else "stale_cached_provider_ohlc",
        volume_available=False,
    )
    logger.info("Market data load completed coin_id=%s daily_rows=%d hourly_rows=%d", coin_id, len(daily_df), len(hourly_df))

    freshness = {}
    for key, state in states.items():
        frame = state["frame"]
        latest_candle = pd.Timestamp(frame["timestamp"].max()) if not frame.empty else None
        freshness[key] = {
            "refresh_status": (
                "stale_cache_fallback" if state["stale"]
                else "refreshed" if state["refreshed"]
                else "cache_reused"
            ),
            "latest_candle_utc": (
                latest_candle.tz_convert("UTC").isoformat().replace("+00:00", "Z")
                if latest_candle is not None else None
            ),
            "latest_candle_age_hours": (
                round(max(0.0, (now - latest_candle.to_pydatetime()).total_seconds() / 3600), 2)
                if latest_candle is not None else None
            ),
            "last_success_utc": state["metadata"].get("last_success_utc"),
        }
    has_stale_fallback = any(
        item["refresh_status"] == "stale_cache_fallback" for item in freshness.values()
    )
    logger.log(
        logging.WARNING if has_stale_fallback else logging.INFO,
        "Market data freshness coin_id=%s status=%s daily=%s daily_latest_candle_utc=%s "
        "daily_last_success_utc=%s hourly=%s hourly_latest_candle_utc=%s hourly_last_success_utc=%s",
        coin_id,
        "stale_cache_fallback" if has_stale_fallback else "ok",
        freshness["daily"]["refresh_status"], freshness["daily"]["latest_candle_utc"], freshness["daily"]["last_success_utc"],
        freshness["hourly"]["refresh_status"], freshness["hourly"]["latest_candle_utc"], freshness["hourly"]["last_success_utc"],
    )

    return {
        "daily": daily_df,
        "hourly": hourly_df,
        "metadata": {
            "source": "coingecko_mcp",
            "coin_id": coin_id,
            "symbol": f"{asset_symbol.upper()}/{vs_currency.upper()}",
            "quote_currency": vs_currency.lower(),
            "ohlc_endpoint": "coins.ohlc.getRange",
            "volume_available": False,
            "timeframes": {"daily": "1d", "hourly": "1h"},
            "requested_history_days": {"daily": daily_days, "hourly": hourly_days},
            "cache_directory": str(CACHE_DIR),
            "cache_refresh_cadence": {"daily": "once per UTC day", "hourly": "once per UTC hour"},
            "stale_cache_fallback": {key: state["stale"] for key, state in states.items()},
            "data_freshness": freshness,
            "requested_dates_utc": {"end": end_date},
        },
    }


def resample_ohlc(
    df: pd.DataFrame,
    timeframe: str,
) -> pd.DataFrame:
    """
    Resample OHLC data locally.

    Currently used to create 4H candles from 1H candles.
    """

    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Cannot resample data missing columns: {sorted(missing)}")
    if df.empty:
        raise ValueError("Cannot resample an empty dataframe.")

    logger.debug("Resampling OHLC target_timeframe=%s source_rows=%d", timeframe, len(df))

    source = df.copy()
    source["timestamp"] = pd.to_datetime(source["timestamp"], utc=True)
    source = source.sort_values("timestamp").drop_duplicates("timestamp")
    interval = pd.Timedelta(timeframe)
    gaps = source["timestamp"].diff().dropna()
    if interval <= pd.Timedelta(0) or gaps.empty:
        raise ValueError("Resampling requires a positive target interval and multiple source candles.")
    source_interval = gaps.median()
    expected_rows = interval / source_interval
    if expected_rows <= 0 or not float(expected_rows).is_integer():
        raise ValueError(
            f"Target timeframe {timeframe!r} is not an integer multiple of the source cadence {source_interval}."
        )
    expected_rows = int(expected_rows)

    result = (
        source
        .set_index("timestamp")
        .resample(timeframe, origin="epoch", closed="left", label="left")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            # Keep unavailable volume as NaN instead of turning it into zero.
            volume=("volume", lambda values: values.sum(min_count=1)),
            source_candle_count=("open", "count"),
        )
        .loc[lambda bars: bars["source_candle_count"] == expected_rows]
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )

    metadata = df.attrs.get("market_data", {})
    _attach_candle_metadata(
        result,
        coin_id=metadata.get("coin_id", "unknown"),
        vs_currency=metadata.get("quote_currency", "unknown"),
        timeframe=timeframe,
        origin="resampled_provider_ohlc",
        volume_available=bool(
            metadata.get("volume_available", source["volume"].notna().any())
        ),
        resampling={
            "source_timeframe": metadata.get("timeframe", "unknown"),
            "rule": timeframe,
            "alignment": "UTC epoch; left-closed, left-labeled",
            "open": "first source open",
            "high": "maximum source high",
            "low": "minimum source low",
            "close": "last source close",
            "volume": "sum only when available; remains missing otherwise",
            "incomplete_buckets": "dropped",
        },
    )
    logger.info(
        "OHLC resampling completed target_timeframe=%s output_rows=%d source_rows=%d",
        timeframe, len(result), len(source),
    )

    return result
