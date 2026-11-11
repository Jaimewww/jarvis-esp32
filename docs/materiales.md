# Lista de Materiales (BOM)

Precios estimados en USD (AliExpress/distribuidores locales), referencia 2025. Los módulos
clónicos suelen costar la mitad y funcionan igual para prototipo.

## Componentes obligatorios

| #   | Componente                 | Especificación                                                                     | Cant. | Precio est. (USD) |
| --- | -------------------------- | ---------------------------------------------------------------------------------- | ----- | ----------------- |
| 1   | ESP32-S3 DevKitC-1         | **con PSRAM** (N8R2 = 8 MB flash + 2 MB PSRAM, o mejor N16R8)                      | 1     | 10–16             |
| 2   | Micrófono digital INMP441  | I2S, omnidireccional                                                               | 1     | 3–5               |
| 3   | Amplificador MAX98357A     | DAC + amp clase D, entrada I2S                                                     | 1     | 4–6               |
| 4   | Altavoz                    | 3 W, 4 Ω u 8 Ω, 40–57 mm                                                           | 1     | 3–5               |
| 5   | Anillo WS2812B (NeoPixels) | 8 o 12 LEDs — núcleo de estado (azul/amarillo/verde/rojo)                          | 1     | 3–5               |
| 6   | Botón pulsador             | Táctil 6×6 o push grande para PTT                                                  | 1     | 0,5–2             |
| 7   | Condensador electrolítico  | **1000 µF / 6,3 V** (filtrado alimentación audio)                                  | 2     | 0,5               |
| 8   | Condensador cerámico       | 100 nF (desacople por módulo)                                                      | 4     | 0,3               |
| 9   | Fuente de alimentación     | USB 5 V ≥ 2 A (cargador de celular) — **no depender solo del puerto USB de la PC** | 1     | 0–8               |
| 10  | Protoboard + jumpers       | Para fase de prototipo                                                             | 1 set | 5–8               |

## Opcionales recomendables

| #   | Componente                       | Para qué                                                | Precio est. |
| --- | -------------------------------- | ------------------------------------------------------- | ----------- |
| 11  | Display OLED SSD1306 0,96" I2C   | Mostrar estado (texto: "escuchando…", "pensando…")      | 3–4         |
| 12  | Placa perfboard o PCB custom     | Versión final soldada                                   | 2–10        |
| 13  | AMS1117-3.3 o regulador dedicado | Alimentar mic/amp a 3,3 V limpio si la placa no alcanza | 1–2         |
| 14 | Cable dupont hembra-hembra | Conexiones entre módulos | 3 |
| 15 | Cable USB-C con líneas de datos | Alimentación del ESP32 + debug serial a la PC | 0–5 |

## Materiales para la caja 3D

| Material | Nota |
|---|---|
| Filamento PLA o PETG (~100–150 g) | PETG si va cerca del sol/calor; PLA alcanza para escritorio |
| Tornillos M2.5 o M3 autorroscantes | Fijar placa y amp al fondo de la caja |
| Insertros roscados M3 (opcional) | Montaje más pro, requiere soldador |
| Malla/espuma acústica fina (opcional) | Dentro de la cámara del altavoz, reduce resonancia |

## Pinout de referencia (ajustar en firmware.md)

| Señal | GPIO ESP32-S3 | Va a |
|---|---|---|
| MIC_SCK (BCLK) | GPIO 4 | INMP441 SCK |
| MIC_WS | GPIO 5 | INMP441 WS |
| MIC_SD (in) | GPIO 6 | INMP441 SD |
| AMP_BCLK | GPIO 15 | MAX98357A BCLK |
| AMP_LRC | GPIO 16 | MAX98357A LRC |
| AMP_DIN (out) | GPIO 7 | MAX98357A DIN |
| NEOPIXEL | GPIO 48 | Anillo WS2812B (data) |
| BOTÓN PTT | GPIO 0 (BOOT) o GPIO 1 | Pulsador a GND |
| GAIN MAX98357A | — | Dejar por defecto (9 dB) o soldar a GND para menos ganancia |

> Nota: el WS2812B se alimenta a 5 V con un condensador de 1000 µF en paralelo; la línea de
> datos funciona a 3,3 V y en la práctica alcanza sin level shifter en anillos cortos.

## Costo total estimado

- **Núcleo (1–9): ~25–45 USD** por equipo.
- Con opcionales y caja: ~40–60 USD.
- Sugerencia: comprar **1 kit completo + repuestos** (2do INMP441 y MAX98357A: son los que más
  se queman si se conectan mal).
