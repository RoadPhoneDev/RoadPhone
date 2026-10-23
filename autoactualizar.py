#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Road Phone - actualizador de la propia aplicacion
-------------------------------------------------
Busca la ultima version publicada en GitHub (RoadPhoneDev/RoadPhone, incluidas las alpha), descarga el zip,
lo valida, lo extrae en una carpeta temporal y deja que la copia NUEVA del programa reemplace a la vieja
cuando esta se cierra. Si algo falla a medias, se restaura la copia de respaldo.

Reglas de seguridad (las mismas que el actualizador del plugin):
  * solo https y solo servidores de GitHub (tambien tras redirecciones);
  * se verifica el sha256 que publica GitHub, si existe;
  * el zip debe traer RoadPhone/RoadPhone.exe de 64 bits y ninguna ruta que se salga de la carpeta;
  * nunca se actualiza solo: siempre lo inicia el usuario con un boton.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

import actualizador
from actualizador import UpdateError, fmt, parse_version, sha256

APP_REPO = "RoadPhoneDev/RoadPhone"
API_APP_LIST = "https://api.github.com/repos/%s/releases?per_page=15" % APP_REPO
ASSET_RE = re.compile(r"^RoadPhone-.+\.zip$", re.I)
MAX_ZIP = 250 * 1024 * 1024
MAX_UNZIPPED = 600 * 1024 * 1024
MAX_FILES = 6000
EXE_NAME = "RoadPhone.exe"
ARG = "--aplicar-actualizacion"


# ---------------------------------------------------------------- buscar
def latest_app(fetcher=None):
    """Version mas alta publicada (con su zip). Incluye pre-releases: mientras todo es alpha, son las unicas."""
    f = fetcher or actualizador.fetch
    lst = actualizador._json(f(API_APP_LIST, accept="application/vnd.github+json", limit=4 * 1024 * 1024))
    best = None
    for r in lst if isinstance(lst, list) else []:
        if not isinstance(r, dict) or r.get("draft"):
            continue
        ver = parse_version(r.get("tag_name") or "")
        if not ver:
            continue
        asset = next((a for a in (r.get("assets") or []) if ASSET_RE.match(a.get("name") or "")), None)
        if not asset:
            continue
        if best is None or ver > best["version"]:
            best = {"tag": r.get("tag_name"), "version": ver, "notes": (r.get("body") or "")[:600],
                    "name": asset["name"], "url": asset.get("browser_download_url") or "",
                    "size": asset.get("size") or 0, "digest": (asset.get("digest") or "").lower()}
    if not best:
        raise UpdateError("No encontre ninguna version publicada de Road Phone.")
    return best


def is_newer(installed_text, rel):
    cur = parse_version(installed_text)
    return bool(cur) and tuple(rel["version"]) > tuple(cur)


# ---------------------------------------------------------------- descargar y validar
def _safe_name(name):
    n = name.replace("\\", "/")
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if n.startswith("/") or ":" in n.split("/")[0] or ".." in parts:
        raise UpdateError("El zip trae una ruta no permitida (%s)." % name)
    return parts


def _extract(zdata, dest):
    """Extrae solo lo que esta dentro de la carpeta RoadPhone/ del zip y devuelve la ruta de esa carpeta."""
    try:
        z = zipfile.ZipFile(__import__("io").BytesIO(zdata))
    except zipfile.BadZipFile:
        raise UpdateError("El archivo descargado no es un zip valido.")
    infos = z.infolist()
    if len(infos) > MAX_FILES or sum(i.file_size for i in infos) > MAX_UNZIPPED:
        raise UpdateError("El zip es demasiado grande o trae demasiados archivos.")
    if os.path.isdir(dest):
        shutil.rmtree(dest, ignore_errors=True)
    root = os.path.join(dest, "RoadPhone")
    found_exe = False
    for info in infos:
        parts = _safe_name(info.filename)
        if not parts or parts[0] != "RoadPhone":
            raise UpdateError("El zip no tiene la estructura esperada (carpeta RoadPhone).")
        if info.is_dir() or len(parts) == 1:
            continue
        target = os.path.join(dest, *parts)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with z.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)
        if len(parts) == 2 and parts[1].lower() == EXE_NAME.lower():
            found_exe = True
    exe = os.path.join(root, EXE_NAME)
    if not found_exe or not os.path.isfile(exe):
        raise UpdateError("El zip no trae %s." % EXE_NAME)
    with open(exe, "rb") as f:
        head = f.read(4096)
    if not actualizador._is_x64(head.ljust(0x100, b"\0")):
        raise UpdateError("El programa descargado no es de 64 bits.")
    return root


def download_app(rel, staging, fetcher=None):
    """Baja el zip, verifica el hash y lo extrae. Devuelve la carpeta con la version nueva."""
    f = fetcher or actualizador.fetch
    raw = f(rel["url"], limit=MAX_ZIP)
    if rel["digest"].startswith("sha256:") and sha256(raw) != rel["digest"][7:]:
        raise UpdateError("La descarga no coincide con el hash que publica GitHub. No se instalo.")
    return _extract(raw, staging)


