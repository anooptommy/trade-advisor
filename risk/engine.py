"""Configurable, analysis-only candidate position sizing."""

from __future__ import annotations

import math
from typing import Any


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _unavailable(reason: str) -> dict:
    return {
        "status": "unavailable",
        "analysis_only": True,
        "execution_authorized": False,
        "reason": reason,
    }


def calculate_candidate_position_size(
    *,
    side: str,
    entry_price: float,
    stop_price: float,
    account_equity: float,
    risk_fraction: float,
    max_position_notional_fraction: float,
    max_leverage: float,
    liquidation_price: float | None,
) -> dict:
    """Size a hypothetical position under explicit limits and a supplied liq price.

    No risk parameters have defaults. The liquidation price must come from a
    later account/venue-aware source; this function does not estimate it.
    """
    side = side.lower() if isinstance(side, str) else ""
    values = {
        "entry_price": _finite(entry_price),
        "stop_price": _finite(stop_price),
        "account_equity": _finite(account_equity),
        "risk_fraction": _finite(risk_fraction),
        "max_position_notional_fraction": _finite(max_position_notional_fraction),
        "max_leverage": _finite(max_leverage),
        "liquidation_price": _finite(liquidation_price),
    }
    if any(value is None for value in values.values()):
        return _unavailable("finite_account_price_limit_and_liquidation_inputs_required")
    entry = values["entry_price"]
    stop = values["stop_price"]
    equity = values["account_equity"]
    risk_fraction_value = values["risk_fraction"]
    notional_fraction = values["max_position_notional_fraction"]
    leverage_limit = values["max_leverage"]
    liquidation = values["liquidation_price"]
    if side not in ("long", "short"):
        return _unavailable("side_must_be_long_or_short")
    if entry <= 0 or stop <= 0 or equity <= 0 or liquidation <= 0:
        return _unavailable("positive_entry_stop_equity_and_liquidation_required")
    if not 0 < risk_fraction_value <= 1 or notional_fraction <= 0 or leverage_limit <= 0:
        return _unavailable("positive_risk_notional_and_leverage_limits_required")

    if side == "long":
        valid_stop = stop < entry
        liquidation_safe = liquidation < stop
    else:
        valid_stop = stop > entry
        liquidation_safe = liquidation > stop
    if not valid_stop:
        return {
            **_unavailable("stop_must_be_beyond_entry_in_the_loss_direction"),
            "status": "rejected",
        }
    if not liquidation_safe:
        return {
            **_unavailable("stop_must_be_reached_before_liquidation"),
            "status": "rejected",
        }

    stop_distance = abs(entry - stop)
    risk_budget = equity * risk_fraction_value
    risk_limited_quantity = risk_budget / stop_distance
    notional_limited_quantity = equity * notional_fraction / entry
    leverage_limited_quantity = equity * leverage_limit / entry
    quantity = min(risk_limited_quantity, notional_limited_quantity, leverage_limited_quantity)
    notional = quantity * entry
    effective_risk = quantity * stop_distance
    return {
        "status": "assessed",
        "analysis_only": True,
        "execution_authorized": False,
        "side": side,
        "quantity": quantity,
        "entry_price": entry,
        "stop_price": stop,
        "liquidation_price": liquidation,
        "stop_distance_per_unit": stop_distance,
        "account_equity": equity,
        "risk_budget": risk_budget,
        "effective_risk_amount": effective_risk,
        "effective_risk_fraction": effective_risk / equity,
        "notional": notional,
        "notional_fraction_of_equity": notional / equity,
        "effective_leverage": notional / equity,
        "quantity_limits": {
            "risk_budget": risk_limited_quantity,
            "position_notional": notional_limited_quantity,
            "leverage": leverage_limited_quantity,
        },
        "limitations": [
            "Sizing excludes fees, slippage, partial fills, funding, and correlated portfolio exposure.",
            "Liquidation safety uses the supplied price; no venue-specific liquidation estimate is calculated.",
            "Venue quantity precision, minimum order size, and notional filters are not checked.",
            "This result is hypothetical and does not authorize an order.",
        ],
    }
