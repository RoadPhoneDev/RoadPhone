#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Road Phone - Launcher
---------------------
Una ventana para iniciar sesion y arrancar el puente con un boton. Lee la telemetria directo del plugin
del juego (scs-telemetry), sin servidor intermedio.
Muestra si la sesion, el plugin, el juego y el puente estan conectados.
El juego NO lo abre: lo abres tu (TruckersMP, HaulMP, Steam...). Cuando conecte, se pone en verde.

Uso:   doble clic en iniciar_launcher.bat      (o:  python launcher.py)

Necesita puente.py en la misma carpeta. Solo usa la biblioteca estandar de Python 3.
"""
import json
import os
import queue
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
import math
import platform
import unicodedata
from collections import deque

import actualizador
import autoactualizar
import puente

TITLE = "Road Phone"

# ---------------------------------------------------------------- version de la aplicacion
# Sube APP_VERSION en cada entrega (0.2.0 -> 0.2.1 arreglos, 0.3.0 funciones nuevas) y anota los cambios abajo.
APP_STAGE = "Alpha"
APP_VERSION = "0.3.0"
VERSION_TAG = "%s %s" % (APP_STAGE, APP_VERSION)
WINDOW_TITLE = "Road Phone %s \u03b1" % APP_VERSION
CHANGELOG = [
    ("0.3.0", [
        "Actualización de Road Phone desde GitHub con un botón (Ajustes → Road Phone).",
        "Descarga verificada con hash y copia de respaldo; si algo falla, vuelve a la versión anterior.",
        "Aviso en la ventana cuando hay una versión nueva de la aplicación.",
        "Botones del plugin renombrados para no confundirlos con los de la aplicación.",
    ]),
    ("0.2.0", [
        "Rediseño: espaciado y jerarquía más limpios, textos de estado más cortos.",
        "Versión visible (pie, barra de título e insignia ALPHA).",
        "Aviso discreto cuando hay una actualización del plugin.",
        "Punto con pulso suave mientras se envían datos.",
        "Panel en vivo: velocidad, camión y límite de velocidad.",
        "Tarjeta del trabajo actual con barra de progreso.",
        "Resumen de hoy: km, entregas e ingresos.",
        "Registro con colores y botones Copiar y Limpiar.",
        "Informe de diagnóstico con un clic (Ajustes).",
        "Opción: iniciar el puente solo al abrir el juego.",
    ]),
    ("0.1.0", ["Primera versión alpha: ventana, puente y gestión del plugin."]),
]

# ---------------------------------------------------------------- carpeta del juego y plugin de telemetria
PLUGIN_FILE = "scs-telemetry.dll"            # plugin de telemetria (RenCloud/scs-sdk-plugin)
GAME_FOLDER = "Euro Truck Simulator 2"


def steam_libraries():
    """Carpetas de bibliotecas de Steam (la principal y las adicionales)."""
    roots = []
    if os.name == "nt":
        try:
            import winreg
            for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        for val in ("SteamPath", "InstallPath"):
                            try:
                                roots.append(winreg.QueryValueEx(k, val)[0])
                            except OSError:
                                pass
                except OSError:
                    pass
        except ImportError:
            pass
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(env):
            roots.append(os.path.join(os.environ[env], "Steam"))
    libs = []
    for r in roots:
        r = os.path.normpath(r)
        if os.path.isdir(r) and r not in libs:
            libs.append(r)
        try:
            with open(os.path.join(r, "steamapps", "libraryfolders.vdf"), "r", encoding="utf-8", errors="replace") as f:
                for m in re.findall(r'"path"\s+"([^"]+)"', f.read()):
                    d = os.path.normpath(m.replace("\\\\", "\\"))
                    if os.path.isdir(d) and d not in libs:
                        libs.append(d)
        except OSError:
            pass
    return libs


def find_game_dirs(name=GAME_FOLDER):
    return [d for d in (os.path.join(lib, "steamapps", "common", name) for lib in steam_libraries()) if os.path.isdir(d)]


def plugins_dir(game_dir):
    return os.path.join(game_dir, "bin", "win_x64", "plugins")


# Al ejecutarse con --aplicar-actualizacion, la copia NUEVA reemplaza a la vieja (ya cerrada) y la vuelve a abrir
if autoactualizar.ARG in sys.argv:
    sys.exit(autoactualizar.apply_from_args(sys.argv[sys.argv.index(autoactualizar.ARG) + 1:]))

# Al ejecutarse con --puente, este mismo programa funciona como el puente (sirve tambien empaquetado en .exe)
if "--puente" in sys.argv:
    sys.argv.remove("--puente")
    puente.main()
    sys.exit(0)


# ---------------------------------------------------------------- logica (sin ventana, se puede probar sola)
class Core:
    def __init__(self):
        self.sess = puente.Session()
        self.cfg = puente.load_config()
        self.tel = puente.Telemetry(events=False, announce=False)
        self.logq = queue.Queue()
        self.events = queue.Queue()
        self.proc = None
        self.busy = False
        self.upd = {"busy": False, "latest": "", "kind": None, "rel": None, "data": None, "msg": ""}
        self.app = {"busy": False, "rel": None, "msg": "", "error": False}
        self._app_retry_at = 0.0
        self._pending_restore = False
        self._retry_at = 0.0
        self._notified = ""
        self.logbuf = deque(maxlen=300)
        self.live = {}
        self.hoy = {"km": 0.0, "entregas": 0, "ingresos": 0.0}
        self._auto_blocked = False
        self.logged = False
        res = autoactualizar.read_result(puente.STATE_DIR)       # resultado de una actualizacion de la app
        if res:
            if res.get("ok"):
                self.events.put(("info", "Road Phone se actualizó a la versión %s." % res.get("version")))
            else:
                self.events.put(("warn", "No se pudo actualizar Road Phone: %s" % res.get("error")))
        self.user = ""
        self._run = True
        self.st = {"plugin": "sin_memoria", "plugin_rev": 0, "plugin_file": False, "game_dir": False,
                   "can_install": False, "game": False, "game_name": "", "bridge": False,
                   "sending": False, "last_send": 0.0, "busy": False}
        puente.log = self.log          # los avisos del puente (lectura del plugin) salen en el registro

    # ---- registro
    def log(self, msg):
        self._emit("[%s] %s" % (time.strftime("%H:%M:%S"), msg))

    def _emit(self, line):
        self.logbuf.append(line)
        self.logq.put(line)

    # ---- sesion
    def saved_email(self):
        try:
            with open(os.path.join(puente.STATE_DIR, "sesion.json"), "r", encoding="utf-8") as f:
                return json.load(f).get("email", "")
        except (OSError, ValueError):
            return ""

    def read_status(self):
        try:
            with open(puente.STATUS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def external_bridge(self):
        s = self.read_status()
        return bool(s) and time.time() - float(s.get("ts", 0)) < 8

    def try_restore(self):
        """Comprueba la sesion guardada. Si el puente ya esta corriendo no se toca el token."""
        if self.external_bridge():
            self.user = self.read_status().get("usuario") or self.saved_email()
            self.logged = True
            return True
        if self.sess.restore():
            self.sess.load_profile()
            self.user = self.sess.nombre or self.sess.email
            self.logged = True
            return True
        return False

    def login(self, email, password):
        ok, msg = self.sess.login(email.strip(), password)
        if ok:
            self.sess.load_profile()
            self.user = self.sess.nombre or self.sess.email
            self.logged = True
            self.cfg["correo"] = email.strip()
            puente.save_config(self.cfg)
        return ok, msg

    def logout(self):
        self.stop()
        self.sess.forget()
        self.logged = False
        self.user = ""

    # ---- plugin de telemetria del juego
    def game_dir(self):
        d = self.cfg.get("carpeta_juego") or ""
        if d and os.path.isdir(d):
            return d
        found = find_game_dirs()
        return found[0] if found else ""

    def set_game_dir(self, path):
        self.cfg["carpeta_juego"] = path
        puente.save_config(self.cfg)

    def plugin_path(self):
        d = self.game_dir()
        return os.path.join(plugins_dir(d), PLUGIN_FILE) if d else ""

    def plugin_installed(self):
        p = self.plugin_path()
        return bool(p) and os.path.isfile(p)

    def bundled_plugin(self):
        return find_asset(PLUGIN_FILE)

    def install_plugin(self):
        """Copia el plugin que viene con el programa a la carpeta plugins del juego."""
        src, d = self.bundled_plugin(), self.game_dir()
        if not d:
            return False, "No encontre la carpeta del juego. Elige la carpeta en Ajustes."
        if not src:
            return False, "Esta version del programa no trae el plugin. Copia %s a: %s" % (PLUGIN_FILE, plugins_dir(d))
        try:
            os.makedirs(plugins_dir(d), exist_ok=True)
            shutil.copy2(src, self.plugin_path())
        except OSError as e:
            return False, "No pude instalar el plugin (%s). Cierra el juego e intentalo de nuevo." % e
        return True, "Plugin instalado en %s. Abre el juego (o reinicialo) y acepta el aviso del SDK." % plugins_dir(d)

    # ---- actualizaciones del plugin (avisa siempre; instala sola solo las versiones menores, con respaldo)
    @property
    def backup_dir(self):
        return os.path.join(puente.STATE_DIR, "plugin_respaldos")

    def plugin_version(self):
        """Version instalada, solo si la instalamos nosotros y el archivo no cambio despues."""
        p = self.plugin_path()
        if not p or not self.cfg.get("plugin_version") or not self.cfg.get("plugin_sha256"):
            return None
        try:
            key = (p, os.path.getmtime(p), os.path.getsize(p))
        except OSError:
            return None
        cache = getattr(self, "_hash_cache", None)
        if not cache or cache[0] != key:
            self._hash_cache = cache = (key, actualizador.file_sha256(p))
        return actualizador.parse_version(self.cfg["plugin_version"]) if cache[1] == self.cfg["plugin_sha256"] else None

    def has_backup(self):
        b = self.cfg.get("plugin_respaldo") or ""
        return bool(b) and os.path.isfile(b)

    def _save(self, **kw):
        self.cfg.update(kw)
        puente.save_config(self.cfg)

    def check_update(self, auto=False, manual=False):
        """Consulta GitHub. Si es una version menor y esta permitido, la instala sola."""
        u = self.upd
        if u["busy"]:
            return
        u["busy"] = True
        try:
            if not self.plugin_installed():
                u.update(kind=None, msg="El plugin no esta instalado.")
                return
            rel = actualizador.latest_release()
            self._save(plugin_ultima_consulta=time.time())
            latest, tag = rel["version"], rel["tag"]
            u["latest"] = actualizador.fmt(latest)
            inst = self.plugin_version()
            data = None
            if actualizador.classify(inst, latest) is not None:
                data, _ = actualizador.download_plugin(rel)
                if inst is None and actualizador.sha256(data) == actualizador.file_sha256(self.plugin_path()):
                    self._save(plugin_version=actualizador.fmt(latest), plugin_sha256=actualizador.sha256(data))
                    inst = latest          # la que tenias ya era la ultima
            kind = actualizador.classify(inst, latest)
            if kind and tag in (self.cfg.get("plugin_omitir") or []) and not manual:
                u.update(kind=None, msg="La version %s ya fallo en este equipo y se omite." % tag)
                return
            u.update(kind=kind, rel=rel if kind else None, data=data if kind else None)
            if kind is None:
                u["msg"] = "El plugin esta al dia (v%s)." % actualizador.fmt(latest)
            elif kind == "menor" and auto and self.cfg.get("plugin_auto", True) and not self.st["game"]:
                self._install_update(rel, data, auto=True)
            else:
                u["msg"] = "Hay una version nueva del plugin: v%s." % u["latest"]
                if self._notified != tag:
                    self._notified = tag
                    self.log("Hay una version nueva del plugin: v%s (instalada: %s). Revisala en Ajustes." % (
                        u["latest"], actualizador.fmt(inst) if inst else "sin identificar"))
        except actualizador.UpdateError as e:
            u["msg"] = str(e)
            self._retry_at = time.time() + 1800
            if manual:
                self.events.put(("warn", str(e)))
            else:
                self.log("Actualizacion del plugin: %s" % e)
        finally:
            u["busy"] = False

    def _install_update(self, rel, data, auto=False):
        path, prev = self.plugin_path(), self.plugin_version()
        label = actualizador.fmt(prev) if prev else "desconocida"
        old_hash = actualizador.file_sha256(path)
        try:
            backup = actualizador.apply_update(path, data, self.backup_dir, label)
        except actualizador.UpdateError as e:
            if auto:
                self.log("No pude actualizar el plugin ahora (%s). Se reintentara." % e)
                self._retry_at = time.time() + 600
                return
            raise
        self._save(plugin_version=actualizador.fmt(rel["version"]), plugin_sha256=actualizador.sha256(data),
                   plugin_respaldo=backup, plugin_respaldo_version=label if prev else "", plugin_respaldo_sha=old_hash,
                   plugin_probando=rel["tag"])
        self.upd.update(kind=None, rel=None, data=None, msg="Plugin actualizado a v%s." % actualizador.fmt(rel["version"]))
        self.log("Plugin actualizado a v%s. Copia de la anterior guardada en: %s" % (actualizador.fmt(rel["version"]), backup or "(no habia)"))
        if not auto:
            self.events.put(("info", "Plugin actualizado a v%s.\nSe guardó una copia de la versión anterior.\nSi el juego estaba abierto, ciérralo y ábrelo otra vez." % actualizador.fmt(rel["version"])))

    def update_now(self):
        """Boton Actualizar: instala la version nueva (la pide el usuario, cualquiera sea su tipo)."""
        u = self.upd
        try:
            rel, data = u.get("rel"), u.get("data")
            if not rel:
                self.check_update(manual=True)
                rel, data = u.get("rel"), u.get("data")
            if not rel:
                self.events.put(("info", u.get("msg") or "No hay nada que actualizar."))
                return
            if data is None:
                data, _ = actualizador.download_plugin(rel)
            self._install_update(rel, data, auto=False)
        except actualizador.UpdateError as e:
            self.events.put(("warn", str(e)))

    def rollback(self, auto=False):
        """Vuelve a la copia guardada. Si el juego esta abierto, queda pendiente hasta que se cierre."""
        path, backup, failed = self.plugin_path(), self.cfg.get("plugin_respaldo") or "", self.cfg.get("plugin_probando") or ""
        try:
            h = actualizador.restore(path, backup)
        except actualizador.UpdateError as e:
            if auto:
                self._pending_restore = True
                self.log("No pude volver a la version anterior todavia (%s). Lo intentare de nuevo." % e)
            else:
                self.events.put(("warn", str(e)))
            return False
        omit = list(self.cfg.get("plugin_omitir") or [])
        if auto and failed and failed not in omit:
            omit.append(failed)
        self._save(plugin_sha256=h, plugin_version=self.cfg.get("plugin_respaldo_version") or "",
                   plugin_probando="", plugin_respaldo="", plugin_omitir=omit)
        self._pending_restore = False
        self.upd.update(kind=None, rel=None, data=None, msg="Se restauro la version anterior del plugin.")
        self.log("Se restauro la version anterior del plugin.")
        if not auto:
            self.events.put(("info", "Se restauró la versión anterior del plugin.\nSi el juego estaba abierto, ciérralo y ábrelo otra vez."))
        return True

    # ---- actualizacion de la propia aplicacion
    def check_app_update(self, auto=False):
        a = self.app
        if a["busy"]:
            return
        a["busy"] = True
        try:
            rel = autoactualizar.latest_app()
            self._save(app_ultima_consulta=time.time())
            if autoactualizar.is_newer(APP_VERSION, rel):
                a.update(rel=rel, error=False, msg="Hay una versión nueva de Road Phone: %s." % rel["tag"])
            else:
                a.update(rel=None, error=False, msg="Road Phone está al día (%s)." % VERSION_TAG)
        except actualizador.UpdateError as e:
            a.update(error=True, msg=str(e))
            self._app_retry_at = time.time() + 1800
        except Exception as e:
            a.update(error=True, msg="No pude buscar actualizaciones (%s)." % e)
            self._app_retry_at = time.time() + 1800
        finally:
            a["busy"] = False

    def update_app_now(self):
        """Descarga, valida y deja que la copia nueva reemplace a esta. Cierra la aplicacion al final."""
        a = self.app
        rel = a.get("rel")
        if not rel or a["busy"]:
            return
        a["busy"] = True
        try:
            folder = autoactualizar.install_dir()
            autoactualizar.check_writable(folder)
            self.log("Descargando Road Phone %s..." % rel["tag"])
            new_root = autoactualizar.download_app(rel, os.path.join(puente.STATE_DIR, "actualizacion", "nueva"))
            self.log("Descarga verificada. Cerrando para instalar...")
            self.stop()
            autoactualizar.launch_helper(new_root, folder, puente.STATE_DIR, rel["tag"].lstrip("vV"))
            self.events.put(("salir",))
        except actualizador.UpdateError as e:
            a.update(error=True, msg=str(e))
            self.log(str(e))
            self.events.put(("warn", str(e)))
        except Exception as e:
            a.update(error=True, msg="No pude actualizar (%s)." % e)
            self.log(a["msg"])
            self.events.put(("warn", a["msg"]))
        finally:
            a["busy"] = False

    def update_loop(self):
        time.sleep(20)
        autoactualizar.cleanup(os.path.join(puente.STATE_DIR, "actualizacion"))
        while True:
            try:
                if time.time() > self._app_retry_at and time.time() - float(self.cfg.get("app_ultima_consulta") or 0) > 12 * 3600:
                    self.check_app_update(auto=True)
                last = float(self.cfg.get("plugin_ultima_consulta") or 0)
                if time.time() > self._retry_at and time.time() - last > 12 * 3600 and self.plugin_installed():
                    self.check_update(auto=True)
                if self._pending_restore:
                    self.rollback(auto=True)
            except Exception:
                pass
            time.sleep(60)

    @staticmethod
    def lan_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return "127.0.0.1"

    # ---- puente
    def bridge_cmd(self):
        if getattr(sys, "frozen", False):
            return [sys.executable, "--puente"]
        return [sys.executable, os.path.abspath(__file__), "--puente"]

    def own_bridge(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=self._start, daemon=True).start()

    def _start(self):
        try:
            if self.own_bridge() or self.external_bridge():
                self.log("El puente ya esta en ejecucion.")
                return
            if not self.plugin_installed() and self.bundled_plugin() and self.game_dir():
                ok, msg = self.install_plugin()
                self.log(msg)
            self.start_bridge()
        finally:
            self.busy = False

    def start_bridge(self):
        env = dict(os.environ, RP_NO_LAUNCH="1", RP_FROM_LAUNCHER="1", PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.log("Iniciando el puente...")
        self.proc = subprocess.Popen(self.bridge_cmd(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, env=env, creationflags=flags,
                                     text=True, encoding="utf-8", errors="replace", bufsize=1)
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, proc):
        try:
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    self._emit(line)
        except (OSError, ValueError):
            pass
        code = proc.wait()
        self.log("El puente se detuvo (codigo %s)." % code)
        if code == 2:
            self.events.put(("session_lost",))

    def stop(self):
        p = self.proc
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        elif self.external_bridge():
            pid = self.read_status().get("pid")
            try:
                if pid and int(pid) != os.getpid():
                    os.kill(int(pid), signal.SIGTERM)
            except (OSError, ValueError):
                pass
        self.proc = None
        try:
            os.remove(puente.STATUS_FILE)
        except OSError:
            pass

    # ---- estado (se actualiza cada segundo en otro hilo)
    def poll_once(self):
        t = self.tel.read()
        g = (t or {}).get("game") or {}
        s = self.read_status()
        now = time.time()
        fresh = bool(s) and now - float(s.get("ts", 0)) < 8
        st = self.st
        rd = self.tel.reader
        st["plugin"] = rd.status if rd is not None else "sin_memoria"
        st["plugin_rev"] = rd.rev if rd is not None else 0
        st["plugin_file"] = self.plugin_installed()
        st["game_dir"] = bool(self.game_dir())
        st["can_install"] = bool(self.bundled_plugin()) and st["game_dir"]
        st["game"] = bool(g.get("connected"))
        st["game_name"] = g.get("gameName") or ""
        st["bridge"] = self.own_bridge() or fresh
        st["last_send"] = float(s.get("ultimo_envio", 0)) if (s and st["bridge"]) else 0.0
        st["sending"] = st["bridge"] and fresh and st["last_send"] > 0 and now - st["last_send"] < 15
        st["busy"] = self.busy
        self._update_live(t)
        self._update_today(t)
        self._auto_start_check()
        probando = self.cfg.get("plugin_probando")
        if probando:
            if st["plugin"] == "ok":
                self._save(plugin_probando="")          # la version nueva funciona: ya no hay que vigilarla
            elif st["plugin"] == "revision" and not self._pending_restore:
                self.log("La version %s del plugin no es compatible (revision %s). Vuelvo a la anterior." % (
                    probando, st["plugin_rev"]))
                self.rollback(auto=True)

    # ---- datos en vivo, resumen de hoy, inicio automatico, diagnostico
    def _update_live(self, t):
        """Velocidad, camion y trabajo actual para la ventana (solo lectura, no toca el puente)."""
        if not (self.st["game"] and t):
            self.live = {}
            return
        num = puente.num
        truck, job, nav = t.get("truck") or {}, t.get("job") or {}, t.get("navigation") or {}
        limit = puente._first(nav.get("speedLimit"), truck.get("speedLimit"))
        live = {"speed": abs(num(truck.get("speed"))) * puente.SPEED_FACTOR,
                "limit": num(limit) if limit is not None and num(limit) > 0 else None,
                "truck": " ".join(str(x) for x in (truck.get("make"), truck.get("model")) if x),
                "job": None}
        try:
            if puente.has_job(t):
                total = num(job.get("plannedDistanceKm"))
                dist = puente._num_or_none(nav.get("estimatedDistance"))     # metros hasta el destino
                rem = None if dist is None else dist / 1000.0
                frac = None
                if rem is not None and total > 0:
                    frac = max(0.0, min(1.0, 1.0 - rem / total))
                live["job"] = {"origen": job.get("sourceCity") or "", "destino": job.get("destinationCity") or "",
                               "carga": job.get("cargo") or "", "peso_t": num(job.get("cargoMass")) / 1000.0,
                               "plazo_min": puente._min_val(job.get("remainingTime"), "min"),
                               "restante_km": rem, "frac": frac}
        except Exception:
            pass
        self.live = live

    def _update_today(self, t):
        """Km de hoy (por el odometro) y entregas/ingresos de hoy (los anota el puente en hoy.json)."""
        today = time.strftime("%Y-%m-%d")
        h = self.cfg.get("hoy") or {}
        odo = puente.num(((t or {}).get("truck") or {}).get("odometer")) if self.st["game"] else 0.0
        if h.get("fecha") != today:
            h = {"fecha": today, "odo0": odo if odo > 0 else None, "km": 0.0}
            self._save(hoy=h)
        elif odo > 0:
            if h.get("odo0") is None or odo < h["odo0"]:
                h["odo0"] = odo
            km = max(0.0, odo - h["odo0"])
            if abs(km - float(h.get("km") or 0)) >= 0.5:
                h["km"] = km
                self._save(hoy=h)
        entregas, ingresos = 0, 0.0
        try:
            with open(os.path.join(puente.STATE_DIR, "hoy.json"), "r", encoding="utf-8") as f:
                d = json.load(f)
            if d.get("fecha") == today:
                entregas, ingresos = int(d.get("entregas") or 0), float(d.get("ingresos") or 0)
        except (OSError, ValueError, TypeError):
            pass
        self.hoy = {"km": float(h.get("km") or 0), "entregas": entregas, "ingresos": ingresos}

    def _auto_start_check(self):
        """Si esta activada la opcion, arranca el puente una vez cuando se abre el juego."""
        if not self.st["game"]:
            self._auto_blocked = False
            return
        if self.own_bridge() or self.external_bridge():
            self._auto_blocked = True          # ya corre: si lo detienes a mano, no se reinicia solo
            return
        if self.cfg.get("puente_auto") and self.logged and not self._auto_blocked and not self.busy:
            self._auto_blocked = True
            self.log("Se detectó el juego: iniciando el puente automáticamente.")
            self.start()

    def diagnostic(self):
        """Texto para pegar en el chat cuando algo falla (sin correo ni contraseña)."""
        st, u, iv = self.st, self.upd, self.plugin_version()
        rows = [
            "Road Phone %s" % VERSION_TAG,
            "Python %s | %s" % (platform.python_version(), platform.platform()),
            "Empaquetado (.exe): %s" % ("sí" if getattr(sys, "frozen", False) else "no"),
            "Sesión: %s" % ("iniciada" if self.logged else "sin sesión"),
            "Carpeta del juego: %s" % (self.game_dir() or "(no detectada)"),
            "Plugin en disco: %s" % ("sí" if st["plugin_file"] else "no"),
            "Plugin (memoria): %s | revisión %s (la app necesita %s)" % (st["plugin"], st["plugin_rev"], puente.telemetria.REVISION),
            "Versión del plugin: %s | última en GitHub: %s" % (actualizador.fmt(iv) if iv else "sin identificar", u.get("latest") or "sin consultar"),
            "Actualización: %s" % (u.get("msg") or "-"),
            "Respaldo del plugin: %s" % ("sí" if self.has_backup() else "no"),
            "Juego conectado: %s %s" % ("sí" if st["game"] else "no", st["game_name"]),
            "Puente: %s | enviando: %s" % ("activo" if st["bridge"] else "detenido", "sí" if st["sending"] else "no"),
            "Instalar menores solas: %s | Puente automático: %s" % (self.cfg.get("plugin_auto", True), bool(self.cfg.get("puente_auto"))),
            "--- Registro (últimas 40 líneas) ---",
        ] + list(self.logbuf)[-40:]
        text = "\n".join(rows)
        mail = self.cfg.get("correo") or ""
        return text.replace(mail, "<correo>") if mail else text

    def poll_loop(self):
        while self._run:
            try:
                self.poll_once()
            except Exception as exc:  # no dejar caer la ventana por un error puntual
                self.log("Error al leer el estado: %r" % (exc,))
            time.sleep(1)

    def shutdown(self):
        self._run = False


# ---------------------------------------------------------------- ventana
# Paleta (cambia estos colores para personalizar el look)
GREEN, RED, AMBER, GRAY, BLUE = "#3fb950", "#f85149", "#d29922", "#8b93a1", "#58a6ff"
BG, CARD, FIELD, FG, BORDER = "#0b0d11", "#141821", "#0e1117", "#e6e8eb", "#222836"
ACCENT, ACCENT_H = "#2f7cf6", "#4a8ff7"
BTN, BTN_HOVER = "#1b2130", "#262e40"
STOP_BG, STOP_H, STOP_FG = "#2b1a1d", "#3a2226", "#ff7b72"
FONT = "Segoe UI"
BANNER_H = 130  # alto de la imagen de cabecera (fondo.png)
WIN_W = 600


GREEN_DIM = "#1d5a2a"
NOTICE_BG = "#2a2312"


def _mix(c1, c2, k):
    """Mezcla dos colores #rrggbb (k=0 -> c1, k=1 -> c2)."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(round(x + (y - x) * k)) for x, y in zip(a, b))