# ---------------------------------------------------------------- aplicar (lo hace la copia NUEVA)
def install_dir():
    """Carpeta donde esta instalado el programa (solo si es el .exe empaquetado)."""
    if not getattr(sys, "frozen", False):
        raise UpdateError("La actualizacion automatica solo funciona con RoadPhone.exe (no con el script .py).")
    return os.path.dirname(os.path.abspath(sys.executable))


def check_writable(folder):
    try:
        p = os.path.join(folder, ".prueba_escritura")
        with open(p, "w") as f:
            f.write("x")
        os.remove(p)
    except OSError:
        raise UpdateError("No puedo escribir en %s. Mueve la carpeta de Road Phone a un lugar como Documentos "
                          "o Escritorio (no a Archivos de programa) y vuelve a intentarlo." % folder)


def launch_helper(new_root, folder, state_dir, version_text):
    """Arranca la copia nueva en modo 'aplicar'. Esta app debe cerrarse justo despues."""
    exe = os.path.join(new_root, EXE_NAME)
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([exe, ARG, str(os.getpid()), folder, new_root, state_dir, version_text],
                     cwd=new_root, creationflags=flags, close_fds=True)


def _log(state_dir, msg):
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(os.path.join(state_dir, "actualizacion.log"), "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def _wait_pid(pid, timeout=60):
    if not pid:
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x00100000, False, int(pid))      # SYNCHRONIZE
        if not h:
            return True
        r = k.WaitForSingleObject(h, int(timeout * 1000))
        k.CloseHandle(h)
        return r == 0
    except Exception:
        time.sleep(3)
        return True


def _put(src, dst, tries=40, pause=0.5):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".nuevo"
    shutil.copy2(src, tmp)
    for _ in range(tries):
        try:
            os.replace(tmp, dst)
            return
        except OSError:
            time.sleep(pause)
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise OSError("archivo en uso: %s" % dst)


def _files(root):
    for r, _d, fs in os.walk(root):
        for n in fs:
            full = os.path.join(r, n)
            yield os.path.relpath(full, root), full


def apply_update(folder, new_root, backup_dir, tries=40, pause=0.5):
    """Respalda la carpeta actual y copia encima la version nueva. Si falla, restaura. Devuelve (ok, mensaje)."""
    if os.path.isdir(backup_dir):
        shutil.rmtree(backup_dir, ignore_errors=True)
    try:
        shutil.copytree(folder, backup_dir, ignore=shutil.ignore_patterns("*.nuevo", ".prueba_escritura"))
    except OSError as e:
        return False, "No pude guardar el respaldo (%s). No se cambio nada." % e
    done, added = [], []
    try:
        for rel, src in _files(new_root):
            dst = os.path.join(folder, rel)
            existed = os.path.exists(dst)
            _put(src, dst, tries, pause)
            done.append(rel)
            if not existed:
                added.append(dst)
        return True, "ok"
    except OSError as e:
        for dst in added:
            try:
                os.remove(dst)
            except OSError:
                pass
        for rel in done:
            b = os.path.join(backup_dir, rel)
            if os.path.isfile(b):
                try:
                    _put(b, os.path.join(folder, rel), 10, pause)
                except OSError:
                    pass
        return False, "No pude reemplazar archivos (%s). Se restauro la version anterior." % e


def apply_from_args(args):
    """Punto de entrada de la copia nueva:  RoadPhone.exe --aplicar-actualizacion <pid> <carpeta> <nueva> <estado> <version>"""
    try:
        pid, folder, new_root, state_dir, version = args[0], args[1], args[2], args[3], args[4]
    except IndexError:
        return 2
    result = {"ok": False, "version": version, "error": ""}
    try:
        _log(state_dir, "Esperando a que se cierre la aplicacion (pid %s)..." % pid)
        if not _wait_pid(int(pid)):
            raise OSError("la aplicacion no se cerro a tiempo")
        ok, msg = apply_update(folder, new_root, os.path.join(state_dir, "app_respaldo"))
        result.update(ok=ok, error="" if ok else msg)
        _log(state_dir, "Resultado: %s" % msg)
    except Exception as e:                                           # nunca dejar al usuario sin programa
        result["error"] = "Error inesperado: %s" % e
        _log(state_dir, result["error"])
    try:
        with open(os.path.join(state_dir, "actualizacion_resultado.json"), "w", encoding="utf-8") as f:
            json.dump(result, f)
    except OSError:
        pass
    try:                                                              # abrir el programa (nuevo o restaurado)
        flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0
        subprocess.Popen([os.path.join(folder, EXE_NAME)], cwd=folder, creationflags=flags, close_fds=True)
    except OSError as e:
        _log(state_dir, "No pude reabrir el programa: %s" % e)
    return 0 if result["ok"] else 1


def read_result(state_dir):
    """Resultado de la ultima actualizacion (y lo borra para mostrarlo una sola vez)."""
    p = os.path.join(state_dir, "actualizacion_resultado.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        os.remove(p)
        return d
    except (OSError, ValueError):
        return None


def cleanup(staging):
    shutil.rmtree(staging, ignore_errors=True)
