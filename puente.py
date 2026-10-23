#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Road Phone - Puente de telemetria
---------------------------------
Lee los datos de ETS2/ATS desde el servidor de telemetria que corre en tu PC
y los envia a tu Supabase: posicion, trabajo tomado, peajes, multas y entrega.

Solo usa la biblioteca estandar de Python 3 (no hay que instalar nada mas).
Nunca guarda tu contrasena: solo una sesion renovable en tu carpeta de usuario.

Uso:   python puente.py           (o doble clic en iniciar_puente.bat)
       python puente.py --debug   (muestra mas detalle)
       python puente.py --salir   (olvida la sesion guardada)
       python puente.py --config  (vuelve a preguntar donde esta el servidor y si abre el juego)

Al abrirlo, tambien arranca solo el servidor de telemetria (si no esta abierto)
y, si lo configuras, el juego.
"""
import getpass
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

import telemetria
from datetime import datetime

# ---------------------------------------------------------------- configuracion
SUPABASE_URL = os.environ.get("RP_SUPABASE_URL", "https://msbtbkcffkkkxqeroosg.supabase.co").rstrip("/")
ANON_KEY = os.environ.get("RP_ANON_KEY", "sb_publishable_tcNwk3dE8S6DUpdHjek43Q__D_hCMjW")
# "plugin": lee directo el plugin scs-telemetry del juego (por defecto, sin servidor intermedio).
# "http":   usa un servidor de telemetria por HTTP (modo antiguo, por si lo necesitas).
SOURCE = (os.environ.get("RP_SOURCE") or "plugin").strip().lower()
TELEMETRY_URLS = ([os.environ["RP_TELEMETRY_URL"]] if os.environ.get("RP_TELEMETRY_URL") else [
    "http://127.0.0.1:31377/api/ets2/telemetry",
    "http://127.0.0.1:25555/api/ets2/telemetry",
])
POLL = float(os.environ.get("RP_POLL", "0.5"))          # segundos entre lecturas
POS_EVERY = float(os.environ.get("RP_POS_EVERY", "1"))  # segundos entre envios de posicion
SPEED_FACTOR = float(os.environ.get("RP_SPEED_FACTOR", "1"))  # ajustar si la velocidad no sale en km/h
STATE_DIR = os.environ.get("RP_STATE_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "RoadPhoneBridge")
DEBUG = "--debug" in sys.argv

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def dbg(msg):
    if DEBUG:
        log("(debug) " + msg)


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- red
def http(method, url, body=None, headers=None, timeout=10):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 0, str(e)


# ---------------------------------------------------------------- sesion
class Session:
    def __init__(self):
        self.access = None
        self.refresh_token = None
        self.expires_at = 0
        self.user_id = None
        self.email = ""
        self.nombre = ""
        self.path = os.path.join(STATE_DIR, "sesion.json")

    def _auth(self, grant, payload):
        st, data = http("POST", "%s/auth/v1/token?grant_type=%s" % (SUPABASE_URL, grant), payload,
                        {"apikey": ANON_KEY, "Content-Type": "application/json"})
        if st != 200 or not isinstance(data, dict) or "access_token" not in data:
            msg = data.get("error_description") or data.get("msg") if isinstance(data, dict) else str(data)
            return False, msg or "error %s" % st
        self.access = data["access_token"]
        self.refresh_token = data.get("refresh_token", self.refresh_token)
        self.expires_at = time.time() + int(data.get("expires_in", 3600))
        self.user_id = (data.get("user") or {}).get("id") or self.user_id
        self.email = (data.get("user") or {}).get("email") or self.email
        self._save()
        return True, ""

    def _save(self):
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"refresh_token": self.refresh_token, "email": self.email}, f)
        except OSError:
            pass

    def forget(self):
        try:
            os.remove(self.path)
        except OSError:
            pass

    def login(self, email, password):
        return self._auth("password", {"email": email, "password": password})

    def restore(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return False
        self.refresh_token = saved.get("refresh_token")
        self.email = saved.get("email", "")
        return bool(self.refresh_token) and self._auth("refresh_token", {"refresh_token": self.refresh_token})[0]

    def ensure(self):
        if time.time() > self.expires_at - 120 and self.refresh_token:
            ok, _ = self._auth("refresh_token", {"refresh_token": self.refresh_token})
            if not ok:
                dbg("no se pudo renovar la sesion")
        return self.access

    def headers(self, extra=None):
        h = {"apikey": ANON_KEY, "Authorization": "Bearer %s" % self.ensure(), "Content-Type": "application/json"}
        h.update(extra or {})
        return h

    def rest(self, method, path, body=None, extra=None):
        return http(method, SUPABASE_URL + path, body, self.headers(extra))

    def load_profile(self):
        st, data = self.rest("GET", "/rest/v1/profiles?id=eq.%s&select=nombre" % self.user_id)
        if st == 200 and isinstance(data, list) and data:
            self.nombre = data[0].get("nombre") or ""


def do_login(sess):
    if "--salir" in sys.argv:
        sess.forget()
        log("Sesion olvidada.")
        sys.exit(0)
    if os.environ.get("RP_EMAIL"):
        ok, msg = sess.login(os.environ["RP_EMAIL"], os.environ.get("RP_PASSWORD", ""))
        if not ok:
            log("No se pudo iniciar sesion: %s" % msg)
            sys.exit(1)
        return
    if sess.restore():
        return
    if os.environ.get("RP_FROM_LAUNCHER"):
        log("La sesion guardada no es valida. Inicia sesion de nuevo en el launcher.")
        sys.exit(2)
    print("Inicia sesion con la misma cuenta que usas en la app del movil.")
    for _ in range(5):
        email = input("Correo: ").strip()
        password = getpass.getpass("Contrasena: ")
        ok, msg = sess.login(email, password)
        if ok:
            return
        log("No se pudo iniciar sesion: %s" % msg)
    sys.exit(1)


# ---------------------------------------------------------------- trabajo en curso (se guarda en disco)
JOB_FILE = os.path.join(STATE_DIR, "trabajo_actual.json")


def save_job(cur, acc, start):
    try:
        if cur is None:
            if os.path.exists(JOB_FILE):
                os.remove(JOB_FILE)
        else:
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(JOB_FILE, "w", encoding="utf-8") as f:
                json.dump({"cur": cur, "acc": acc, "start": start}, f)
    except OSError:
        pass


def load_job():
    try:
        with open(JOB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- cola de envios
class Queue:
    """Guarda los envios pendientes (tambien en disco) y reintenta si falla la red."""

    def __init__(self, sess):
        self.sess = sess
        self.items = []
        self.path = os.path.join(STATE_DIR, "pendientes.json")
        self.next_try = 0
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.items = json.load(f)
        except (OSError, ValueError):
            self.items = []

    def _save(self):
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.items, f)
        except OSError:
            pass

    def add(self, fn, payload):
        self.items.append({"fn": fn, "payload": payload})
        self._save()

    def flush(self):
        if not self.items or time.time() < self.next_try:
            return
        while self.items:
            it = self.items[0]
            st, data = self.sess.rest("POST", "/rest/v1/rpc/%s" % it["fn"], {"p": it["payload"]})
            if st in (200, 204):
                self.items.pop(0)
                self._save()
                self._report(it["fn"], it["payload"], data)
            elif st == 0 or st >= 500:
                self.next_try = time.time() + 10
                log("Sin conexion con el servidor. Reintento en 10 s (pendientes: %d)." % len(self.items))
                return
            else:
                msg = data.get("message") if isinstance(data, dict) else data
                log("El servidor rechazo '%s': %s" % (it["fn"], msg))
                self.items.pop(0)  # un rechazo no se repite
                self._save()

    @staticmethod
    def _report(fn, payload, data):
        if fn == "puente_trabajo_entregado" and isinstance(data, dict):
            log("Entrega registrada: factura #%s por %s (%s)" % (
                str(data.get("folio", "?")).zfill(4), data.get("neto"), "ya estaba registrada" if data.get("repetido") else "nueva"))


# ---------------------------------------------------------------- lectura del juego
class Telemetry:
    def __init__(self, events=True, announce=True):
        self.url = None
        self.reader = telemetria.Reader(events=events) if SOURCE != "http" else None
        self.announce = announce          # avisar en el registro cuando cambia el estado del plugin
        self._last_status = None
        self._hinted = False

    def _announce(self):
        r = self.reader
        st = r.status
        if self.announce and st != self._last_status:
            prev, self._last_status = self._last_status, st
            if st == "ok":
                log("Plugin de telemetria conectado (revision %s)." % r.rev)
            elif st == "revision":
                log("El plugin del juego es de otra version (revision %s). Esta app necesita la revision %s." % (
                    r.rev, telemetria.REVISION))
            elif st == "inactivo" and prev in (None, "sin_memoria"):
                log("Plugin de telemetria cargado. Esperando datos del juego...")
        if self.announce and not self._hinted:
            hint = r.error_hint()
            if hint:
                self._hinted = True
                log(hint)

    def read(self):
        if self.reader is not None:
            data = self.reader.read()
            self._announce()
            return data
        for u in ([self.url] if self.url else TELEMETRY_URLS):
            st, data = http("GET", u, None, {"Cache-Control": "no-cache"}, timeout=2)
            if st == 200 and isinstance(data, dict):
                if self.url != u:
                    self.url = u
                    log("Servidor de telemetria encontrado en %s" % u)
                return data
        if self.url:
            self.url = None
        return None


def job_info(t):
    j = t.get("job") or {}
    return {
        "ciudad_origen": j.get("sourceCity") or "",
        "empresa_origen": j.get("sourceCompany") or "",
        "ciudad_destino": j.get("destinationCity") or "",
        "empresa_destino": j.get("destinationCompany") or "",
        "carga": j.get("cargo") or "",
        "peso_t": round(num(j.get("cargoMass")) / 1000.0, 2),
        "km": num(j.get("plannedDistanceKm")),
        "pago": num(j.get("income")),
    }


def job_fingerprint(t):
    j = t.get("job") or {}
    keys = ("sourceCityId", "sourceCompanyId", "destinationCityId", "destinationCompanyId", "cargoId", "deadlineTime", "income")
    return "B:" + "|".join(str(j.get(k)) for k in keys)


def has_job(t):
    j = t.get("job") or {}
    return bool((t.get("gameplay") or {}).get("onJob")) and num(j.get("income")) > 0 and bool(j.get("sourceCityId") or j.get("sourceCity"))



# ---------------------------------------------------------------- datos extra para el dashboard (daño y hora de envio)
# Si las columnas nuevas todavia no existen en Supabase, el puente sigue enviando lo de siempre.
EXTRAS = {"ok": True}
EXTRAS_SQL = ("alter table public.positions add column if not exists danio jsonb, "
              "add column if not exists enviado timestamptz, "
              "add column if not exists datos jsonb;")


def _pct(v):
    if v is None:
        return None
    x = num(v)
    if x > 1.0:          # algunos servidores ya lo dan en porcentaje (0-100)
        x /= 100.0
    return round(max(0.0, min(1.0, x)) * 100.0, 1)


def _first(*vals):
    for v in vals:
        if v is not None:
            return v
    return None


def _num_or_none(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _min_val(v, unidad="min"):
    """Tiempo del servidor a minutos. Acepta texto ISO (0001-01-01T09:00:00Z) o numeros (unidad 'min' o 's')."""
    import re as _re
    if v is None:
        return None
    if isinstance(v, str):
        m = _re.match(r"\d{4}-\d{2}-(\d{2})T(\d{2}):(\d{2})", v)
        return ((int(m.group(1)) - 1) * 24 + int(m.group(2))) * 60 + int(m.group(3)) if m else None
    n = _num_or_none(v)
    if n is None:
        return None
    return n / 60.0 if unidad == "s" else n


def _hallar_pal(plano, palabras):
    """Primer campo numerico cuyo nombre contiene todas las palabras (por si tu servidor usa otros nombres)."""
    for k, v in plano.items():
        n = k.lower().replace("_", "").replace(".", "")
        if all(w in n for w in palabras):
            return v
    return None


def _dash_extras(t, truck, trailer, nav, job):
    """Datos opcionales para el dashboard: viaje, plazo, temperaturas, presiones. Lo que no exista simplemente no se envia."""
    game = t.get("game") or {}
    pt = _plano(truck)
    ex = {}

    def put(k, v, nd=1):
        if v is None:
            return
        if isinstance(v, str):
            if v.strip():
                ex[k] = v.strip()
            return
        n = _num_or_none(v)
        if n is not None:
            ex[k] = round(n, nd)

    put("temp_aceite", _first(truck.get("oilTemperature"), _hallar_pal(pt, ("oil", "temp"))))
    put("temp_agua", _first(truck.get("waterTemperature"), _hallar_pal(pt, ("water", "temp"))))
    put("temp_frenos", _first(truck.get("brakeTemperature"), _hallar_pal(pt, ("brake", "temp"))))
    put("aire_psi", _first(truck.get("airPressure"), _hallar_pal(pt, ("air", "press"))))
    put("aceite_psi", _first(truck.get("oilPressure"), _hallar_pal(pt, ("oil", "press"))))
    put("bateria_v", _first(truck.get("batteryVoltage"), _hallar_pal(pt, ("batt",))))
    put("adblue_l", truck.get("adblue"))
    put("adblue_cap", truck.get("adblueCapacity"))
    put("odometro_km", truck.get("odometer"), 0)
    put("limite_kmh", _first(nav.get("speedLimit"), truck.get("speedLimit")), 0)
    if truck.get("cruiseControlOn"):
        put("crucero_kmh", truck.get("cruiseControlSpeed"), 0)
    put("eta_min", _min_val(nav.get("estimatedTime"), "s"), 0)
    put("descanso_min", _min_val(_first(game.get("nextRestStopTime"), game.get("nextRestStop"), game.get("restStop")), "min"), 0)
    put("plazo_min", _min_val(job.get("remainingTime"), "min"), 0)
    put("origen", job.get("sourceCity"))
    put("carga", job.get("cargo"))
    put("pago", job.get("income"), 0)
    put("km_total", job.get("plannedDistanceKm"), 0)
    put("camion", " ".join(str(x) for x in (truck.get("make"), truck.get("model")) if x))
    return ex


_DBG_CAMPOS = {"hecho": False}


def position_extras(t):
    truck = t.get("truck") or {}
    trailer = t.get("trailer") or {}
    nav = t.get("navigation") or {}
    job = t.get("job") or {}
    if DEBUG and not _DBG_CAMPOS["hecho"]:
        _DBG_CAMPOS["hecho"] = True
        dbg("campos del camion: %s" % json.dumps({k: truck.get(k) for k in (
            "fuel", "fuelCapacity", "fuelAverageConsumption", "fuelRange",
            "wearEngine", "wearTransmission", "wearCabin", "wearChassis", "wearWheels")}))
        dbg("campos del trailer: %s" % json.dumps({k: v for k, v in trailer.items() if not isinstance(v, (dict, list))})[:700])
        dbg("campos de navegacion: %s" % json.dumps(nav)[:300])
    attached = bool(trailer.get("attached", True))
    cargo = _first(trailer.get("cargoDamage"), trailer.get("cargo_damage"), job.get("cargoDamage"),
                   (t.get("cargo") or {}).get("damage"), trailer.get("damage"))
    danio = {
        "motor": _pct(truck.get("wearEngine")),
        "transmision": _pct(truck.get("wearTransmission")),
        "cabina": _pct(truck.get("wearCabin")),
        "chasis": _pct(truck.get("wearChassis")),
        "ruedas": _pct(truck.get("wearWheels")),
        "remolque": _pct(trailer.get("wear")) if attached else None,
        "carga": _pct(cargo) if attached else None,
    }
    pt = _plano(truck)
    for clave, parte in (("motor", "engine"), ("transmision", "transmission"), ("cabina", "cabin"),
                         ("chasis", "chassis"), ("ruedas", "wheel")):
        if danio[clave] is None:
            danio[clave] = _pct(_hallar(pt, parte))
    if attached:
        if danio["remolque"] is None:
            danio["remolque"] = _pct(_hallar(_plano(trailer), "", ("cargo",)))
        if danio["carga"] is None:
            danio["carga"] = _pct(_hallar({**_plano(trailer), **_plano(job), **_plano(t.get("cargo"))}, "cargo"))
    consumo = _num_or_none(truck.get("fuelAverageConsumption"))
    if consumo is not None and consumo < 3:      # el juego lo da en litros por km
        consumo *= 100.0
    dist = _num_or_none(nav.get("estimatedDistance"))   # metros hasta el destino
    datos = {
        "combustible_l": _num_or_none(truck.get("fuel")),
        "capacidad_l": _num_or_none(truck.get("fuelCapacity")),
        "consumo_100km": consumo,
        "autonomia_km": _num_or_none(truck.get("fuelRange")),
        "restante_km": None if dist is None else round(dist / 1000.0, 1),
    }
    res = {
        "danio": {k: v for k, v in danio.items() if v is not None},
        "datos": {k: (round(v, 2) if v is not None else None) for k, v in datos.items() if v is not None},
        "enviado": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        res["datos"].update(_dash_extras(t, truck, trailer, nav, job))
    except Exception as exc:  # los datos extra nunca deben impedir el envio de la posicion
        dbg("datos extra no disponibles: %r" % (exc,))
    return res


def _plano(d, pref="", out=None, depth=0):
    out = {} if out is None else out
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, dict) and depth < 2:
                _plano(v, pref + str(k) + ".", out, depth + 1)
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                out[pref + str(k)] = v
    return out


def _hallar(plano, parte, excluir=()):
    """Busca un campo numerico de desgaste/daño cuyo nombre contenga la parte (por si tu servidor usa otros nombres)."""
    for k, v in plano.items():
        n = k.lower().replace("_", "").replace(".", "")
        if parte in n and ("wear" in n or "damage" in n) and not any(x in n for x in excluir):
            return v
    return None


_DUMP = {"base": False, "trailer": False}


def maybe_dump(t):
    """Guarda en campos_telemetria.txt (junto a puente.py) lo que entrega tu servidor, para ajustar el dashboard."""
    ra = bool((t.get("trailer") or {}).get("attached"))
    if _DUMP["base"] and (_DUMP["trailer"] or not ra):
        return
    _DUMP["base"] = True
    if ra:
        _DUMP["trailer"] = True
    data = {"_secciones": sorted(t.keys()), "_remolque_enganchado": ra}
    for sec in ("truck", "trailer", "navigation", "job"):
        data[sec] = _plano(t.get(sec))
    for sec in t:
        if "damage" in str(sec).lower() or "wear" in str(sec).lower():
            data[sec] = _plano(t.get(sec))
    for folder in (os.path.dirname(os.path.abspath(__file__)), STATE_DIR):
        try:
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, "campos_telemetria.txt")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1, ensure_ascii=False)
            log("Guarde los campos que entrega tu servidor en: %s" % path)
            return
        except OSError:
            continue


def learn_city(queue, cur, t):
    """Al entregar, el camion esta en la ciudad de destino: se guarda su posicion para que la app
    aprenda donde esta cada ciudad (sirve para cualquier DLC)."""
    try:
        nombre = ((cur or {}).get("ciudad_destino") or "").strip()
        pl = ((t or {}).get("truck") or {}).get("placement") or {}
        if nombre and "x" in pl and "z" in pl:
            queue.add("registrar_ciudad", {"nombre": nombre, "x": num(pl.get("x")), "z": num(pl.get("z"))})
            dbg("ciudad registrada: %s (%s, %s)" % (nombre, pl.get("x"), pl.get("z")))
    except Exception as exc:
        dbg("no se pudo registrar la ciudad: %r" % (exc,))


# ---------------------------------------------------------------- estado para el launcher (archivo pequeno en STATE_DIR)
STATUS_FILE = os.path.join(STATE_DIR, "estado_puente.json")
STATUS = {"pid": os.getpid(), "juego": False, "ultimo_envio": 0.0, "usuario": "", "ts": 0.0}
_STATUS_T = [0.0]


def write_status(force=False):
    now = time.time()
    if not force and now - _STATUS_T[0] < 2:
        return
    _STATUS_T[0] = now
    STATUS["ts"] = now
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = STATUS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(STATUS, f)
        os.replace(tmp, STATUS_FILE)
    except OSError:
        pass


HOY_FILE = os.path.join(STATE_DIR, "hoy.json")


def count_today(ingreso):
    """Suma una entrega al contador del dia (lo muestra la ventana). Nunca debe impedir el envio."""
    try:
        hoy = time.strftime("%Y-%m-%d")
        try:
            with open(HOY_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        if d.get("fecha") != hoy:
            d = {"fecha": hoy, "entregas": 0, "ingresos": 0.0}
        d["entregas"] = int(d.get("entregas") or 0) + 1
        d["ingresos"] = round(float(d.get("ingresos") or 0) + float(ingreso or 0), 2)
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = HOY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, HOY_FILE)
    except Exception:
        pass


# ---------------------------------------------------------------- arranque automatico (servidor y juego)
CONFIG_PATH = os.path.join(STATE_DIR, "config.json")
GAMES = {"ets2": ("Euro Truck Simulator 2", "227300", "eurotrucks2.exe"),
         "ats": ("American Truck Simulator", "270880", "amtrucks.exe")}


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def find_server_candidates():
    """Busca en las carpetas de programas un .exe del servidor de telemetria."""
    roots = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "APPDATA")]
    found = []
    for root in filter(None, roots):
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip("\\/").count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath.count(os.sep) - base_depth >= 3:
                dirnames[:] = []
            low = dirpath.lower()
            if "telemetry" in low or "trucksim" in low:
                for fn in filenames:
                    f = fn.lower()
                    if f.endswith(".exe") and "unins" not in f and "update" not in f:
                        found.append(os.path.join(dirpath, fn))
    return found[:6]


def ask_server_path():
    cands = find_server_candidates()
    if cands:
        print("Encontre estos posibles servidores de telemetria:")
        for i, c in enumerate(cands, 1):
            print("  %d) %s" % (i, c))
        ans = input("Elige el numero (Enter = 1, 0 = buscar yo mismo): ").strip()
        if ans != "0":
            try:
                return cands[int(ans or "1") - 1]
            except (ValueError, IndexError):
                pass
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(title="Elige el programa del servidor de telemetria (.exe)",
                                          filetypes=[("Programa", "*.exe"), ("Todos", "*.*")])
        root.destroy()
        if path:
            return path
    except Exception:
        pass
    return input("Escribe la ruta completa del .exe del servidor (Enter = no abrirlo solo): ").strip().strip('"')


def game_running(key):
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % GAMES[key][2]], capture_output=True, text=True, timeout=10).stdout
        return GAMES[key][2].lower() in out.lower()
    except Exception:
        return False


def ensure_server(tel, cfg):
    """Si el servidor de telemetria no esta abierto, lo abre. Devuelve el proceso si lo abrimos nosotros."""
    if SOURCE != "http" or os.environ.get("RP_NO_LAUNCH") or tel.read() is not None:
        return None
    cmd = cfg.get("servidor")
    if cmd is None:
        cmd = ask_server_path() or ""
        cfg["servidor"] = cmd
        save_config(cfg)
    if not cmd:
        log("No hay un servidor configurado: abrelo tu a mano antes de jugar (o ejecuta con --config).")
        return None
    args = cmd if isinstance(cmd, list) else [cmd]
    log("Abriendo el servidor de telemetria...")
    try:
        proc = subprocess.Popen(args, cwd=os.path.dirname(args[0]) if os.path.isabs(args[0]) else None)
    except OSError as e:
        log("No pude abrir el servidor (%s). Revisa la ruta con --config." % e)
        return None
    for _ in range(40):
        time.sleep(1)
        if tel.read() is not None:
            return proc
        if proc.poll() is not None and tel.read() is None:
            break
    log("El servidor se abrio, pero todavia no responde. Sigo esperando mientras abres el juego.")
    return proc


def ensure_game(cfg):
    if os.environ.get("RP_NO_LAUNCH"):
        return
    key = cfg.get("juego")
    if key is None:
        print("Quieres que el puente tambien abra el juego?")
        print("  1) Euro Truck Simulator 2\n  2) American Truck Simulator\n  3) No, lo abro yo")
        key = {"1": "ets2", "2": "ats"}.get(input("Elige (Enter = 3): ").strip(), "no")
        cfg["juego"] = key
        save_config(cfg)
    if key in GAMES and not game_running(key):
        log("Abriendo %s en Steam..." % GAMES[key][0])
        uri = "steam://rungameid/%s" % GAMES[key][1]
        try:
            if os.name == "nt":
                os.startfile(uri)  # noqa
            else:
                import webbrowser
                webbrowser.open(uri)
        except Exception as e:
            log("No pude abrir el juego (%s)." % e)


# ---------------------------------------------------------------- bucle principal
def main():
    if "--config" in sys.argv:
        try:
            os.remove(CONFIG_PATH)
        except OSError:
            pass
    cfg = load_config()
    tel = Telemetry()
    server_proc = ensure_server(tel, cfg)
    ensure_game(cfg)
    sess = Session()
    do_login(sess)
    sess.load_profile()
    log("Sesion iniciada como %s. Deja esta ventana abierta mientras juegas." % (sess.nombre or sess.email))
    STATUS["usuario"] = sess.nombre or sess.email
    write_status(True)
    queue = Queue(sess)

    saved = load_job()
    cur = saved.get("cur")            # datos del trabajo en curso (se retoma si reinicias el puente)
    acc = saved.get("acc") or {"peajes": 0.0, "multa": 0.0, "motivo": ""}
    start_details = saved.get("start") or {}
    if cur:
        log("Retomo el trabajo que estaba en curso: %s -> %s" % (cur.get("ciudad_origen"), cur.get("ciudad_destino")))
    prev = {}
    primed = False        # la primera lectura solo "toma la foto": no cuenta como eventos
    limbo_since = None
    warned = False
    last_pos = 0.0
    waiting_msg = 0.0
    was_connected = False

    def rise(gp, name):
        v = bool(gp.get(name))
        r = v and not prev.get(name, False)
        prev[name] = v
        return r

    while True:
        try:
            t = tel.read()
            now = time.time()
            if t is None or not (t.get("game") or {}).get("connected"):
                primed = False
                STATUS["juego"] = False
                write_status()
                if was_connected:
                    log("El juego se desconecto. Esperando... (el trabajo en curso se conserva)")
                    was_connected = False
                elif now - waiting_msg > 30:
                    log("Esperando al servidor de telemetria y al juego..." if SOURCE == "http"
                        else "Esperando al juego (plugin de telemetria)...")
                    waiting_msg = now
                queue.flush()
                time.sleep(max(POLL, 1.0))
                continue
            if not was_connected:
                log("Juego conectado (%s)." % (t["game"].get("gameName") or "ETS2"))
                was_connected = True

            STATUS["juego"] = True
            write_status()
            gp = t.get("gameplay") or {}
            e_toll, e_ferry, e_train = rise(gp, "tollgate"), rise(gp, "ferry"), rise(gp, "train")
            e_fine, e_deliv, e_cancel = rise(gp, "fined"), rise(gp, "jobDelivered"), rise(gp, "jobCancelled")
            if not primed:
                # avisos que ya estaban encendidos al abrir el puente (o al volver el juego) no son eventos nuevos
                e_toll = e_ferry = e_train = e_fine = e_deliv = e_cancel = False
                primed = True

            if cur is not None:
                if e_toll:
                    v = num((gp.get("tollgateDetails") or {}).get("payAmount"))
                    acc["peajes"] += v
                    log("Peaje: +%s" % v)
                    save_job(cur, acc, start_details)
                if e_ferry:
                    v = num((gp.get("ferryDetails") or {}).get("payAmount"))
                    acc["peajes"] += v
                    log("Ferry: +%s" % v)
                if e_train:
                    v = num((gp.get("trainDetails") or {}).get("payAmount"))
                    acc["peajes"] += v
                    log("Tren: +%s" % v)
                if e_fine:
                    fd = gp.get("finedDetails") or {}
                    v = num(fd.get("amount"))
                    acc["multa"] += v
                    if fd.get("offence") and fd.get("offence") != "NoValue":
                        acc["motivo"] = str(fd.get("offence"))
                    log("Multa: +%s (%s)" % (v, acc["motivo"] or "sin motivo"))
                    save_job(cur, acc, start_details)

            def finish(details, learn=False):
                nonlocal cur, limbo_since, start_details
                if learn:
                    learn_city(queue, cur, t)
                payload = dict(cur)
                payload.update({
                    "revenue": num(details.get("revenue")),
                    "distancia_km": num(details.get("distanceKm")),
                    "carga_danio": num(details.get("cargoDamage")),
                    "tiempo_entrega": num(details.get("deliveryTime")),
                    "xp": num(details.get("earnedXp")),
                    "peajes": round(acc["peajes"], 2),
                    "multa": round(acc["multa"], 2),
                    "multa_motivo": acc["motivo"],
                })
                queue.add("puente_trabajo_entregado", payload)
                count_today(payload["revenue"] or cur["pago"])
                log("Trabajo entregado: %s -> %s (pago %s, peajes %s, multas %s)" % (
                    cur["ciudad_origen"], cur["ciudad_destino"], payload["revenue"] or cur["pago"], payload["peajes"], payload["multa"]))
                cur, limbo_since, start_details = None, None, {}
                save_job(None, acc, start_details)

            def cancel(why):
                nonlocal cur, limbo_since, start_details
                queue.add("puente_trabajo_cancelado", {"jid": cur["jid"]})
                log("Trabajo cancelado (%s): %s -> %s" % (why, cur["ciudad_origen"], cur["ciudad_destino"]))
                cur, limbo_since, start_details = None, None, {}
                save_job(None, acc, start_details)

            if cur is not None and e_deliv:
                finish(gp.get("jobDeliveredDetails") or {}, learn=True)
            elif cur is not None and e_cancel:
                cancel("juego")
            else:
                if has_job(t):
                    jid = job_fingerprint(t)
                    limbo_since = None
                    warned = False
                    if cur is not None and cur["jid"] == jid and not start_details:
                        start_details = json.loads(json.dumps(gp.get("jobDeliveredDetails") or {}))
                        save_job(cur, acc, start_details)
                    if cur is None or cur["jid"] != jid:
                        if cur is not None:
                            cancel("reemplazado por otro trabajo")
                        cur = dict(job_info(t), jid=jid)
                        acc.update({"peajes": 0.0, "multa": 0.0, "motivo": ""})
                        start_details = json.loads(json.dumps(gp.get("jobDeliveredDetails") or {}))
                        save_job(cur, acc, start_details)
                        queue.add("puente_trabajo_iniciado", cur)
                        log("Trabajo detectado: %s -> %s | %s | %s t | pago %s" % (
                            cur["ciudad_origen"], cur["ciudad_destino"], cur["carga"], cur["peso_t"], cur["pago"]))
                elif cur is not None:
                    # El trabajo ya no aparece activo. NUNCA se cancela solo (puede ser una pantalla de
                    # carga, un reinicio del servidor o una pausa). Solo se da por entregado si el juego
                    # informa una entrega NUEVA (distinta de la que ya estaba al empezar este trabajo).
                    details = gp.get("jobDeliveredDetails") or {}
                    if num(details.get("revenue")) > 0 and details != start_details:
                        finish(details)
                    elif limbo_since is None:
                        limbo_since = now
                        dbg("el trabajo dejo de estar activo; lo dejo abierto")
                    elif now - limbo_since > 120 and not warned:
                        warned = True
                        log("El trabajo no esta activo en el juego desde hace un rato. Lo dejo abierto; si lo cancelaste, cancelalo tambien en el movil.")

            # posicion
            if now - last_pos >= POS_EVERY:
                last_pos = now
                pl = (t.get("truck") or {}).get("placement") or {}
                if sess.user_id and "x" in pl:
                    body = {
                        "user_id": sess.user_id,
                        "conductor": sess.nombre or sess.email,
                        "x": num(pl.get("x")),
                        "z": num(pl.get("z")),
                        "heading": round(num(pl.get("heading")) * 360.0, 2),
                        "speed_kmh": round(abs(num((t.get("truck") or {}).get("speed"))) * SPEED_FACTOR, 1),
                        "en_trabajo": cur is not None,
                        "destino": cur["ciudad_destino"] if cur else "",
                    }
                    base_body = body
                    maybe_dump(t)
                    if EXTRAS["ok"]:
                        body = dict(base_body)
                        body.update(position_extras(t))
                    st, data = sess.rest("POST", "/rest/v1/positions?on_conflict=user_id", body,
                                         {"Prefer": "resolution=merge-duplicates,return=minimal"})
                    if st in (400, 404) and EXTRAS["ok"] and ("column" in str(data).lower() or "PGRST204" in str(data)):
                        EXTRAS["ok"] = False
                        log("Falta activar el dashboard de dano en Supabase. Ejecuta esto una vez en el SQL Editor y reinicia el puente: " + EXTRAS_SQL)
                        st, data = sess.rest("POST", "/rest/v1/positions?on_conflict=user_id", base_body,
                                             {"Prefer": "resolution=merge-duplicates,return=minimal"})
                    if st not in (200, 201, 204):
                        dbg("posicion no enviada (%s): %s" % (st, data))
                    else:
                        STATUS["ultimo_envio"] = now
            queue.flush()
            time.sleep(POLL)
        except KeyboardInterrupt:
            log("Puente detenido.")
            if server_proc is not None and cfg.get("cerrar_servidor", True):
                try:
                    server_proc.terminate()
                    log("Servidor de telemetria cerrado.")
                except OSError:
                    pass
            return
        except Exception as exc:  # no dejar caer el puente por un error puntual
            log("Error inesperado: %r" % (exc,))
            time.sleep(2)


if __name__ == "__main__":
    main()
