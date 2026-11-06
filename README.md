# FinancialAgent

Un agente personal que manda transferencias en pesos desde una cuenta de Fiwind. Le escribís como a una persona, por ejemplo "transferile 2000 a mi hermano", y el agente busca el contacto, arma la transferencia y te la muestra con un botón para confirmar.

Esta creado para que sea usado individualmente,osea el dueño del bot. No tiene web ni app: todo pasa en un chat privado de Telegram.

## Cómo es

- **Se habla, no se usan comandos.** El agente entiende pedidos escritos como mensajes: transferir, guardar un CBU o alias, crear una rutina mensual, consultar el saldo o el historial. Los comandos existen para ver y editar datos sin pasar por Claude.
- **Nada importante pasa sin tu confirmación.** Para transferir, borrar un contacto o un destino, cambiar el destino principal o corregir un CBU o alias, el agente crea una acción pendiente. El bot la muestra con los botones Confirmar o Cancelar. La acción vence a los 10 minutos, o a las 24 horas si viene de una rutina.
- **Claude no maneja Fiwind.** El agente solo pide la transferencia. Cuando tocás Confirmar, el código abre Fiwind con [agent-browser](https://github.com/vercel-labs/agent-browser) y sigue una secuencia fija: busca el destino, compara titular y cuenta con lo guardado, revisa el saldo, carga el monto y compara el diálogo de Fiwind con la orden. Recién ahí toca Enviar. Si algo falla antes de Enviar, no sale nada. Si falla después, la transferencia queda "sin confirmar" y nunca se reintenta.
- **Solo se transfiere a destinos verificados.** Al guardar un CBU, CVU o alias, el bot lo busca en Fiwind y guarda titular, banco y cuenta. Antes revisa el formato, y en el CBU o CVU también los dígitos verificadores. Se pueden guardar emails de Mercado Pago, pero Fiwind no transfiere a ellos.
- **Rutinas mensuales.** "Pagale 50000 a Fede el 10 de cada mes" crea una rutina. Ese día, a la hora de `ROUTINE_HOUR`, el bot te manda la transferencia lista para confirmar. Si el mes no tiene ese día, usa el último. Una vez por día lee el saldo y te avisa si no alcanza para las rutinas de los próximos 3 días.
- **Memoria entre sesiones.** La sesión del agente se cierra sola a los 30 minutos sin mensajes. Lo que importa queda como nota de memoria en SQLite y se carga en cada sesión nueva. Lo que describe a un contacto ("Fede es mi hermano") va al contacto como apodo, no a la memoria. Cuando el agente guarda o cambia algo por su cuenta, lo avisa con una línea entre paréntesis al final de la respuesta.
- **Un solo usuario.** El bot responde solo al ID de Telegram del `.env` y solo en chat privado. Los mensajes de cualquier otra persona se descartan antes de llegar a Claude. Los mensajes que reenviás le llegan al agente marcados como reenviados, para que no los tome como pedidos tuyos.
- **El agente solo tiene sus herramientas.** Las herramientas de Claude Code (archivos, terminal, web) están apagadas y no se carga nada de `~/.claude`. Si al abrir la sesión el agente ve una herramienta que no es del proyecto, el bot corta la sesión. Al cerrarla, borra los archivos de sesión de Claude.
- **Los secretos no van a los logs.** Los tokens y la contraseña de Fiwind se reemplazan por `***`. El código que manda Fiwind por mail no pasa por Claude ni se loguea. El titular, el banco y la cuenta que lee Fiwind los ves vos en el mensaje de confirmación; a Claude no le llegan.

## Comandos

| Comando | Qué hace |
|---|---|
| `/saldo` | Lee el saldo disponible en la cuenta. |
| `/contactos` | Lista los contactos. Desde cada uno se cambia el nombre, se reemplazan los apodos y se agregan, editan o borran destinos. |
| `/memory` | Lista las notas de memoria, con botones para editarlas o borrarlas. |
| `/rutinas` | Lista las rutinas mensuales y el total por mes. Se pueden borrar. |
| `/historial` | Las transferencias terminadas, de a 10 por página. |

El bot solo lee texto. Si editás un mensaje ya enviado, no lo procesa: mandalo de nuevo.

## Qué pasa con una transferencia

`/historial` muestra uno de estos estados:

| Estado | Qué significa |
|---|---|
| enviada | Fiwind aceptó la transferencia y mostró el estado "En proceso". |
| no salió | Falló antes de tocar Enviar o no alcanzaba el saldo. No se mandó nada. |
| sin confirmar | Falló después de tocar Enviar, Fiwind mostró otro estado o la app se reinició durante el envío. **Puede haber salido:** revisá los movimientos de Fiwind antes de pedirla de nuevo. |
| hecha a mano | No salió por el bot y la hiciste vos desde Fiwind. |

Si una transferencia no sale porque falló el navegador o venció la sesión de Fiwind, el bot te pasa titular, cuenta y monto para hacerla a mano, con los botones "Ya la hice" y "No la hago". Si Fiwind muestra otro titular o cuenta para un destino guardado, no manda nada y te ofrece actualizar los datos.

## La sesión de Fiwind

El bot usa su propio perfil de navegador, guardado en `FIWIND_PROFILE_DIR`, y la sesión de Fiwind queda en ese perfil. Cuando vence, el bot te manda el aviso de sesión vencida con el botón "Iniciar sesión". Al tocarlo:

1. El bot escribe `FIWIND_EMAIL` y `FIWIND_PASSWORD` en la página de login de Fiwind.
2. Si Fiwind no reconoce el navegador, manda un código de 6 números a tu mail y el bot te lo pide.
3. Le mandás el código por Telegram. Tenés 10 minutos.

Si Fiwind rechaza el email o la contraseña, el bot no ofrece reintentar, porque varios intentos fallidos pueden bloquear la cuenta. Corregí el `.env` y reiniciá el bot. El aviso de sesión vencida se repite como mucho una vez cada 10 minutos.

### Login manual

En una VM, Fiwind rechaza el login automático. El formulario manda un token de reCAPTCHA, y Google marca como automatizado al Chromium del contenedor. Fiwind responde "Unexpected error" y la página lo muestra como email o contraseña incorrectos. Ahí el login lo hacés vos, en un Chromium común con el perfil del bot, que ves desde el navegador de tu PC.

Sacá `FIWIND_EMAIL` y `FIWIND_PASSWORD` del `.env` de la VM. Sin esas variables el bot no intenta el login automático, que solo suma intentos fallidos, y el aviso de sesión vencida te manda a esta sección.

Cuando vence la sesión:

1. En la VM, frená el bot y abrí el Chromium del login. El bot usa el mismo perfil, y Chromium no puede abrirlo dos veces.

   ```sh
   docker compose stop app
   docker compose --profile login run --rm --service-ports fiwind-login
   ```

2. En tu PC, abrí un túnel SSH. Después entrá a http://localhost:6080/vnc.html y tocá Connect.

   ```sh
   ssh -L 6080:localhost:6080 usuario@ip-de-la-vm
   ```

3. Iniciá sesión en Fiwind. Si te pide el código del mail, cargalo ahí.
4. Cuando veas el panel, cerrá la ventana de Chromium. Ctrl+C en la terminal de la VM también sirve: el script cierra la ventana antes de salir. Chromium guarda la sesión al cerrar la ventana. Si recibe una señal sin cerrar la ventana, solo guarda lo que escribió en su guardado periódico, cada unos 30 segundos.
5. Levantá el bot de nuevo y probá `/saldo`.

   ```sh
   docker compose start app
   ```

El puerto 6080 se publica solo en `127.0.0.1` de la VM, así que solo se llega por el túnel.

## Cómo está hecho

Python 3.13 · FastAPI · python-telegram-bot (long polling) · SQLite · [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) · agent-browser.

El agente usa una suscripción de Claude con un token de `claude setup-token`, no una API key. Sirve porque el bot lo usa solo su dueño con su propia cuenta. Si lo usara otra persona, hay que pasar a una API key.

Las rutas son relativas a `src/financial_agent/`.

| Archivo | Qué hace |
|---|---|
| `app.py` | Arranca la base, la wallet, la sesión del agente, el bot y el scheduler. Expone `/health`. Al arrancar vence las acciones pendientes del chat y marca como "sin confirmar" las transferencias que quedaron a mitad de camino. |
| `agent/session.py` | La sesión del agente: abre el SDK con solo las herramientas del proyecto, revisa esa lista, la cierra por inactividad y borra los archivos de sesión. |
| `agent/tools.py` | Las 22 herramientas del agente: contactos, memoria, transferencias, rutinas, historial y saldo. Las que empiezan con `pedir_` crean acciones pendientes. |
| `agent/instructions.py` | Las instrucciones del agente. Cada sesión nueva carga las notas de memoria y las rutinas. |
| `bot/application.py` | Registra los handlers y el filtro que deja pasar solo al dueño. |
| `bot/chat.py` | Pasa el texto al agente y muestra los botones Confirmar y Cancelar. |
| `bot/transfer_flow.py` | Qué pasa al confirmar una transferencia, y el camino para hacerla a mano. |
| `bot/fiwind_session.py` | El inicio de sesión en Fiwind desde Telegram. |
| `bot/*_cmd.py` | Los comandos. |
| `transfers.py` | Elige y verifica el destino, arma la transferencia y traduce el resultado de Fiwind. |
| `fiwind/client.py` | La wallet: la secuencia fija de pasos en Fiwind para buscar un destino, transferir, leer el saldo e iniciar sesión. |
| `fiwind/browser.py` · `fiwind/pages.py` | Corre el CLI de agent-browser y lee el texto de las páginas de Fiwind. |
| `scheduler.py` | Corre cada minuto: el chequeo diario de saldo, las rutinas del día y las confirmaciones de rutina vencidas. |
| `store/` | SQLite: contactos, memoria, acciones pendientes, rutinas y estado. Las migraciones están en `store/migrations/`. |
| `validation.py` · `money.py` | Validación de CBU/CVU, alias y email. Montos en centavos. |
| `logs.py` | Los logs, con los secretos tapados. |

Los términos del proyecto (wallet, sesión de Fiwind, sesión del agente) están definidos en [GLOSSARY.md](GLOSSARY.md).

## Correrlo en tu máquina

Necesitás:

- Python 3.13 y [uv](https://docs.astral.sh/uv/).
- Un bot de Telegram creado con [@BotFather](https://t.me/BotFather).
- Claude Code y una suscripción de Claude, para generar el token con `claude setup-token`.
- Node, para instalar agent-browser.
- Una cuenta de Fiwind.

```sh
uv sync
cp .env.example .env      # completá los tokens y tu ID de Telegram
npm install -g agent-browser
agent-browser install     # descarga Chrome la primera vez
uv run --env-file .env uvicorn financial_agent.app:create_app --factory --host 127.0.0.1 --port 8000
uv run pytest
```

Los tests no usan la red: Telegram, Claude y Fiwind están simulados.

Para saber tu ID de Telegram, poné cualquier número en `TELEGRAM_OWNER_ID`, levantá el bot y escribile. El log muestra `mensaje descartado de user_id=...` con tu ID.

Sin agent-browser el bot arranca igual. La charla, los contactos y la memoria funcionan, pero no puede verificar destinos ni transferir. Con `FIWIND_HEADED=1` se ve la ventana del navegador mientras opera Fiwind, y sirve para entender en qué paso falla.

### En Windows

`.\agent.ps1` levanta el bot. Usa `%LOCALAPPDATA%\FinancialAgent\.env` si existe y, si no, el `.env` del proyecto. Ahí conviene dejar el `.env`, la base, la configuración de Claude y el perfil de Fiwind, fuera de OneDrive. `.env.example` tiene las rutas de ejemplo.

### Variables del `.env`

| Variable | Para qué |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token del bot, de BotFather. Obligatoria. |
| `TELEGRAM_OWNER_ID` | Tu ID numérico de Telegram. Obligatoria. |
| `CLAUDE_CODE_OAUTH_TOKEN` | El token de `claude setup-token`. Obligatoria. |
| `FIWIND_EMAIL` · `FIWIND_PASSWORD` | Para iniciar sesión en Fiwind desde Telegram. Sin ellas, el botón "Iniciar sesión" no funciona. |
| `CLAUDE_MODEL` | Modelo del agente. Por defecto `claude-sonnet-5-5`. |
| `SESSION_IDLE_MINUTES` | Minutos sin mensajes antes de cerrar la sesión del agente. Por defecto 30. |
| `ROUTINE_HOUR` | Hora, de 1 a 23, a la que llegan las rutinas. Por defecto 10. |
| `TZ` | Zona horaria. Por defecto `America/Argentina/Buenos_Aires`. |
| `FIWIND_HEADED` | `1` muestra el navegador mientras opera Fiwind. |
| `FIWIND_PROFILE_DIR` · `AGENT_BROWSER_BIN` | Carpeta del perfil del navegador y ruta de agent-browser si no está en el PATH. |
| `DB_PATH` · `CLAUDE_CONFIG_DIR` · `AGENT_CWD` | Rutas de la base, de la configuración de Claude y del directorio de trabajo del agente. Por defecto, dentro de `data/`. |
| `LOG_LEVEL` | Nivel de logs. Por defecto `INFO`. |

### Probar la conexión con Claude

```sh
uv run --env-file .env python scripts/check_sdk.py
```

Abre una sesión real con una herramienta de prueba y revisa que el agente vea solo esa herramienta y que no queden archivos de sesión. Imprime OK o FALLA por cada chequeo. Gasta uso del plan de Claude.

## Correrlo con Docker

```sh
mkdir -p data && sudo chown 1000:1000 data   # solo en Linux, la primera vez
docker compose up -d --build
```

El contenedor corre como el usuario 1000 y guarda la base y la configuración en `data/`. No publica puertos: el bot usa long polling y `/health` solo lo consulta el healthcheck.

La imagen trae agent-browser y el Chromium de Debian, que funciona en x64 y en ARM. Para iniciar sesión en Fiwind desde un servidor, ver "Login manual".

## Comandos
levantarlo
tmux new -s bot ./agent.sh

restaurar la session
tmux attach -t bot

frenarlo
tmux send-keys -t bot C-c