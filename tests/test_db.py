import sqlite3

import pytest

from financial_agent.store.db import _apply_script, connect, migrate


def test_migrate_sets_version_and_is_idempotent(tmp_path):
    conn = connect(tmp_path / "x" / "t.db")
    assert migrate(conn) == 1
    assert migrate(conn) == 1
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_failed_migration_leaves_no_partial_state(tmp_path):
    conn = connect(tmp_path / "t.db")
    broken = "CREATE TABLE a (id INTEGER);\nCREATE TABLE a (id INTEGER);"
    with pytest.raises(sqlite3.OperationalError):
        _apply_script(conn, 7, broken)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'a'").fetchone() is None
    conn.close()
