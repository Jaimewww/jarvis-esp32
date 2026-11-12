# Plan de pruebas y riesgos

Actualizado el **2026-11-11**. Son pruebas futuras: no hay firmware ni sistema integrado
para ejecutarlas. Los roles corresponden al [plan](planificacion.md).

## Checks por subsistema

| Responsable | Prueba prevista | Evidencia a registrar |
|---|---|---|
| H2 | «Jarvis» con voces del equipo, silencio y ruido de aula. | Aciertos, falsos rechazos y falsas activaciones; condiciones y modelo usado. |
| H2 | Pulsador como respaldo y detección de fin del habla. | Captura completa sin botón en el flujo principal; respaldo operativo. |
| H2/H3 | Captura a 16 kHz y reproducción I2S. | Claridad, clipping, cortes y efecto acústico de la caja. |
| H1/H2 | Wi-Fi y audio simultáneos; 30 min de uso. | Ruido, consumo, brownouts, memoria y reinicios. |
| H2/S1 | Envío a PC, respuesta de audio y pérdida de Wi-Fi. | Formatos acordados, tiempos, errores visibles y recuperación. |
| S1 | Whisper frente a Parakeet local con los mismos audios en español. | Errores de transcripción, latencia y recursos; decisión justificada, no prefijada. |
| S1/S2 | Proceso Gentle Shell, respuesta y tareas largas. | Integración real, fallos del proceso y estado pensando mientras trabaja. |
| S2 | Abrir aplicación y cambiar volumen. | Efecto real en PC y registro de éxito/error en TUI. |
| S2 | Acción fuera del catálogo acordado. | Rechazo visible sin ejecución no autorizada. |
| Todos | Veinte turnos de voz consecutivos. | Éxitos/fallos, tiempos por etapa y estados de anillo/TUI. |

La autenticación, los mensajes y los límites de tiempo deben acordarse antes de escribir
pruebas de contrato. Si se usan APIs externas, incluir errores de credenciales/cuota;
no asumir dependencia obligatoria de nube ni funcionamiento enteramente offline.

## Demo de aceptación prevista

1. Encender por USB, con PC encendida y conectada a la misma red.
2. Decir «Jarvis» y una pregunta sin pulsar: respuesta audible y conversación en TUI.
3. Pedir abrir una aplicación y cambiar volumen: verificar efecto y registro.
4. Observar reposo, escuchando, pensando, hablando y error; incluir una tarea larga.
5. Probar pérdida de red y recuperación; ensayar el pulsador de respaldo por separado.
6. Repetir con el prototipo en su carcasa, sin convertir el respaldo en modo principal.

No se prueban como capacidades del producto domótica, batería, operación sin PC,
identificación de hablante ni multiusuario simultáneo: están fuera de alcance.

## Riesgos prioritarios

WakeNet con entrenamiento sintético puede no generalizar a voces reales; medir antes
de prometer fiabilidad. Ruido eléctrico/acústico, memoria, permisos del SO y latencia
del agente necesitan evidencia del montaje. No hay resultados aprobados todavía.
