import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from claude_agent_sdk import CLIConnectionError
from claude_agent_sdk.types import (
    AssistantMessage,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    SystemMessage,
    ToolUseBlock,
)

from financial_agent.agent.session import (
    MSG_GENERIC,
    MSG_MAX_TURNS,
    MSG_TOKEN,
    MSG_TOOLS,
    AgentError,
    AgentSession,
    build_options,
    delete_session_files,
    limit_message,
    unexpected_tools,
)
from financial_agent.agent.tools import ALLOWED_TOOLS
from financial_agent.store.memory import add_note


class FakeClient:
    """Cliente falso: cada query reproduce el guion hasta el ResultMessage.

    Con repeat=False el guion se reproduce solo en la primera query. Una
    excepción dentro del guion se levanta al llegar a ella.
    """

    def __init__(self, script, on_query=None, repeat=False):
        self.script = list(script)
        self.on_query = on_query
        self.repeat = repeat
        self.queries = []
        self.connected = False
        self.interrupted = False
        self.disconnected = False
        self.interrupt_error = None
        self._queue = []

    async def connect(self):
        self.connected = True

    async def query(self, prompt):
        if self.repeat or not self.queries:
            self._queue.extend(self.script)
        self.queries.append(prompt)
        if self.on_query:
            self.on_query()

    async def receive_response(self):
        while self._queue:
            message = self._queue.pop(0)
            if isinstance(message, Exception):
                raise message
            yield message
            if isinstance(message, ResultMessage):
                return

    async def interrupt(self):
        self.interrupted = True
        if self.interrupt_error:
            raise self.interrupt_error

    async def disconnect(self):
        self.disconnected = True


class FakeFactory:
    def __init__(self, script):
        self.script = script
        self.clients = []
        self.options = []

    def __call__(self, options):
        self.options.append(options)
        client = FakeClient(self.script, repeat=True)
        self.clients.append(client)
        return client


def result(text="Hola", subtype="success", is_error=False):
    return ResultMessage(subtype=subtype, duration_ms=1, duration_api_ms=1,
                         is_error=is_error, num_turns=1, session_id="s1", result=text)


INIT_OK = SystemMessage(subtype="init", data={"tools": ALLOWED_TOOLS})


def make_session(conn, settings, script, on_query=None, repeat=False):
    fake = FakeClient(script, repeat=repeat)
    session = AgentSession(settings, conn, client_factory=lambda options: fake)
    if on_query:
        fake.on_query = lambda: on_query(session)
    return session, fake


def make_session_factory(conn, settings, idle_seconds=None):
    factory = FakeFactory([INIT_OK, result()])
    session = AgentSession(settings, conn, client_factory=factory, idle_seconds=idle_seconds)
    return session, factory


async def test_reply_with_notices(conn, settings):
    session, fake = make_session(conn, settings, [INIT_OK, result("Listo")],
                                 on_query=lambda s: s.turn.notices.append("(creé el contacto Fede)"))
    reply = await session.send("Fede es mi hermano")
    assert reply.text == "Listo\n(creé el contacto Fede)"
    assert fake.connected and fake.queries == ["Fede es mi hermano"]


async def test_open_passes_routines_to_prompt(conn, settings):
    from financial_agent.store.contacts import add_destination, create_contact
    from financial_agent.store.routines import create_routine

    c = create_contact(conn, "Tío Pepe")
    add_destination(conn, c.id, "alias", "tio.pepe")
    rid = create_routine(conn, c.id, 3000000, 5, hour=10, tz=settings.timezone).id
    ana = create_contact(conn, "Ana")
    add_destination(conn, ana.id, "alias", "ana.mp")
    create_routine(conn, ana.id, 1500000, 10, hour=10, tz=settings.timezone)
    factory = FakeFactory([INIT_OK, result()])
    session = AgentSession(settings, conn, client_factory=factory)
    await session.send("hola")
    prompt = factory.options[0].system_prompt
    assert f"[id={rid}] $30.000 a Tío Pepe los días 5" in prompt
    assert "$15.000 a Ana los días 10\nTotal por mes: $45.000" in prompt
    today = datetime.now(ZoneInfo(settings.timezone))
    assert f"Hoy es {today:%d/%m/%Y}." in prompt


