# EPG izzi Monterrey (XMLTV) para Xtream UI

Genera automáticamente una guía de programación (EPG en formato **XMLTV**) de
**izzi TV — región Monterrey**, lista para cargarse en **Xtream UI** u otro panel
/ reproductor IPTV.

Se ejecuta solo con **GitHub Actions** una vez al día y hace **commit** del
resultado al propio repositorio, por lo que tienes una URL pública y estable.

## Salida

El workflow genera y commitea en la raíz del repo:

- `izzimty.xml` — XMLTV sin comprimir
- `izzimty.xml.gz` — XMLTV comprimido (recomendado para Xtream UI)

URL pública (sustituye `USUARIO` y `REPO`):

```
https://raw.githubusercontent.com/USUARIO/REPO/main/izzimty.xml.gz
```

## Cómo cargarlo en Xtream UI

1. En el panel ve a **EPG → Add EPG**.
2. Nombre: `izzi Monterrey` (el que quieras).
3. **EPG URL:** la URL `raw.githubusercontent.com/...izzimty.xml.gz` de arriba.
4. Guarda y deja que Xtream UI lo descargue con su propio cron.
5. En cada canal/stream, pon el **EPG ID** que coincida con el id del XML:
   tiene el formato **`IzzI.<serviceId>`** (p. ej. `IzzI.123456`).

> El mapeo del `epg_channel_id` debe coincidir **exactamente** con el `id`
> del `<channel>` en el XML, o Xtream UI no asociará la programación.

## Configuración (variables de entorno, opcionales)

El script `epg_monterrey.py` se controla por entorno (ya fijadas en el workflow):

| Variable          | Default      | Descripción                              |
|-------------------|--------------|------------------------------------------|
| `EPG_OUTPUT_DIR`  | `.`          | Carpeta de salida.                       |
| `EPG_FILENAME`    | `izzimty.xml`| Nombre del archivo XML.                  |
| `EPG_DAYS`        | `7`          | Días de programación a futuro.           |
| `EPG_REGION`      | `Monterrey`  | Región de izzi.                          |
| `EPG_MAX_WORKERS` | `20`         | Descargas en paralelo.                   |
| `EPG_VERIFY_TLS`  | `false`      | Verificar certificados TLS.              |

## Frecuencia

Definida en `.github/workflows/epg.yml` con cron `0 5 * * *` (cada 24 h, 05:00 UTC).
Puedes lanzarlo manualmente desde la pestaña **Actions → Run workflow**.

## Ejecutar localmente (opcional)

```bash
pip install -r requirements.txt
python epg_monterrey.py
```

Genera `izzimty.xml` y `izzimty.xml.gz` en la carpeta actual.

## Notas

- El script es tolerante a fallos: reintenta automáticamente, omite canales que
  fallen y **no** escribe un EPG vacío (si la API bloquea/falla por completo, el
  job termina en error y conserva el último EPG válido ya commiteado).
- Riesgo principal: que la API de izzi limite/bloquee las IP de los runners de
  GitHub. Si ocurre, se vería en los logs del job como errores en todos los canales.
