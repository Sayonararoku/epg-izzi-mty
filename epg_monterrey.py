#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generador de EPG (XMLTV) para izzi TV - región Monterrey.

Descarga la lista de canales y su programación desde la API de izzigo.tv,
construye un archivo XMLTV y lo comprime en .gz para consumirlo desde
Xtream UI (u otro panel/reproductor IPTV).

Diseñado para ejecutarse de forma desatendida (GitHub Actions): reintentos
automáticos, manejo de errores tolerante a fallos parciales y rutas
configurables por variables de entorno.

Variables de entorno (todas opcionales):
    EPG_OUTPUT_DIR   Carpeta de salida.            (def: directorio actual ".")
    EPG_FILENAME     Nombre del XML.               (def: "izzimty.xml")
    EPG_DAYS         Días de programación a futuro. (def: 7)
    EPG_REGION       Región de izzi.               (def: "Monterrey")
    EPG_MAX_WORKERS  Hilos de descarga paralela.   (def: 20)
    EPG_VERIFY_TLS   Verificar certificados TLS.   (def: "false")
"""

import os
import sys
import gzip
import shutil
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from html import escape

import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except ImportError:  # urllib3 < 1.26 fallback
    from requests.packages.urllib3.util.retry import Retry


# ========= CONFIGURACIÓN GENERAL =========
def _env(name, default):
    val = os.environ.get(name)
    return val if val not in (None, "") else default


REGION         = _env("EPG_REGION", "Monterrey")
EPG_SALIDA_DIR = _env("EPG_OUTPUT_DIR", ".")
EPG_FILENAME   = _env("EPG_FILENAME", "izzimty.xml")
EPG_DAYS       = int(_env("EPG_DAYS", "7"))
MAX_WORKERS    = int(_env("EPG_MAX_WORKERS", "20"))
VERIFY_TLS     = _env("EPG_VERIFY_TLS", "false").lower() in ("1", "true", "yes")

EPG_PATH    = os.path.join(EPG_SALIDA_DIR, EPG_FILENAME)
EPG_GZ_PATH = EPG_PATH + ".gz"

URL_CANALES = (
    "https://www.izzigo.tv/managetv/tvinfo/channels/get"
    f"?language=SPA&partition=OTHERS&region={REGION}"
)
URL_SCHEDULE = "https://www.izzigo.tv/managetv/tvinfo/events/schedule"
IMG_BASE     = "https://www.izzigo.tv/images/"
NO_LOGO      = "https://www.izzigo.tv/webclient/img/channel_no_logo.svg"

HEADERS = {
    'accept'            : 'application/json',
    'accept-charset'    : 'utf-8',
    'accept-encoding'   : 'gzip',
    'connection'        : 'Keep-Alive',
    'host'              : 'www.izzigo.tv',
    'iris-app-name'     : 'izzigo',
    'iris-app-version'  : '(9010303)',
    'iris-device-class' : 'TABLET',
    'iris-device-type'  : 'TABLET/ANDROID',
    'iris-hw-device-id' : '318e96d1e40b0638f251d87922287e63b2c05fcdd765a8a6b6c039cf8a01ba8f',
    'user-agent'        : 'Android-Retrofit2',
}

UTC       = timezone.utc
TZ_SUFFIX = "+0000"
REQ_TIMEOUT = (10, 30)  # (connect, read) segundos

if not VERIFY_TLS:
    # Evita que la salida se llene de InsecureRequestWarning cuando verify=False.
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")


def build_session():
    """Sesión HTTP con reintentos automáticos y pool dimensionado a los hilos."""
    session = requests.Session()
    session.headers.update(HEADERS)
    session.verify = VERIFY_TLS
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=1.5,                       # 0s, 1.5s, 3s, 6s, 12s
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=MAX_WORKERS,
        pool_maxsize=MAX_WORKERS,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


SESSION = build_session()


# ========= FECHA DINÁMICA (presente + próximos EPG_DAYS días) =========
StartDate = date.today()
EndDate   = StartDate + timedelta(days=EPG_DAYS)
print(f"Generando EPG de '{REGION}' del {StartDate} al {EndDate} "
      f"({EPG_DAYS} días) -> {EPG_PATH}")


# ========= DESCARGA DE CANALES =========
def descargar_canales():
    print("Descargando lista de canales…")
    resp = SESSION.get(URL_CANALES, timeout=REQ_TIMEOUT)
    resp.raise_for_status()
    canales_json = resp.json()

    canales = []
    for canal in canales_json.get('chs', []):
        try:
            ch_img = IMG_BASE + canal['loc'][0]['img']['dir'] + "/LOGO/m/0"
        except (KeyError, IndexError, TypeError):
            ch_img = NO_LOGO
        try:
            orden = int(canal.get('ord', 9999))
        except (ValueError, TypeError):
            orden = 9999
        try:
            nombre = canal['loc'][0]['nam']
            sid    = canal['sid']
        except (KeyError, IndexError, TypeError):
            continue  # canal sin datos mínimos -> se omite
        canales.append((orden, nombre, sid, ch_img))

    canales.sort(key=lambda c: (c[0], c[1]))
    print(f"Canales encontrados: {len(canales)}")
    return canales


# ========= DESCARGA DE PROGRAMACIÓN =========
def descargar_programacion(canal_id, nombre, ts):
    """Devuelve (canal_id, nombre, json) o None si falla tras los reintentos."""
    params = {
        "controlvn": ts,
        "start": f"{StartDate}T00:00:00Z",
        "end": f"{EndDate}T00:00:00Z",
        "language": "SPA",
        "serviceId": canal_id,
        "view": "cd-events-grid-view",
    }
    try:
        r = SESSION.get(URL_SCHEDULE, params=params, timeout=REQ_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        n = len(data.get('evs', []))
        print(f"OK: {nombre} ({canal_id}) - {n} eventos")
        return (canal_id, nombre, data)
    except Exception as e:
        print(f"ERROR: {nombre} ({canal_id}) -> {e}")
        return None


def descargar_toda_la_programacion(canales):
    print("Descargando programación de canales…")
    ts = int(datetime.now(UTC).timestamp() * 1000)
    resultados = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futuros = [
            executor.submit(descargar_programacion, canal_id, nombre, ts)
            for _orden, nombre, canal_id, _logo in canales
        ]
        for fut in as_completed(futuros):
            res = fut.result()
            if res is not None:
                resultados.append(res)

    fallidos = len(canales) - len(resultados)
    if fallidos:
        print(f"AVISO: {fallidos}/{len(canales)} canales sin programación.")
    return resultados


# ========= ESCRITURA DEL XMLTV =========
def _parse_dt(valor):
    """'YYYY-MM-DDTHH:MM:SSZ' -> string XMLTV, o None si no es parseable."""
    dt = datetime.strptime(valor, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return dt.strftime("%Y%m%d%H%M%S") + TZ_SUFFIX


def escribir_xmltv(canales, programacion):
    print(f"Escribiendo archivo EPG en: {EPG_PATH}")
    if EPG_SALIDA_DIR not in ("", "."):
        os.makedirs(EPG_SALIDA_DIR, exist_ok=True)

    total_prog = 0
    omitidos   = 0

    with open(EPG_PATH, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(
            '<tv generator-info-name="izzi-epg-grabber" '
            'timezone="America/Mexico_City">\n'
        )

        # --- Canales ---
        for _orden, nombre, canal_id, logo in canales:
            f.write(f'<channel id="IzzI.{canal_id}">\n')
            f.write(f'  <display-name>{escape(nombre)}</display-name>\n')
            f.write(f'  <icon src="{escape(logo)}"/>\n')
            f.write('</channel>\n')

        # --- Programas ---
        for canal_id, _nombre, epg_json in programacion:
            for evento in epg_json.get('evs', []):
                try:
                    start_str = _parse_dt(evento['sta'])
                    stop_str  = _parse_dt(evento['end'])
                except (KeyError, ValueError, TypeError):
                    omitidos += 1
                    continue  # sin fecha válida -> se omite (no se mete basura)

                con = evento.get('con', {}) or {}
                loc0 = (con.get('loc') or [{}])[0] or {}

                titulo      = con.get('oti', "No disponible")
                subtitulo   = loc0.get('cti', "")
                descripcion = loc0.get('syn', "Sin descripción")

                icono = ""
                try:
                    icono = f"{IMG_BASE}{loc0['img']['dir']}/SNAPSHOT/m/0"
                except (KeyError, TypeError):
                    pass

                f.write(
                    f'<programme channel="IzzI.{canal_id}" '
                    f'start="{start_str}" stop="{stop_str}">\n'
                )
                f.write(f'  <title lang="es">{escape(titulo)}</title>\n')
                if subtitulo:
                    f.write(f'  <sub-title lang="es">{escape(subtitulo)}</sub-title>\n')
                f.write(f'  <desc lang="es">{escape(descripcion)}</desc>\n')
                if icono:
                    f.write(f'  <icon src="{escape(icono)}"/>\n')
                f.write('</programme>\n')
                total_prog += 1

        f.write('</tv>\n')

    print(f"EPG generado: {len(canales)} canales, {total_prog} programas "
          f"({omitidos} eventos omitidos por fecha inválida).")
    return total_prog


# ========= COMPRESIÓN =========
def comprimir():
    print(f"Comprimiendo {EPG_PATH} -> {EPG_GZ_PATH} …")
    with open(EPG_PATH, 'rb') as f_in, gzip.open(EPG_GZ_PATH, 'wb') as f_out:
        shutil.copyfileobj(f_in, f_out)
    print("Archivo comprimido correctamente.")


# ========= MAIN =========
def main():
    try:
        canales = descargar_canales()
    except Exception as e:
        print(f"FATAL: no se pudo descargar la lista de canales -> {e}",
              file=sys.stderr)
        return 1

    if not canales:
        print("FATAL: la lista de canales llegó vacía.", file=sys.stderr)
        return 1

    programacion = descargar_toda_la_programacion(canales)
    total_prog = escribir_xmltv(canales, programacion)

    if total_prog == 0:
        # No commiteamos un EPG vacío: probablemente la API falló/bloqueó.
        print("FATAL: 0 programas generados; no se escribe un EPG vacío.",
              file=sys.stderr)
        return 1

    comprimir()
    print("¡Proceso completado!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
