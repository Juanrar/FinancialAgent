import asyncio
import logging
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import NoReturn
from zoneinfo import ZoneInfo

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    McpSdkServerConfig,
)
from claude_agent_sdk.types import (
    AssistantMessage,
    RateLimitEvent,
    ResultMessage,
    SystemMessage,
    ToolUseBlock,
)

from financial_agent.agent.instructions import build_system_prompt
from financial_agent.agent.tools import ALLOWED_TOOLS, SERVER_NAME, TurnState, build_server
from financial_agent.config import Settings
from financial_agent.fiwind.client import FiwindClient
from financial_agent.store.memory import list_notes
from financial_agent.store.routines import describe_routine, describe_total, list_routines
from financial_agent.store.pending import PendingAction

logger = logging.getLogger(__name__)

MAX_TURNS = 10

MSG_TOOLS = "No puedo responder: el agente ve herramientas que no son del proyecto. Revisá los logs."
MSG_TOKEN = "No pude conectarme con Claude, revisá el token."
MSG_MAX_TURNS = "Corté porque el pedido llevó demasiados pasos."
MSG_GENERIC = "Algo falló, probá de nuevo."


def limit_message(resets_at: int | None, tz: str) -> str:
    message = "Llegué al límite de uso del plan."
    if resets_at is None:
        return message
    hour = datetime.fromtimestamp(resets_at, ZoneInfo(tz)).strftime("%H:%M")
    return f"{message} Se libera a las {hour}."


class AgentError(Exception):
    def __init__(self, user_message: str, pending: list[PendingAction] | None = None):
        super().__init__(user_message)
        self.user_message = user_message
        self.pending = pending or []


@dataclass(frozen=True)
class AgentReply:
    text: str
    pending: list[PendingAction]
    session_expired: bool = False


def build_options(
    settings: Settings, system_prompt: str, server: McpSdkServerConfig
) -> ClaudeAgentOptions:
    settings.claude_config_dir.mkdir(parents=True, exist_ok=True)
    settings.agent_cwd.mkdir(parents=True, exist_ok=True)
    return ClaudeAgentOptions(
        model=settings.claude_model,
        system_prompt=system_prompt,
        tools=[],
        allowed_tools=list(ALLOWED_TOOLS),
        permission_mode="dontAsk",
        setting_sources=[],
        strict_mcp_config=True,
        mcp_servers={SERVER_NAME: server},
        max_turns=MAX_TURNS,
        cwd=settings.agent_cwd,
        extra_args={"no-session-persistence": None},
        verbatim_prompts=True,
        env={
            "CLAUDE_CONFIG_DIR": str(settings.claude_config_dir),
            "CLAUDE_CODE_OAUTH_TOKEN": settings.claude_oauth_token,
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        },
    )


def unexpected_tools(tool_names: Iterable[str], allowed: Iterable[str]) -> list[str]:
    allowed_set = set(allowed)
    return [name for name in tool_names if name not in allowed_set]


def delete_session_files(config_dir: Path, session_id: str) -> int:
    # Compara el nombre exacto en vez de usar session_id como patrón de glob.
    target = f"{session_id}.jsonl"
    deleted = 0
    for path in (config_dir / "projects").rglob("*.jsonl"):
        if path.name == target:
            path.unlink()
            deleted += 1
    return deleted