def fmt_minutes(m):
    if m is None:
        return ""
    if m < 0:
        return "vencido"
    h, mi = int(m // 60), int(m % 60)
    return ("%d h %02d min" % (h, mi)) if h else ("%d min" % mi)


def fmt_num(x):
    return "{:,.0f}".format(x).replace(",", ".")


def _log_tag(line):
    l = "".join(c for c in unicodedata.normalize("NFD", line.lower()) if unicodedata.category(c) != "Mn")
    if any(w in l for w in ("error", "fallo", "no pude", "incompatible", "no es compatible", "traceback", "no valida")):
        return "err"
    if any(w in l for w in ("aviso", "hay una version", "esperando", "multa", "reintentara", "no encontr", "omite")):
        return "warn"
    if any(w in l for w in ("conectado", "entregado", "instalado", "actualizado", "restaur", "iniciando el puente", "peaje")):
        return "ok"
    return ""


def find_asset(*names):
    """Busca un archivo junto al .exe/launcher y dentro del paquete (icono.ico, fondo.png...)."""
    dirs = [os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__)),
            getattr(sys, "_MEIPASS", None), puente.STATE_DIR]
    for d in dirs:
        for n in names:
            if d and os.path.isfile(os.path.join(d, n)):
                return os.path.join(d, n)
    return None


