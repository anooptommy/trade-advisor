from contextlib import closing

from app.database import connect_database, initialize_database
from risk.portfolio import evaluate_candidate_portfolio_risk, load_paper_portfolio_state


def _seed_portfolio(path):
    initialize_database(path)
    with closing(connect_database(path)) as connection:
        with connection:
            account_id = connection.execute(
                "INSERT INTO paper_accounts(account_key, display_name, base_currency, status) "
                "VALUES ('paper-test', 'Paper Test', 'USD', 'active')"
            ).lastrowid
            instrument_id = connection.execute(
                "INSERT INTO instruments(canonical_symbol, display_name) VALUES ('BTC/USD', 'Bitcoin')"
            ).lastrowid
            connection.execute(
                "INSERT INTO portfolio_snapshots(paper_account_id, captured_at_utc, equity) "
                "VALUES (?, '2026-10-10T10:00:00.000Z', '1000.00')",
                (account_id,),
            )
            for entry_type, amount in (("realized_pnl", "-5"), ("fee", "-1"), ("funding_payment", "0.5")):
                connection.execute(
                    "INSERT INTO portfolio_ledger(paper_account_id, entry_type, amount, currency, effective_at_utc) "
                    "VALUES (?, ?, ?, 'USD', '2026-10-10T10:30:00.000Z')",
                    (account_id, entry_type, amount),
                )
            for closed_at, realized in (
                ("2026-10-08T12:00:00.000Z", "4"),
                ("2026-10-09T12:00:00.000Z", "-2"),
                ("2026-10-10T09:00:00.000Z", "-3"),
            ):
                connection.execute(
                    """INSERT INTO positions(
                           paper_account_id, instrument_id, direction, status, opened_at_utc,
                           closed_at_utc, quantity, average_entry_price, realized_pnl
                       ) VALUES (?, ?, 'long', 'closed', '2026-10-08T11:00:00.000Z', ?, '1', '100', ?)""",
                    (account_id, instrument_id, closed_at, realized),
                )
            connection.execute(
                """INSERT INTO positions(
                       paper_account_id, instrument_id, direction, status, opened_at_utc,
                       quantity, average_entry_price
                   ) VALUES (?, ?, 'short', 'partially_closed', '2026-10-10T10:00:00.000Z', '1', '110')""",
                (account_id, instrument_id),
            )


def test_paper_portfolio_state_aggregates_measured_state_and_requires_fresh_equity(tmp_path):
    database = tmp_path / "portfolio.sqlite3"
    _seed_portfolio(database)

    state = load_paper_portfolio_state(
        "paper-test", as_of_utc="2026-10-10T11:00:00Z", max_snapshot_age_seconds=7200,
        database_path=database,
    )
    stale = load_paper_portfolio_state(
        "paper-test", as_of_utc="2026-10-10T11:00:00Z", max_snapshot_age_seconds=30,
        database_path=database,
    )

    assert state["status"] == "available"
    assert state["portfolio_state"] == {
        "account_equity": 1000.0,
        "daily_realized_pnl": -5.5,
        "open_position_count": 1,
        "consecutive_losses": 2,
    }
    assert state["daily_pnl_components"] == {
        "realized_pnl": -5.0, "fee": -1.0, "funding_payment": 0.5,
    }
    assert stale["status"] == "unavailable"
    assert stale["reason"] == "portfolio_equity_snapshot_is_stale"


def test_candidate_risk_composition_uses_portfolio_equity_and_never_authorizes_execution(tmp_path):
    database = tmp_path / "portfolio.sqlite3"
    _seed_portfolio(database)
    portfolio = load_paper_portfolio_state(
        "paper-test", as_of_utc="2026-10-10T11:00:00Z", max_snapshot_age_seconds=7200,
        database_path=database,
    )

    result = evaluate_candidate_portfolio_risk(
        portfolio_result=portfolio,
        candidate_eligibility={"status": "assessed", "matrix_result": "eligible"},
        sizing_policy={
            "risk_fraction": 0.01,
            "max_position_notional_fraction": 0.2,
            "max_leverage": 2.0,
        },
        controls={
            "max_daily_loss_fraction": 0.02,
            "max_open_positions": 2,
            "max_consecutive_losses": 3,
            "setup_ttl_minutes": 60,
        },
        side="long", entry_price=100, stop_price=99, liquidation_price=95,
        setup_age_minutes=5,
    )

    assert result["position_size"]["status"] == "assessed"
    assert result["position_size"]["account_equity"] == 1000.0
    assert result["governor"]["status"] == "approved_for_later_risk_review"
    assert result["execution_authorized"] is False


def test_candidate_risk_composition_fails_closed_without_portfolio_state():
    result = evaluate_candidate_portfolio_risk(
        portfolio_result={"status": "unavailable", "reason": "portfolio_equity_snapshot_missing"},
        candidate_eligibility={}, sizing_policy={}, controls={}, side="long",
        entry_price=100, stop_price=99, liquidation_price=95, setup_age_minutes=5,
    )

    assert result["status"] == "unavailable"
    assert result["portfolio_reason"] == "portfolio_equity_snapshot_missing"
    assert result["execution_authorized"] is False
