"""SQLite connection and versioned-schema initialization helpers."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "trading_agent.sqlite3"
MIGRATIONS_PATH = PROJECT_ROOT / "db" / "migrations"


def connect_database(path: str | Path = DEFAULT_DATABASE_PATH) -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys and a row mapping enabled."""
    connection = sqlite3.connect(Path(path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def initialize_database(path: str | Path = DEFAULT_DATABASE_PATH) -> Path:
    """Create the local database and apply each unapplied SQL migration once."""
    database_path = Path(path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    migrations = sorted(MIGRATIONS_PATH.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    if not migrations:
        raise RuntimeError(f"No database migrations found under {MIGRATIONS_PATH}")

    with closing(connect_database(database_path)) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   version TEXT PRIMARY KEY,
                   migration_name TEXT NOT NULL,
                   applied_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
               )"""
        )
        applied = {
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations")
        }
        for migration in migrations:
            version = migration.name.split("_", 1)[0]
            if version in applied:
                continue
            sql = migration.read_text(encoding="utf-8")
            try:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + sql
                    + "\nINSERT INTO schema_migrations(version, migration_name) VALUES ("
                    + "'" + version + "', '" + migration.name + "');\nCOMMIT;"
                )
            except Exception:
                connection.rollback()
                raise
        latest_version = max(int(path.name.split("_", 1)[0]) for path in migrations)
        connection.execute(f"PRAGMA user_version = {latest_version}")
        connection.commit()
    return database_path


if __name__ == "__main__":
    print(initialize_database())
