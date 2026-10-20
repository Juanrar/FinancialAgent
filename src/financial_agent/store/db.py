import re
import sqlite3
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

_MIGRATION_RE = re.compile(r"^(\d+)_.+\.sql$")


class NotFoundError(LookupError):
    """No existe el registro pedido."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _apply_script(conn: sqlite3.Connection, number: int, sql: str) -> None:
    """Aplica un script y sube user_version en una sola transacción."""
    try:
        conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def migrate(conn: sqlite3.Connection) -> int:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    scripts = []
    for entry in files("financial_agent.store.migrations").iterdir():
        match = _MIGRATION_RE.match(entry.name)
        if match:
            scripts.append((int(match.group(1)), entry))
    for number, entry in sorted(scripts, key=lambda s: s[0]):
        if number <= current:
            continue
        _apply_script(conn, number, entry.read_text(encoding="utf-8"))
        current = number
    return current
