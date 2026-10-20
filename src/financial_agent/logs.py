import logging
import sys
from collections.abc import Iterable

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
MASK = "***"


class RedactFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str]):
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, MASK)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redact(record.getMessage())
        record.args = None
        if record.exc_info:
            # Se formatea acá para poder redactar el traceback y el mensaje de la excepción.
            record.exc_text = self._redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = self._redact(record.exc_text)
        if record.stack_info:
            record.stack_info = self._redact(record.stack_info)
        return True


def setup_logging(level: str, secrets: Iterable[str] = ()) -> None:
    logging.basicConfig(
        level=level.upper(),
        format=LOG_FORMAT,
        stream=sys.stdout,
        force=True,
    )
    redact = RedactFilter(secrets)
    for handler in logging.getLogger().handlers:
        handler.addFilter(redact)
    # Las URLs de httpx incluyen el token del bot.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # A DEBUG, python-telegram-bot loguea cada update y los parámetros de cada llamada a la API.
    logging.getLogger("telegram").setLevel(max(logging.INFO, logging.getLogger().level))
    # El SDK mete en sus errores texto de la salida de Claude. La sesión ya loguea la clase.
    logging.getLogger("claude_agent_sdk").setLevel(logging.CRITICAL)
