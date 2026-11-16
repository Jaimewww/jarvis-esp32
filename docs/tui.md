# Agente y TUI en la computadora

Diseño actualizado el **2026-11-11**. Desde el 2026-11-16 existe un cliente en
`host/jarvis/` (`client.py` y `tui.py`): la TUI es un cliente WebSocket del daemon de
Gentle Dot (fork `Jaimewww/gentle-dot`, rama `feat/linux-computer-use`) y no un daemon
propio de este repositorio. Gentle Shell corre en segundo plano dentro de ese daemon,
sin interfaz propia; la TUI muestra conversación, estado y registro de acciones.

## Responsabilidades

1. Mostrar lo que dijo el usuario y la respuesta del asistente, con los fragmentos que
   llegan mientras el agente escribe.
2. Registrar acciones sobre la PC, como abrir aplicaciones o pulsar teclas, con su
   estado (`running`, `done`, `failed`) y sus errores.
3. Reflejar el estado del agente — reposo, pensando, trabajando, esperando al usuario,
   reiniciando y error — y los fallos de conexión con el daemon.
4. Indicar que una tarea del agente sigue en curso y cuántos mensajes están en cola.

Los estados «escuchando» y «hablando» son locales del ESP32; la TUI muestra los estados
del agente del daemon: `idle`, `thinking`, `working`, `needs_you`, `restarting` y
`error`.

## Stack y protocolo elegidos

- Python 3.11+ con **Textual** para la ventana y **websockets** para el transporte; sin
  otras dependencias (`host/requirements.txt`).
- Protocolo **WebSocket v1** del daemon, el mismo del escritorio y del navegador
  (`packages/protocol/src/index.ts`): `ws://127.0.0.1:4317/ws`, primer mensaje
  `{"type":"hello","token":…,"protocol":1}`, token de `~/.gentle-dot/token` (modo
  `0600`), y cierre `4401` si el token no vale, `4400` si la versión no se admite.
- El cliente se reconecta con espera creciente (500, 1000, 2000 y 5000 ms), no
  reintenta tras un `4401`, y pide de nuevo `get_history` si un salto en `seq` muestra
  que se perdió un mensaje.
- Qué muestra: conversación, estado del agente, registro de acciones, cola y estado de
  la conexión. Entra texto con Enter y se detiene la respuesta en curso con Ctrl+A.
- Qué no hace: no contesta los diálogos de aprobación (`ask`). Esos los muestra la
  aplicación de escritorio por su canal privado; la TUI solo avisa que están
  esperando. Tampoco adjunta archivos, cambia de conversación ni retrocede a páginas
  anteriores del historial.

Instrucciones de ejecución en [host/README.md](../host/README.md).

## Antes de dar la interfaz por terminada

- Conservar el objetivo de seguridad de un catálogo acotado y rechazo de acciones
  desconocidas. La garantía vive en el fork (grant, parada de emergencia, blocklist,
  límite de frecuencia, confirmaciones y lista de herramientas de solo lectura del
  proxy MCP); la TUI solo muestra y no es una frontera de seguridad. Mostrar una
  acción no equivale a que esté autorizada.
- Confirmar con el backend el arranque del daemon y el formato de conversación, estado
  y resultados de acciones. El protocolo v1 quedó tomado de su código, no de un
  contrato acordado con el equipo del fork, así que un cambio allí rompe este cliente
  (`4400` si sube la versión).
- Fijar el sistema operativo de demostración y comprobar permisos de acciones; en
  Linux/Wayland el catálogo del fork usa `grim`, `wtype` y `gtk-launch`, y rechaza
  clics, arrastres y desplazamiento.
- Probar errores visibles y correspondencia entre resultado real y registro en la TUI,
  incluida la ausencia de daemon y un token inválido.

El input escrito ya está implementado; las capturas de pantalla del diseño anterior
siguen sin ser requisito. Ver [backend](nube.md), [plan](planificacion.md) y
[pruebas](pruebas.md).
