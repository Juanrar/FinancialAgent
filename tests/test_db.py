from financial_agent.store.db import connect, migrate


def test_migrate_sets_version_and_is_idempotent(tmp_path):
    conn = connect(tmp_path / "x" / "t.db")
    assert migrate(conn) == 1
    assert migrate(conn) == 1
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()
