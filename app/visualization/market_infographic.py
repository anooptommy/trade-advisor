"""Dark mobile dashboard rendered from real OHLC and deterministic snapshots.

This module only maps already-computed evidence for display and draws it. It
does not calculate or change setup eligibility, confidence, or risk decisions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import math
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import pandas as pd


BG = "#07111d"
PANEL = "#0c1a29"
BORDER = "#203348"
TEXT = "#e7eef8"
MUTED = "#9aadc2"
GREEN = "#14c784"
AMBER = "#f5b82e"
RED = "#f45b69"
GRAY = "#8190a2"
BLUE = "#36a4ff"


def proximity(price: float | None, level: float | None,
              thresholds: tuple[float, float, float] = (0.01, 0.03, 0.08)) -> dict:
    """Classify absolute percentage distance to one valid price level."""
    if price is None or level is None or not math.isfinite(float(price)) or not math.isfinite(float(level)) or float(price) <= 0 or float(level) <= 0:
        return {"label": "N/A", "distance_pct": None}
    distance = abs(float(price) - float(level)) / float(price)
    at, close, approaching = thresholds
    label = "AT LEVEL" if distance <= at else "CLOSE" if distance <= close else "APPROACHING" if distance <= approaching else "FAR"
    return {"label": label, "distance_pct": distance * 100}


def _factor(label: str, status: str, direction: str, detail: str,
            prox: dict | None = None) -> dict:
    return {"label": label, "status": status, "direction": direction,
            "detail": detail, "proximity": prox or {"label": "N/A", "distance_pct": None},
            "change": "CHANGE UNKNOWN"}


def build_factor_statuses(snapshot: dict, *, stale: bool = False,
                          proximity_thresholds: tuple[float, float, float] = (0.01, 0.03, 0.08)) -> list[dict]:
    """Map only existing rule/structure evidence into the dashboard's eight rows."""
    daily, four, one = (snapshot.get(k) or {} for k in ("daily", "four_hour", "one_hour"))
    ds, fs = snapshot.get("daily_structure") or {}, snapshot.get("four_hour_structure") or {}
    entry = snapshot.get("entry_confirmation") or {}
    confirms = entry.get("confirmations") or {}
    price = snapshot.get("price")

    def trend_row(label, timeframe, structure):
        trend = (timeframe.get("trend") or {}).get("trend", "unknown")
        if trend not in ("bullish", "bearish", "neutral"):
            return _factor(label, "N/A", "unavailable", "Trend indicators unavailable.")
        struct = structure.get("structure", "unknown")
        return _factor(label, trend.upper(), trend,
                       f"Trend {trend}; swing structure {struct}.")

    factors = [trend_row("Daily Trend", daily, ds), trend_row("4H Trend", four, fs)]

    quality = snapshot.get("level_quality") or {}
    rating = quality.get("rating") if quality.get("rating_available") else None
    if rating:
        factors.append(_factor("HTF Level Quality", str(rating).upper(), "neutral",
                               f"{quality.get('observed_points', 0)} observed points; {quality.get('coverage_pct', 0)}% evidence coverage."))
    else:
        factors.append(_factor("HTF Level Quality", "N/A", "unavailable", "Existing level-quality rating unavailable."))

    sweep = confirms.get("liquidity_sweep") or {}
    if sweep.get("available"):
        confirmed = bool(sweep.get("confirmed"))
        evidence = "Closed 1H candle swept and reclaimed a directional 4H level." if confirmed else "No qualifying 1H sweep/reclaim found at available 4H levels."
        sweep_direction = (snapshot.get("context") or {}).get("daily_context", "neutral")
        factors.append(_factor("Liquidity Sweep", "CONFIRMED" if confirmed else "ABSENT", sweep_direction if confirmed and sweep_direction in ("bullish", "bearish") else "neutral", evidence))
    else:
        factors.append(_factor("Liquidity Sweep", "N/A", "unavailable", "Required directional 4H level data unavailable."))

    structure = confirms.get("structure_break") or {}
    if structure.get("available"):
        passed = bool(structure.get("confirmed"))
        direction = (snapshot.get("context") or {}).get("daily_context", "neutral") if passed else "neutral"
        factors.append(_factor("1H Structure Confirmation", "CONFIRMED" if passed else "NOT CONFIRMED", direction, "Closed 1H close broke the latest qualifying swing." if passed else "No qualifying closed 1H swing break."))
    else:
        factors.append(_factor("1H Structure Confirmation", "N/A", "unavailable", "Required 1H swing data unavailable."))

    trend = (one.get("trend") or {}).get("trend", "unknown")
    if all(one.get(k) is not None for k in ("ema9", "ema21", "sma50")) and trend in ("bullish", "bearish", "neutral"):
        price_text = f"{float(price):g}" if price is not None else "unavailable"
        factors.append(_factor("EMA/SMA Confluence", trend.upper(), trend, f"Price {price_text}; EMA 9 {one['ema9']:g}, EMA 21 {one['ema21']:g}, SMA 50 {one['sma50']:g}."))
    else:
        factors.append(_factor("EMA/SMA Confluence", "N/A", "unavailable", "One or more 1H moving averages unavailable."))

    rsi = (one.get("momentum") or {}).get("rsi")
    macd, signal = one.get("macd"), one.get("macd_signal")
    if rsi is not None and macd is not None and signal is not None:
        rsi_dir = "bullish" if rsi >= 50 else "bearish"
        macd_dir = "bullish" if macd > signal else "bearish" if macd < signal else "neutral"
        direction = rsi_dir if rsi_dir == macd_dir else "mixed"
        factors.append(_factor("RSI/MACD Confirmation", direction.upper(), direction, f"RSI {rsi:g}; MACD {'above' if macd > signal else 'below' if macd < signal else 'at'} signal."))
    else:
        factors.append(_factor("RSI/MACD Confirmation", "N/A", "unavailable", "RSI or MACD evidence unavailable."))

    structure_states = (ds.get("structure"), fs.get("structure"))
    if structure_states == ("bullish", "bullish"):
        regime, regime_dir, regime_detail = "TRENDING BULLISH", "bullish", "Daily and 4H swing structures are bullish."
    elif structure_states == ("bearish", "bearish"):
        regime, regime_dir, regime_detail = "TRENDING BEARISH", "bearish", "Daily and 4H swing structures are bearish."
    elif all(s in ("bullish", "bearish", "mixed") for s in structure_states):
        regime, regime_dir, regime_detail = "MIXED STRUCTURE", "neutral", "Daily and 4H structures do not agree."
    else:
        regime, regime_dir, regime_detail = "N/A", "unavailable", "No supported regime classification from available swing structures."
    factors.append(_factor("Market Regime", regime, regime_dir, regime_detail))

    # Show the closest available support/resistance distance alongside HTF quality.
    levels = [("support", item) for item in ds.get("support", []) + fs.get("support", [])]
    levels += [("resistance", item) for item in ds.get("resistance", []) + fs.get("resistance", [])]
    valid = [(kind, item) for kind, item in levels if isinstance(item, dict) and item.get("price") is not None]
    if valid and price is not None:
        kind, nearest = min(valid, key=lambda pair: abs(float(price)-float(pair[1]["price"])))
        factors[2]["proximity"] = proximity(price, nearest["price"], proximity_thresholds)
        factors[2]["detail"] += f" Nearest {kind} {float(nearest['price']):g} ({factors[2]['proximity']['distance_pct']:.2f}% away)."
        factors[2]["level"] = {"type": kind, "price": float(nearest["price"])}
    if stale:
        for item in factors:
            item["detail"] += " Source cache is stale."
    return factors


