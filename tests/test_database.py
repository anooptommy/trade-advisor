import json
from contextlib import closing

from app.database import connect_database, initialize_database


def test_initialize_database_creates_schema_and_is_idempotent(tmp_path):
    path = tmp_path / "db" / "paper.sqlite3"

    assert initialize_database(path) == path
    assert initialize_database(path) == path

    with closing(connect_database(path)) as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        migration_count = connection.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0]
        foreign_keys_enabled = connection.execute("PRAGMA foreign_keys").fetchone()[0]

    assert migration_count == 3
    assert foreign_keys_enabled == 1
    assert {
        "instruments", "candles", "scan_runs", "scan_results", "trade_suggestions",
        "risk_assessments", "advisor_assessments", "paper_accounts", "orders", "fills", "positions",
        "portfolio_ledger", "portfolio_snapshots", "portfolio_marks",
    }.issubset(tables)


def test_portfolio_snapshot_preserves_unmodeled_paper_api_fields(tmp_path):
    path = initialize_database(tmp_path / "paper.sqlite3")
    provider_payload = {
        "cash": "1000.00",
        "margin_summary": {"cross": "250.00"},
        "future_api_field": "preserve-me",
    }
    with closing(connect_database(path)) as connection:
        with connection:
            account = connection.execute(
                "INSERT INTO paper_accounts(account_key, display_name, base_currency, status) "
                "VALUES ('paper-main', 'Paper Main', 'USD', 'active')"
            ).lastrowid
            connection.execute(
                """INSERT INTO portfolio_snapshots(
                       paper_account_id, captured_at_utc, provider_payload_json
                   ) VALUES (?, '2026-10-07T00:00:00.000Z', ?)""",
                (account, json.dumps(provider_payload)),
            )
            row = connection.execute(
                "SELECT cash_balance, equity, provider_payload_json, schema_version "
                "FROM portfolio_snapshots WHERE paper_account_id = ?",
                (account,),
            ).fetchone()

    assert row["cash_balance"] is None
    assert row["equity"] is None
    assert row["schema_version"] == 1
    assert json.loads(row["provider_payload_json"]) == provider_payload
