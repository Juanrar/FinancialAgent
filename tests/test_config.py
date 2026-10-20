import pytest

from financial_agent.config import ConfigError, load_settings

REQUIRED = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_OWNER_ID": "123", "CLAUDE_CODE_OAUTH_TOKEN": "o"}


def test_load_settings_defaults():
    s = load_settings(REQUIRED)
    assert s.telegram_owner_id == 123
    assert s.claude_model == "claude-sonnet-5-5"
    assert s.session_idle_minutes == 30
    assert s.timezone == "America/Argentina/Buenos_Aires"
    assert s.db_path.is_absolute() and s.db_path.parts[-2:] == ("data", "financial_agent.db")
    assert s.claude_config_dir.parts[-2:] == ("data", "claude")
    assert s.agent_cwd.parts[-2:] == ("data", "agent")


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_var_is_named(missing):
    env = {k: v for k, v in REQUIRED.items() if k != missing}
    with pytest.raises(ConfigError, match=missing):
        load_settings(env)


def test_owner_id_must_be_integer():
    with pytest.raises(ConfigError, match="TELEGRAM_OWNER_ID"):
        load_settings({**REQUIRED, "TELEGRAM_OWNER_ID": "abc"})
