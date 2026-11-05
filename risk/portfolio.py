"""Read paper-account state and compose an explicit analysis-only risk review."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, time, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.database import DEFAULT_DATABASE_PATH, connect_database
from risk.engine import calculate_candidate_position_size
from risk.governor import evaluate_risk_governor


_PNL_ENTRY_TYPES = {"realized_pnl", "fee", "funding_payment"}


def _utc_datetime(value: datetime | str) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    return value.astimezone(timezone.utc)


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _unavailable(reason: str) -> dict:
    return {
        "status": "unavailable",
        "analysis_only": True,
        "execution_authorized": False,
        "reason": reason,
    }


def load_paper_portfolio_state(
    account_key: str,
    *,
    as_of_utc: datetime | str,
    max_snapshot_age_seconds: float,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> dict:
    """Load measured account state without applying any risk-policy defaults.

    Equity comes from the latest portfolio snapshot at or before ``as_of_utc``.
    A caller-supplied snapshot age limit is mandatory; stale account state fails
    closed rather than reaching the risk governor.
    Daily net realized P&L is the UTC-day sum of realized P&L, fees, and funding
    ledger entries in the account's base currency. Open positions include both
    open and partially closed positions. The loss streak uses closed-position
    realized P&L, newest first; a non-loss ends the streak.
    """
    as_of = _utc_datetime(as_of_utc)
    max_age = _decimal(max_snapshot_age_seconds)
    if not isinstance(account_key, str) or not account_key.strip() or as_of is None:
        return _unavailable("account_key_and_timezone_aware_as_of_time_required")
    if max_age is None or max_age <= 0:
        return _unavailable("positive_portfolio_snapshot_age_limit_required")
    if not Path(database_path).is_file():
        return _unavailable("paper_portfolio_database_missing")

    day_start = datetime.combine(as_of.date(), time.min, tzinfo=timezone.utc)
    as_of_text = as_of.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    day_start_text = day_start.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    with closing(connect_database(database_path)) as connection:
        account = connection.execute(
            "SELECT id, account_key, base_currency, status FROM paper_accounts WHERE account_key = ?",
            (account_key.strip(),),
        ).fetchone()
        if account is None:
            return _unavailable("paper_account_not_found")
        if account["status"] != "active":
            return _unavailable("paper_account_not_active")

        snapshot = connection.execute(
            """SELECT captured_at_utc, equity
               FROM portfolio_snapshots
               WHERE paper_account_id = ? AND equity IS NOT NULL
                 AND julianday(captured_at_utc) <= julianday(?)
               ORDER BY julianday(captured_at_utc) DESC, id DESC LIMIT 1""",
            (account["id"], as_of_text),
        ).fetchone()
        if snapshot is None:
            return _unavailable("portfolio_equity_snapshot_missing")
        equity = _decimal(snapshot["equity"])
        if equity is None or equity <= 0:
            return _unavailable("latest_portfolio_equity_must_be_finite_and_positive")
        captured_at = _utc_datetime(snapshot["captured_at_utc"])
        if captured_at is None:
            return _unavailable("portfolio_equity_snapshot_timestamp_is_invalid")
        snapshot_age_seconds = (as_of - captured_at).total_seconds()
        if snapshot_age_seconds < 0 or Decimal(str(snapshot_age_seconds)) > max_age:
            return _unavailable("portfolio_equity_snapshot_is_stale")

        ledger_rows = connection.execute(
            """SELECT entry_type, amount, currency
               FROM portfolio_ledger
               WHERE paper_account_id = ?
                 AND julianday(effective_at_utc) >= julianday(?)
                 AND julianday(effective_at_utc) <= julianday(?)""",
            (account["id"], day_start_text, as_of_text),
        ).fetchall()
        pnl = {entry_type: Decimal("0") for entry_type in _PNL_ENTRY_TYPES}
        for row in ledger_rows:
            if row["currency"] != account["base_currency"]:
                return _unavailable("daily_ledger_contains_non_base_currency_without_conversion")
            if row["entry_type"] == "adjustment":
                return _unavailable("daily_ledger_contains_adjustment_with_undefined_pnl_treatment")
            if row["entry_type"] not in _PNL_ENTRY_TYPES:
                continue
            amount = _decimal(row["amount"])
            if amount is None:
                return _unavailable("daily_pnl_ledger_contains_invalid_amount")
            pnl[row["entry_type"]] += amount

        open_count = connection.execute(
            """SELECT COUNT(*) FROM positions
               WHERE paper_account_id = ?
                 AND julianday(opened_at_utc) <= julianday(?)
                 AND (
                   status IN ('open', 'partially_closed')
                   OR (status = 'closed' AND julianday(closed_at_utc) > julianday(?))
                 )""",
            (account["id"], as_of_text, as_of_text),
        ).fetchone()[0]
        closed_positions = connection.execute(
            """SELECT realized_pnl FROM positions
               WHERE paper_account_id = ? AND status = 'closed'
                 AND julianday(closed_at_utc) <= julianday(?)
               ORDER BY julianday(closed_at_utc) DESC, id DESC""",
            (account["id"], as_of_text),
        ).fetchall()

    consecutive_losses = 0
    for row in closed_positions:
        result = _decimal(row["realized_pnl"])
        if result is None:
            return _unavailable("closed_position_has_invalid_realized_pnl")
        if result < 0:
            consecutive_losses += 1
        else:
            break

    net_daily_pnl = sum(pnl.values(), Decimal("0"))
    return {
        "status": "available",
        "analysis_only": True,
        "execution_authorized": False,
        "portfolio_state": {
            "account_equity": float(equity),
            "daily_realized_pnl": float(net_daily_pnl),
            "open_position_count": int(open_count),
            "consecutive_losses": consecutive_losses,
        },
        "account": {
            "account_key": account["account_key"],
            "base_currency": account["base_currency"],
        },
        "as_of_utc": as_of_text,
        "equity_snapshot_at_utc": snapshot["captured_at_utc"],
        "equity_snapshot_age_seconds": snapshot_age_seconds,
        "daily_pnl_components": {key: float(value) for key, value in pnl.items()},
        "daily_pnl_basis": "UTC-day realized_pnl + fee + funding_payment ledger entries in base currency",
        "loss_streak_basis": "closed positions' realized_pnl, newest first; zero or positive ends the streak",
    }


def evaluate_candidate_portfolio_risk(
    *,
    portfolio_result: dict,
    candidate_eligibility: dict,
    sizing_policy: dict,
    controls: dict,
    side: str,
    entry_price: float,
    stop_price: float,
    liquidation_price: float | None,
    setup_age_minutes: float,
    market_data_stale: bool = False,
    same_direction_cooldown_active: bool = False,
) -> dict:
    """Connect measured paper state to explicit sizing and governor policies."""
    if not isinstance(portfolio_result, dict) or portfolio_result.get("status") != "available":
        return {
            **_unavailable("reliable_paper_portfolio_state_required"),
            "portfolio_reason": portfolio_result.get("reason") if isinstance(portfolio_result, dict) else None,
        }
    state = portfolio_result.get("portfolio_state")
    if not isinstance(state, dict):
        return _unavailable("paper_portfolio_state_payload_missing")

    policy = sizing_policy if isinstance(sizing_policy, dict) else {}
    position_size = calculate_candidate_position_size(
        side=side,
        entry_price=entry_price,
        stop_price=stop_price,
        account_equity=state.get("account_equity"),
        risk_fraction=policy.get("risk_fraction"),
        max_position_notional_fraction=policy.get("max_position_notional_fraction"),
        max_leverage=policy.get("max_leverage"),
        liquidation_price=liquidation_price,
    )
    governor = evaluate_risk_governor(
        candidate_eligibility=candidate_eligibility,
        position_size=position_size,
        portfolio_state=state,
        controls=controls,
        setup_age_minutes=setup_age_minutes,
        market_data_stale=market_data_stale,
        same_direction_cooldown_active=same_direction_cooldown_active,
    )
    return {
        "status": governor["status"],
        "analysis_only": True,
        "execution_authorized": False,
        "eligibility": candidate_eligibility,
        "portfolio_state": portfolio_result,
        "position_size": position_size,
        "governor": governor,
    }
