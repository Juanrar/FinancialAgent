set -eu

PROFILE="${FIWIND_PROFILE_DIR:-data/browser/fiwind}"
case "$PROFILE" in
    /*) ;;
    *) PROFILE="$PWD/$PROFILE" ;;
esac
mkdir -p "$PROFILE"
if pgrep -f -- "--user-data-dir=$PROFILE" > /dev/null 2>&1; then
    echo "Hay un Chromium usando el perfil. Frená el bot antes del login." >&2
    exit 1
fi


rm -f "$PROFILE/SingletonLock" "$PROFILE/SingletonCookie" "$PROFILE/SingletonSocket"

export DISPLAY=:99
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp > /dev/null 2>&1 &
for _ in $(seq 50); do
    [ -e /tmp/.X11-unix/X99 ] && break
    sleep 0.1
done
x11vnc -display :99 -localhost -nopw -forever -shared -quiet > /dev/null 2>&1 &

websockify --web /usr/share/novnc "${NOVNC_LISTEN:-127.0.0.1:6080}" localhost:5900 > /dev/null 2>&1 &

"${AGENT_BROWSER_EXECUTABLE_PATH:-chromium}" $(echo "${AGENT_BROWSER_ARGS:-}" | tr ',' ' ') \
    --password-store=basic \
    --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check \
    --window-position=0,0 --window-size=1280,800 \
    https://panel.fiwind.io/dashboard/home > /dev/null 2>&1 &
CHROME=$!

cerrar() {
    for w in $(xdotool search --onlyvisible --class chromium 2>/dev/null); do
        xdotool windowfocus --sync "$w" 2>/dev/null || true
        xdotool key --window "$w" ctrl+shift+w 2>/dev/null || true
    done
    for _ in $(seq 16); do
        kill -0 "$CHROME" 2>/dev/null || return 0
        sleep 0.5
    done
    kill -TERM "$CHROME" 2>/dev/null || true
}
trap cerrar INT TERM

echo "Con el túnel SSH abierto, entrá a http://localhost:6080/vnc.html y tocá Connect."
echo "Iniciá sesión en Fiwind y cerrá la ventana de Chromium (o Ctrl+C acá)."

wait "$CHROME" || true
wait "$CHROME" 2>/dev/null || true
echo "Chromium cerrado. La sesión quedó en el perfil."
