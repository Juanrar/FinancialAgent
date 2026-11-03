#!/bin/sh
# Login manual en Fiwind.
#
# Abre Chromium con el perfil del bot en una pantalla virtual y la publica con
# noVNC en el puerto 6080. Juan la ve desde su PC por un túnel SSH, inicia
# sesión y cierra la ventana. Chromium guarda la sesión en el perfil al cerrarse
# y el bot la reusa. Antes hay que frenar el bot, que usa el mismo perfil.
set -eu

PROFILE="${FIWIND_PROFILE_DIR:-data/browser/fiwind}"
case "$PROFILE" in
    /*) ;;
    *) PROFILE="/app/$PROFILE" ;;
esac
mkdir -p "$PROFILE"
# Un Chromium cortado de golpe deja el candado del perfil con el nombre de su
# contenedor, y otro contenedor no puede abrirlo: lo toma como "en uso en otra
# computadora".
rm -f "$PROFILE/SingletonLock" "$PROFILE/SingletonCookie" "$PROFILE/SingletonSocket"

export DISPLAY=:99
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp > /dev/null 2>&1 &
for _ in $(seq 50); do
    [ -e /tmp/.X11-unix/X99 ] && break
    sleep 0.1
done
x11vnc -display :99 -localhost -nopw -forever -shared -quiet > /dev/null 2>&1 &
websockify --web /usr/share/novnc 6080 localhost:5900 > /dev/null 2>&1 &

# --password-store=basic es el cifrado de cookies que usa agent-browser. Con
# otro, el bot no puede leer la sesión.
chromium --no-sandbox --disable-dev-shm-usage --password-store=basic \
    --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check \
    --window-position=0,0 --window-size=1280,800 \
    https://panel.fiwind.io/dashboard/home > /dev/null 2>&1 &
CHROME=$!

# Con Ctrl+C o docker stop, cierra la ventana como si fuera con el mouse. Así
# Chromium guarda las cookies. Si recibe TERM, solo quedan las que llegó a
# escribir en su guardado periódico, cada unos 30 segundos.
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

# El primer wait se corta con la señal. El segundo espera a que Chromium termine
# de guardar.
wait "$CHROME" || true
wait "$CHROME" 2>/dev/null || true
echo "Chromium cerrado. La sesión quedó en el perfil."