class AgentSession:
    def __init__(
        self,
        settings: Settings,
        conn: sqlite3.Connection,
        client_factory: Callable[[ClaudeAgentOptions], ClaudeSDKClient] = ClaudeSDKClient,
        idle_seconds: float | None = None,
        wallet: FiwindClient | None = None,
    ):
        self._settings = settings
        self._conn = conn
        self._wallet = FiwindClient(None) if wallet is None else wallet
        self._client_factory = client_factory
        self._idle_seconds = (
            settings.session_idle_minutes * 60 if idle_seconds is None else idle_seconds
        )
        # Las herramientas del servidor son closures sobre este objeto: se
        # reutiliza en todas las charlas y se limpia con reset() en cada mensaje.
        self.turn = TurnState()
        self._client: ClaudeSDKClient | None = None
        self._session_id: str | None = None
        self._context_notes: list[str] = []
        self._lock = asyncio.Lock()
        self._idle_task: asyncio.Task | None = None

    def add_context_note(self, note: str) -> None:
        self._context_notes.append(note)

    async def send(self, text: str) -> AgentReply:
        async with self._lock:
            # Limpia lo que haya dejado un turno anterior: si este falla antes de
            # arrancar, no hay avisos ni botones propios que conservar.
            self.turn.reset()
            try:
                return await self._send(text)
            except AgentError as error:
                self._attach_turn_results(error)
                raise
            except ClaudeSDKError as error:
                logger.error("Error del SDK de Claude: %s", type(error).__name__)
                await self._disconnect()
                generic = AgentError(MSG_GENERIC)
                self._attach_turn_results(generic)
                raise generic from error
            finally:
                self._schedule_idle()

    def _attach_turn_results(self, error: AgentError) -> None:
        # Si las herramientas ya cambiaron datos antes del error, Juan tiene que
        # enterarse y poder confirmar las acciones pendientes que quedaron.
        if self.turn.notices:
            error.user_message = "\n".join([error.user_message, *self.turn.notices])
            error.args = (error.user_message,)
        error.pending = list(self.turn.pending)

    async def close(self) -> None:
        async with self._lock:
            await self._disconnect()

    async def _send(self, text: str) -> AgentReply:
        client = self._client or await self._open()
        prefix = "".join(f"[Aviso del sistema: {note}]\n" for note in self._context_notes)
        self._context_notes.clear()
        self.turn.reset()
        await client.query(prefix + text)
        reply = await self._receive(client)
        parts = [reply, *self.turn.notices]
        return AgentReply(
            text="\n".join(part for part in parts if part),
            pending=list(self.turn.pending),
            session_expired=self.turn.session_expired,
        )

    async def _open(self) -> ClaudeSDKClient:
        # Las herramientas se verifican en el mensaje init porque
        # get_mcp_status() devuelve una lista vacía antes del primer query.
        # Hasta ese chequeo, allowed_tools y permission_mode="dontAsk"
        # impiden que corra una herramienta ajena.
        notes = list_notes(self._conn)
        routine_lines = [
            f"[id={r.id}] {describe_routine(self._conn, r)}" for r in list_routines(self._conn)
        ]
        if routine_lines:
            routine_lines.append(describe_total(self._conn))
        server = build_server(
            self._conn,
            self.turn,
            self._wallet,
            routine_hour=self._settings.routine_hour,
            timezone=self._settings.timezone,
        )
        prompt = build_system_prompt(notes, routine_lines)
        options = build_options(self._settings, prompt, server)
        client = self._client_factory(options)
        self._client = client
        await client.connect()
        logger.info("Sesión abierta")
        return client

    async def _receive(self, client: ClaudeSDKClient) -> str:
        auth_failed = False
        rate_limited = False
        rejected = False
        resets_at: int | None = None
        result: ResultMessage | None = None
        # Se recorre la respuesta hasta el ResultMessage aunque haya un error
        # antes, para que no queden mensajes de este turno para el siguiente.
        async for message in client.receive_response():
            if isinstance(message, SystemMessage) and message.subtype == "init":
                self._session_id = message.data.get("session_id") or self._session_id
                await self._check_tools(client, message.data.get("tools"))
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        logger.info("Herramienta usada: %s", block.name)
                if message.error == "authentication_failed":
                    auth_failed = True
                elif message.error == "rate_limit":
                    rate_limited = True
            elif isinstance(message, RateLimitEvent):
                if message.rate_limit_info.status == "rejected":
                    rejected = True
                    resets_at = message.rate_limit_info.resets_at
            elif isinstance(message, ResultMessage):
                self._session_id = message.session_id
                result = message

        if result is None:
            logger.error("La respuesta terminó sin ResultMessage")
            await self._disconnect()
        if auth_failed:
            logger.error("Claude rechazó la autenticación")
            raise AgentError(MSG_TOKEN)
        if rate_limited:
            logger.warning("Límite de uso del plan (error del asistente)")
            raise AgentError(limit_message(resets_at, self._settings.timezone))
        if result is None:
            raise AgentError(MSG_GENERIC)
        if result.subtype == "success" and not result.is_error:
            return result.result or ""
        if result.subtype == "error_max_turns":
            logger.warning("Se alcanzó max_turns")
            raise AgentError(MSG_MAX_TURNS)
        if rejected:
            logger.warning("Límite de uso del plan (rate limit rechazado)")
            raise AgentError(limit_message(resets_at, self._settings.timezone))
        logger.error(
            "Resultado con error: subtype=%s api_error_status=%s",
            result.subtype,
            result.api_error_status,
        )
        raise AgentError(MSG_GENERIC)

    async def _check_tools(self, client: ClaudeSDKClient, tools: list[str] | None) -> None:
        if tools is None:
            logger.error("El init no trae la lista de herramientas")
            await self._abort(client)
        unexpected = unexpected_tools(tools, ALLOWED_TOOLS)
        if unexpected:
            logger.error("Herramientas que no son del proyecto: %s", ", ".join(unexpected))
            await self._abort(client)

    async def _abort(self, client: ClaudeSDKClient) -> NoReturn:
        # El SDK levanta un Exception común si el control request de interrupt
        # vence o el CLI lo rechaza. Igual hay que cerrar: el chequeo no puede
        # dejar abierto un cliente que ve herramientas ajenas.
        try:
            await client.interrupt()
        except Exception as error:
            logger.error("No se pudo interrumpir la sesión: %s", type(error).__name__)
        finally:
            await self._disconnect()
        raise AgentError(MSG_TOOLS)

    async def _disconnect(self) -> None:
        self._cancel_idle()
        client, self._client = self._client, None
        session_id, self._session_id = self._session_id, None
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception as error:
            logger.error("No se pudo desconectar el cliente: %s", type(error).__name__)
        deleted = 0
        if session_id:
            try:
                deleted = delete_session_files(self._settings.claude_config_dir, session_id)
            except OSError as error:
                logger.error("No se pudieron borrar los archivos de sesión: %s", error)
        logger.info("Sesión cerrada, archivos de sesión borrados: %d", deleted)

    def _schedule_idle(self) -> None:
        self._cancel_idle()
        if self._client is not None:
            self._idle_task = asyncio.create_task(self._close_when_idle())

    def _cancel_idle(self) -> None:
        task, self._idle_task = self._idle_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _close_when_idle(self) -> None:
        await asyncio.sleep(self._idle_seconds)
        logger.info("Cierre por inactividad")
        await self.close()
