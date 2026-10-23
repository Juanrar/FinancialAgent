from pathlib import Path

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


def test_settings_repr_hides_tokens():
    text = repr(load_settings({**REQUIRED, "TELEGRAM_BOT_TOKEN": "tok-telegram-1", "CLAUDE_CODE_OAUTH_TOKEN": "tok-oauth-2"}))
    assert "tok-telegram-1" not in text
    assert "tok-oauth-2" not in text


def test_fiwind_defaults(tmp_path):
    s = load_settings(REQUIRED | {"LOCALAPPDATA": str(tmp_path)})
    assert s.fiwind_profile_dir == tmp_path / "FinancialAgent" / "browser" / "fiwind"
    assert s.fiwind_headed is False and s.agent_browser_bin is None and s.routine_hour == 10


def test_fiwind_profile_without_localappdata():
    assert load_settings(REQUIRED).fiwind_profile_dir == Path("data/browser/fiwind").resolve()


def test_fiwind_overrides(tmp_path):
    s = load_settings(REQUIRED | {
        "FIWIND_PROFILE_DIR": str(tmp_path / "p"), "FIWIND_HEADED": "1",
        "AGENT_BROWSER_BIN": "C:/npm/agent-browser.CMD", "ROUTINE_HOUR": "8",
    })
    assert s.fiwind_profile_dir == tmp_path / "p" and s.fiwind_headed is True
    assert s.agent_browser_bin == "C:/npm/agent-browser.CMD" and s.routine_hour == 8


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Si", "si"])
def test_fiwind_headed_truthy_values(value):
    assert load_settings(REQUIRED | {"FIWIND_HEADED": value}).fiwind_headed is True


@pytest.mark.parametrize("hour", ["0", "24", "x"])
def test_routine_hour_invalid(hour):
    with pytest.raises(ConfigError, match="ROUTINE_HOUR debe ser una hora entre 1 y 23"):
        load_settings(REQUIRED | {"ROUTINE_HOUR": hour})
