# FinancialAgent

Agente personal de Juan por Telegram. Guarda contactos y memoria, y manda transferencias en pesos desde su cuenta de Fiwind con la confirmación de Juan.

## Lenguaje

### Fiwind

**Wallet**:
La cuenta de Fiwind de Juan. De ahí sale la plata de las transferencias y de ahí se lee el saldo.
_Evitar_: cliente de Fiwind, billetera, cuenta

**Sesión de Fiwind**:
El inicio de sesión de Juan en Fiwind que queda guardado en la PC. Mientras está vencida, ninguna operación con la wallet funciona.
_Evitar_: sesión (sola, se confunde con la sesión del agente), login

**Aviso de sesión vencida**:
El mensaje de Telegram que le dice a Juan que venció la sesión de Fiwind, con el botón "Iniciar sesión".
_Evitar_: alerta, notificación

### Agente

**Sesión del agente**:
La charla abierta con Claude, que se cierra sola por inactividad.
_Evitar_: sesión (sola), conversación
