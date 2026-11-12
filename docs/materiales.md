# Lista de materiales (BOM)

Precios en USD consultados el **2026-11-11**, transcritos del presupuesto del informe;
no son cotizaciones actuales ni compras realizadas. Sustituyen las estimaciones de 2025.

## Componentes obligatorios

| Componente | Tienda / ciudad | Cant. | Unitario USD | Subtotal USD |
|---|---|---:|---:|---:|
| ESP32-S3 DevKit N16R8, 16 MB flash / 8 MB PSRAM | bmlogics / Loja | 1 | 15,25 | 15,25 |
| Micrófono INMP441 I2S | bmlogics / Loja | 1 | 6,90 | 6,90 |
| Amplificador MAX98357A I2S | Sigma Elektronik / Guayaquil | 1 | 7,50 | 7,50 |
| Parlante 45 mm, 4 Ω, 3 W | Megatronica / Quito | 1 | 2,50 | 2,50 |
| Anillo de 8 LED WS2812 | AV Electronics / Quito | 1 | 3,40 | 3,40 |
| Pulsador táctil 6 mm, respaldo de activación | AV Electronics / Quito | 1 | 0,15 | 0,15 |
| Condensador electrolítico 1000 µF, 16 V | Megatronica / Quito | 2 | 0,35 | 0,70 |
| Condensador cerámico 100 nF | Megatronica / Quito | 4 | 0,10 | 0,40 |
| Cargador USB 5 V, 3 A | Megatronica / Quito | 1 | 5,94 | 5,94 |
| Kit protoboard 830 puntos + 65 cables | bmlogics / Loja | 1 | 7,90 | 7,90 |
| **Total obligatorio** | | | | **50,64** |

## Componentes opcionales

| Componente | Tienda / ciudad | Cant. | Unitario USD | Subtotal USD |
|---|---|---:|---:|---:|
| OLED SSD1306 de 0,96 pulgadas | bmlogics / Loja | 1 | 5,50 | 5,50 |
| Placa perforada 5 × 7 cm | Megatronica / Quito | 1 | 0,95 | 0,95 |
| Regulador AMS1117 de 3,3 V | Megatronica / Quito | 1 | 1,00 | 1,00 |
| Kit de 40 cables dupont hembra-hembra | Megatronica / Quito | 1 | 1,99 | 1,99 |
| Cable USB-A a USB-C con datos | APM Electrónica / Sangolquí | 1 | 4,50 | 4,50 |
| **Total opcional** | | | | **13,94** |

**Total con opcionales: USD 64,58**, sin envío ni filamento PLA para la carcasa.
Primero se buscaron precios en Loja; solo placa, micrófono, protoboard y pantalla tenían
precio publicado allí: USD 35,55, con retiro en tienda sin costo. Para los demás se
recurrió a Quito, Sangolquí y Guayaquil. Mega Electronics de Loja no publicaba precios.

bmlogics y AV Electronics incluyen IVA; Megatronica, Sigma Elektronik y APM Electrónica
no lo indican. Si los precios de estas últimas no incluyen IVA, esos componentes suben
un 15 %. Sigma Elektronik indica USD 6 de envío a otras provincias; las otras tiendas
no publican tarifa. Confirmar impuestos, disponibilidad y envío antes de comprar.

## Conexiones propuestas, aún sin validar

| Señal | GPIO ESP32-S3 propuesto | Destino |
|---|---|---|
| MIC_SCK / MIC_WS / MIC_SD | 4 / 5 / 6 | INMP441 SCK / WS / SD |
| AMP_BCLK / AMP_LRC / AMP_DIN | 15 / 16 / 7 | MAX98357A BCLK / LRC / DIN |
| LED data | 48 | Anillo WS2812 |
| Pulsador de respaldo | 0 (BOOT) o 1 | Pulsador a GND |

Revisar pines contra la placa comprada, arranque y controladores antes de cablear.
Validar niveles lógicos del anillo, alimentación y filtrado; no asumir que 3,3 V en datos
sea suficiente para cualquier módulo de 5 V. El regulador opcional no sustituye el
dimensionamiento de la alimentación. Ver [firmware](firmware.md) y [caja](caja-3d.md).