class Ui:
    def __init__(self, core):
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
        self.tk, self.ttk, self.filedialog, self.messagebox = tk, ttk, filedialog, messagebox
        self.core = core
        self.root = root = tk.Tk()
        root.title(WINDOW_TITLE)
        root.resizable(False, False)
        root.configure(bg=BG)
        self.settings_open = False
        self.log_open = False
        self._pulse_on = False
        self._notice_shown = False
        self._layout_key = None
        self._theme()
        self._icon()
        self._banner()
        self.container = tk.Frame(root, bg=BG, padx=24, pady=16)
        self.container.pack(fill="both", expand=True)
        self._build_footer()
        self._build_login()
        self._build_main()
        self._show("login")
        self._dark_titlebar()
        self.login_info.config(text="Comprobando la sesión guardada...", foreground=GRAY)
        self._enable(self.btn_login, False)
        threading.Thread(target=self._restore_worker, daemon=True).start()
        threading.Thread(target=core.poll_loop, daemon=True).start()
        threading.Thread(target=core.update_loop, daemon=True).start()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._pulse()
        self.tick()

    # ---- estilo
    def _theme(self):
        st = self.ttk.Style()
        st.theme_use("clam")
        st.configure("TEntry", fieldbackground=FIELD, foreground=FG, insertcolor=FG, padding=7,
                     bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        st.map("TEntry", fieldbackground=[("readonly", FIELD), ("disabled", FIELD)],
               foreground=[("readonly", FG)],
               bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)], darkcolor=[("focus", ACCENT)])

    def _icon(self):
        ico = find_asset("icono.ico")
        if ico:
            try:
                self.root.iconbitmap(ico)
            except Exception:
                pass

    def _dark_titlebar(self):
        """Barra de titulo oscura en Windows 10/11 (si no se puede, se queda normal)."""
        try:
            import ctypes
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            val = ctypes.c_int(1)
            for attr in (20, 19):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
        except Exception:
            pass

    def _banner(self):
        """Imagen de cabecera: pon fondo.png (o .jpg) junto al programa (600x130). Si no existe, no se muestra."""
        path = find_asset("fondo.png", "fondo.jpg", "fondo.jpeg", "fondo.gif")
        if not path:
            return
        tk = self.tk
        try:
            try:
                from PIL import Image, ImageOps, ImageTk
                src = Image.open(path).convert("RGB")
                bgc = src.getpixel((2, 2))
                src = ImageOps.contain(src, (WIN_W, BANNER_H))
                img = Image.new("RGB", (WIN_W, BANNER_H), bgc)
                img.paste(src, ((WIN_W - src.width) // 2, (BANNER_H - src.height) // 2))
                self._banner_img = ImageTk.PhotoImage(img)
            except ImportError:
                img = tk.PhotoImage(file=path)  # sin Pillow solo lee PNG/GIF y no la ajusta
                k = max(1, -(-img.width() // WIN_W))
                self._banner_img = img.subsample(k, k) if k > 1 else img
            holder = tk.Frame(self.root, bg=BG, height=BANNER_H, width=WIN_W)
            holder.pack(fill="x")
            holder.pack_propagate(False)
            tk.Label(holder, image=self._banner_img, bg=BG, bd=0).pack(expand=True)
        except Exception:
            pass

    def fit(self):
        """Ajusta el alto de la ventana al contenido (al plegar/desplegar)."""
        self.root.update_idletasks()
        self.root.geometry("%dx%d" % (WIN_W, self.root.winfo_reqheight()))

    # ---- piezas
    def _enable(self, btn, on):
        btn.config(state="normal" if on else "disabled")

    def _button(self, parent, text, cmd, bg=BTN, hover=BTN_HOVER, fg=FG, size=9, bold=False, **pack):
        b = self.tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground=hover, activeforeground=fg,
                           disabledforeground=GRAY, bd=0, relief="flat", highlightthickness=0, cursor="hand2",
                           font=(FONT, size, "bold" if bold else "normal"), padx=14, pady=6)
        b._bg, b._hover = bg, hover
        b.bind("<Enter>", lambda e: b["state"] == "normal" and b.config(bg=b._hover))
        b.bind("<Leave>", lambda e: b.config(bg=b._bg))
        return b

    def _style_button(self, b, text, bg, hover, fg, enabled=True):
        b._bg, b._hover = bg, hover
        b.config(text=text, bg=bg, fg=fg, activebackground=hover, activeforeground=fg,
                 state="normal" if enabled else "disabled", cursor="hand2" if enabled else "arrow")

    def _link(self, parent, text, cmd, bg=BG, color=GRAY, size=9):
        lb = self.tk.Label(parent, text=text, bg=bg, fg=color, cursor="hand2", font=(FONT, size))
        lb.bind("<Button-1>", lambda e: cmd())
        lb.bind("<Enter>", lambda e: lb.config(fg=FG))
        lb.bind("<Leave>", lambda e: lb.config(fg=color))
        return lb

    def _tile(self, parent, col, title):
        tk = self.tk
        f = tk.Frame(parent, bg=CARD, padx=12, pady=12)
        f.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 4, 0 if col == 3 else 4))
        parent.grid_columnconfigure(col, weight=1, uniform="tile")
        tk.Label(f, text=title, bg=CARD, fg=GRAY, font=(FONT, 8, "bold"), anchor="w").pack(fill="x")
        row = tk.Frame(f, bg=CARD)
        row.pack(fill="x", pady=(8, 0))
        dot = tk.Label(row, text="●", bg=CARD, fg=GRAY, font=(FONT, 9))
        dot.pack(side="left")
        val = tk.Label(row, text="-", bg=CARD, fg=FG, font=(FONT, 10), anchor="w", justify="left", wraplength=92)
        val.pack(side="left", padx=(6, 0))
        return dot, val

    def _set_tile(self, tile, color, text):
        tile[0].config(fg=color)
        tile[1].config(text=text)

    def _fold(self, parent, title, toggle):
        tk = self.tk
        head = tk.Label(parent, text="▸  " + title, bg=BG, fg=GRAY, font=(FONT, 9, "bold"), anchor="w", cursor="hand2")
        head.pack(fill="x", pady=(16, 0))
        head.bind("<Button-1>", lambda e: toggle())
        head.bind("<Enter>", lambda e: head.config(fg=FG))
        head.bind("<Leave>", lambda e: head.config(fg=GRAY))
        body = tk.Frame(parent, bg=CARD, padx=14, pady=12)
        return head, body

    def _build_footer(self):
        tk = self.tk
        foot = tk.Frame(self.container, bg=BG)
        foot.pack(side="bottom", fill="x", pady=(16, 0))
        tk.Frame(foot, bg=BORDER, height=1).pack(fill="x", pady=(0, 8))
        self.lb_ver = tk.Label(foot, text="Road Phone  ·  %s" % VERSION_TAG, bg=BG, fg=GRAY, font=(FONT, 8), cursor="hand2")
        self.lb_ver.pack(side="left")
        self.lb_ver.bind("<Button-1>", lambda e: self.show_about())
        self.lb_net = tk.Label(foot, text="", bg=BG, fg=GRAY, font=(FONT, 8))
        self.lb_net.pack(side="right")
        self.dot_net = tk.Label(foot, text="●", bg=BG, fg=GRAY, font=(FONT, 8))
        self.dot_net.pack(side="right", padx=(0, 6))

    def _build_panel(self, parent):
        """Panel unico: velocidad en vivo, trabajo actual y resumen de hoy (se muestra lo que haya)."""
        tk = self.tk
        self.panel = tk.Frame(parent, bg=CARD, padx=16, pady=12)
        self.panel.pack(fill="x", pady=(8, 0))
        self.live_row = tk.Frame(self.panel, bg=CARD)
        self.lb_speed = tk.Label(self.live_row, text="", bg=CARD, fg=FG, font=(FONT, 16, "bold"))
        self.lb_speed.pack(side="left")
        self.lb_truck = tk.Label(self.live_row, text="", bg=CARD, fg=GRAY, font=(FONT, 9))
        self.lb_truck.pack(side="left", padx=(8, 0), pady=(6, 0))
        self.lb_limit = tk.Label(self.live_row, text="", bg=CARD, fg=GRAY, font=(FONT, 9))
        self.lb_limit.pack(side="right", pady=(6, 0))
        self.job_row = tk.Frame(self.panel, bg=CARD)
        tk.Label(self.job_row, text="TRABAJO ACTUAL", bg=CARD, fg=GRAY, font=(FONT, 8, "bold"), anchor="w").pack(fill="x")
        self.lb_route = tk.Label(self.job_row, text="", bg=CARD, fg=FG, font=(FONT, 11, "bold"), anchor="w")
        self.lb_route.pack(fill="x", pady=(4, 0))
        self.lb_jobinfo = tk.Label(self.job_row, text="", bg=CARD, fg=GRAY, font=(FONT, 9), anchor="w")
        self.lb_jobinfo.pack(fill="x", pady=(2, 0))
        self.bar = tk.Canvas(self.job_row, height=4, bg=BORDER, bd=0, highlightthickness=0)
        self.bar.pack(fill="x", pady=(8, 0))
        self.today_row = tk.Frame(self.panel, bg=CARD)
        self.v_today = []
        for i, cap in enumerate(("KM HOY", "ENTREGAS", "INGRESOS")):
            col = tk.Frame(self.today_row, bg=CARD)
            col.grid(row=0, column=i, sticky="w")
            self.today_row.grid_columnconfigure(i, weight=1, uniform="today")
            v = tk.Label(col, text="0", bg=CARD, fg=FG, font=(FONT, 12, "bold"), anchor="w")
            v.pack(fill="x")
            tk.Label(col, text=cap, bg=CARD, fg=GRAY, font=(FONT, 8, "bold"), anchor="w").pack(fill="x")
            self.v_today.append(v)
        self._layout_panel(False, False)

    def _layout_panel(self, show_live, show_job):
        for w in (self.live_row, self.job_row, self.today_row):
            w.pack_forget()
        if show_live:
            self.live_row.pack(fill="x")
        if show_job:
            self.job_row.pack(fill="x", pady=(12, 0))
        self.today_row.pack(fill="x", pady=(12 if (show_live or show_job) else 0, 0))

    def _draw_bar(self, frac):
        w = max(self.bar.winfo_width(), 10)
        self.bar.delete("all")
        if frac:
            self.bar.create_rectangle(0, 0, int(w * frac), 4, fill=ACCENT, width=0)

    def _pulse(self):
        """Latido suave del punto mientras se envian datos."""
        if self._pulse_on:
            col = _mix(GREEN_DIM, GREEN, 0.5 + 0.5 * math.sin(time.time() * 3.0))
            self.t_bridge[0].config(fg=col)
            self.dot_net.config(fg=col)
        self.root.after(80, self._pulse)

    # ---- pantallas
    def _build_login(self):
        tk, ttk = self.tk, self.ttk
        f = self.login_frame = tk.Frame(self.container, bg=BG)
        box = tk.Frame(f, bg=CARD, padx=26, pady=22)
        box.pack(fill="x", pady=(14, 8))
        tk.Label(box, text="Iniciar sesión", bg=CARD, fg=FG, font=(FONT, 14, "bold"), anchor="w").pack(fill="x")
        tk.Label(box, text="Usa la misma cuenta que en la app Road Phone.", bg=CARD, fg=GRAY,
                 font=(FONT, 9), anchor="w").pack(fill="x", pady=(2, 14))
        tk.Label(box, text="CORREO", bg=CARD, fg=GRAY, font=(FONT, 8, "bold"), anchor="w").pack(fill="x")
        self.var_mail = tk.StringVar(value=self.core.cfg.get("correo") or self.core.saved_email())
        self.ent_mail = ttk.Entry(box, textvariable=self.var_mail)
        self.ent_mail.pack(fill="x", pady=(4, 12))
        tk.Label(box, text="CONTRASEÑA", bg=CARD, fg=GRAY, font=(FONT, 8, "bold"), anchor="w").pack(fill="x")
        self.var_pass = tk.StringVar()
        self.ent_pass = ttk.Entry(box, textvariable=self.var_pass, show="*")
        self.ent_pass.pack(fill="x", pady=(4, 16))
        self.btn_login = self._button(box, "Iniciar sesión", self.do_login, bg=ACCENT, hover=ACCENT_H, fg="white",
                                      size=10, bold=True)
        self.btn_login.pack(fill="x", ipady=4)
        self.login_info = tk.Label(f, text="", bg=BG, fg=GRAY, font=(FONT, 9), wraplength=540, anchor="w", justify="left")
        self.login_info.pack(fill="x", padx=4, pady=(4, 0))
        self.ent_mail.bind("<Return>", lambda e: self.ent_pass.focus_set())
        self.ent_pass.bind("<Return>", lambda e: self.do_login())

    def _build_main(self):
        tk, ttk = self.tk, self.ttk
        f = self.main_frame = tk.Frame(self.container, bg=BG)
        # franja de sesion
        top = tk.Frame(f, bg=BG)
        top.pack(fill="x", pady=(0, 16))
        self.dot_session = tk.Label(top, text="●", bg=BG, fg=GRAY, font=(FONT, 9))
        self.dot_session.pack(side="left")
        self.lb_session = tk.Label(top, text="-", bg=BG, fg=FG, font=(FONT, 10))
        self.lb_session.pack(side="left", padx=(6, 0))
        tk.Label(top, text=APP_STAGE.upper(), bg=BTN, fg=AMBER, font=(FONT, 7, "bold"), padx=6, pady=1).pack(side="left", padx=(10, 0))
        self._link(top, "Cerrar sesión", self.do_logout).pack(side="right")
        # mosaicos de estado
        row = tk.Frame(f, bg=BG)
        row.pack(fill="x")
        self.t_plugin = self._tile(row, 0, "PLUGIN")
        self.t_game = self._tile(row, 1, "JUEGO")
        self.t_bridge = self._tile(row, 2, "PUENTE")
        self.t_last = self._tile(row, 3, "ÚLTIMO ENVÍO")
        self.notice = tk.Frame(f, bg=NOTICE_BG, padx=12, pady=8)
        self.lb_notice = tk.Label(self.notice, text="", bg=NOTICE_BG, fg=AMBER, font=(FONT, 9), anchor="w")
        self.lb_notice.pack(side="left")
        self._link(self.notice, "Ver", lambda: self.toggle_settings(True), bg=NOTICE_BG, color=AMBER).pack(side="right")
        self._build_panel(f)
        # boton principal
        self.btn_main = self._button(f, "Iniciar", self.on_main, bg=ACCENT, hover=ACCENT_H, fg="white", size=11, bold=True)
        self.btn_main.pack(fill="x", pady=(16, 0), ipady=6)
        self.lb_hint = tk.Label(f, text="", bg=BG, fg=GRAY, font=(FONT, 9), wraplength=552, anchor="w", justify="left")
        self.lb_hint.pack(fill="x", pady=(8, 0))
        # ajustes (plegado)
        self.head_set, self.body_set = self._fold(f, "Ajustes", self.toggle_settings)
        tk.Label(self.body_set, text="ROAD PHONE", bg=CARD, fg=GRAY,
                 font=(FONT, 8, "bold"), anchor="w").pack(fill="x")
        self.var_aver = tk.StringVar(value="")
        tk.Label(self.body_set, textvariable=self.var_aver, bg=CARD, fg=GRAY, font=(FONT, 9), anchor="w",
                 justify="left", wraplength=520).pack(fill="x", pady=(4, 6))
        arow = tk.Frame(self.body_set, bg=CARD)
        arow.pack(fill="x", pady=(0, 10))
        self.btn_acheck = self._button(arow, "Buscar actualización", self.do_check_app)
        self.btn_acheck.pack(side="left")
        self.btn_aupdate = self._button(arow, "Actualizar Road Phone", self.do_update_app, bg=ACCENT, hover=ACCENT_H, fg="white")
        self.btn_aupdate.pack(side="left", padx=(8, 0))
        tk.Label(self.body_set, text="PLUGIN DEL JUEGO", bg=CARD, fg=GRAY,
                 font=(FONT, 8, "bold"), anchor="w").pack(fill="x")
        prow = tk.Frame(self.body_set, bg=CARD)
        prow.pack(fill="x", pady=(4, 10))
        self.var_plugin = tk.StringVar(value="")
        ttk.Entry(prow, textvariable=self.var_plugin, state="readonly").pack(side="left", fill="x", expand=True)
        self.btn_install = self._button(prow, "Instalar", self.do_install)
        self.btn_install.pack(side="left", padx=(8, 0))
        self.var_pver = tk.StringVar(value="")
        tk.Label(self.body_set, textvariable=self.var_pver, bg=CARD, fg=GRAY, font=(FONT, 9), anchor="w",
                 justify="left", wraplength=520).pack(fill="x", pady=(0, 6))
        urow = tk.Frame(self.body_set, bg=CARD)
        urow.pack(fill="x", pady=(0, 6))
        self.btn_check = self._button(urow, "Buscar plugin nuevo", self.do_check_update)
        self.btn_check.pack(side="left")
        self.btn_update = self._button(urow, "Actualizar plugin", self.do_update_plugin, bg=ACCENT, hover=ACCENT_H, fg="white")
        self.btn_update.pack(side="left", padx=(8, 0))
        self.btn_restore = self._button(urow, "Restaurar anterior", self.do_restore_plugin)
        self.btn_restore.pack(side="left", padx=(8, 0))
        self.var_auto = tk.BooleanVar(value=bool(self.core.cfg.get("plugin_auto", True)))
        tk.Checkbutton(self.body_set, text="Instalar solas las versiones menores (con copia de respaldo)",
                       variable=self.var_auto, command=self.on_auto_toggle, bg=CARD, fg=FG, selectcolor=FIELD,
                       activebackground=CARD, activeforeground=FG, bd=0, highlightthickness=0, anchor="w",
                       font=(FONT, 9)).pack(fill="x", pady=(0, 4))
        self.var_pauto = tk.BooleanVar(value=bool(self.core.cfg.get("puente_auto", False)))
        tk.Checkbutton(self.body_set, text="Iniciar el puente solo al abrir el juego",
                       variable=self.var_pauto, command=self.on_pauto_toggle, bg=CARD, fg=FG, selectcolor=FIELD,
                       activebackground=CARD, activeforeground=FG, bd=0, highlightthickness=0, anchor="w",
                       font=(FONT, 9)).pack(fill="x", pady=(0, 10))
        tk.Label(self.body_set, text="CARPETA DEL JUEGO", bg=CARD, fg=GRAY,
                 font=(FONT, 8, "bold"), anchor="w").pack(fill="x")
        grow = tk.Frame(self.body_set, bg=CARD)
        grow.pack(fill="x", pady=(4, 10))
        self.var_gdir = tk.StringVar(value=self.core.game_dir())
        ttk.Entry(grow, textvariable=self.var_gdir, state="readonly").pack(side="left", fill="x", expand=True)
        self._button(grow, "Examinar", self.pick_game_dir).pack(side="left", padx=(8, 0))
        orow = tk.Frame(self.body_set, bg=CARD)
        orow.pack(fill="x", pady=(10, 0))
        self._link(orow, "Abrir carpeta de datos", self.open_data, bg=CARD).pack(side="left")
        self._link(orow, "Copiar informe de diagnóstico", self.copy_diag, bg=CARD).pack(side="left", padx=(16, 0))
        self._link(orow, "Acerca de", self.show_about, bg=CARD).pack(side="left", padx=(16, 0))
        # registro (plegado)
        self.head_log, self.body_log = self._fold(f, "Registro", self.toggle_log)
        self.txt = tk.Text(self.body_log, height=7, wrap="word", state="disabled", font=("Consolas", 9),
                           relief="flat", bd=0, highlightthickness=0, bg=FIELD, fg=FG, insertbackground=FG,
                           selectbackground=BTN_HOVER, padx=8, pady=6)
        self.txt.pack(fill="both", expand=True)
        for tag, col in (("err", RED), ("warn", AMBER), ("ok", GREEN)):
            self.txt.tag_configure(tag, foreground=col)
        brow = tk.Frame(self.body_log, bg=CARD)
        brow.pack(fill="x", pady=(8, 0))
        self._link(brow, "Copiar", self.copy_log, bg=CARD).pack(side="left")
        self._link(brow, "Limpiar", self.clear_log, bg=CARD).pack(side="left", padx=(16, 0))

    def _show(self, which):
        self.login_frame.pack_forget()
        self.main_frame.pack_forget()
        (self.login_frame if which == "login" else self.main_frame).pack(fill="both", expand=True)
        self.fit()

    def _fold_titles(self):
        k = self.core.upd.get("kind")
        self.head_set.config(text=("▾  " if self.settings_open else "▸  ") + "Ajustes" +
                             ("   ·   actualización del plugin disponible" if k else ""),
                             fg=AMBER if k else GRAY)

    def toggle_settings(self, open_=None):
        self.settings_open = (not self.settings_open) if open_ is None else open_
        if self.settings_open:
            self.body_set.pack(fill="x", pady=(6, 0), after=self.head_set)
        else:
            self.body_set.pack_forget()
        self._fold_titles()
        self.fit()

    def toggle_log(self):
        self.log_open = not self.log_open
        if self.log_open:
            self.body_log.pack(fill="x", pady=(6, 0), after=self.head_log)
            self.txt.see("end")
        else:
            self.body_log.pack_forget()
        self.head_log.config(text=("▾  " if self.log_open else "▸  ") + "Registro")
        self.fit()

    # ---- acciones
    def on_main(self):
        st = self.core.st
        if st["busy"]:
            return
        if st["bridge"]:
            self.do_stop()
        elif not st["plugin_file"] and st["can_install"] and st["plugin"] != "ok":
            self.do_install()
        else:
            self.core.start()

    def show_about(self):
        notas = []
        for ver, items in CHANGELOG[:3]:
            notas.append("v%s" % ver)
            notas.extend("  • " + i for i in items)
        self.messagebox.showinfo(TITLE, "Road Phone  ·  %s\n\nLee la telemetría del juego con su plugin y la envía a Road Phone.\n"
                                        "El juego se abre aparte.\n\nNovedades\n%s" % (VERSION_TAG, "\n".join(notas)))

    def _to_clipboard(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

    def copy_diag(self):
        self._to_clipboard(self.core.diagnostic())
        self.messagebox.showinfo(TITLE, "Informe copiado. Pégalo en el chat (no incluye tu correo ni tu contraseña).")

    def copy_log(self):
        self._to_clipboard(self.txt.get("1.0", "end").strip())

    def clear_log(self):
        self.core.logbuf.clear()
        self.txt.config(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.config(state="disabled")

    def on_pauto_toggle(self):
        self.core._save(puente_auto=bool(self.var_pauto.get()))

    def _restore_worker(self):
        ok = False
        try:
            ok = self.core.try_restore()
        except Exception as exc:
            self.core.log("No pude comprobar la sesion: %r" % (exc,))
        self.core.events.put(("session", ok))

    def do_login(self):
        mail, pw = self.var_mail.get(), self.var_pass.get()
        if not mail.strip() or not pw:
            self.login_info.config(text="Escribe tu correo y tu contraseña.", foreground=RED)
            return
        self._enable(self.btn_login, False)
        self.login_info.config(text="Iniciando sesión...", foreground=GRAY)

        def work():
            try:
                ok, msg = self.core.login(mail, pw)
            except Exception as exc:
                ok, msg = False, str(exc)
            self.core.events.put(("login", ok, msg))
        threading.Thread(target=work, daemon=True).start()

    def do_logout(self):
        def work():
            self.core.logout()
            self.core.events.put(("session", False))
        threading.Thread(target=work, daemon=True).start()

    def do_stop(self):
        threading.Thread(target=self.core.stop, daemon=True).start()

    def _bg(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def do_check_update(self):
        def work():
            self.core.check_update(manual=True)
            if self.core.upd["kind"] is None and not self.core.upd["busy"]:
                self.core.events.put(("info", self.core.upd["msg"] or "Listo."))
        self._bg(work)

    def do_check_app(self):
        def work():
            self.core.check_app_update()
            self.core.events.put(("warn" if self.core.app["error"] else "info", self.core.app["msg"] or "Listo."))
        self._bg(work)

    def do_update_app(self):
        rel = self.core.app.get("rel")
        if not rel:
            return
        notas = (rel.get("notes") or "").strip()[:400]
        if self.messagebox.askyesno(TITLE, "Se descargará Road Phone %s y la aplicación se reiniciará sola.\n"
                                           "Se guarda una copia de la versión actual por si algo falla.\n\n%s\n\n¿Continuar?" % (
                rel["tag"], notas)):
            self._bg(self.core.update_app_now)

    def do_update_plugin(self):
        u = self.core.upd
        extra = ""
        if u["kind"] in ("mayor", "desconocida"):
            extra = "\n\nEs un salto mayor o de una versión sin identificar: si no es compatible, volveré a la anterior sola."
        if self.messagebox.askyesno(TITLE, "Se guardará una copia del plugin actual y se instalará la versión v%s.%s\n\n¿Continuar?" % (
                u["latest"], extra)):
            self._bg(self.core.update_now)

    def do_restore_plugin(self):
        if self.messagebox.askyesno(TITLE, "¿Volver a la versión anterior del plugin (la copia de respaldo)?"):
            self._bg(self.core.rollback)

    def on_auto_toggle(self):
        self.core._save(plugin_auto=bool(self.var_auto.get()))

    def pick_game_dir(self):
        path = self.filedialog.askdirectory(title="Elige la carpeta de Euro Truck Simulator 2")
        if path:
            path = os.path.normpath(path)
            self.core.set_game_dir(path)
            self.var_gdir.set(path)

    def do_install(self):
        ok, msg = self.core.install_plugin()
        self.core.log(msg)
        (self.messagebox.showinfo if ok else self.messagebox.showwarning)(TITLE, msg)

    def open_data(self):
        try:
            os.makedirs(puente.STATE_DIR, exist_ok=True)
            os.startfile(puente.STATE_DIR)  # noqa
        except Exception:
            webbrowser.open(puente.STATE_DIR)

    def on_close(self):
        if self.core.own_bridge():
            if not self.messagebox.askyesno(TITLE, "El puente sigue en ejecución.\n¿Detenerlo y salir?"):
                return
            self.core.stop()
        self.core.shutdown()
        self.root.destroy()

    # ---- refresco (cada 500 ms)
    def tick(self):
        c, st = self.core, self.core.st
        while True:
            try:
                ev = c.events.get_nowait()
            except queue.Empty:
                break
            if ev[0] == "session":
                if ev[1]:
                    self._show("main")
                else:
                    self._show("login")
                    self.login_info.config(text="", foreground=GRAY)
                    self._enable(self.btn_login, True)
            elif ev[0] == "login":
                if ev[1]:
                    self.var_pass.set("")
                    self.login_info.config(text="")
                    self._show("main")
                else:
                    self._enable(self.btn_login, True)
                    self.login_info.config(text="No se pudo iniciar sesión: %s" % ev[2], foreground=RED)
            elif ev[0] == "info":
                self.messagebox.showinfo(TITLE, ev[1])
            elif ev[0] == "warn":
                self.messagebox.showwarning(TITLE, ev[1])
            elif ev[0] == "salir":
                self.core.shutdown()
                self.root.destroy()
                return
            elif ev[0] == "session_lost":
                self.messagebox.showwarning(TITLE, "La sesión guardada ya no es válida. Inicia sesión de nuevo.")
                self.do_logout()
        # sesion
        self.dot_session.config(fg=GREEN if c.logged else RED)
        self.lb_session.config(text=c.user if c.logged else "Sin sesión")
        # mosaicos
        running = bool(st["bridge"] or st["busy"])
        pl = st["plugin"]
        if pl == "ok":
            self._set_tile(self.t_plugin, GREEN, "Conectado")
        elif pl == "revision":
            self._set_tile(self.t_plugin, RED, "Incompatible")
        elif pl == "inactivo" or st["plugin_file"]:
            self._set_tile(self.t_plugin, GREEN, "Instalado")
        elif st["game_dir"]:
            self._set_tile(self.t_plugin, RED, "Falta")
        else:
            self._set_tile(self.t_plugin, GRAY, "Sin detectar")
        if st["game"]:
            self._set_tile(self.t_game, GREEN, "Conectado")
        elif pl in ("ok", "inactivo") or (st["bridge"] and st["plugin_file"]):
            self._set_tile(self.t_game, AMBER, "Esperando")
        else:
            self._set_tile(self.t_game, GRAY, "Sin datos")
        if st["sending"]:
            self._set_tile(self.t_bridge, GREEN, "Enviando")
        elif st["bridge"]:
            self._set_tile(self.t_bridge, AMBER, "Activo")
        elif st["busy"]:
            self._set_tile(self.t_bridge, AMBER, "Iniciando")
        else:
            self._set_tile(self.t_bridge, GRAY, "Detenido")
        if st["last_send"] > 0 and st["bridge"]:
            self._set_tile(self.t_last, FG, "hace %d s" % max(0, int(time.time() - st["last_send"])))
        else:
            self._set_tile(self.t_last, GRAY, "—")
        # panel en vivo / trabajo / hoy
        lv = c.live
        job = lv.get("job") if lv else None
        key = (bool(lv), bool(job))
        if key != self._layout_key:
            self._layout_key = key
            self._layout_panel(*key)
            self.fit()
        if lv:
            self.lb_speed.config(text="%d km/h" % round(lv["speed"]))
            self.lb_truck.config(text=lv["truck"])
            lim = lv["limit"]
            over = bool(lim) and lv["speed"] > lim + 3
            self.lb_limit.config(text=("Límite %d" % round(lim)) if lim else "", fg=RED if over else GRAY)
        if job:
            self.lb_route.config(text="%s  →  %s" % (job["origen"] or "?", job["destino"] or "?"))
            bits = []
            if job["carga"]:
                bits.append("%s (%.0f t)" % (job["carga"], job["peso_t"]) if job["peso_t"] > 0 else job["carga"])
            if job["plazo_min"] is not None:
                bits.append("Plazo " + fmt_minutes(job["plazo_min"]))
            if job["restante_km"] is not None:
                bits.append("%s km restantes" % fmt_num(job["restante_km"]))
            self.lb_jobinfo.config(text="  ·  ".join(bits))
            self._draw_bar(job["frac"])
        hy = c.hoy
        self.v_today[0].config(text=fmt_num(hy["km"]))
        self.v_today[1].config(text=str(hy["entregas"]))
        self.v_today[2].config(text=fmt_num(hy["ingresos"]))
        # pie: estado del puente
        self._pulse_on = bool(st["sending"] and c.logged)
        if not c.logged:
            self.dot_net.config(fg=GRAY)
            self.lb_net.config(text="")
        elif st["sending"]:
            self.lb_net.config(text="Enviando datos", fg=GREEN)
        elif st["bridge"]:
            self.dot_net.config(fg=AMBER)
            self.lb_net.config(text="Puente activo", fg=AMBER)
        else:
            self.dot_net.config(fg=GRAY)
            self.lb_net.config(text="Puente detenido", fg=GRAY)
        # boton principal y mensaje
        need_install = (not st["plugin_file"]) and st["can_install"] and pl != "ok"
        if st["busy"]:
            self._style_button(self.btn_main, "Iniciando…", BTN, BTN, GRAY, enabled=False)
            hint = "Preparando el puente..."
        elif st["bridge"]:
            self._style_button(self.btn_main, "Detener", STOP_BG, STOP_H, STOP_FG)
            if st["sending"]:
                hint = "Enviando datos%s." % ((" de %s" % st["game_name"]) if st["game_name"] else "")
            elif pl == "revision":
                hint = "El plugin del juego es la revisión %s y esta app necesita la %s." % (
                    st["plugin_rev"], puente.telemetria.REVISION)
            else:
                hint = "Esperando a que abras el juego (TruckersMP, HaulMP...)."
        elif need_install:
            self._style_button(self.btn_main, "Instalar plugin", ACCENT, ACCENT_H, "white")
            hint = "Falta instalar el plugin de telemetría en tu juego. Un clic y queda listo."
        else:
            self._style_button(self.btn_main, "Iniciar", ACCENT, ACCENT_H, "white")
            if not st["game_dir"] and pl != "ok":
                hint = "No encontré la carpeta del juego. Elígela en Ajustes > Examinar."
            elif not st["plugin_file"] and pl != "ok":
                hint = "Falta %s en la carpeta plugins del juego (Ajustes > Plugin del juego)." % PLUGIN_FILE
            else:
                hint = "El juego se abre aparte. Pulsa Iniciar para preparar todo."
        u = c.upd
        app_new = bool(c.app["rel"])
        want_notice = (app_new or bool(u["kind"])) and not st["busy"]
        if want_notice:
            self.lb_notice.config(text=("Hay una versión nueva de Road Phone (%s)." % c.app["rel"]["tag"]) if app_new
                                  else "Hay una versión nueva del plugin (v%s)." % u["latest"])
        if want_notice != self._notice_shown:
            self._notice_shown = want_notice
            if want_notice:
                self.notice.pack(fill="x", pady=(8, 0), before=self.panel)
            else:
                self.notice.pack_forget()
            self.fit()
        self.lb_hint.config(text=hint)
        iv = c.plugin_version()
        inst = ("v" + actualizador.fmt(iv)) if iv else "versión sin identificar"
        if u["busy"]:
            ptxt = "Buscando actualizaciones..."
        elif u["kind"] == "menor":
            ptxt = "Instalada: %s · Nueva: v%s (menor%s)" % (inst, u["latest"],
                   ", se instalará sola al cerrar el juego" if self.var_auto.get() else "")
        elif u["kind"]:
            ptxt = "Instalada: %s · Nueva: v%s (necesita tu confirmación)" % (inst, u["latest"])
        elif u["latest"]:
            ptxt = "Instalada: %s · Al día (última: v%s)" % (inst, u["latest"])
        else:
            ptxt = "Instalada: %s · Aún sin consultar" % inst
        self.var_pver.set(ptxt)
        ap = c.app
        self.var_aver.set("Instalada: %s%s" % (VERSION_TAG, ("   ·   Última: %s" % ap["rel"]["tag"]) if ap["rel"] else ""))
        self._enable(self.btn_acheck, not ap["busy"])
        self._enable(self.btn_aupdate, bool(ap["rel"]) and not ap["busy"])
        self._enable(self.btn_check, not u["busy"] and st["plugin_file"])
        self._enable(self.btn_update, bool(u["kind"]) and not u["busy"] and st["plugin_file"])
        self._enable(self.btn_restore, c.has_backup() and not u["busy"])
        self._fold_titles()
        # ajustes: estado del plugin
        if pl == "ok":
            ptxt = "Conectado · revisión %s" % st["plugin_rev"]
        elif pl == "revision":
            ptxt = "Versión incompatible (revisión %s)" % st["plugin_rev"]
        elif st["plugin_file"] or pl == "inactivo":
            ptxt = "Instalado · listo"
        elif st["game_dir"]:
            ptxt = "No instalado"
        else:
            ptxt = "Carpeta del juego sin detectar"
        self.var_plugin.set(ptxt)
        self.btn_install.config(text="Reinstalar" if st["plugin_file"] else "Instalar")
        self._enable(self.btn_install, bool(st["can_install"]))
        # registro
        lines = []
        while True:
            try:
                lines.append(c.logq.get_nowait())
            except queue.Empty:
                break
        if lines:
            self.txt.config(state="normal")
            for ln in lines:
                self.txt.insert("end", ln + "\n", _log_tag(ln))
            n = int(self.txt.index("end-1c").split(".")[0])
            if n > 400:
                self.txt.delete("1.0", "%d.0" % (n - 400))
            self.txt.see("end")
            self.txt.config(state="disabled")
        self.root.after(500, self.tick)

    def run(self):
        self.root.mainloop()


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    core = Core()
    try:
        Ui(core).run()
    except Exception as exc:
        try:
            os.makedirs(puente.STATE_DIR, exist_ok=True)
            with open(os.path.join(puente.STATE_DIR, "launcher_error.txt"), "w", encoding="utf-8") as f:
                f.write(repr(exc))
        except OSError:
            pass
        raise
    finally:
        core.shutdown()


if __name__ == "__main__":
    main()
