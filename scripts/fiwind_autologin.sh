# Renueva la sesión de Fiwind sin intervención, en la VM.
#
# Abre un Chromium con ventana en una pantalla virtual, con el perfil del bot, y
# lo maneja por CDP. Así el login pasa el reCAPTCHA, que rechaza al Chromium sin
# ventana que lanza agent-browser. Si la sesión sigue activa, no hace nada.
#
# Salida: 0 sesión activa, 1 falla técnica, 2 hace falta una persona.
# Después de un 2 no vuelve a intentar hasta que borres el archivo de bloqueo:
# varios intentos fallidos pueden bloquear la cuenta.
set -eu
cd "$(dirname "$0")/.."

env_value() {
    sed -n "s/^$1=//p" .env 2>/dev/null | tail -1 \
        | sed -e 's/\r$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}
setting() {
    eval "current=\${$1:-}"
    if [ -n "$current" ]; then echo "$current"; else env_value "$1"; fi
}

EMAIL="$(setting FIWIND_EMAIL)"
PASSWORD="$(setting FIWIND_PASSWORD)"
PROFILE="$(setting FIWIND_PROFILE_DIR)"
PROFILE="${PROFILE:-data/browser/fiwind}"
case "$PROFILE" in
    /*) ;;
    *) PROFILE="$PWD/$PROFILE" ;;
esac
CHROMIUM="$(setting AGENT_BROWSER_EXECUTABLE_PATH)"
CHROMIUM="${CHROMIUM:-chromium}"
EXTRA_ARGS="$(setting AGENT_BROWSER_ARGS | tr ',' ' ')"
BLOCK="data/fiwind_autologin.bloqueado"
SCREEN=98
PORT=9223
PANEL_URL="https://panel.fiwind.io/dashboard/home"
EMAIL_INPUT="input[placeholder='Tu email']"
PASSWORD_INPUT="input[type=password]"
LOGIN_DONE_JS="location.pathname !== '/auth/login' || document.body.innerText.includes('incorrectos')"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') fiwind_autologin: $*"; }

if [ -e "$BLOCK" ]; then
    log "bloqueado por un intento anterior ($(cat "$BLOCK")). Revisá y borrá $BLOCK."
    exit 2
fi
if [ -z "$EMAIL" ] || [ -z "$PASSWORD" ]; then
    log "faltan FIWIND_EMAIL o FIWIND_PASSWORD"
    exit 1
fi
if pgrep -f -- "--user-data-dir=$PROFILE" > /dev/null 2>&1; then
    log "el perfil está en uso, se reintenta en la próxima corrida"
    exit 1
fi

mkdir -p "$PROFILE"
rm -f "$PROFILE/SingletonLock" "$PROFILE/SingletonCookie" "$PROFILE/SingletonSocket"

export DISPLAY=":$SCREEN"
Xvfb "$DISPLAY" -screen 0 1280x800x24 -nolisten tcp > /dev/null 2>&1 &
XVFB=$!
CHROME=""

ab() { agent-browser --session fiwind-autologin --cdp "$PORT" "$@"; }

cleanup() {
    ab close > /dev/null 2>&1 || true
    if [ -n "$CHROME" ] && kill -0 "$CHROME" 2>/dev/null; then
        # Cerrar la ventana hace que Chromium guarde la sesión en el perfil.
        for w in $(xdotool search --onlyvisible --class chromium 2>/dev/null); do
            xdotool windowfocus --sync "$w" 2>/dev/null || true
            xdotool key --window "$w" ctrl+shift+w 2>/dev/null || true
        done
        for _ in $(seq 20); do
            kill -0 "$CHROME" 2>/dev/null || break
            sleep 0.5
        done
        kill -TERM "$CHROME" 2>/dev/null || true
        wait "$CHROME" 2>/dev/null || true
    fi
    kill "$XVFB" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 1' INT TERM

for _ in $(seq 50); do
    [ -e "/tmp/.X11-unix/X$SCREEN" ] && break
    sleep 0.1
done

# shellcheck disable=SC2086
"$CHROMIUM" $EXTRA_ARGS --password-store=basic --user-data-dir="$PROFILE" \
    --no-first-run --no-default-browser-check --remote-debugging-port="$PORT" \
    --window-position=0,0 --window-size=1280,800 "$PANEL_URL" > /dev/null 2>&1 &
CHROME=$!

ready=""
for _ in $(seq 60); do
    if curl -s --max-time 2 "http://127.0.0.1:$PORT/json/version" > /dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 0.5
done
if [ -z "$ready" ]; then
    log "Chromium no abrió el puerto de control"
    exit 1
fi

page_url() { ab get url 2>/dev/null | tail -1; }
field_is() { [ "$(ab get value "$1" 2>/dev/null | tail -1)" = "$2" ]; }
# Chromium puede traer el campo cargado. Sin vaciarlo, lo escrito se suma y
# Fiwind marca "Email inválido".
clear_and_type() {
    ab click "$1" > /dev/null
    ab press Control+a > /dev/null
    ab press Backspace > /dev/null
    ab type "$1" "$2" > /dev/null
}

ab wait --load networkidle > /dev/null 2>&1 || true
ab wait 3000 > /dev/null 2>&1 || true
url="$(page_url)"
case "$url" in
    *panel.fiwind.io/auth/*) ;;
    *panel.fiwind.io/*)
        log "la sesión sigue activa"
        exit 0
        ;;
    *)
        log "no pude abrir Fiwind"
        exit 1
        ;;
esac

log "sesión vencida, inicio sesión"
if ! clear_and_type "$EMAIL_INPUT" "$EMAIL"; then
    log "no pude cargar el email"
    exit 1
fi
ab wait 4000 > /dev/null 2>&1 || true
if ! clear_and_type "$PASSWORD_INPUT" "$PASSWORD"; then
    log "no pude cargar la contraseña"
    exit 1
fi
if ! field_is "$EMAIL_INPUT" "$EMAIL" || ! field_is "$PASSWORD_INPUT" "$PASSWORD"; then
    log "los campos no quedaron bien cargados, no envío el formulario"
    exit 1
fi
ab find role button click --name Ingresar > /dev/null
ab wait --fn "$LOGIN_DONE_JS" > /dev/null 2>&1 || true

url="$(page_url)"
case "$url" in
    *"/auth/authorize-device"*)
        echo "Fiwind pidió el código del mail" > "$BLOCK"
        log "Fiwind pidió el código del mail. Hacé el login manual y borrá $BLOCK."
        exit 2
        ;;
    *"/auth/"*)
        echo "Fiwind no aceptó el login" > "$BLOCK"
        log "Fiwind no aceptó el login. Revisá las credenciales y borrá $BLOCK."
        exit 2
        ;;
    *panel.fiwind.io/*)
        # Margen para que Chromium escriba las cookies antes de cerrar.
        sleep 3
        log "sesión iniciada"
        exit 0
        ;;
    *)
        log "Fiwind quedó en una página inesperada"
        exit 1
        ;;
esac