async def test_unexpected_tool_aborts(conn, settings):
    init = SystemMessage(subtype="init", data={"tools": [*ALLOWED_TOOLS, "Bash"]})
    session, fake = make_session(conn, settings, [init, result()])
    with pytest.raises(AgentError) as e:
        await session.send("hola")
    assert e.value.user_message == MSG_TOOLS and fake.disconnected
    assert fake.interrupted


async def test_unexpected_tool_aborts_even_if_interrupt_fails(conn, settings):
    init = SystemMessage(subtype="init", data={"tools": [*ALLOWED_TOOLS, "Bash"]})
    first = FakeClient([init, result("turno abortado")])
    first.interrupt_error = Exception("Control request timeout: interrupt")
    second = FakeClient([INIT_OK, result("nuevo")])
    clients = [first, second]
    session = AgentSession(settings, conn, client_factory=lambda options: clients.pop(0))
    with pytest.raises(AgentError) as e:
        await session.send("hola")
    assert e.value.user_message == MSG_TOOLS and first.disconnected
    reply = await session.send("otra vez")
    assert reply.text == "nuevo" and second.queries == ["otra vez"]


async def test_init_without_tools_list_aborts(conn, settings):
    init = SystemMessage(subtype="init", data={})
    session, fake = make_session(conn, settings, [init, result()])
    with pytest.raises(AgentError) as e:
        await session.send("hola")
    assert e.value.user_message == MSG_TOOLS and fake.disconnected


