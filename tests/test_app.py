import logging

from fastapi.testclient import TestClient

from financial_agent.app import create_app
from financial_agent.logs import setup_logging


def test_health_and_migrations(settings):
    app = create_app(settings, with_bot=False)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
    assert settings.db_path.exists()


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
