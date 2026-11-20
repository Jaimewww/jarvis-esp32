# Sitio Quarto: cómo estamos haciendo Jarvis ESP32

Sitio HTML que cuenta las piezas del proyecto, la forma de trabajo (pistas, contratos,
documentos ODD, worktrees, pruebas y CI), el estado y las decisiones. Se arma con lo que
dicen los archivos del repo y `git log`; los documentos de `docs/` y `odd/tasks/` siguen
siendo la fuente y se enlazan, no se mueven.

Requiere [Quarto](https://quarto.org) (probado con 1.11.9). Desde la raíz del repo:

```bash
quarto render docs/sitio     # genera docs/sitio/_site/
quarto preview docs/sitio    # servidor local con recarga
```

| Archivo | Contenido |
|---|---|
| `_quarto.yml` | Proyecto, barra de navegación y temas |
| `index.qmd` | Qué es Jarvis y el mapa de piezas |
| `como-trabajamos.qmd` | Pistas, CODEOWNERS, contratos, ODD, worktrees, pruebas, CI |
| `estado.qmd` | Qué está hecho por pista y línea de tiempo de las solicitudes |
| `decisiones.qmd` | Resumen de `docs/decisiones.md` |
| `theme-dark.scss`, `theme-light.scss` | Paletas `gentleman-cute` y `paper` de `host/jarvis/theme.py` |

La salida (`_site/`, `.quarto/`) está ignorada en `docs/sitio/.gitignore`.
