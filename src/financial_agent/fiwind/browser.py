"""Ejecutor sincrónico del CLI agent-browser."""

import logging
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
    return configured or shutil.which("agent-browser")


class AgentBrowser:
    def __init__(self, binary: str, session: str, profile: Path, headed: bool,
                 timeout: float = 60):
        self.binary = binary
        self.session = session
        self.profile = profile
        self.headed = headed
        self.timeout = timeout

    def open(self, url: str, headed: bool | None = None) -> None:
        # --profile y --headed van solo en open. Si el navegador ya está
        # corriendo y un comando los repite, agent-browser avisa que los ignora
        # y deja la página en about:blank.
        show = self.headed if headed is None else headed
        args = ["--profile", str(self.profile)]
        if show:
            args.append("--headed")
        self._exec(self._prefix() + args + ["open", url], ("open", url))

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

    def _exec(self, cmd: list[str], command: tuple[str, ...]) -> str:
        name = command[0] if command else ""
        # La salida va a un archivo temporal y no a un pipe: si el comando
        # arranca el daemon, este hereda el pipe, lo mantiene abierto y
        # subprocess.run se cuelga para siempre (ni el timeout lo corta).
        with tempfile.TemporaryFile("w+b") as out:
            try:
                result = subprocess.run(
                    cmd, stdin=subprocess.DEVNULL, stdout=out,
                    stderr=subprocess.STDOUT, timeout=self.timeout,
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
