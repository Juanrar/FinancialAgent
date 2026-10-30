import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from telegram.error import NetworkError

from financial_agent.store.contacts import add_destination, create_contact, set_verification
from financial_agent.store.db import connect, migrate
from financial_agent.store.pending import claim_transfer, create_action, get_action

from financial_agent.app import create_app
from financial_agent.logs import setup_logging


def test_health_and_migrations(settings):
    app = create_app(settings, with_bot=False)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
    assert settings.db_path.exists()


def test_app_starts_without_agent_browser(settings, monkeypatch):
    monkeypatch.setattr("financial_agent.app.find_binary", lambda configured: None)
    app = create_app(settings, with_bot=False)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def _record_wallets(monkeypatch):
    from financial_agent.fiwind.client import FiwindClient

    calls = []

    def make_wallet(*args, **kwargs):
        calls.append(args)
        return FiwindClient(*args, **kwargs)

    monkeypatch.setattr("financial_agent.app.find_binary", lambda configured: "agent-browser")
    monkeypatch.setattr("financial_agent.app.FiwindClient", make_wallet)
    return calls


def test_app_passes_fiwind_credentials_and_hides_password(settings, monkeypatch, capsys):
    from dataclasses import replace

    from financial_agent.fiwind.client import Credentials

    calls = _record_wallets(monkeypatch)
    active = replace(settings, fiwind_email="juan@mail.com", fiwind_password="clave-123")
    with TestClient(create_app(active, with_bot=False)):
        logging.getLogger("prueba").warning("falló con clave-123")
    assert calls[0][1] == Credentials("juan@mail.com", "clave-123")
    assert "clave-123" not in capsys.readouterr().out


def test_app_without_fiwind_password_has_no_credentials(settings, monkeypatch):
    from dataclasses import replace

    calls = _record_wallets(monkeypatch)
    with TestClient(create_app(replace(settings, fiwind_email="juan@mail.com"), with_bot=False)):
        pass
    assert calls[0][1] is None


def test_app_warns_when_configured_binary_is_missing(settings, tmp_path, capsys):
    from dataclasses import replace

    missing = replace(settings, agent_browser_bin=str(tmp_path / "no-existe.CMD"))
    with TestClient(create_app(missing, with_bot=False)) as client:
        assert client.get("/health").status_code == 200
    assert "agent-browser no está instalado" in capsys.readouterr().out


def test_setup_logging_silences_httpx():
    setup_logging("INFO")
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_setup_logging_redacts_secrets_in_messages_and_tracebacks(capsys):
    setup_logging("INFO", ["SECRETO123"])
    logger = logging.getLogger("telegram.ext")
    logger.info("url con SECRETO123 adentro")
    try:
        raise ValueError("falló con SECRETO123")
    except ValueError:
        logger.exception("error con %s", "SECRETO123")
    out = capsys.readouterr().out
    assert "SECRETO123" not in out
    assert "***" in out
    assert "ValueError" in out


def test_setup_logging_debug_keeps_libraries_quiet():
    setup_logging("DEBUG")
    try:
        assert logging.getLogger().getEffectiveLevel() == logging.DEBUG
        assert logging.getLogger("telegram").getEffectiveLevel() >= logging.INFO
        assert not logging.getLogger("telegram.ext").isEnabledFor(logging.DEBUG)
        assert logging.getLogger("claude_agent_sdk").getEffectiveLevel() == logging.CRITICAL
    finally:
        setup_logging("INFO")


CVU = "0000168300000000123459"
RESTART_TEXT = (
    "La transferencia quedó sin confirmar porque la app se reinició mientras se enviaba: "
    "Transferir $2.000 a Fede: Federico Pérez, Brubank, CVU {cvu}. "
    "Revisá los movimientos de Fiwind antes de pedirla de nuevo."
)


def _seed_in_flight(settings):
    conn = connect(settings.db_path)
    migrate(conn)
    c = create_contact(conn, "Fede")
    d = add_destination(conn, c.id, "alias", "fede.mp")
    set_verification(conn, d.id, "Federico Pérez", CVU, "Brubank")
    payload = {"destination_id": d.id, "amount_cents": 200000,
               "agent_text": "Transferencia de $2.000 a Fede, alias fede.mp"}
    action = create_action(conn, "transfer", payload)
    claim_transfer(conn, action.id)
    conn.close()
    return action.id


def _patch_bot(monkeypatch, send):
    sessions = []
    real_session = __import__("financial_agent.app", fromlist=["AgentSession"]).AgentSession

    def make_session(*args, **kwargs):
        sessions.append(real_session(*args, **kwargs))
        return sessions[-1]

    monkeypatch.setattr("financial_agent.app.find_binary", lambda configured: None)
    monkeypatch.setattr("financial_agent.app.AgentSession", make_session)
    monkeypatch.setattr(
        "financial_agent.app.build_application",
        lambda *a, **k: SimpleNamespace(bot=SimpleNamespace(send_message=send)),
    )
    monkeypatch.setattr("financial_agent.app.start_bot", AsyncMock())
    monkeypatch.setattr("financial_agent.app.stop_bot", AsyncMock())
    return sessions


def test_restart_with_transfer_in_flight_notifies_juan(settings, monkeypatch):
    action_id = _seed_in_flight(settings)
    send = AsyncMock()
    sessions = _patch_bot(monkeypatch, send)
    with TestClient(create_app(settings)):
        pass
    send.assert_any_await(1, RESTART_TEXT.format(cvu=CVU))
    assert (
        "Resultado de la transferencia (Transferencia de $2.000 a Fede, alias fede.mp): "
        "sin confirmar, la app se reinició durante el envío"
    ) in sessions[0]._context_notes
    conn = connect(settings.db_path)
    assert get_action(conn, action_id).status == "sin_confirmar"
    conn.close()


def test_restart_notice_failure_does_not_stop_startup(settings, monkeypatch):
    _seed_in_flight(settings)
    send = AsyncMock(side_effect=NetworkError("sin red"))
    sessions = _patch_bot(monkeypatch, send)
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
    assert len(sessions[0]._context_notes) == 1


def test_wallet_sends_session_expired_notice_to_juan(settings, monkeypatch):
    from financial_agent.bot.fiwind_session import MSG_SESSION_EXPIRED
    from financial_agent.fiwind.client import FiwindClient

    send = AsyncMock()
    _patch_bot(monkeypatch, send)
    notifiers = []

    def make_wallet(*args, **kwargs):
        notifiers.append(kwargs["on_session_expired"])
        return FiwindClient(*args, **kwargs)

    monkeypatch.setattr("financial_agent.app.FiwindClient", make_wallet)
    with TestClient(create_app(settings)):
        asyncio.run(notifiers[0]())
    send.assert_awaited_once()
    assert send.await_args.args == (1, MSG_SESSION_EXPIRED)


def test_restart_without_bot_still_marks_unconfirmed(settings, monkeypatch):
    action_id = _seed_in_flight(settings)
    monkeypatch.setattr("financial_agent.app.find_binary", lambda configured: None)
    with TestClient(create_app(settings, with_bot=False)):
        pass
    conn = connect(settings.db_path)
    assert get_action(conn, action_id).status == "sin_confirmar"
    conn.close()
