# Agente y TUI en la computadora

Diseño actualizado el **2026-11-11**; no existe aún implementación en `host/`.
Gentle Shell corre en segundo plano sin interfaz propia. La TUI muestra conversación,
estado y registro de acciones; no se presupone un daemon Python que sustituya al agente.

## Responsabilidades previstas

1. Mostrar lo que dijo el usuario y la respuesta del asistente.
2. Registrar acciones sobre la PC, como abrir aplicaciones y cambiar volumen, y sus errores.
3. Reflejar reposo, escuchando, pensando, hablando y error junto con el anillo.
4. Indicar que una tarea del agente sigue en curso y mostrar fallos de conexión.

Gentle Dot es referencia para el proceso persistente y sus mensajes, no una integración
probada. Python/Textual y bibliotecas de control del sistema del diseño anterior siguen
como candidatas; no hay stack ni protocolo RPC/WebSocket confirmado.

## Antes de construir la interfaz

- Acordar con backend la conexión a Gentle Shell y el formato de conversación, estado
  y resultados de acciones; no imponer el antiguo JSON como contrato vigente.
- Fijar el sistema operativo de demostración y comprobar permisos de acciones.
- Conservar el objetivo de seguridad de un catálogo acotado y rechazo de acciones
  desconocidas; validar cómo aplicarlo al agente, sin afirmar que ya está garantizado.
- Probar errores visibles y correspondencia entre resultado real y registro en TUI.

El input escrito y las capturas de pantalla del diseño anterior no son requisitos de
esta actualización. Ver [backend](nube.md), [plan](planificacion.md) y [pruebas](pruebas.md).
