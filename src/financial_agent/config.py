import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_owner_id: int
    db_path: Path
    claude_oauth_token: str
    claude_config_dir: Path
    agent_cwd: Path
    claude_model: str
    session_idle_minutes: int
    timezone: str
    log_level: str


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
    )
