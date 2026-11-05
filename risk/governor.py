"""Independent analysis-only portfolio and candidate veto controls."""

from __future__ import annotations

import math
from typing import Any


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def evaluate_risk_governor(
    *,
    candidate_eligibility: dict,
    position_size: dict,
    portfolio_state: dict,
    controls: dict,
    setup_age_minutes: float,
    market_data_stale: bool = False,
    same_direction_cooldown_active: bool = False,
) -> dict:
    """Apply configured hard vetoes; approval is never execution authorization.

    Daily-loss limits, position limits, loss-streak limits, and setup TTL are
    required policy inputs. This module intentionally supplies no defaults.
    """
    if market_data_stale:
        return {
            "status": "vetoed",
            "approved_for_later_risk_review": False,
            "execution_authorized": False,
            "veto_reasons": ["stale_market_data"],
        }
    if not all(isinstance(value, dict) for value in (candidate_eligibility, position_size, portfolio_state, controls)):
        return _unavailable("candidate_portfolio_and_policy_objects_required")

    required_state = ("account_equity", "daily_realized_pnl", "open_position_count", "consecutive_losses")
    required_controls = ("max_daily_loss_fraction", "max_open_positions", "max_consecutive_losses", "setup_ttl_minutes")
    if any(key not in portfolio_state for key in required_state) or any(key not in controls for key in required_controls):
        return _unavailable("explicit_portfolio_state_and_governor_limits_required")

    state = {key: _number(portfolio_state.get(key)) for key in required_state}
    limits = {key: _number(controls.get(key)) for key in required_controls}
    age = _number(setup_age_minutes)
    if any(value is None for value in state.values()) or any(value is None for value in limits.values()) or age is None:
        return _unavailable("finite_governor_inputs_required")
    equity = state["account_equity"]
    max_daily_loss = limits["max_daily_loss_fraction"]
    max_open = limits["max_open_positions"]
    max_losses = limits["max_consecutive_losses"]
    ttl = limits["setup_ttl_minutes"]
    if equity <= 0 or not 0 < max_daily_loss <= 1 or max_open < 1 or max_losses < 1 or ttl <= 0:
        return _unavailable("positive_equity_and_valid_governor_limits_required")
    if (
        not state["open_position_count"].is_integer()
        or not state["consecutive_losses"].is_integer()
        or not max_open.is_integer()
        or not max_losses.is_integer()
        or age < 0
    ):
        return _unavailable("position_and_loss_counts_must_be_integers_and_setup_age_nonnegative")
    if any(state[key] < 0 for key in ("open_position_count", "consecutive_losses")):
        return _unavailable("portfolio_counts_cannot_be_negative")

    vetoes = []
    if candidate_eligibility.get("status") != "assessed" or candidate_eligibility.get("matrix_result") != "eligible":
        vetoes.append("candidate_not_eligible_under_confidence_rr_matrix")
    if position_size.get("status") != "assessed":
        vetoes.append("position_size_not_assessed")
    daily_loss_fraction = max(0.0, -state["daily_realized_pnl"] / equity)
    if daily_loss_fraction >= max_daily_loss:
        vetoes.append("daily_loss_limit_reached")
    if state["open_position_count"] >= max_open:
        vetoes.append("maximum_open_positions_reached")
    if state["consecutive_losses"] >= max_losses:
        vetoes.append("maximum_consecutive_losses_reached")
    if age > ttl:
        vetoes.append("setup_expired")
    if same_direction_cooldown_active:
        vetoes.append("same_direction_cooldown_active")

    return {
        "status": "vetoed" if vetoes else "approved_for_later_risk_review",
        "approved_for_later_risk_review": not vetoes,
        "execution_authorized": False,
        "veto_reasons": vetoes,
        "daily_loss_fraction": daily_loss_fraction,
        "setup_age_minutes": age,
        "limitations": [
            "This governor does not authorize execution or send orders.",
            "Position count, loss streak, daily P&L, and cooldown state must come from a reliable portfolio journal.",
            "Exposure aggregation, correlated risk, volatility vetoes, and loss-streak recovery rules remain unimplemented.",
        ],
    }


def _unavailable(reason: str) -> dict:
    return {
        "status": "unavailable",
        "approved_for_later_risk_review": False,
        "execution_authorized": False,
        "reason": reason,
        "veto_reasons": [],
    }
