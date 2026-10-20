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
