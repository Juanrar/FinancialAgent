import sqlite3
from importlib.resources import files

import pytest

from financial_agent.store.db import _apply_script, connect, migrate


def test_migrate_sets_version_and_is_idempotent(tmp_path):
    conn = connect(tmp_path / "x" / "t.db")
    assert migrate(conn) == 2
    assert migrate(conn) == 2
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


def test_migration_2_keeps_pending_actions(tmp_path):
    conn = connect(tmp_path / "v1.db")
    sql1 = files("financial_agent.store.migrations").joinpath("001_init.sql").read_text("utf-8")
    _apply_script(conn, 1, sql1)
    conn.execute("INSERT INTO pending_actions (kind, payload, description, status, created_at,"
                 " expires_at) VALUES ('set_primary', '{}', 'x', 'confirmada', 'a', 'b')")
    conn.commit()
    assert migrate(conn) == 2
    row = conn.execute("SELECT * FROM pending_actions").fetchone()
    assert (row["id"], row["status"], row["routine_id"], row["detail"]) == (1, "confirmada", None, None)
    conn.close()


def test_pending_actions_accept_new_values(conn):
    conn.execute("INSERT INTO pending_actions (kind, payload, description, status, created_at,"
                 " expires_at) VALUES ('transfer', '{}', 'x', 'sin_confirmar', 'a', 'b')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO pending_actions (kind, payload, description, status,"
                     " created_at, expires_at) VALUES ('otra', '{}', 'x', 'pendiente', 'a', 'b')")
