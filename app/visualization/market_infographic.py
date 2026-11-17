"""Dark mobile dashboard rendered from real OHLC and deterministic snapshots.

This module only maps already-computed evidence for display and draws it. It
does not calculate or change setup eligibility, confidence, or risk decisions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import math
import re
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle
import pandas as pd
from app.advisor import setup_grade


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

    location = snapshot.get("htf_location") or {}
    if location.get("available"):
        confirmed = bool(location.get("confirmed"))
        factors.append(_factor("HTF Location", "CONFIRMED" if confirmed else "NOT CONFIRMED",
                               "neutral", location.get("reason", "HTF location evidence is available.")))
    else:
        factors.append(_factor("HTF Location", "N/A", "unavailable", "HTF location evidence unavailable."))

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
                              output_dir: str | Path, width: int = 1600,
                              height: int = 1200,
                              proximity_thresholds: tuple[float, float, float] = (0.01, 0.03, 0.08),
                              stale: bool = False, assessment: dict | None = None,
                              watch_conditions: list[str] | None = None) -> Path:
    """Render a PNG from a deterministic snapshot and indicator-enriched OHLC frames."""
    if width < 900 or height < 800:
        raise ValueError("Infographic dimensions must be at least 900x800 pixels.")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    symbol = str(snapshot.get("symbol") or "UNKNOWN")
    safe_symbol = _safe_filename(symbol.split("/")[0])
    path = output / f"{safe_symbol}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.png"
    fig = plt.figure(figsize=(width / 100, height / 100), dpi=100, facecolor=BG)
    grid = fig.add_gridspec(10, 12, left=.035, right=.975, top=.91, bottom=.045, hspace=.22, wspace=.18,
                            height_ratios=[1, 1, 1, 1, 1, 1, .72, .72, .72, .72])

    fig.text(.05, .976, f"{symbol}  •  HTF CONFLUENCE", color=TEXT, fontsize=21, weight="bold", va="top")
    price = snapshot.get("price")
    price_text = f"{float(price):,.6g}" if price is not None else "unavailable"
    stamp = str(snapshot.get("timestamp") or "unavailable")
    fig.text(.05, .952, f"Price  {price_text}     Analysis  {stamp} UTC" if not stamp.endswith("Z") else f"Price  {price_text}     Analysis  {stamp}", color=MUTED, fontsize=13, va="top")
    setup = snapshot.get("setup", "unavailable")
    grade = setup_grade(snapshot)
    grade_text = f" · GRADE {grade}" if grade else ""
    fig.text(.975, .976, f"{setup.replace('_', ' ').upper()}{grade_text}", color=GREEN if setup == "potential_long" else RED if setup == "potential_short" else AMBER, fontsize=16, weight="bold", ha="right", va="top")

    chart_specs = [("1d", "DAILY · HTF", 0), ("4h", "4H · SWING", 1), ("1h", "1H · ENTRY", 2)]
    htf_structures = [("D", snapshot.get("daily_structure") or {}, "#c792ea", "-"),
                      ("4H", snapshot.get("four_hour_structure") or {}, "#36a4ff", "--")]
    for key, title, column in chart_specs:
        ax = fig.add_subplot(grid[:6, column*4:(column+1)*4])
        ax.set_facecolor(PANEL)
        frame = chart_data.get(key)
        if frame is None or frame.empty:
            ax.text(.5, .5, "OHLC data unavailable", transform=ax.transAxes, color=GRAY, ha="center", va="center")
        else:
            visible = frame.tail(70 if key == "1d" else 90)
            times = pd.to_datetime(visible["timestamp"], utc=True, errors="coerce")
            x_values = mdates.date2num(times.dt.to_pydatetime())
            interval_days = {"1d": 1.0, "4h": 4/24, "1h": 1/24}[key]
            candle_width = interval_days * .68
            for x, (_, row) in zip(x_values, visible.iterrows()):
                values = [row.get(name) for name in ("open", "high", "low", "close")]
                if any(value is None or pd.isna(value) for value in values):
                    continue
                open_price, high_price, low_price, close_price = map(float, values)
                candle_color = GREEN if close_price >= open_price else RED
                ax.vlines(x, low_price, high_price, color=candle_color, linewidth=.7, alpha=.9)
                body_height = max(abs(close_price-open_price), max(abs(close_price)*.00015, 1e-10))
                body = Rectangle((x-candle_width/2, min(open_price, close_price)),
                                 candle_width, body_height,
                                 facecolor=candle_color, edgecolor=candle_color, linewidth=.45)
                ax.add_patch(body)
            ax.plot(x_values, visible["close"], color=TEXT, linewidth=.55, alpha=.35, label="Close")
            for column, color, label in (("ema9", GREEN, "EMA 9"), ("ema21", BLUE, "EMA 21"), ("sma50", AMBER, "SMA 50")):
                if column in visible and visible[column].notna().any():
                    ax.plot(x_values, visible[column], color=color, linewidth=.85, alpha=.9, label=label)
            low_bound, high_bound = float(visible["low"].min()), float(visible["high"].max())
            for timeframe, structure, level_color, linestyle in htf_structures:
                for kind, marker in (("support", "S"), ("resistance", "R")):
                    for level in structure.get(kind, [])[:3]:
                        level_price = level.get("price")
                        if level_price is None or not low_bound <= float(level_price) <= high_bound:
                            continue
                        ax.axhline(float(level_price), color=level_color, linewidth=1.2, alpha=.78,
                                   linestyle=linestyle, zorder=1)
                        touches = level.get("touches")
                        touch_text = f" · {touches} pivots" if touches is not None else ""
                        ax.text(.015, float(level_price), f"{timeframe} {marker} {float(level_price):,.5g}{touch_text}",
                                transform=ax.get_yaxis_transform(), color=level_color, fontsize=8,
                                ha="left", va="bottom", clip_on=True,
                                bbox={"facecolor": PANEL, "edgecolor": "none", "alpha": .82, "pad": 1.2})
            ax.xaxis_date(tz=timezone.utc)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b", tz=timezone.utc))
            ax.tick_params(axis="x", labelsize=9, colors=MUTED, pad=2)
            ax.tick_params(axis="y", labelsize=9, colors=MUTED, pad=2)
            ax.legend(loc="upper left", fontsize=9, ncol=2, frameon=False, labelcolor=MUTED)
        ax.set_title(title, color=TEXT, loc="left", fontsize=14, weight="bold", pad=4)
        ax.grid(color=BORDER, linewidth=.5, alpha=.7)
        for spine in ax.spines.values(): spine.set_color(BORDER)

    factors = build_factor_statuses(snapshot, stale=stale, proximity_thresholds=proximity_thresholds)
    factors_by_label = {item["label"]: item for item in factors}
    daily_state = (snapshot.get("daily_structure") or {}).get("structure", "N/A")
    four_state = (snapshot.get("four_hour_structure") or {}).get("structure", "N/A")
    bias = (snapshot.get("context") or {}).get("daily_context", "unavailable")
    trend_1h = ((snapshot.get("one_hour") or {}).get("trend") or {}).get("trend", "unavailable")
    one_hour = snapshot.get("one_hour") or {}
    momentum = one_hour.get("momentum") or {}
    rsi = momentum.get("rsi")
    macd, macd_signal = one_hour.get("macd"), one_hour.get("macd_signal")

    def factor_line(label: str, *, detail: bool = False) -> str:
        item = factors_by_label.get(label)
        if not item:
            return f"{label}: unavailable"
        value = f"{label}: {item['status']}"
        if detail and item.get("detail"):
            value += f" · {item['detail']}"
        return textwrap.shorten(value, width=105, placeholder="…")

    htf_lines = [
        factor_line("Daily Trend", detail=True),
        factor_line("4H Trend", detail=True),
        factor_line("HTF Location", detail=True),
        factor_line("Liquidity Sweep"),
    ]
    nearest = next((item.get("level") for item in factors if item.get("level")), None)
    if nearest:
        htf_lines.append(f"Nearest {nearest['type']}: {float(nearest['price']):,.6g}")

    rsi_text = f"RSI {float(rsi):.1f}" if isinstance(rsi, (int, float)) else "RSI unavailable"
    macd_text = "MACD unavailable" if macd is None or macd_signal is None else (
        f"MACD {'above' if macd > macd_signal else 'below' if macd < macd_signal else 'at'} signal"
    )
    btsm_lines = [
        f"Bias: {bias}",
        f"Trend: Daily {((snapshot.get('daily') or {}).get('trend') or {}).get('trend', 'unavailable')} · 4H {((snapshot.get('four_hour') or {}).get('trend') or {}).get('trend', 'unavailable')} · 1H {trend_1h}",
        f"Swing: Daily {daily_state} · 4H {four_state}",
        f"Momentum: {rsi_text} · {macd_text}",
        factor_line("1H Structure Confirmation"),
    ]

    panels = []
    for col, title, lines in (
        (0, "HTF CONFLUENCE", htf_lines),
        (4, "BTSM", btsm_lines),
    ):
        panel = fig.add_subplot(grid[6:, col:col+4])
        panel.set_facecolor(PANEL); panel.axis("off")
        panel.text(.04, .94, title, transform=panel.transAxes, color=TEXT,
                   fontsize=14, weight="bold", va="top")
        if title == "BTSM":
            panel.text(.04, .84, "Bias · Trend · Swing · Momentum", transform=panel.transAxes,
                       color=BLUE, fontsize=10, weight="bold", va="top")
        panels.append(panel)

    htf_ax, btsm_ax = panels
    for ax, lines in ((htf_ax, htf_lines), (btsm_ax, btsm_lines)):
        step = min(.16, .68 / max(1, len(lines)))
        for idx, line in enumerate(lines):
            ax.text(.05, .75 - idx*step, textwrap.fill(str(line), width=52),
                    transform=ax.transAxes, color=MUTED, fontsize=11, va="top", linespacing=1.25)

    watch_ax = fig.add_subplot(grid[6:, 8:])
    watch_ax.set_facecolor(PANEL); watch_ax.axis("off")
    watch_ax.text(.04, .94, "PRICE ACTION TO WATCH", transform=watch_ax.transAxes,
                  color=TEXT, fontsize=14, weight="bold", va="top")
    watch_lines = []
    for scenario in (assessment or {}).get("scenarios", [])[:2]:
        direction = str(scenario.get("direction", "wait")).upper()
        trigger = scenario.get("trigger", "Trigger unavailable")
        invalidation = scenario.get("stop_or_invalidation", "")
        watch_lines.append(f"{direction}: {trigger}" + (f" · Invalidate: {invalidation}" if invalidation else ""))
    if not watch_lines:
        watch_lines = list(watch_conditions or [])[:2]
    if not watch_lines:
        fallback = (assessment or {}).get("wait_conditions")
        watch_lines = [fallback] if fallback else ["No specific conditional trigger is supported by this snapshot."]
    for idx, line in enumerate(watch_lines[:2]):
        watch_ax.text(.04, .78-idx*.34, textwrap.fill(textwrap.shorten(str(line), width=145, placeholder="…"), width=52),
                      transform=watch_ax.transAxes, color=MUTED, fontsize=11, va="top", linespacing=1.25)

    fig.text(.035, .018, "CoinGecko OHLC · no provider volume · HTF levels use historical swing reversals", color=GRAY, fontsize=9)
    fig.savefig(path, dpi=100, facecolor=BG, format="png")
    plt.close(fig)
    return path
