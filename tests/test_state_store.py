from financial_agent.store.state import get_state, set_state


def test_state_roundtrip(conn):
    assert get_state(conn, "summary_month") is None
    set_state(conn, "summary_month", "2026-10")
    set_state(conn, "summary_month", "2026-11")
    assert get_state(conn, "summary_month") == "2026-11"
