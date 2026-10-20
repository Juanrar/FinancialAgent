# Verificación del SDK

Fecha: 2026-10-20. claude-agent-sdk 0.2.168 (Claude Code incluido: ver `claude_code_version` en `init`).
Comando: `uv run python scripts/check_sdk.py --existing-login` (login ya existente en la PC, modelo `claude-sonnet-5-5`, cwd temporal).
Una sola corrida, `permission_mode="dontAsk"`, sin errores.

## Resultados

1. Herramientas en `init`: pasó. `tools == ['mcp__fa__ping']`. `mcp_servers == [{'name': 'fa', 'status': 'connected', 'source': 'sdk'}]`.
2. `get_mcp_status()` antes del primer `query`: no lista los servidores. Devolvió `{'mcpServers': []}`. El servidor `fa` solo aparece en `init`, que llega con el primer mensaje.
3. `--no-session-persistence`: pasó. No quedó ningún `.jsonl` con el `session_id` de la corrida bajo `~/.claude/projects/`.
4. `permission_mode="dontAsk"`: pasó. Se aceptó, Claude llamó a `mcp__fa__ping` y `result == "OK"` (`subtype: success`, `is_error: False`).
5. Autenticación con `CLAUDE_CODE_OAUTH_TOKEN` y `CLAUDE_CONFIG_DIR` aislado: pendiente: correr `uv run --env-file .env python scripts/check_sdk.py` cuando exista el .env.

## Formas que usa la Task 8

`get_mcp_status()` antes del primer query: dict con la clave `mcpServers` (lista, vacía en este momento). La docstring del SDK indica que cada entrada tiene `name`, `status` (`connected`, `pending`, `failed`, `needs-auth`, `disabled`) y `serverInfo` cuando está conectado. Esa forma de entrada no se observó con datos en esta corrida.

`init` (`SystemMessage.data`, `subtype == "init"`), claves:
`additional_directories, agents, analytics_disabled, apiKeySource, capabilities, claude_code_version, cwd, fast_mode_disabled_reason, fast_mode_state, mcp_servers, memory_paths, messaging_socket_path, model, original_cwd, output_style, per_turn_effort_active, permissionMode, plugins, powershell_path, product_feedback_disabled, session_id, skills, slash_commands, startup_timing, subtype, terminal_slash_commands, tools, type, uuid, view_mode`.
- `tools`: lista de strings (`['mcp__fa__ping']`).
- `mcp_servers`: lista de dicts con `name`, `status`, `source`.

`get_server_info()`, claves: `account, agents, analytics_disabled, available_output_styles, capabilities, claude_code_version, commands, current_permission_mode, fast_mode_disabled_reason, fast_mode_state, feedback_mode, ide_rc_auto_enable_gate, models, output_style, pid, remote_control_auto_connect_default, remote_control_auto_enable, remote_control_auto_on_by_default, remote_control_available, session_state, user_output_styles_dir`.

Orden de mensajes en `receive_response()`: `SystemMessage` (init), `AssistantMessage` (con `ToolUseBlock`), `RateLimitEvent`, `UserMessage`, `AssistantMessage`, `ResultMessage`.

## Reglas para la Task 8

- Punto 2 no funciona: la sesión solo verifica `init` (`tools` y `mcp_servers`) y el primer mensaje llega a Claude antes del chequeo. Desvío del spec, anotado acá.
- Punto 3 pasó, pero la Task 8 borra igual los archivos de sesión al cerrar.
- Punto 4 pasó: usar `permission_mode="dontAsk"`. No hace falta `can_use_tool`.
- Punto 1 trae `tools`: se puede seguir.
