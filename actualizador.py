#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Road Phone - actualizador del plugin de telemetria
--------------------------------------------------
Busca la ultima version publicada de scs-telemetry (RenCloud/scs-sdk-plugin), la descarga, comprueba
que es una DLL valida, guarda una copia de la anterior y la instala. Nada de interfaz: solo logica.

Reglas de seguridad:
  * solo se descarga del repositorio fijado en REPO y solo desde servidores de GitHub;
  * si GitHub publica el hash (sha256) del archivo, se verifica;
  * se comprueba que el contenido sea una DLL de Windows de 64 bits que exporta el plugin;
  * siempre se guarda un respaldo antes de reemplazar y se puede volver atras.
"""
import hashlib
import io
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request
import zipfile
from urllib.parse import urlparse

REPO = "RenCloud/scs-sdk-plugin"
API_LATEST = "https://api.github.com/repos/%s/releases/latest" % REPO
API_LIST = "https://api.github.com/repos/%s/releases?per_page=8" % REPO
DLL_NAME = "scs-telemetry.dll"
MAX_DOWNLOAD = 20 * 1024 * 1024
MAX_DLL = 20 * 1024 * 1024
MIN_DLL = 10 * 1024
KEEP_BACKUPS = 3
USER_AGENT = "RoadPhone-Updater"


class UpdateError(Exception):
    """Error que se le puede mostrar tal cual al usuario."""


# ---------------------------------------------------------------- versiones
def parse_version(text):
    """'v1.12.1' -> (1, 12, 1). Devuelve None si no hay numero de version."""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def fmt(v):
    return "%d.%d.%d" % tuple(v) if v else "desconocida"


def classify(installed, latest):
    """None = al dia | 'menor' = misma serie (1.12.x) | 'mayor' = otra serie | 'desconocida' = no se sabe la instalada."""
    if latest is None:
        return None
    if installed is None:
        return "desconocida"
    if tuple(latest) <= tuple(installed):
        return None
    return "menor" if tuple(latest[:2]) == tuple(installed[:2]) else "mayor"


# ---------------------------------------------------------------- red
def _host_ok(url):
    p = urlparse(url)
    h = (p.hostname or "").lower()
    return p.scheme == "https" and (h in ("github.com", "api.github.com") or h.endswith(".githubusercontent.com"))


def fetch(url, accept=None, timeout=30, limit=MAX_DOWNLOAD):
    """Descarga una URL de GitHub (solo https y solo servidores de GitHub, tambien tras redirecciones)."""
    if not _host_ok(url):
        raise UpdateError("Direccion no permitida: %s" % url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept or "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if not _host_ok(r.geturl()):
                raise UpdateError("GitHub redirigio a un servidor no permitido.")
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise UpdateError("GitHub limito las consultas por ahora. Se reintentara mas tarde.")
        raise UpdateError("GitHub respondio con error %s en %s." % (e.code, urlparse(url).path))
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = str(getattr(e, "reason", e))
        if "CERTIFICATE" in reason.upper():
            reason += " - tu antivirus, proxy o red esta interceptando la conexion segura"
        raise UpdateError("No pude conectar con GitHub (%s)." % reason)
    if len(data) > limit:
        raise UpdateError("El archivo descargado es demasiado grande.")
    return data


def _json(data):
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError:
        raise UpdateError("Respuesta de GitHub no valida.")


def _parse_release(j):
    tag = (j or {}).get("tag_name") or ""
    ver = parse_version(tag)
    if not ver:
        raise UpdateError("No pude leer la version publicada (%r)." % tag)
    assets = [{"name": a.get("name") or "", "url": a.get("browser_download_url") or "", "size": a.get("size") or 0,
               "digest": a.get("digest") or ""} for a in (j.get("assets") or [])]
    return {"tag": tag, "version": ver, "assets": assets, "notes": (j.get("body") or "")[:600]}


def latest_release(fetcher=None):
    f = fetcher or fetch
    try:
        return _parse_release(_json(f(API_LATEST, accept="application/vnd.github+json", limit=2 * 1024 * 1024)))
    except UpdateError as e:
        if "404" not in str(e):
            raise
    # /releases/latest no existe: probar con la lista de versiones
    lst = _json(f(API_LIST, accept="application/vnd.github+json", limit=4 * 1024 * 1024))
    pubs = [r for r in lst if isinstance(r, dict) and not r.get("draft")]
    stable = [r for r in pubs if not r.get("prerelease")]
    pick = (stable or pubs or [None])[0]
    if not pick:
        raise UpdateError("El repositorio no tiene versiones publicadas.")
    return _parse_release(pick)


# ---------------------------------------------------------------- validacion
def sha256(data):
    return hashlib.sha256(data).hexdigest()


def validate_dll(data):
    if not (MIN_DLL <= len(data) <= MAX_DLL):
        raise UpdateError("El plugin descargado tiene un tamano raro.")
    if data[:2] != b"MZ":
        raise UpdateError("El archivo descargado no es una DLL de Windows.")
    pe = int.from_bytes(data[0x3C:0x40], "little")
    if pe + 6 > len(data) or data[pe:pe + 4] != b"PE\0\0":
        raise UpdateError("El archivo descargado no es una DLL de Windows.")
    if int.from_bytes(data[pe + 4:pe + 6], "little") != 0x8664:
        raise UpdateError("El plugin descargado no es de 64 bits.")
    marcas = (b"scs_telemetry_init", b"SCSTelemetry", "SCSTelemetry".encode("utf-16-le"))
    if not any(m in data for m in marcas):
        raise UpdateError("El archivo no parece ser el plugin de telemetria.")


def _is_x64(data):
    if data[:2] != b"MZ" or len(data) < 0x100:
        return False
    pe = int.from_bytes(data[0x3C:0x40], "little")
    return data[pe:pe + 4] == b"PE\0\0" and int.from_bytes(data[pe + 4:pe + 6], "little") == 0x8664


def _dll_from_zip(zdata):
    """Busca scs-telemetry.dll de 64 bits dentro del zip, sin importar como se llamen las carpetas."""
    try:
        z = zipfile.ZipFile(io.BytesIO(zdata))
    except zipfile.BadZipFile:
        return None
    best = None
    for info in z.infolist():
        name = info.filename.replace("\\", "/")
        if info.is_dir() or name.lower().rsplit("/", 1)[-1] != DLL_NAME:
            continue
        if info.file_size > MAX_DLL:
            continue
        data = z.read(info)
        if not _is_x64(data):
            continue
        low = name.lower()
        score = 2 if ("win64" in low or "win_x64" in low or "x64" in low) else 1
        if best is None or score > best[0]:
            best = (score, data)
    return best[1] if best else None


def download_plugin(release, fetcher=None):
    """Baja la version y devuelve (bytes_de_la_dll, nombre_del_archivo). Acepta un zip con la DLL o la DLL suelta."""
    fetcher = fetcher or fetch
    cands = [a for a in release["assets"] if a["name"].lower().endswith((".zip", ".dll")) and "source" not in a["name"].lower()]
    if not cands:
        nombres = ", ".join(a["name"] for a in release["assets"]) or "ninguno"
        raise UpdateError("La version %s no trae un zip ni una DLL (archivos: %s)." % (release["tag"], nombres))
    for a in cands[:4]:
        raw = fetcher(a["url"])
        dg = a["digest"].lower()
        if dg.startswith("sha256:") and sha256(raw) != dg[7:]:
            raise UpdateError("La descarga no coincide con el hash que publica GitHub. No se instalo.")
        dll = raw if a["name"].lower().endswith(".dll") else _dll_from_zip(raw)
        if dll:
            validate_dll(dll)
            return dll, a["name"]
    raise UpdateError("No encontre %s (64 bits) dentro de la version %s (archivos: %s)." % (
        DLL_NAME, release["tag"], ", ".join(a["name"] for a in cands[:4])))


# ---------------------------------------------------------------- instalar / respaldar / volver atras
def _safe_replace(tmp, dest):
    try:
        os.replace(tmp, dest)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise UpdateError("El juego esta usando el plugin. Cierra el juego y vuelve a intentarlo.")


def _write_tmp(dest, data):
    tmp = dest + ".nuevo"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
    except OSError as e:
        raise UpdateError("No pude escribir en la carpeta del juego (%s)." % e)
    return tmp


def list_backups(backup_dir):
    try:
        names = [n for n in os.listdir(backup_dir) if n.endswith(".dll.bak")]
    except OSError:
        return []
    return sorted((os.path.join(backup_dir, n) for n in names), key=lambda p: os.path.getmtime(p), reverse=True)


def apply_update(dll_path, new_bytes, backup_dir, prev_label="desconocida"):
    """Guarda copia de la DLL actual y pone la nueva. Devuelve la ruta del respaldo (o '')."""
    backup = ""
    if os.path.isfile(dll_path):
        os.makedirs(backup_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = os.path.join(backup_dir, "scs-telemetry_%s_%s.dll.bak" % (prev_label, stamp))
        try:
            shutil.copy2(dll_path, backup)
        except OSError as e:
            raise UpdateError("No pude guardar el respaldo (%s). No se actualizo." % e)
    os.makedirs(os.path.dirname(dll_path), exist_ok=True)
    _safe_replace(_write_tmp(dll_path, new_bytes), dll_path)
    for old in list_backups(backup_dir)[KEEP_BACKUPS:]:
        try:
            os.remove(old)
        except OSError:
            pass
    return backup


def restore(dll_path, backup_path):
    if not backup_path or not os.path.isfile(backup_path):
        raise UpdateError("No hay respaldo para restaurar.")
    with open(backup_path, "rb") as f:
        data = f.read()
    validate_dll(data)
    _safe_replace(_write_tmp(dll_path, data), dll_path)
    return sha256(data)


def file_sha256(path):
    try:
        with open(path, "rb") as f:
            return sha256(f.read())
    except OSError:
        return ""


def main(argv=None):
    """Diagnostico:  python actualizador.py [--guardar]   (muestra cada paso y donde falla)"""
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    try:
        import ssl
        print("Python", sys.version.split()[0], "|", ssl.OPENSSL_VERSION)
        print("1) Consultando", API_LATEST)
        rel = latest_release()
        print("   ultima version:", rel["tag"], "->", fmt(rel["version"]))
        for a in rel["assets"]:
            print("   archivo: %s (%s bytes) hash de GitHub: %s" % (a["name"], a["size"], a["digest"] or "no publicado"))
        print("2) Descargando y validando...")
        dll, nombre = download_plugin(rel)
        print("   OK: %s de 64 bits dentro de %s | %d bytes | sha256 %s" % (DLL_NAME, nombre, len(dll), sha256(dll)))
        if "--guardar" in argv:
            with open(DLL_NAME, "wb") as f:
                f.write(dll)
            print("3) Guardada como", os.path.abspath(DLL_NAME))
        print("RESULTADO: todo bien")
        return 0
    except UpdateError as e:
        print("ERROR:", e)
    except Exception:
        import traceback
        traceback.print_exc()
    print("RESULTADO: fallo (copia este texto y envialo)")
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
