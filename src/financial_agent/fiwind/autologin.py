"""Renovación de la sesión de Fiwind con scripts/fiwind_autologin.sh.

El script abre un Chromium con ventana en una pantalla virtual, que pasa el
reCAPTCHA del login. El navegador del bot tiene que estar cerrado: los dos usan
el mismo perfil.
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

SCRIPT = Path("scripts/fiwind_autologin.sh")
TIMEOUT = 240
# El script cierra Chromium y la pantalla virtual al recibir SIGTERM.
CLEANUP_TIMEOUT = 30


def run_autologin(script: Path = SCRIPT, timeout: float = TIMEOUT) -> bool:
    """Devuelve True si la sesión quedó activa."""
    # Sin pipes: Chromium los hereda y los deja abiertos (ver browser.py).
    process = subprocess.Popen(
        ["sh", str(script)], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        code = process.wait(timeout)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(CLEANUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
        logger.warning("fiwind autologin: timeout")
        return False
    logger.info("fiwind autologin: código %s", code)
    return code == 0
