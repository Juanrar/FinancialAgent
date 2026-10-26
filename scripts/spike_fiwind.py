"""Spike descartable de la etapa 2: maneja la web de Fiwind con agent-browser. No commitear.

Uso:
    uv run python scripts/spike_fiwind.py login
    uv run python scripts/spike_fiwind.py sesion
    uv run python scripts/spike_fiwind.py buscar DESTINO [--oculto]
    uv run python scripts/spike_fiwind.py retiro DESTINO "Titular" MONTO [--oculto]

login abre una ventana visible para iniciar sesión a mano. El perfil queda en
data/browser/fiwind. sesion abre el panel sin ventana y dice si la sesión sigue
abierta. Ninguno de los dos mueve plata.

retiro manda pesos reales a un CBU, CVU o alias. Verifica titular, destino y
monto en la página antes de tocar "Enviar" y pide escribir SI.
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

LOGIN_URL = "https://panel.fiwind.io/auth/login"
PANEL_URL = "https://panel.fiwind.io/"
WITHDRAWAL_URL = "https://panel.fiwind.io/dashboard/withdrawal/fiat"
PROFILE = Path("data/browser/fiwind").resolve()
SESSION = "fiwind"
DESTINATION_INPUT = "input[placeholder='Ingresá el CBU, CVU o Alias']"
AMOUNT_INPUT = "input[placeholder='Ingresá el monto en pesos']"


def browser(*args: str, headed: bool = False, check: bool = True) -> str:
    binary = shutil.which("agent-browser")
    if binary is None:
        sys.exit("No encuentro agent-browser. Instalalo con: npm install -g agent-browser")
    command = [binary, "--session", SESSION]
    # --profile y --headed solo al arrancar: con el navegador abierto, un comando
    # que vuelve a pasar --profile cae en una página en blanco.
    if args[0] == "open":
        command += ["--profile", str(PROFILE)]
        if headed:
            command.append("--headed")
    print(f"-> agent-browser {' '.join(args)}", flush=True)
    # Salida a un archivo y no a un pipe: el daemon que lanza open hereda el pipe,
    # lo deja abierto y Python se queda esperando para siempre.
    with tempfile.TemporaryFile("w+", encoding="utf-8") as out:
        try:
            result = subprocess.run(
                [*command, *args],
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            sys.exit(f"agent-browser {' '.join(args)} no respondió en 60 segundos.")
        out.seek(0)
        text = out.read()
    if check and result.returncode != 0:
        sys.exit(f"agent-browser {' '.join(args)} falló:\n{text}")
    lines = [line for line in text.splitlines() if "ignored: daemon already running" not in line]
    return "\n".join(lines).strip()


def fiwind_amount(amount: Decimal) -> str:
    """Formato que muestra Fiwind: 1.500,00 ARS."""
    us = f"{amount:,.2f}"
    return us.replace(",", "_").replace(".", ",").replace("_", ".") + " ARS"


def require(text: str, expected: str, what: str) -> None:
    if expected.lower() not in text.lower():
        browser("close", check=False)
        sys.exit(f"Frené: la página no muestra {what} '{expected}'.\nTexto de la página:\n{text}")


def cmd_login(args) -> None:
    PROFILE.mkdir(parents=True, exist_ok=True)
    browser("open", LOGIN_URL, headed=True)
    input("Iniciá sesión en la ventana. Cuando veas el panel, apretá Enter acá.")
    print(f"URL actual: {browser('get', 'url')}")
    browser("close")
    print(f"Perfil guardado en {PROFILE}")


def cmd_sesion(args) -> None:
    if not PROFILE.exists():
        sys.exit("No hay perfil. Corré primero: uv run python scripts/spike_fiwind.py login")
    browser("open", PANEL_URL)
    browser("wait", "--load", "networkidle")
    url = browser("get", "url")
    browser("close")
    if "/auth/" in url:
        print(f"La sesión venció. URL: {url}")
    else:
        print(f"Sesión activa. URL: {url}")


def open_withdrawal_form(headed: bool) -> None:
    # Si el daemon ya corre, ignora --profile y --headed: hay que cerrarlo antes.
    browser("close", check=False)
    browser("open", WITHDRAWAL_URL, headed=headed)
    browser("wait", "--load", "networkidle")
    if "/auth/" in browser("get", "url"):
        browser("close", check=False)
        sys.exit("La sesión venció. Corré: uv run python scripts/spike_fiwind.py login")
    # Abriendo la URL directo, Fiwind no elige moneda y no muestra el campo del destino.
    browser("find", "role", "combobox", "click", "--name", "Seleccioná una moneda")
    browser("find", "role", "option", "click", "--name", "ARS", "--exact")


def cmd_buscar(args) -> None:
    open_withdrawal_form(headed=not args.oculto)
    browser("wait", DESTINATION_INPUT)
    browser("fill", DESTINATION_INPUT, args.destino)
    browser("find", "role", "button", "click", "--name", "Buscar")
    # Espera fija para ver también lo que muestra Fiwind si no encuentra el destino.
    browser("wait", "5000")
    print("=== get text form ===")
    print(repr(browser("get", "text", "form", check=False)))
    print("=== snapshot -c ===")
    print(browser("snapshot", "-c", check=False))
    browser("close")


def cmd_retiro(args) -> None:
    try:
        amount = Decimal(args.monto.replace(",", "."))
    except InvalidOperation:
        sys.exit("El monto tiene que ser un número, por ejemplo 10 o 10.50.")
    if amount <= 0 or amount != amount.quantize(Decimal("0.01")):
        sys.exit("El monto tiene que ser positivo y con 2 decimales como máximo.")
    is_account_number = re.fullmatch(r"[0-9]{22}", args.destino) is not None

    open_withdrawal_form(headed=not args.oculto)
    # Los campos aparecen después de elegir moneda o destino, y find no espera.
    browser("wait", DESTINATION_INPUT)
    browser("fill", DESTINATION_INPUT, args.destino)
    browser("find", "role", "button", "click", "--name", "Buscar")
    browser("wait", "--text", "Destinatario")
    form = browser("get", "text", "form")
    require(form, args.titular, "el titular")
    if is_account_number:
        require(form, args.destino, "el CBU o CVU")

    browser("wait", AMOUNT_INPUT)
    browser("fill", AMOUNT_INPUT, str(amount))
    browser("find", "role", "button", "click", "--name", "Continuar")
    browser("wait", "--text", "Confirmar envío")
    dialog = browser("get", "text", "[role=dialog]")
    require(dialog, fiwind_amount(amount), "el monto")
    require(dialog, args.titular, "el titular")
    require(dialog, args.destino, "el destino")

    print(dialog)
    if input("Escribí SI para tocar Enviar: ").strip().upper() != "SI":
        browser("close", check=False)
        sys.exit("Cancelado. No se mandó nada.")
    browser("find", "role", "button", "click", "--name", "Enviar", "--exact")
    browser("wait", "--text", "Entendido")
    print(browser("get", "text", "[role=dialog]"))
    browser("find", "role", "button", "click", "--name", "Entendido")
    browser("close")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(required=True)
    sub.add_parser("login", help="Iniciar sesión a mano").set_defaults(func=cmd_login)
    sub.add_parser("sesion", help="Ver si la sesión sigue abierta").set_defaults(func=cmd_sesion)

    buscar = sub.add_parser("buscar", help="Busca un destino sin cargar monto. No mueve plata")
    buscar.add_argument("destino", help="CBU, CVU o alias")
    buscar.add_argument("--oculto", action="store_true", help="Sin ventana visible")
    buscar.set_defaults(func=cmd_buscar)

    retiro = sub.add_parser("retiro", help="Retiro real a un CBU, CVU o alias")
    retiro.add_argument("destino", help="CBU, CVU o alias")
    retiro.add_argument("titular", help="Nombre del titular tal como lo muestra Fiwind")
    retiro.add_argument("monto", help="En pesos, por ejemplo 10 o 10.50")
    retiro.add_argument("--oculto", action="store_true", help="Sin ventana visible")
    retiro.set_defaults(func=cmd_retiro)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
