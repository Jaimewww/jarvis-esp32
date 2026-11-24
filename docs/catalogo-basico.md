# Catálogo básico del camino rápido

Esta es la lista acordada para el camino rápido (D13, D22): las órdenes que el backend
reconoce sin el agente y ejecuta directo. Lo programa la pista S1 (D21). Las tablas de
abajo sirven como casos de prueba: cada fila «entra» tiene que producir esa acción, y
cada fila «no entra» tiene que pasar al agente sin demora.

Meta: desde el fin del habla hasta la acción, **~0,5-1 s** (hoy ~4,9 s).

## Reglas del reconocedor

1. **Normalizar** el texto transcrito antes de comparar:
   - pasar a minúsculas y quitar tildes, signos y espacios repetidos;
   - quitar el prefijo «jarvis» y las cortesías «por favor» y «porfa» al principio o
     al final.
2. **La frase completa tiene que coincidir** con un patrón; no alcanza con que una parte
   coincida. «abrí spotify y poné algo de rock» no entra.
3. **Ante la duda, al agente:** si no coincide o coincide con más de una acción, el
   pedido sigue como hoy y el reconocedor no le agrega demora.
4. **Voseo y tuteo:** se aceptan las dos formas («abrí» y «abre», «subí» y «sube»).
5. **Sin confirmación:** son acciones de bajo riesgo (D8).

## 1. Multimedia: `media_key`

| Dice | Acción |
|---|---|
| pausa, pausá, pausa la música, pausar | `{"type":"media_key","value":"play_pause"}` |
| seguí, sigue, reanudá, reanuda, play, dale play, reproducí, reproduce | `{"type":"media_key","value":"play_pause"}` |
| siguiente, siguiente canción, pasá la canción, pasa de canción | `{"type":"media_key","value":"next"}` |
| anterior, canción anterior, volvé a la anterior, vuelve a la anterior | `{"type":"media_key","value":"previous"}` |

Como `playerctl play-pause` alterna, «pausa» con la música ya pausada la reanuda. Si
después se quiere separar pausar de reanudar, se agregan los valores `pause` y `play`
(eso cambia el contrato C4).

## 2. Volumen: `volume`

| Dice | Acción |
|---|---|
| subí el volumen, sube el volumen, más volumen, subí, más fuerte | `{"type":"volume","value":"up"}` |
| bajá el volumen, baja el volumen, menos volumen, bajá, más bajo | `{"type":"volume","value":"down"}` |
| silencio, silenciá, mutea, muteá, sin sonido | `{"type":"volume","value":"mute"}` |

«poné el volumen al 30» no entra: necesita un valor numérico que C4 todavía no tiene.

## 3. Abrir aplicaciones: `open_app`, solo con lista cerrada

Patrón: «abrí | abre | abrime | abrir» + nombre, o «poné | pon» + «Spotify». El nombre
se busca en esta tabla. Si no está, el pedido va al agente, que puede usar una
aplicación no probada (D8).

| Nombre dicho | `value` (id de escritorio) |
|---|---|
| spotify | `spotify` (si está instalada) |
| chrome, google chrome, el navegador | `google-chrome` |
| vs code, visual studio code, code, el editor | `com.microsoft.VSCode` |
| terminal, la terminal, ghostty | `com.mitchellh.ghostty` |
| archivos, el explorador de archivos, nautilus | `org.gnome.Nautilus` |
| discord | `discord` |

Los ids son los de esta máquina (`/usr/share/applications`). La lista final vive en la
configuración del backend, no en el código, para poder cambiarla por máquina.

## 4. Hora y fecha: respuesta sin acción

| Dice | Respuesta (sin acción y sin modelo) |
|---|---|
| qué hora es, me decís la hora, hora | «Son las 20:15.» |
| qué día es, qué día es hoy, qué fecha es | «Hoy es lunes 23 de noviembre.» |

Se usa la hora local de la máquina del backend. La respuesta sale en `reply_text` y
`actions` va vacía.

## No entran: van al agente

| Dice | Por qué |
|---|---|
| abrí spotify y poné algo de rock | La frase completa no coincide (regla 2) |
| abrí el correo de Juan | «el correo de Juan» no está en la lista |
| abrí photoshop | No está en la lista; puede resolverlo el agente |
| poné el volumen al 30 | Necesita un valor que C4 todavía no tiene |
| cerrá la ventana | No es del catálogo |
| escribí hola en VS Code | Lo resuelve P2 (`pc_write_in`), no el camino rápido |
| qué hora es en Tokio | La frase completa no coincide |
| pausa el video y abrí chrome | Son dos órdenes; por ahora van al agente |

## Cómo se mide

`python -m jarvis.bench` (T4) incluye órdenes de esta lista. Antes y después de P1 se
comparan la mediana y el peor caso.