async def test_rate_limit_message(conn, settings):
    resets = int(datetime(2026, 10, 20, 18, 30, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires")).timestamp())
    event = RateLimitEvent(rate_limit_info=RateLimitInfo(status="rejected", resets_at=resets), uuid="u", session_id="s1")
    session, _ = make_session(conn, settings, [INIT_OK, event, result(subtype="error_during_execution", is_error=True)])
    with pytest.raises(AgentError) as e:
        await session.send("hola")
    assert e.value.user_message == "Llegué al límite de uso del plan. Se libera a las 18:30."


async def test_allowed_rate_limit_event_is_ignored(conn, settings):
    event = RateLimitEvent(rate_limit_info=RateLimitInfo(status="allowed"), uuid="u", session_id="s1")
    session, _ = make_session(conn, settings, [INIT_OK, event, result("Listo")])
    reply = await session.send("hola")
    assert reply.text == "Listo"


async def test_auth_error(conn, settings):
    msg = AssistantMessage(content=[], model="x", error="authentication_failed")
    session, _ = make_session(conn, settings, [INIT_OK, msg, result()])
    with pytest.raises(AgentError) as e:
        await session.send("hola")
    assert e.value.user_message == MSG_TOKEN


async def test_error_consumes_rest_of_response(conn, settings):
    msg = AssistantMessage(content=[], model="x", error="authentication_failed")
    session, fake = make_session(conn, settings, [INIT_OK, msg, result("viejo")])
    with pytest.raises(AgentError):
        await session.send("hola")
    fake.script = [INIT_OK, result("nuevo")]
    fake.repeat = True
    reply = await session.send("otra vez")
    assert reply.text == "nuevo"


async def test_assistant_rate_limit_error(conn, settings):
    msg = AssistantMessage(content=[], model="x", error="rate_limit")
    session, _ = make_session(conn, settings, [INIT_OK, msg, result(subtype="error_during_execution", is_error=True)])
    with pytest.raises(AgentError) as e:
        await session.send("hola")
    assert e.value.user_message == "Llegué al límite de uso del plan."


async def test_max_turns(conn, settings):
    session, _ = make_session(conn, settings, [INIT_OK, result(subtype="error_max_turns", is_error=True)])
    with pytest.raises(AgentError) as e:
        await session.send("hola")
    assert e.value.user_message == MSG_MAX_TURNS


async def test_failed_turn_keeps_notices_and_pending(conn, settings):
    from financial_agent.store import contacts
    from financial_agent.store.pending import create_action

    contact = contacts.create_contact(conn, "Fede")
    action = create_action(conn, "delete_contact", {"contact_id": contact.id})

    def tools_ran(s):
        s.turn.notices.append("(creé el contacto Fede)")
        s.turn.pending.append(action)

    session, _ = make_session(
        conn, settings, [INIT_OK, result(subtype="error_max_turns", is_error=True)], on_query=tools_ran
    )
    with pytest.raises(AgentError) as e:
        await session.send("hola")
    assert e.value.user_message == MSG_MAX_TURNS + "\n(creé el contacto Fede)"
    assert e.value.pending == [action]


async def test_failed_turn_without_tools_has_no_extras(conn, settings):
    session, _ = make_session(conn, settings, [INIT_OK, result(subtype="error_max_turns", is_error=True)])
    with pytest.raises(AgentError) as e:
        await session.send("hola")
    assert e.value.user_message == MSG_MAX_TURNS
    assert e.value.pending == []


async def test_other_result_error_is_generic(conn, settings):
    session, _ = make_session(conn, settings, [INIT_OK, result(subtype="success", is_error=True)])
    with pytest.raises(AgentError) as e:
        await session.send("hola")
    assert e.value.user_message == MSG_GENERIC


async def test_sdk_error_closes_session(conn, settings):
    session, fake = make_session(conn, settings, [INIT_OK, CLIConnectionError("se cortó")])
    with pytest.raises(AgentError) as e:
        await session.send("hola")
    assert e.value.user_message == MSG_GENERIC and fake.disconnected


async def test_tool_use_does_not_break_reply(conn, settings):
    call = AssistantMessage(
        content=[ToolUseBlock(id="t1", name="mcp__fa__listar_contactos", input={})], model="x"
    )
    session, _ = make_session(conn, settings, [INIT_OK, call, result("No hay contactos.")])
    reply = await session.send("listá")
    assert reply.text == "No hay contactos."


async def test_context_note_prefixed_once(conn, settings):
    session, fake = make_session(conn, settings, [INIT_OK, result()], repeat=True)
    session.add_context_note("Juan confirmó: Borrar a Fede")
    await session.send("¿ya está?")
    await session.send("gracias")
    assert fake.queries[0] == "[Aviso del sistema: Juan confirmó: Borrar a Fede]\n¿ya está?"
    assert fake.queries[1] == "gracias"


async def test_idle_closes_and_new_session_reloads_notes(conn, settings):
    session, factory = make_session_factory(conn, settings, idle_seconds=0.01)
    await session.send("hola")
    add_note(conn, "cobro el día 5")
    await asyncio.sleep(0.05)
    assert factory.clients[0].disconnected
    await session.send("hola")
    assert len(factory.clients) == 2
    assert "cobro el día 5" in factory.options[1].system_prompt


async def test_close_deletes_session_files(conn, settings):
    f = settings.claude_config_dir / "projects" / "abc" / "s9.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text("{}")
    init = SystemMessage(subtype="init", data={"tools": ALLOWED_TOOLS, "session_id": "s9"})
    session, fake = make_session(conn, settings, [init, CLIConnectionError("se cortó")])
    with pytest.raises(AgentError):
        await session.send("hola")
    assert fake.disconnected and not f.exists()


def test_build_options_isolation(settings):
    o = build_options(settings, "x", server=object())
    assert o.tools == [] and o.setting_sources == [] and o.strict_mcp_config is True
    assert o.allowed_tools == ALLOWED_TOOLS and o.max_turns == 10
    assert o.model == "claude-sonnet-5-5"
    assert "no-session-persistence" in o.extra_args
    assert o.env["CLAUDE_CONFIG_DIR"] == str(settings.claude_config_dir)
    assert o.permission_mode == "dontAsk"
    assert o.verbatim_prompts is True
    assert settings.claude_config_dir.is_dir() and settings.agent_cwd.is_dir()


def test_unexpected_tools():
    assert unexpected_tools(["mcp__fa__a", "Bash", "Read"], ["mcp__fa__a"]) == ["Bash", "Read"]
    assert unexpected_tools([], ["mcp__fa__a"]) == []


def test_limit_message_without_reset():
    assert limit_message(None, "America/Argentina/Buenos_Aires") == "Llegué al límite de uso del plan."


def test_delete_session_files(tmp_path):
    f = tmp_path / "projects" / "abc" / "s1.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text("{}")
    assert delete_session_files(tmp_path, "s1") == 1 and not f.exists()


def test_delete_session_files_without_projects_dir(tmp_path):
    assert delete_session_files(tmp_path, "s1") == 0
