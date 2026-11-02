"""Ejecutor sincrónico del CLI agent-browser."""

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_DAEMON_WARNING = "ignored: daemon already running"


class BrowserCommandError(Exception):
    def __init__(self, command: tuple[str, ...], output: str):
        super().__init__(f"agent-browser {command[0] if command else ''} falló")
        self.command = command
        self.output = output


class BrowserTimeout(BrowserCommandError):
    pass


def find_binary(configured: str | None) -> str | None:
    if configured:
        # Una ruta configurada que no existe se trata como "no instalado".
        return configured if Path(configured).is_file() else None
    return shutil.which("agent-browser")


class AgentBrowser:
    def __init__(self, binary: str, session: str, profile: Path, headed: bool,
                 timeout: float = 60):
        self.binary = binary
        self.session = session
        self.profile = profile
        self.headed = headed
        self.timeout = timeout

    def open(self, url: str) -> None:
        self._exec(self._prefix() + ["open", url], ("open", url))

    def run(self, *args: str) -> str:
        output = self._exec(self._prefix() + list(args), tuple(args))
        lines = [line for line in output.splitlines() if _DAEMON_WARNING not in line]
        return "\n".join(lines).strip()

    def close(self) -> None:
        try:
            self.run("close")
        except Exception as exc:
            logger.warning("close falló: %s", type(exc).__name__)

    def _prefix(self) -> list[str]:
        return [self.binary, "--session", self.session]

    def _env(self) -> dict[str, str]:
        # Perfil y headed van en el entorno de todos los comandos. Si un comando
        # llega con opciones de arranque distintas a las del navegador abierto
        # (por ejemplo, AGENT_BROWSER_EXECUTABLE_PATH sin el perfil), agent-browser
        # reinicia el navegador y la página queda en about:blank.
        env = dict(os.environ)
        env["AGENT_BROWSER_PROFILE"] = str(self.profile)
        if self.headed:
            env["AGENT_BROWSER_HEADED"] = "1"
        else:
            env.pop("AGENT_BROWSER_HEADED", None)
        return env

    def _exec(self, cmd: list[str], command: tuple[str, ...]) -> str:
        name = command[0] if command else ""
        # La salida va a un archivo temporal y no a un pipe: si el comando
        # arranca el daemon, este hereda el pipe, lo mantiene abierto y
        # subprocess.run se cuelga para siempre (ni el timeout lo corta).
        with tempfile.TemporaryFile("w+b") as out:
            try:
                result = subprocess.run(
                    cmd, stdin=subprocess.DEVNULL, stdout=out,
                    stderr=subprocess.STDOUT, timeout=self.timeout, env=self._env(),
                )
            except subprocess.TimeoutExpired:
                logger.warning("agent-browser %s: timeout", name)
                out.seek(0)
                raise BrowserTimeout(command, _decode(out.read())) from None
            out.seek(0)
            output = _decode(out.read())
        if result.returncode != 0:
            logger.warning("agent-browser %s: código %s", name, result.returncode)
            raise BrowserCommandError(command, output)
        return output


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")
