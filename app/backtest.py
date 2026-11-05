"""Offline historical replay of deterministic analysis; never calls market-data APIs."""

from __future__ import annotations

import argparse
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.market.data import resample_ohlc
from app.market.indicators import calculate_indicators
from app.strategy.market_structure import analyze_market_structure
from app.strategy.rules import evaluate_market

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WATCHLIST = ROOT / "config" / "watchlist.json"
DEFAULT_CACHE = ROOT / "data" / "cache" / "coingecko"
DEFAULT_OUTPUT = ROOT / "data" / "backtest"
logger = logging.getLogger(__name__)


class _IncrementalSwingState:
    """Track only the latest confirmed lookback-2 hourly pivots in one pass."""

    def __init__(self, frame: pd.DataFrame, lookback: int = 2) -> None:
        self.lookback = lookback
        self.highs = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype=float)
        self.lows = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype=float)
        self.latest_high: float | None = None
        self.latest_low: float | None = None

    def advance(self, index: int) -> dict:
        """Confirm the pivot whose final right-hand candle just closed."""
        pivot_index = index - self.lookback
        if pivot_index >= self.lookback:
            high = self.highs[pivot_index]
            low = self.lows[pivot_index]
            left_highs = self.highs[pivot_index - self.lookback:pivot_index]
            right_highs = self.highs[pivot_index + 1:index + 1]
            left_lows = self.lows[pivot_index - self.lookback:pivot_index]
            right_lows = self.lows[pivot_index + 1:index + 1]
            finite_highs = all(math.isfinite(value) for value in (*left_highs, *right_highs))
            finite_lows = all(math.isfinite(value) for value in (*left_lows, *right_lows))
            if math.isfinite(high) and finite_highs and high >= max(*left_highs, *right_highs):
                self.latest_high = float(high)
            if math.isfinite(low) and finite_lows and low <= min(*left_lows, *right_lows):
                self.latest_low = float(low)
        return {"latest_high": self.latest_high, "latest_low": self.latest_low}


