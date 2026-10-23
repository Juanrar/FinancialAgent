HOLDER, BANK, CVU, ALIAS = "Ana María Pérez", "Lemon", "0000168300000000123459", "Ana.M.Perez.Lemon"

LOOKUP_FOUND = (f"Destinatario\nAP\n{HOLDER}\n{BANK}\n- CVU: {CVU}\nclose\nMonto\nMAX\n"
                "Usando tu balance de\nARS\n(15,00 ARS)\nContinuar")
LOOKUP_NOT_FOUND = ("Ingresá el destino\nNo se encontraron resultados que coincidan con tu"
                    " búsqueda\nBuscar")
CONFIRM_DIALOG = (f"close\nConfirmar envío\nAP\nVas a enviar\n10,00 ARS\nNombre y apellido\n"
                  f"{HOLDER}\nCVU\n{CVU}\nALIAS\n{ALIAS}\nBanco\n{BANK}\nEnviar")
RESULT_DIALOG = (f"close\n10,00 ARS\n23 oct, 2026 - 13:09 hs\nEstado\nEn proceso\nOperación\n"
                 f"Retiro\nTitular\n{HOLDER}\nCUIT\n27-12345678-3\nBanco\n{BANK}\nCVU\n{CVU}\n"
                 "Entendido")
DASHBOARD = """- generic
  - main
    - generic
      - heading "Primeros pasos" [level=2, ref=e8]
    - StaticText "Balance disponible"
    - StaticText "Ver precios en"
    - combobox "ARS" [expanded=false, ref=e3]
      - generic [ref=e11] clickable [cursor:pointer]
    - StaticText "3"
    - StaticText "00"
    - generic
      - image
      - StaticText "ARS"
"""
