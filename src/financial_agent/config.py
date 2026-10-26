import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = field(repr=False)
    telegram_owner_id: int
    db_path: Path
    claude_oauth_token: str = field(repr=False)
    claude_config_dir: Path
    agent_cwd: Path
    claude_model: str
    session_idle_minutes: int
    timezone: str
    log_level: str
    fiwind_profile_dir: Path
    fiwind_headed: bool
    agent_browser_bin: str | None
    routine_hour: int


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Falta la variable de entorno {name}")
    return value


def _int(environ: Mapping[str, str], name: str, default: str | None = None) -> int:
    raw = _required(environ, name) if default is None else environ.get(name, "").strip() or default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"La variable {name} debe ser un entero") from None


def _path(environ: Mapping[str, str], name: str, default: str) -> Path:
    return Path(environ.get(name, "").strip() or default).resolve()


def _routine_hour(environ: Mapping[str, str]) -> int:
    try:
        hour = _int(environ, "ROUTINE_HOUR", "10")
    except ConfigError:
        hour = 0
    if not 1 <= hour <= 23:
        raise ConfigError("La variable ROUTINE_HOUR debe ser una hora entre 1 y 23")
    return hour


def _fiwind_profile_dir(environ: Mapping[str, str]) -> Path:
    local_app_data = environ.get("LOCALAPPDATA", "").strip()
    default = (
        str(Path(local_app_data) / "FinancialAgent" / "browser" / "fiwind")
        if local_app_data
        else "data/browser/fiwind"
    )
    return _path(environ, "FIWIND_PROFILE_DIR", default)


def load_settings(environ: Mapping[str, str] = os.environ) -> Settings:
    return Settings(
        telegram_bot_token=_required(environ, "TELEGRAM_BOT_TOKEN"),
        telegram_owner_id=_int(environ, "TELEGRAM_OWNER_ID"),
        db_path=_path(environ, "DB_PATH", "data/financial_agent.db"),
        claude_oauth_token=_required(environ, "CLAUDE_CODE_OAUTH_TOKEN"),
        claude_config_dir=_path(environ, "CLAUDE_CONFIG_DIR", "data/claude"),
        agent_cwd=_path(environ, "AGENT_CWD", "data/agent"),
        claude_model=environ.get("CLAUDE_MODEL", "").strip() or "claude-sonnet-5-5",
        session_idle_minutes=_int(environ, "SESSION_IDLE_MINUTES", "30"),
        timezone=environ.get("TZ", "").strip() or "America/Argentina/Buenos_Aires",
        log_level=environ.get("LOG_LEVEL", "").strip() or "INFO",
        fiwind_profile_dir=_fiwind_profile_dir(environ),
        fiwind_headed=environ.get("FIWIND_HEADED", "").strip().lower() in ("1", "true", "si", "sí"),
        agent_browser_bin=environ.get("AGENT_BROWSER_BIN", "").strip() or None,
        routine_hour=_routine_hour(environ),
    )
