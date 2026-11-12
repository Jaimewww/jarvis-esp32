# Caja impresa en 3D — Especificaciones

Diseño para el prototipo J.A.R.V.I.S. de escritorio. Herramienta sugerida: **Fusion 360**
(para estudiantes es gratis) o FreeCAD. Archivos CAD previstos en `cad/` (Fase 7), aún inexistentes.
Actualización 2026-11-11: alimentación USB, sin batería; pulsador solo como respaldo.

## Concepto de diseño

Cuerpo cilíndrico o prismático tipo "torre", con el anillo WS2812B como núcleo visible
detrás de un difusor. El objetivo estético es replicar el núcleo de J.A.R.V.I.S.: el anillo
cambia de color según el estado y se ve desde afuera.

```
        ┌─ tapa superior (tornillos M3)
        │  ┌─ difusor translúcido (anillo LED debajo)
        │  │
   ═════╪══╪═══  ← anillo WS2812B con difusor (PLA blanco o TPU)
        │  │
        │  └─ pared con rejilla frontal del altavoz (cámara acústica)
        │
        └─ base con antideslizante, paso de cable USB
```

## Requisitos funcionales

| # | Requisito | Detalle |
|---|---|---|
| RF1 | Cámara acústica separada | El altavoz en su propio volumen cerrado (salvo agujeros de salida); evita que el mic capte el cuerpo resonando |
| RF2 | Agujero del micrófono | Ø 2–3 mm frente al INMP441, con cámara de aire mínima; nunca taparlo con superficie plana directa |
| RF3 | Difusor del anillo LED | Pared de ~1,5 mm en PLA blanco/transparente, o TPU; separación de 3–5 mm entre LED y difusor |
| RF4 | Ventilación/pasaje de cables | Tapa trasera con salida para USB y acceso a botón BOOT/RESET |
| RF5 | Montaje sin cola (ideal) | Soportes internos con tornillos M2.5/M3 autorroscantes para ESP32, amp y altavoz |
| RF6 | Pulsador de respaldo accesible | En el frente o arriba; taladro con tolerancia +0,3 mm; la activación principal es por voz |

## Medidas de referencia (medir las piezas físicas antes de cerrar el modelo)

| Componente | Medida típica |
|---|---|
| ESP32-S3 DevKitC | 69 × 26 mm, pines altura 2–4 mm |
| Altavoz 45 mm, 4 Ω, 3 W | Medir diámetro exterior, profundidad y fijaciones del comprado |
| Anillo WS2812 de 8 LED | Medir diámetro y altura del comprado |
| MAX98357A | 15,5 × 13,5 mm |

**Diámetro interior sugerido: 70–80 mm; altura total: 100–130 mm.**

## Reglas de impresión

- **Paredes:** 2,4 mm mínimo (6 perímetros en modo espiral para el difusor).
- **Material:** PLA para V1/V2; PETG para la final si va cerca de fuentes de calor.
- **Relleno:** 15–20 % en cuerpo, 40 % en soportes de montaje.
- **Tolerancias:** agujeros +0,3 mm; para encajes a presión +0,15 mm. Las impresoras
  caseras varían: imprimir primero una **pieza de prueba de tolerancias** (combs de 0,1–0,4 mm).
- **Sin soportes** en lo posible: orientar rejillas del altavoz hacia arriba al imprimir, o
  diseñarlas en puente (bridge) de ≤ 10 mm.

## Plan de iteración (Fase 7)

| Versión | Objetivo | Qué se prueba |
|---|---|---|
| V1 | Caja "fea pero cierra" | Tolerancias reales, posiciones de módulos, acceso al USB |
| V2 | Acústica y estética | Cámara del altavoz sellada, agujero del mic, difusor del LED, sin buzz mecánico |
| V3 | Final | Perfboard montada, manejo de cables, tapa con tornillos, acabado (lijado/pintado opcional) |

## Checklist antes de imprimir

- [ ] Medidas tomadas de las piezas físicas con calibre, no del datasheet.
- [ ] Agujero del mic alineado con el puerto del INMP441.
- [ ] El anillo LED queda a 3–5 mm del difusor.
- [ ] Acceso al pulsador de respaldo sin abrir la caja; validar «Jarvis» con la carcasa cerrada.
- [ ] Cable USB entra sin forzar y la caja no se apoya sobre el cable.
- [ ] Pieza de tolerancias impresa y validada en la impresora objetivo.