def _load_candles(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Local candle cache is missing: {path}")
    frame = pd.read_csv(path)
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Cache file {path.name} lacks columns: {sorted(missing)}")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def _as_of(frame: pd.DataFrame, cutoff: pd.Timestamp, duration: pd.Timedelta) -> pd.DataFrame:
    """Keep only bars fully closed by the replay cutoff."""
    if frame.empty:
        return frame.copy()
    timestamps = pd.to_datetime(frame["timestamp"], utc=True)
    return frame.loc[timestamps + duration <= cutoff].reset_index(drop=True)


def _forward_metrics(hourly: pd.DataFrame, index: int, direction: str, horizon: int) -> dict:
    signal_close_time = pd.to_datetime(hourly.iloc[index]["timestamp"], utc=True) + pd.Timedelta(hours=1)
    expected = pd.date_range(signal_close_time, periods=horizon, freq="h")
    future = hourly.loc[pd.to_datetime(hourly["timestamp"], utc=True) >= signal_close_time].head(horizon)
    if len(future) != horizon or not pd.DatetimeIndex(pd.to_datetime(future["timestamp"], utc=True)).equals(expected):
        return {
            f"forward_{horizon}h_return_pct": None,
            f"favorable_excursion_{horizon}h_pct": None,
            f"adverse_excursion_{horizon}h_pct": None,
        }
    entry = float(hourly.iloc[index]["close"])
    exit_price = float(future.iloc[-1]["close"])
    if direction == "potential_long":
        signed_return = (exit_price / entry - 1) * 100
        favorable = (float(future["high"].max()) / entry - 1) * 100
        adverse = (1 - float(future["low"].min()) / entry) * 100
    else:
        signed_return = (entry - exit_price) / entry * 100 if entry else None
        favorable = (1 - float(future["low"].min()) / entry) * 100
        adverse = (float(future["high"].max()) / entry - 1) * 100
    return {
        f"forward_{horizon}h_return_pct": round(signed_return, 4) if signed_return is not None else None,
        f"favorable_excursion_{horizon}h_pct": round(favorable, 4),
        f"adverse_excursion_{horizon}h_pct": round(adverse, 4),
    }


def _summarize_events(events: list[dict], horizons: tuple[int, ...]) -> dict:
    result = {"candidate_count": len(events)}
    for horizon in horizons:
        key = f"forward_{horizon}h_return_pct"
        values = [event[key] for event in events if event.get(key) is not None]
        result[f"matured_{horizon}h_count"] = len(values)
        result[f"mean_directional_return_{horizon}h_pct"] = round(sum(values) / len(values), 4) if values else None
        result[f"positive_directional_return_{horizon}h_pct"] = round(sum(value > 0 for value in values) / len(values) * 100, 2) if values else None
        mfe_key = f"favorable_excursion_{horizon}h_pct"
        mae_key = f"adverse_excursion_{horizon}h_pct"
        mfe = [event[mfe_key] for event in events if event.get(mfe_key) is not None]
        mae = [event[mae_key] for event in events if event.get(mae_key) is not None]
        result[f"mean_favorable_excursion_{horizon}h_pct"] = round(sum(mfe) / len(mfe), 4) if mfe else None
        result[f"mean_adverse_excursion_{horizon}h_pct"] = round(sum(mae) / len(mae), 4) if mae else None
    fractions = [event["score_fraction"] for event in events if event.get("score_fraction") is not None]
    result["mean_score_fraction"] = round(sum(fractions) / len(fractions), 4) if fractions else None
    result["mean_score_coverage_pct"] = round(sum(event["coverage_pct"] for event in events) / len(events), 2) if events else None
    return result


def _summarize_by_structure_regime(events: list[dict], horizons: tuple[int, ...]) -> dict:
    """Stratify observed outcomes by the causal Daily/4H swing-structure pair."""
    groups: dict[str, list[dict]] = {}
    for event in events:
        daily_state = event.get("daily_structure") or "unknown"
        four_hour_state = event.get("four_hour_structure") or "unknown"
        key = f"daily_{daily_state}__4h_{four_hour_state}"
        groups.setdefault(key, []).append(event)
    return {key: _summarize_events(group, horizons) for key, group in sorted(groups.items())}


def _threshold_variant_events(events: list[dict], variant: str) -> list[dict]:
    """Re-score observed HTF evidence without changing setup eligibility."""
    results = []
    for event in events:
        raw = event.get("htf_threshold_evidence", {})
        current_points = int(event.get("level_quality_points") or 0)
        new_points = current_points
        factor_name = None
        confirmed = False
        available = False
        if variant.startswith("major_swing_atr_"):
            threshold = float(variant.rsplit("_", 1)[1])
            factor_name = "major_swing"
            evidence = raw.get(factor_name, {})
            available = bool(evidence.get("available"))
            confirmed = any(float(item.get("excursion_atr", 0)) >= threshold for item in evidence.get("candidate_interactions", []))
        elif variant.startswith("ema_sma_proximity_atr_"):
            threshold = float(variant.rsplit("_", 1)[1])
            factor_name = "ema_sma_confluence"
            evidence = raw.get(factor_name, {})
            available = bool(evidence.get("available"))
            confirmed = any(float(item.get("distance_atr", float("inf"))) <= threshold for item in evidence.get("proximity_evidence", []))
        elif variant.startswith("rejection_wick_"):
            threshold = float(variant.rsplit("_", 1)[1])
            factor_name = "strong_rejection"
            evidence = raw.get(factor_name, {})
            available = bool(evidence.get("available"))
            detail = evidence.get("evidence", {})
            close_pos = detail.get("close_position")
            is_long = event["direction"] == "potential_long"
            close_ok = close_pos is not None and (float(close_pos) >= 0.65 if is_long else float(close_pos) <= 0.35)
            confirmed = bool(detail.get("area_touched") and detail.get("close_rejected_area") and detail.get("directional_candle") and (detail.get("wick_to_body") or 0) >= threshold and close_ok)
        elif variant.startswith("rejection_close_edge_"):
            threshold = float(variant.rsplit("_", 1)[1])
            factor_name = "strong_rejection"
            evidence = raw.get(factor_name, {})
            available = bool(evidence.get("available"))
            detail = evidence.get("evidence", {})
            close_pos = detail.get("close_position")
            is_long = event["direction"] == "potential_long"
            close_ok = close_pos is not None and (float(close_pos) >= 1 - threshold if is_long else float(close_pos) <= threshold)
            confirmed = bool(detail.get("area_touched") and detail.get("close_rejected_area") and detail.get("directional_candle") and (detail.get("wick_to_body") or 0) >= 1.5 and close_ok)
        else:
            raise ValueError(f"Unknown threshold variant: {variant}")

        if available and factor_name:
            factor = raw[factor_name]
            weight = int(factor.get("points", 0))
            # A zero-point baseline still has its weight in the strategy definition.
            weight = {"strong_rejection": 2, "major_swing": 2, "ema_sma_confluence": 1}[factor_name]
            new_points += weight if confirmed else 0
            if factor.get("confirmed"):
                new_points -= weight
        adjusted = dict(event)
        assessed = int(event.get("assessed_confidence_points") or 0)
        observed = int(event.get("observed_confidence_points") or 0)
        adjusted["score_fraction"] = (observed - current_points + new_points) / assessed if assessed else None
        adjusted["threshold_variant_points"] = new_points
        adjusted["threshold_variant_factor_confirmed"] = confirmed if available else None
        results.append(adjusted)
    return results


def _score_filter_summary(
    events: list[dict],
    horizons: tuple[int, ...],
    cutoffs: tuple[float, ...] = (0.70, 0.75),
    coverage_floors: tuple[float, ...] = (85.0, 90.0, 95.0),
) -> dict:
    summaries = {}
    for coverage_floor in coverage_floors:
        for cutoff in cutoffs:
            selected = [
                event for event in events
                if event.get("score_fraction") is not None
                and event["score_fraction"] >= cutoff
                and event.get("coverage_pct") is not None
                and event["coverage_pct"] >= coverage_floor
            ]
            summary = _summarize_events(selected, horizons)
            summary["minimum_coverage_pct"] = coverage_floor
            summary["minimum_score_fraction"] = cutoff
            for horizon in horizons:
                summary[f"positive_{horizon}h_pct"] = summary.get(f"positive_directional_return_{horizon}h_pct")
            summaries[f"coverage_gte_{coverage_floor:.0f}_score_fraction_gte_{cutoff:.2f}"] = summary
    return summaries


def replay_asset(
    daily_raw: pd.DataFrame,
    hourly_raw: pd.DataFrame,
    *,
    symbol: str,
    coin_id: str,
    horizons: tuple[int, ...] = (24, 72),
) -> dict:
    """Replay one asset causally, recalculating structure from data known at each close."""
    if daily_raw.empty or hourly_raw.empty:
        raise ValueError("Replay requires non-empty cached Daily and hourly candles.")
    daily = calculate_indicators(daily_raw)
    hourly = calculate_indicators(hourly_raw)
    four_hour = calculate_indicators(resample_ohlc(hourly_raw, "4h"))
    daily_close_times = pd.to_datetime(daily["timestamp"], utc=True) + pd.Timedelta(days=1)
    four_hour_close_times = pd.to_datetime(four_hour["timestamp"], utc=True) + pd.Timedelta(hours=4)
    events: list[dict] = []
    strict_events: list[dict] = []
    structure_regime_candle_counts: dict[str, int] = {}
    previous_setup_by_threshold = {2: "no_setup", 3: "no_setup"}
    replayed_candles = 0
    daily_structure_cache: tuple[int, dict] | None = None
    four_hour_structure_cache: tuple[int, dict] | None = None
    daily_count = four_hour_count = 0
    hourly_swing_state = _IncrementalSwingState(hourly, lookback=2)

    for index, row in hourly.iterrows():
        latest_hourly_swings = hourly_swing_state.advance(index)
        cutoff = pd.to_datetime(row["timestamp"], utc=True) + pd.Timedelta(hours=1)
        while daily_count < len(daily_close_times) and daily_close_times.iloc[daily_count] <= cutoff:
            daily_count += 1
        while four_hour_count < len(four_hour_close_times) and four_hour_close_times.iloc[four_hour_count] <= cutoff:
            four_hour_count += 1
        asof_daily = daily.iloc[:daily_count]
        asof_four_hour = four_hour.iloc[:four_hour_count]
        # This replay cutoff is exactly the close of `row`, so its prefix is
        # already the complete hourly history available at that instant.
        asof_hourly = hourly.iloc[: index + 1]
        for frame in (asof_daily, asof_four_hour, asof_hourly):
            frame.attrs["all_candles_closed"] = True
        # Wait for enough Daily/4H bars to initialize the existing trend indicators.
        if len(asof_daily) < 50 or len(asof_four_hour) < 50 or asof_hourly.empty:
            continue
        # HTF bars only advance at their own close. Reuse the exact causal
        # snapshot until another complete candle is available.
        if daily_structure_cache is None or daily_structure_cache[0] != len(asof_daily):
            daily_structure_cache = (len(asof_daily), analyze_market_structure(asof_daily, timeframe="1d"))
        if four_hour_structure_cache is None or four_hour_structure_cache[0] != len(asof_four_hour):
            four_hour_structure_cache = (len(asof_four_hour), analyze_market_structure(asof_four_hour, timeframe="4h"))
        daily_structure = daily_structure_cache[1]
        four_hour_structure = four_hour_structure_cache[1]
        regime_key = (
            f"daily_{daily_structure.get('structure', 'unknown')}"
            f"__4h_{four_hour_structure.get('structure', 'unknown')}"
        )
        structure_regime_candle_counts[regime_key] = structure_regime_candle_counts.get(regime_key, 0) + 1
        snapshot = evaluate_market(
            daily_df=asof_daily,
            four_hour_df=asof_four_hour,
            one_hour_df=asof_hourly,
            daily_structure=daily_structure,
            four_hour_structure=four_hour_structure,
            symbol=f"{symbol}/USD",
            market_data_metadata={"source": "local_coingecko_cache", "coin_id": coin_id, "volume_available": False, "open_interest_available": False},
            swing_point_state=latest_hourly_swings,
        )
        replayed_candles += 1
        entry_confirmation = snapshot.get("entry_confirmation", {})
        confirmations = entry_confirmation.get("confirmations", {})
        confirmation_flags = {name: bool(value.get("confirmed")) for name, value in confirmations.items()}
        confirmed_count = sum(confirmation_flags.values())
        context = snapshot.get("context", {})
        daily_direction = context.get("daily_context")
        gates_pass = bool(
            context.get("context_aligned")
            and context.get("structure_aligned")
            and snapshot.get("htf_location", {}).get("confirmed")
            and daily_direction in ("bullish", "bearish")
        )
        setup_by_threshold = {
            2: snapshot["setup"],
            3: ("potential_long" if daily_direction == "bullish" else "potential_short") if gates_pass and confirmed_count >= 3 else "no_setup",
        }
        for required_count, setup in setup_by_threshold.items():
            previous_setup = previous_setup_by_threshold[required_count]
            if setup in ("potential_long", "potential_short") and setup != previous_setup:
                level_quality = snapshot.get("level_quality", {})
                factors = level_quality.get("factors", {})
                event = {
                    "symbol": symbol,
                    "coin_id": coin_id,
                    "timestamp": cutoff.isoformat(),
                    "direction": setup,
                    "entry_reference_close": float(row["close"]),
                    "observed_confidence_points": snapshot["confidence"].get("observed_points"),
                    "assessed_confidence_points": snapshot["confidence"].get("assessed_points"),
                    "score_fraction": snapshot["confidence"].get("score_fraction"),
                    "coverage_pct": snapshot["confidence"].get("coverage_pct"),
                    "level_quality_points": level_quality.get("observed_points"),
                    "level_quality_assessed_weight": level_quality.get("assessed_weight"),
                    "daily_structure": daily_structure.get("structure"),
                    "four_hour_structure": four_hour_structure.get("structure"),
                    "required_1h_confirmations": required_count,
                    "confirmed_1h_count": confirmed_count,
                    "confirmed_1h_rules": [name for name, confirmed in confirmation_flags.items() if confirmed],
                    "critical_setup_gates": {
                        "aligned_daily_4h_trend": bool(context.get("context_aligned")),
                        "aligned_daily_4h_structure": bool(context.get("structure_aligned")),
                        "confirmed_htf_location": bool(snapshot.get("htf_location", {}).get("confirmed")),
                        "minimum_1h_confirmations": confirmed_count >= 2,
                    },
                    "confidence_component_availability": {
                        name: bool(component.get("available"))
                        for name, component in snapshot.get("confidence", {}).get("components", {}).items()
                    },
                    "missing_confidence_inputs": snapshot.get("confidence", {}).get("missing_inputs", []),
                    "htf_threshold_evidence": {
                        name: factors.get(name, {})
                        for name in ("strong_rejection", "major_swing", "ema_sma_confluence")
                    },
                }
                for horizon in horizons:
                    event.update(_forward_metrics(hourly, index, setup, horizon))
                (events if required_count == 2 else strict_events).append(event)
            previous_setup_by_threshold[required_count] = setup

    timestamps = pd.to_datetime(hourly_raw["timestamp"], utc=True)
    return {
        "symbol": symbol,
        "coin_id": coin_id,
        "data_start": timestamps.min().isoformat(),
        "data_end": timestamps.max().isoformat(),
        "daily_candles": len(daily_raw),
        "hourly_candles": len(hourly_raw),
        "replayed_closed_hourly_candles": replayed_candles,
        "structure_regime_candle_counts": dict(sorted(structure_regime_candle_counts.items())),
        "method": "causal per-hour replay; indicators use trailing inputs; HTF structure is refreshed only when a new complete Daily or 4H candle closes",
        "outcome_limitations": "Directional forward returns and excursions only; no entry fill model, stops, targets, fees, slippage, position sizing, or P&L.",
        "summary": _summarize_events(events, horizons),
        "confirmation_scenarios": {
            "2_of_4": {"summary": _summarize_events(events, horizons), "event_count": len(events)},
            "3_of_4": {"summary": _summarize_events(strict_events, horizons), "event_count": len(strict_events)},
        },
        "structure_regime_summaries": {
            "2_of_4": _summarize_by_structure_regime(events, horizons),
            "3_of_4": _summarize_by_structure_regime(strict_events, horizons),
            "definition": "causal Daily and 4H confirmed swing-structure states at candidate time; groups with fewer than 5 events are descriptive only",
        },
        "htf_threshold_sweeps": {
            variant: {
                "all_events": _summarize_events(variant_events, horizons),
                "score_filters": _score_filter_summary(variant_events, horizons),
            }
            for variant in (
                "major_swing_atr_1.5", "major_swing_atr_2.5", "ema_sma_proximity_atr_0.50",
                "rejection_wick_1.0", "rejection_wick_2.0",
                "rejection_close_edge_0.25", "rejection_close_edge_0.45",
            )
            for variant_events in [_threshold_variant_events(events, variant)]
        },
        "events": events,
        "strict_events": strict_events,
    }


def run_replay(watchlist_path: Path = DEFAULT_WATCHLIST, output_dir: Path = DEFAULT_OUTPUT, selected_symbols: set[str] | None = None, horizons: tuple[int, ...] = (24, 72)) -> dict:
    with watchlist_path.open(encoding="utf-8") as stream:
        watchlist = json.load(stream)
    assets = [asset for asset in watchlist["assets"] if selected_symbols is None or asset["symbol"].upper() in selected_symbols]
    if selected_symbols is not None and {asset["symbol"].upper() for asset in assets} != selected_symbols:
        missing = selected_symbols - {asset["symbol"].upper() for asset in assets}
        raise ValueError(f"Unknown watchlist symbols: {sorted(missing)}")
    logging.getLogger("app.strategy.rules").setLevel(logging.WARNING)
    reports = []
    for asset in assets:
        prefix = f"{asset['coin_id']}_{watchlist.get('quote_currency', 'usd')}"
        try:
            daily = _load_candles(DEFAULT_CACHE / f"{prefix}_daily.csv")
            hourly = _load_candles(DEFAULT_CACHE / f"{prefix}_hourly.csv")
            report = replay_asset(daily, hourly, symbol=asset["symbol"].upper(), coin_id=asset["coin_id"], horizons=horizons)
            report["status"] = "completed"
        except Exception as exc:
            logger.exception("Offline replay failed symbol=%s", asset["symbol"])
            report = {"symbol": asset["symbol"].upper(), "coin_id": asset["coin_id"], "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        reports.append(report)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"historical_replay_{stamp}.json"
    csv_path = output_dir / f"historical_replay_{stamp}.csv"
    baseline_events = [event for report in reports for event in report.get("events", [])]
    strict_events = [event for report in reports for event in report.get("strict_events", [])]
    threshold_variants = (
        "major_swing_atr_1.5", "major_swing_atr_2.5", "ema_sma_proximity_atr_0.50",
        "rejection_wick_1.0", "rejection_wick_2.0",
        "rejection_close_edge_0.25", "rejection_close_edge_0.45",
    )
    payload = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "local cached CoinGecko OHLC; no network access",
        "horizons_hours": list(horizons),
        "evaluation": {
            "confirmation_scenarios": {
                "2_of_4": _summarize_events(baseline_events, horizons),
                "3_of_4": _summarize_events(strict_events, horizons),
            },
            "structure_regime_summaries": {
                "2_of_4": _summarize_by_structure_regime(baseline_events, horizons),
                "3_of_4": _summarize_by_structure_regime(strict_events, horizons),
                "definition": "causal Daily and 4H confirmed swing-structure states at candidate time; groups with fewer than 5 events are descriptive only",
            },
            "htf_threshold_sweeps": {
                variant: {
                    "all_events": _summarize_events(_threshold_variant_events(baseline_events, variant), horizons),
                    "score_filters": _score_filter_summary(_threshold_variant_events(baseline_events, variant), horizons),
                }
                for variant in threshold_variants
            },
        },
        "assets": reports,
    }
    json_path.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    event_rows = [event for report in reports for event in report.get("events", [])]
    pd.DataFrame(event_rows).to_csv(csv_path, index=False)
    return {"report": payload, "json_path": str(json_path), "csv_path": str(csv_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay the deterministic strategy against local cached candles (offline only).")
    parser.add_argument("--assets", help="Comma-separated watchlist symbols; default is all configured assets.")
    parser.add_argument("--horizons", default="24,72", help="Comma-separated forward horizons in hours (default: 24,72).")
    args = parser.parse_args()
    symbols = {item.strip().upper() for item in args.assets.split(",") if item.strip()} if args.assets else None
    horizons = tuple(int(item) for item in args.horizons.split(",") if item.strip())
    if not horizons or any(value <= 0 for value in horizons):
        parser.error("--horizons must contain positive hour counts.")
    result = run_replay(selected_symbols=symbols, horizons=horizons)
    for asset in result["report"]["assets"]:
        if asset["status"] == "completed":
            strict_count = asset["confirmation_scenarios"]["3_of_4"]["event_count"]
            print(f"{asset['symbol']}: 2-of-4={asset['summary']['candidate_count']} setups; 3-of-4={strict_count}; "+", ".join(f"{h}h mean directional return={asset['summary'][f'mean_directional_return_{h}h_pct']}% ({asset['summary'][f'matured_{h}h_count']} matured)" for h in horizons))
        else:
            print(f"{asset['symbol']}: failed — {asset['error']}")
    print(f"Detailed JSON: {result['json_path']}")
    print(f"Candidate CSV: {result['csv_path']}")


if __name__ == "__main__":
    main()
