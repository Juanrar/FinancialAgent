"""Verificación manual del SDK de Claude. No es un test de pytest.

Uso:
    uv run --env-file .env python scripts/check_sdk.py
    uv run python scripts/check_sdk.py --existing-login

Con --existing-login no se lee .env: se usa el login de Claude Code que ya
existe en esta PC, un directorio temporal como cwd y el modelo por defecto.
"""

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

import claude_agent_sdk
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    RateLimitEvent,
    ResultMessage,
    SystemMessage,
    ToolUseBlock,
    create_sdk_mcp_server,
    tool,
)

DEFAULT_MODEL = "claude-sonnet-5-5"
PROMPT = "Llamá a la herramienta ping y después respondé solo OK."


@tool("ping", "Devuelve pong", {})
async def ping(args):
    return {"content": [{"type": "text", "text": "pong"}]}


def build_options(args) -> tuple[ClaudeAgentOptions, Path, Path | None]:
    """Devuelve las opciones, el directorio de proyectos a revisar y el cwd temporal."""
    env = {
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    tmp_cwd: Path | None = None
    if args.existing_login:
        model = DEFAULT_MODEL
        tmp_cwd = Path(tempfile.mkdtemp(prefix="check_sdk_"))
        cwd = tmp_cwd
        projects = Path.home() / ".claude" / "projects"
    else:
        from financial_agent.config import load_settings

        settings = load_settings()
        settings.claude_config_dir.mkdir(parents=True, exist_ok=True)
        settings.agent_cwd.mkdir(parents=True, exist_ok=True)
        env["CLAUDE_CONFIG_DIR"] = str(settings.claude_config_dir)
        env["CLAUDE_CODE_OAUTH_TOKEN"] = settings.claude_oauth_token
        model = settings.claude_model
        cwd = settings.agent_cwd
        projects = settings.claude_config_dir / "projects"

    server = create_sdk_mcp_server(name="fa", version="1.0.0", tools=[ping])
    kwargs = {}
    if args.fallback_permissions:
        async def can_use_tool(tool_name, tool_input, context):
            from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

            if tool_name in ("mcp__fa__ping",):
                return PermissionResultAllow()
            return PermissionResultDeny(message="No permitido")

        kwargs["can_use_tool"] = can_use_tool
    options = ClaudeAgentOptions(
        model=model,
        tools=[],
        allowed_tools=["mcp__fa__ping"],
        permission_mode="default" if args.fallback_permissions else "dontAsk",
        setting_sources=[],
        strict_mcp_config=True,
        mcp_servers={"fa": server},
        max_turns=10,
        cwd=cwd,
        env=env,
        extra_args={"no-session-persistence": None},
        verbatim_prompts=True,
        system_prompt="Sos un probador. Respondé corto.",
        **kwargs,
    )
    return options, projects, tmp_cwd


async def run(options: ClaudeAgentOptions, state: dict) -> None:
    async with ClaudeSDKClient(options) as client:
        try:
            info = await client.get_server_info()
            print("server_info keys:", sorted(info) if info else info)
        except Exception as exc:
            print("get_server_info falló:", type(exc).__name__, exc)
        try:
            print("mcp_status (antes del query):", await client.get_mcp_status())
        except Exception as exc:
            print("get_mcp_status falló:", type(exc).__name__, exc)

        await client.query(PROMPT)
        async for msg in client.receive_response():
            print("mensaje:", type(msg).__name__)
            if isinstance(msg, SystemMessage) and msg.subtype == "init":
                data = msg.data
                state["session_id"] = data.get("session_id")
                print("  init keys:", sorted(data))
                print("  init tools:", data.get("tools"))
                print("  init mcp_servers:", data.get("mcp_servers"))
            elif isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        print("  tool_use:", block.name)
            elif isinstance(msg, RateLimitEvent):
                print("  rate_limit:", msg)
            elif isinstance(msg, ResultMessage):
                state["session_id"] = msg.session_id
                print("  subtype:", msg.subtype)
                print("  is_error:", msg.is_error)
                print("  result:", msg.result)
                print("  session_id:", msg.session_id)


def report_files(projects: Path, session_id: str | None, delete_session: bool) -> None:
    print("SDK:", claude_agent_sdk.__version__)
    if not projects.exists():
        print(f"{projects} no existe")
        return
    if delete_session:
        if session_id is None:
            print("no se pudo verificar: no hay session_id")
            return
        found = list(projects.rglob(f"{session_id}.jsonl"))
        print(f"archivos .jsonl de la sesión {session_id}: {len(found)}")
        for path in found:
            print("  ", path)
            path.unlink()
        if found:
            print("  (borrados)")
    else:
        files = [p for p in projects.rglob("*") if p.is_file()]
        print(f"archivos bajo {projects}: {len(files)}")
        for path in files:
            print("  ", path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--existing-login", action="store_true")
    parser.add_argument("--fallback-permissions", action="store_true")
    args = parser.parse_args()
    options, projects, tmp_cwd = build_options(args)
    state: dict = {"session_id": None}
    try:
        asyncio.run(run(options, state))
    finally:
        report_files(projects, state["session_id"], args.existing_login)
        if tmp_cwd:
            shutil.rmtree(tmp_cwd, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