def _safe_filename(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", symbol).strip("_") or "asset"


def render_market_infographic(snapshot: dict, chart_data: dict[str, pd.DataFrame], *,
                              output_dir: str | Path, width: int = 1200,
                              height: int = 1600,
                              proximity_thresholds: tuple[float, float, float] = (0.01, 0.03, 0.08),
                              stale: bool = False) -> Path:
    """Render a PNG from a deterministic snapshot and indicator-enriched OHLC frames."""
    if width < 600 or height < 800:
        raise ValueError("Infographic dimensions must be at least 600x800 pixels.")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    symbol = str(snapshot.get("symbol") or "UNKNOWN")
    safe_symbol = _safe_filename(symbol.split("/")[0])
    path = output / f"{safe_symbol}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.png"
    fig = plt.figure(figsize=(width / 100, height / 100), dpi=100, facecolor=BG)
    grid = fig.add_gridspec(14, 12, left=.045, right=.955, top=.965, bottom=.035, hspace=.42, wspace=.32)

    fig.text(.05, .976, f"{symbol}  •  HTF CONFLUENCE", color=TEXT, fontsize=21, weight="bold", va="top")
    price = snapshot.get("price")
    price_text = f"{float(price):,.6g}" if price is not None else "unavailable"
    stamp = str(snapshot.get("timestamp") or "unavailable")
    fig.text(.05, .952, f"Price  {price_text}     Analysis  {stamp} UTC" if not stamp.endswith("Z") else f"Price  {price_text}     Analysis  {stamp}", color=MUTED, fontsize=10, va="top")
    setup = snapshot.get("setup", "unavailable")
    confidence = snapshot.get("confidence") or {}
    fraction = confidence.get("score_fraction")
    score = f"{float(fraction)*10:.1f}/10" if fraction is not None else "N/A"
    fig.text(.95, .976, f"{setup.replace('_', ' ').upper()}  •  SCORE {score}", color=GREEN if setup == "potential_long" else RED if setup == "potential_short" else AMBER, fontsize=11, weight="bold", ha="right", va="top")

    chart_specs = [("1d", "DAILY  ·  HTF", slice(0, 5)), ("4h", "4H  ·  SWING", slice(5, 10)), ("1h", "1H  ·  ENTRY STRUCTURE", slice(10, 14))]
    structures = {"1d": snapshot.get("daily_structure") or {}, "4h": snapshot.get("four_hour_structure") or {}, "1h": {}}
    for key, title, rows in chart_specs:
        ax = fig.add_subplot(grid[rows, :5])
        ax.set_facecolor(PANEL)
        frame = chart_data.get(key)
        if frame is None or frame.empty:
            ax.text(.5, .5, "OHLC data unavailable", transform=ax.transAxes, color=GRAY, ha="center", va="center")
        else:
            visible = frame.tail(70 if key == "1d" else 90)
            times = pd.to_datetime(visible["timestamp"], utc=True, errors="coerce")
            x_values = mdates.date2num(times.dt.to_pydatetime())
            ax.plot(x_values, visible["close"], color=TEXT, linewidth=1.35, label="OHLC close")
            for column, color, label in (("ema9", GREEN, "EMA 9"), ("ema21", BLUE, "EMA 21"), ("sma50", AMBER, "SMA 50")):
                if column in visible and visible[column].notna().any():
                    ax.plot(x_values, visible[column], color=color, linewidth=.85, alpha=.9, label=label)
            structure = structures[key]
            for level in structure.get("support", [])[:2]:
                ax.axhline(level["price"], color=GREEN, linewidth=.7, alpha=.55, linestyle="--")
            for level in structure.get("resistance", [])[:2]:
                ax.axhline(level["price"], color=RED, linewidth=.7, alpha=.55, linestyle="--")
            ax.xaxis_date(tz=timezone.utc)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b", tz=timezone.utc))
            ax.tick_params(axis="x", labelsize=7, colors=MUTED)
            ax.tick_params(axis="y", labelsize=7, colors=MUTED)
            ax.legend(loc="upper left", fontsize=7, ncol=2, frameon=False, labelcolor=MUTED)
        ax.set_title(title, color=TEXT, loc="left", fontsize=10, weight="bold", pad=8)
        ax.grid(color=BORDER, linewidth=.5, alpha=.7)
        for spine in ax.spines.values(): spine.set_color(BORDER)

    factors = build_factor_statuses(snapshot, stale=stale, proximity_thresholds=proximity_thresholds)
    ax = fig.add_subplot(grid[0:9, 5:])
    ax.set_facecolor(PANEL)
    ax.set_xlim(0, 1); ax.set_ylim(0, 8); ax.axis("off")
    ax.text(.02, 7.78, "HTF CONFLUENCE STATUS", color=TEXT, weight="bold", fontsize=12)
    counts = {state: sum(item["status"] == state for item in factors) for state in ("BULLISH", "BEARISH", "NEUTRAL", "N/A")}
    counts["BULLISH"] = sum(item["direction"] == "bullish" for item in factors)
    counts["BEARISH"] = sum(item["direction"] == "bearish" for item in factors)
    counts["NEUTRAL"] = sum(item["direction"] in ("neutral", "mixed") for item in factors)
    counts["N/A"] = sum(item["direction"] == "unavailable" for item in factors)
    for idx, item in enumerate(factors):
        y = 6.85 - idx * .91
        color = GREEN if item["direction"] == "bullish" else RED if item["direction"] == "bearish" else AMBER if item["direction"] in ("neutral", "mixed") else GRAY
        ax.add_patch(FancyBboxPatch((.015, y-.06), .97, .81, boxstyle="round,pad=0.008,rounding_size=.025", facecolor="#0a1623", edgecolor=BORDER, linewidth=.6))
        arrow = "↑" if item["direction"] == "bullish" else "↓" if item["direction"] == "bearish" else "→" if item["direction"] in ("neutral", "mixed") else "·"
        prox = item["proximity"]
        prox_label = prox["label"] + (f" {prox['distance_pct']:.1f}%" if prox["distance_pct"] is not None else "")
        ax.text(.04, y+.55, f"{arrow}  {item['label']}", color=TEXT, fontsize=9, weight="bold", va="center")
        ax.text(.98, y+.55, item["status"], color=color, fontsize=8, weight="bold", ha="right", va="center")
        ax.text(.05, y+.27, item["detail"][:125], color=MUTED, fontsize=7, va="center")
        ax.text(.05, y+.08, f"{prox_label}  ·  CHANGE UNKNOWN", color=GRAY, fontsize=6.5, va="center")
        ax.add_patch(FancyBboxPatch((.72, y+.045), .22, .075, boxstyle="round,pad=0.002,rounding_size=.025", facecolor="#263444", edgecolor="none"))
        progress, progress_color = {
            "AT LEVEL": (.98, GREEN), "CLOSE": (.72, GREEN),
            "APPROACHING": (.44, AMBER), "FAR": (.15, RED), "N/A": (0, GRAY),
        }[prox["label"]]
        if progress:
            ax.add_patch(FancyBboxPatch((.72, y+.045), .22*progress, .075, boxstyle="round,pad=0.002,rounding_size=.025", facecolor=progress_color, edgecolor="none"))

    ax = fig.add_subplot(grid[9:12, 5:])
    ax.set_facecolor(PANEL); ax.axis("off")
    ax.text(.04, .94, "SUMMARY  ·  ANALYSIS ONLY", transform=ax.transAxes, color=TEXT, fontsize=11, weight="bold", va="top")
    daily_state = (snapshot.get("daily_structure") or {}).get("structure", "N/A")
    four_state = (snapshot.get("four_hour_structure") or {}).get("structure", "N/A")
    ax.text(.04, .75, f"Daily / 4H alignment: {daily_state} / {four_state}", transform=ax.transAxes, color=MUTED, fontsize=9)
    ax.text(.04, .58, f"Factors  ↑ {counts['BULLISH']}   ↓ {counts['BEARISH']}   → {counts['NEUTRAL']}   · {counts['N/A']} unavailable", transform=ax.transAxes, color=TEXT, fontsize=9)
    score_context = f"Confidence evidence: {score}/10"
    if confidence.get("observed_points") is not None:
        score_context += f"  ({confidence.get('observed_points')}/{confidence.get('assessed_points')} points; {confidence.get('coverage_pct')}% coverage)"
    score_context += "  · score is not probability"
    ax.text(.04, .41, score_context, transform=ax.transAxes, color=MUTED, fontsize=8)
    conflicting = daily_state in ("bullish", "bearish") and four_state in ("bullish", "bearish") and daily_state != four_state
    confirming = next((item["label"] for item in factors if item["direction"] == ("bullish" if snapshot.get("setup") == "potential_long" else "bearish") and item["status"] not in ("N/A", "NOT CONFIRMED", "ABSENT")), "No directional confirmation available")
    nearest = factors[2].get("level")
    level_text = f"Nearest {nearest['type']} {nearest['price']:g}" if nearest else "Nearest level unavailable"
    conflict_text = "Daily/4H structures conflict" if conflicting else f"Strongest directional evidence: {confirming}"
    ax.text(.04, .25, f"{conflict_text}  ·  {level_text}", transform=ax.transAxes, color=AMBER if conflicting else MUTED, fontsize=8)
    ax.text(.04, .12, f"Rule eligible for further evaluation: {'YES' if snapshot.get('setup') in ('potential_long', 'potential_short') else 'NO'}", transform=ax.transAxes, color=GREEN if snapshot.get("setup") in ("potential_long", "potential_short") else AMBER, fontsize=9, weight="bold")
    ax.text(.04, .015, "No trade authorization. Review freshness and unresolved evidence.", transform=ax.transAxes, color=GRAY, fontsize=8)

    fig.text(.05, .022, "CoinGecko OHLC  •  no provider volume  •  levels from existing swing-structure analysis", color=GRAY, fontsize=7)
    fig.savefig(path, dpi=100, facecolor=BG, format="png")
    plt.close(fig)
    return path
