import pytest

from financial_agent.config import load_settings
from financial_agent.store.db import connect, migrate


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "test.db")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def settings(tmp_path):
    return load_settings(
        {
            "TELEGRAM_BOT_TOKEN": "telegram-test-token",
            "TELEGRAM_OWNER_ID": "1",
            "CLAUDE_CODE_OAUTH_TOKEN": "oauth-test-token",
            "DB_PATH": str(tmp_path / "test.db"),
            "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
            "AGENT_CWD": str(tmp_path / "agent"),
        }
    )
