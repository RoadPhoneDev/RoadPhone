#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Road Phone - lector de telemetria
---------------------------------
Lee directamente la memoria compartida que deja el plugin "scs-telemetry" dentro del juego
(Euro Truck Simulator 2 / American Truck Simulator), sin servidor intermedio.

Plugin:  https://github.com/RenCloud/scs-sdk-plugin   (licencia MIT, revision 12)
Memoria: "Local\\SCSTelemetry"  (32 KB, formato definido en scs-telemetry-common.hpp)

La salida de Reader.read() tiene la misma forma que el JSON del servidor de telemetria que usaba
el puente (game, gameplay, job, truck, trailer, navigation), asi que puente.py casi no cambia.

Solo biblioteca estandar de Python. Funciona en Windows (en otros sistemas simplemente no hay datos).
"""
import ctypes
import os
import threading
import time
from ctypes import c_bool, c_char, c_double, c_float, c_int, c_int64, c_uint, c_uint64, Structure

MMF_NAME = "Local\\SCSTelemetry"
MMF_SIZE = 32 * 1024
REVISION = 12                 # revision del plugin que entiende este lector
FILE_MAP_READ = 0x0004
ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5

# Banderines que el plugin ALTERNA (true<->false) cada vez que ocurre el evento.
FLIPS = ("jobDelivered", "jobCancelled", "fined", "tollgate", "ferry", "train")
GAMES = {1: "ETS2", 2: "ATS"}


# ---------------------------------------------------------------- formato de la memoria (scs-telemetry-common.hpp, rev 12)
def _S(n):
    return c_char * n


def _F(n):
    return c_float * n


def _B(n):
    return c_bool * n


def _U(n):
    return c_uint * n


class _Trailer(Structure):  # 1560 bytes
    _fields_ = [
        ("wheelSteerable", _B(16)), ("wheelSimulated", _B(16)), ("wheelPowered", _B(16)), ("wheelLiftable", _B(16)),
        ("wheelOnGround", _B(16)), ("attached", c_bool), ("buffer_b", _S(3)),
        ("wheelSubstance", _U(16)), ("wheelCount", c_uint),
        ("cargoDamage", c_float), ("wearChassis", c_float), ("wearWheels", c_float), ("wearBody", c_float),
        ("wheelSuspDeflection", _F(16)), ("wheelVelocity", _F(16)), ("wheelSteering", _F(16)),
        ("wheelRotation", _F(16)), ("wheelLift", _F(16)), ("wheelLiftOffset", _F(16)),
        ("wheelRadius", _F(16)),
        ("linearVelocityX", c_float), ("linearVelocityY", c_float), ("linearVelocityZ", c_float),
        ("angularVelocityX", c_float), ("angularVelocityY", c_float), ("angularVelocityZ", c_float),
        ("linearAccelerationX", c_float), ("linearAccelerationY", c_float), ("linearAccelerationZ", c_float),
        ("angularAccelerationX", c_float), ("angularAccelerationY", c_float), ("angularAccelerationZ", c_float),
        ("hookPositionX", c_float), ("hookPositionY", c_float), ("hookPositionZ", c_float),
        ("wheelPositionX", _F(16)), ("wheelPositionY", _F(16)), ("wheelPositionZ", _F(16)),
        ("buffer_fv", _S(4)),
        ("worldX", c_double), ("worldY", c_double), ("worldZ", c_double),
        ("rotationX", c_double), ("rotationY", c_double), ("rotationZ", c_double),
        ("id", _S(64)), ("cargoAcessoryId", _S(64)), ("bodyType", _S(64)), ("brandId", _S(64)), ("brand", _S(64)),
        ("name", _S(64)), ("chainType", _S(64)), ("licensePlate", _S(64)), ("licensePlateCountry", _S(64)),
        ("licensePlateCountryId", _S(64)),
    ]


class _Scs(Structure):
    _fields_ = [("telemetry_plugin_revision", c_uint), ("version_major", c_uint), ("version_minor", c_uint),
                ("game", c_uint), ("telemetry_version_game_major", c_uint), ("telemetry_version_game_minor", c_uint)]


class _CommonUi(Structure):
    _fields_ = [("time_abs", c_uint)]


class _ConfigUi(Structure):
    _fields_ = [("gears", c_uint), ("gears_reverse", c_uint), ("retarderStepCount", c_uint),
                ("truckWheelCount", c_uint), ("selectorCount", c_uint), ("time_abs_delivery", c_uint),
                ("maxTrailerCount", c_uint), ("unitCount", c_uint), ("plannedDistanceKm", c_uint)]


class _TruckUi(Structure):
    _fields_ = [("shifterSlot", c_uint), ("retarderBrake", c_uint), ("lightsAuxFront", c_uint),
                ("lightsAuxRoof", c_uint), ("truck_wheelSubstance", _U(16)),
                ("hshifterPosition", _U(32)), ("hshifterBitmask", _U(32))]


class _GameplayUi(Structure):
    _fields_ = [("jobDeliveredDeliveryTime", c_uint), ("jobStartingTime", c_uint), ("jobFinishedTime", c_uint)]


class _CommonI(Structure):
    _fields_ = [("restStop", c_int)]


class _TruckI(Structure):
    _fields_ = [("gear", c_int), ("gearDashboard", c_int), ("hshifterResulting", c_int * 32)]


class _GameplayI(Structure):
    _fields_ = [("jobDeliveredEarnedXp", c_int)]


class _CommonF(Structure):
    _fields_ = [("scale", c_float)]


class _ConfigF(Structure):
    _fields_ = [("fuelCapacity", c_float), ("fuelWarningFactor", c_float), ("adblueCapacity", c_float),
                ("adblueWarningFactor", c_float), ("airPressureWarning", c_float), ("airPressurEmergency", c_float),
                ("oilPressureWarning", c_float), ("waterTemperatureWarning", c_float),
                ("batteryVoltageWarning", c_float), ("engineRpmMax", c_float), ("gearDifferential", c_float),
                ("cargoMass", c_float), ("truckWheelRadius", _F(16)), ("gearRatiosForward", _F(24)),
                ("gearRatiosReverse", _F(8)), ("unitMass", c_float)]


class _TruckF(Structure):
    _fields_ = [(n, c_float) for n in (
        "speed", "engineRpm", "userSteer", "userThrottle", "userBrake", "userClutch", "gameSteer", "gameThrottle",
        "gameBrake", "gameClutch", "cruiseControlSpeed", "airPressure", "brakeTemperature", "fuel",
        "fuelAvgConsumption", "fuelRange", "adblue", "oilPressure", "oilTemperature", "waterTemperature",
        "batteryVoltage", "lightsDashboard", "wearEngine", "wearTransmission", "wearCabin", "wearChassis",
        "wearWheels", "truckOdometer", "routeDistance", "routeTime", "speedLimit")] + [
        ("truck_wheelSuspDeflection", _F(16)), ("truck_wheelVelocity", _F(16)), ("truck_wheelSteering", _F(16)),
        ("truck_wheelRotation", _F(16)), ("truck_wheelLift", _F(16)), ("truck_wheelLiftOffset", _F(16))]


class _GameplayF(Structure):
    _fields_ = [("jobDeliveredCargoDamage", c_float), ("jobDeliveredDistanceKm", c_float),
                ("refuelAmount", c_float)]


class _JobF(Structure):
    _fields_ = [("cargoDamage", c_float)]


class _ConfigB(Structure):
    _fields_ = [("truckWheelSteerable", _B(16)), ("truckWheelSimulated", _B(16)), ("truckWheelPowered", _B(16)),
                ("truckWheelLiftable", _B(16)), ("isCargoLoaded", c_bool), ("specialJob", c_bool)]


class _TruckB(Structure):
    _fields_ = [(n, c_bool) for n in (
        "parkBrake", "motorBrake", "airPressureWarning", "airPressureEmergency", "fuelWarning", "adblueWarning",
        "oilPressureWarning", "waterTemperatureWarning", "batteryVoltageWarning", "electricEnabled",
        "engineEnabled", "wipers", "blinkerLeftActive", "blinkerRightActive", "blinkerLeftOn", "blinkerRightOn",
        "lightsParking", "lightsBeamLow", "lightsBeamHigh", "lightsBeacon", "lightsBrake", "lightsReverse",
        "lightsHazard", "cruiseControl")] + [
        ("truck_wheelOnGround", _B(16)), ("shifterToggle", _B(2)), ("differentialLock", c_bool),
        ("liftAxle", c_bool), ("liftAxleIndicator", c_bool), ("trailerLiftAxle", c_bool),
        ("trailerLiftAxleIndicator", c_bool)]


class _GameplayB(Structure):
    _fields_ = [("jobDeliveredAutoparkUsed", c_bool), ("jobDeliveredAutoloadUsed", c_bool)]


class _ConfigFv(Structure):
    _fields_ = [("cabinPositionX", c_float), ("cabinPositionY", c_float), ("cabinPositionZ", c_float),
                ("headPositionX", c_float), ("headPositionY", c_float), ("headPositionZ", c_float),
                ("truckHookPositionX", c_float), ("truckHookPositionY", c_float), ("truckHookPositionZ", c_float),
                ("truckWheelPositionX", _F(16)), ("truckWheelPositionY", _F(16)), ("truckWheelPositionZ", _F(16))]


class _TruckFv(Structure):
    _fields_ = [(n, c_float) for n in (
        "lv_accelerationX", "lv_accelerationY", "lv_accelerationZ", "av_accelerationX", "av_accelerationY",
        "av_accelerationZ", "accelerationX", "accelerationY", "accelerationZ", "aa_accelerationX",
        "aa_accelerationY", "aa_accelerationZ", "cabinAVX", "cabinAVY", "cabinAVZ", "cabinAAX", "cabinAAY",
        "cabinAAZ")]


class _TruckFp(Structure):
    _fields_ = [(n, c_float) for n in (
        "cabinOffsetX", "cabinOffsetY", "cabinOffsetZ", "cabinOffsetrotationX", "cabinOffsetrotationY",
        "cabinOffsetrotationZ", "headOffsetX", "headOffsetY", "headOffsetZ", "headOffsetrotationX",
        "headOffsetrotationY", "headOffsetrotationZ")]


class _TruckDp(Structure):
    _fields_ = [("coordinateX", c_double), ("coordinateY", c_double), ("coordinateZ", c_double),
                ("rotationX", c_double), ("rotationY", c_double), ("rotationZ", c_double)]


class _ConfigS(Structure):
    _fields_ = [(n, _S(64)) for n in (
        "truckBrandId", "truckBrand", "truckId", "truckName", "cargoId", "cargo", "cityDstId", "cityDst",
        "compDstId", "compDst", "citySrcId", "citySrc", "compSrcId", "compSrc")] + [
        ("shifterType", _S(16)), ("truckLicensePlate", _S(64)), ("truckLicensePlateCountryId", _S(64)),
        ("truckLicensePlateCountry", _S(64)), ("jobMarket", _S(32))]


class _GameplayS(Structure):
    _fields_ = [("fineOffence", _S(32))] + [(n, _S(64)) for n in (
        "ferrySourceName", "ferryTargetName", "ferrySourceId", "ferryTargetId", "trainSourceName",
        "trainTargetName", "trainSourceId", "trainTargetId")]


class _ConfigUll(Structure):
    _fields_ = [("jobIncome", c_uint64)]


class _GameplayLl(Structure):
    _fields_ = [("jobCancelledPenalty", c_int64), ("jobDeliveredRevenue", c_int64), ("fineAmount", c_int64),
                ("tollgatePayAmount", c_int64), ("ferryPayAmount", c_int64), ("trainPayAmount", c_int64)]


class _SpecialB(Structure):
    _fields_ = [(n, c_bool) for n in ("onJob", "jobFinished", "jobCancelled", "jobDelivered", "fined", "tollgate",
                                      "ferry", "train", "refuel", "refuelPayed")]


class _Substances(Structure):
    _fields_ = [("substance", (c_char * 64) * 25)]


class _Trailers(Structure):
    _fields_ = [("trailer", _Trailer * 10)]


class TelemetryMap(Structure):
    """Misma disposicion que scsTelemetryMap_t del plugin (revision 12). Cada zona empieza en un offset fijo."""
    _fields_ = [
        # zona 1 (0)
        ("sdkActive", c_bool), ("placeHolder", _S(3)), ("paused", c_bool), ("placeHolder2", _S(3)),
        ("time", c_uint64), ("simulatedTime", c_uint64), ("renderTime", c_uint64), ("multiplayerTimeOffset", c_int64),
        # zona 2 (40)
        ("scs_values", _Scs), ("common_ui", _CommonUi), ("config_ui", _ConfigUi), ("truck_ui", _TruckUi),
        ("gameplay_ui", _GameplayUi), ("buffer_ui", _S(48)),
        # zona 3 (500)
        ("common_i", _CommonI), ("truck_i", _TruckI), ("gameplay_i", _GameplayI), ("buffer_i", _S(56)),
        # zona 4 (700)
        ("common_f", _CommonF), ("config_f", _ConfigF), ("truck_f", _TruckF), ("gameplay_f", _GameplayF),
        ("job_f", _JobF), ("buffer_f", _S(28)),
        # zona 5 (1500)
        ("config_b", _ConfigB), ("truck_b", _TruckB), ("gameplay_b", _GameplayB), ("buffer_b", _S(25)),
        # zona 6 (1640)
        ("config_fv", _ConfigFv), ("truck_fv", _TruckFv), ("buffer_fv", _S(60)),
        # zona 7 (2000)
        ("truck_fp", _TruckFp), ("buffer_fp", _S(152)),
        # zona 8 (2200)
        ("truck_dp", _TruckDp), ("buffer_dp", _S(52)),
        # zona 9 (2300)
        ("config_s", _ConfigS), ("gameplay_s", _GameplayS), ("buffer_s", _S(20)),
        # zona 10 (4000)
        ("config_ull", _ConfigUll), ("buffer_ull", _S(192)),
        # zona 11 (4200)
        ("gameplay_ll", _GameplayLl), ("buffer_ll", _S(52)),
        # zona 12 (4300)
        ("special_b", _SpecialB), ("buffer_special", _S(90)),
        # zona 13 (4400)
        ("substances", _Substances),
        # zona 14 (6000)
        ("trailer", _Trailers),
    ]


# Posiciones que documenta el plugin: si alguna no coincide, el formato cambio y hay que revisar este archivo.
ZONE_OFFSETS = {"scs_values": 40, "common_i": 500, "common_f": 700, "config_b": 1500, "config_fv": 1640,
                "truck_fp": 2000, "truck_dp": 2200, "config_s": 2300, "config_ull": 4000, "gameplay_ll": 4200,
                "special_b": 4300, "substances": 4400, "trailer": 6000}
MAP_SIZE = ctypes.sizeof(TelemetryMap)


def layout_ok():
    return all(getattr(TelemetryMap, k).offset == v for k, v in ZONE_OFFSETS.items()) and MAP_SIZE <= MMF_SIZE


# ---------------------------------------------------------------- conversion a la forma que usa el puente
def _s(b):
    return b.decode("utf-8", "replace") if b else ""


def _details(m, name):
    if name == "jobDelivered":
        return {"revenue": m.gameplay_ll.jobDeliveredRevenue, "distanceKm": m.gameplay_f.jobDeliveredDistanceKm,
                "cargoDamage": m.gameplay_f.jobDeliveredCargoDamage,
                "deliveryTime": m.gameplay_ui.jobDeliveredDeliveryTime, "earnedXp": m.gameplay_i.jobDeliveredEarnedXp,
                "autoParked": bool(m.gameplay_b.jobDeliveredAutoparkUsed),
                "autoLoaded": bool(m.gameplay_b.jobDeliveredAutoloadUsed)}
    if name == "fined":
        return {"amount": m.gameplay_ll.fineAmount, "offence": _s(m.gameplay_s.fineOffence)}
    if name == "tollgate":
        return {"payAmount": m.gameplay_ll.tollgatePayAmount}
    if name == "ferry":
        return {"payAmount": m.gameplay_ll.ferryPayAmount}
    if name == "train":
        return {"payAmount": m.gameplay_ll.trainPayAmount}
    if name == "jobCancelled":
        return {"penalty": m.gameplay_ll.jobCancelledPenalty}
    return {}


def to_json(m, connected, pulses=None, pdet=None):
    """Convierte la memoria del plugin al formato (game, gameplay, job, truck, trailer, navigation) del puente."""
    pulses, pdet = pulses or {}, pdet or {}
    tf, td = m.truck_f, m.truck_dp
    tr = m.trailer.trailer[0]
    gp = {"onJob": bool(m.special_b.onJob)}
    for n in FLIPS:
        gp[n] = bool(pulses.get(n))
    for n, key in (("jobDelivered", "jobDeliveredDetails"), ("fined", "finedDetails"), ("tollgate", "tollgateDetails"),
                   ("ferry", "ferryDetails"), ("train", "trainDetails"), ("jobCancelled", "jobCancelledDetails")):
        gp[key] = pdet[n] if pulses.get(n) and n in pdet else _details(m, n)
    cs = m.config_s
    return {
        "game": {
            "connected": bool(connected), "paused": bool(m.paused),
            "gameName": GAMES.get(m.scs_values.game, ""),
            "version": "%d.%d" % (m.scs_values.version_major, m.scs_values.version_minor),
            "telemetryPluginVersion": m.scs_values.telemetry_plugin_revision,
            "time": m.common_ui.time_abs,                  # hora del juego (minutos absolutos)
            "nextRestStopTime": m.common_i.restStop,       # minutos hasta el proximo descanso (puede ser negativo)
        },
        "gameplay": gp,
        "job": {
            "sourceCity": _s(cs.citySrc), "sourceCityId": _s(cs.citySrcId),
            "sourceCompany": _s(cs.compSrc), "sourceCompanyId": _s(cs.compSrcId),
            "destinationCity": _s(cs.cityDst), "destinationCityId": _s(cs.cityDstId),
            "destinationCompany": _s(cs.compDst), "destinationCompanyId": _s(cs.compDstId),
            "cargo": _s(cs.cargo), "cargoId": _s(cs.cargoId), "cargoMass": m.config_f.cargoMass,
            "plannedDistanceKm": m.config_ui.plannedDistanceKm, "income": m.config_ull.jobIncome,
            "deadlineTime": m.config_ui.time_abs_delivery, "cargoDamage": m.job_f.cargoDamage,
            "remainingTime": (m.config_ui.time_abs_delivery - m.common_ui.time_abs)
            if (m.special_b.onJob and m.config_ui.time_abs_delivery > 0) else None,   # minutos de juego
        },
        "truck": {
            "make": _s(cs.truckBrand), "model": _s(cs.truckName), "id": _s(cs.truckId),
            "speed": tf.speed * 3.6,                       # el plugin da m/s; el puente espera km/h
            "fuel": tf.fuel, "fuelCapacity": m.config_f.fuelCapacity,
            "fuelAverageConsumption": tf.fuelAvgConsumption, "fuelRange": tf.fuelRange,
            "wearEngine": tf.wearEngine, "wearTransmission": tf.wearTransmission, "wearCabin": tf.wearCabin,
            "wearChassis": tf.wearChassis, "wearWheels": tf.wearWheels, "odometer": tf.truckOdometer,
            "oilTemperature": tf.oilTemperature, "waterTemperature": tf.waterTemperature,
            "brakeTemperature": tf.brakeTemperature, "airPressure": tf.airPressure, "oilPressure": tf.oilPressure,
            "batteryVoltage": tf.batteryVoltage, "adblue": tf.adblue, "adblueCapacity": m.config_f.adblueCapacity,
            "cruiseControlOn": bool(m.truck_b.cruiseControl), "cruiseControlSpeed": tf.cruiseControlSpeed * 3.6,
            "placement": {"x": td.coordinateX, "y": td.coordinateY, "z": td.coordinateZ,
                          "heading": td.rotationX, "pitch": td.rotationY, "roll": td.rotationZ},
        },
        "trailer": {
            "attached": bool(tr.attached), "cargoDamage": tr.cargoDamage,
            "wear": max(tr.wearBody, tr.wearChassis, tr.wearWheels),   # el peor de los tres
            "wearBody": tr.wearBody, "wearChassis": tr.wearChassis, "wearWheels": tr.wearWheels,
            "name": _s(tr.name),
        },
        "navigation": {"estimatedDistance": tf.routeDistance, "estimatedTime": tf.routeTime,
                       "speedLimit": tf.speedLimit * 3.6},     # el plugin da m/s
    }


# ---------------------------------------------------------------- lector
class Reader:
    """Abre la memoria del plugin y la vigila en un hilo (20 veces por segundo).

    Los eventos (entrega, multa, peaje...) el plugin los marca ALTERNANDO un valor. Este lector los
    convierte en un pulso (True en una sola lectura), que es lo que espera el puente.
    status: "sin_memoria" (plugin no cargado / juego cerrado), "inactivo" (plugin presente pero el juego
    no manda datos), "revision" (otra version del plugin) o "ok".
    """

    def __init__(self, events=True, hz=20):
        self.events = events
        self.interval = 1.0 / hz if events else 0.5
        self.status = "sin_memoria"
        self.rev = 0
        self.last_error = 0
        self._lock = threading.Lock()
        self._thread = None
        self._view = None
        self._k32 = None
        self._last_open_try = 0.0
        self._snap = None
        self._connected = False
        self._last_ts = None
        self._ts_change = 0.0
        self._prev = None
        self._cand = {}
        self._pending = {}
        self._pdet = {}

    # -- acceso a la memoria
    def _fetch(self):
        """Devuelve los bytes de la memoria, o None si el plugin no esta cargado."""
        if os.name != "nt":
            return None
        if self._view is None:
            now = time.time()
            if now - self._last_open_try < 1.0:
                return None
            self._last_open_try = now
            if self._k32 is None:
                from ctypes import wintypes
                k = ctypes.WinDLL("kernel32", use_last_error=True)
                k.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
                k.OpenFileMappingW.restype = wintypes.HANDLE
                k.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                            ctypes.c_size_t]
                k.MapViewOfFile.restype = ctypes.c_void_p
                self._k32 = k
            h = self._k32.OpenFileMappingW(FILE_MAP_READ, False, MMF_NAME)
            if not h:
                self.last_error = ctypes.get_last_error()
                return None
            view = self._k32.MapViewOfFile(h, FILE_MAP_READ, 0, 0, 0)
            if not view:
                self.last_error = ctypes.get_last_error()
                self._k32.CloseHandle(h)
                return None
            self.last_error = 0
            self._view = view
        try:
            return ctypes.string_at(self._view, MAP_SIZE)
        except OSError:
            self._view = None
            return None

    # -- hilo
    def _ensure_thread(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def _loop(self):
        while True:
            t0 = time.time()
            try:
                self._tick(t0)
            except Exception:
                pass
            time.sleep(max(0.005, self.interval - (time.time() - t0)))

    def _reset_events(self):
        self._prev = None
        self._cand.clear()
        self._pending.clear()

    def _tick(self, now):
        raw = self._fetch()
        with self._lock:
            if raw is None:
                self.status = "sin_memoria"
                self._connected = False
                self._snap = None
                self._reset_events()
                return
            m = TelemetryMap.from_buffer_copy(raw)
            self.rev = m.scs_values.telemetry_plugin_revision
            if m.time != self._last_ts:
                self._last_ts, self._ts_change = m.time, now
            fresh = bool(m.paused) or (now - self._ts_change) < 3.0
            self._snap = m
            if self.rev != REVISION:
                self.status, self._connected = "revision", False
                self._reset_events()
                return
            self._connected = bool(m.sdkActive) and fresh
            self.status = "ok" if self._connected else "inactivo"
            if not self._connected:
                self._reset_events()          # al volver, la primera lectura solo toma la "foto" de los banderines
                return
            if not self.events:
                return
            flags = {n: bool(getattr(m.special_b, n)) for n in FLIPS}
            if self._prev is None:
                self._prev = flags
                return
            # los detalles (monto, motivo...) se escriben justo despues de alternar el banderin: se confirman en la vuelta siguiente
            for n, ts in list(self._cand.items()):
                if now - ts >= 0.04:
                    self._pending[n] = self._pending.get(n, 0) + 1
                    self._pdet[n] = _details(m, n)
                    del self._cand[n]
            for n in FLIPS:
                if flags[n] != self._prev[n]:
                    self._cand[n] = now
            self._prev = flags

    # -- uso
    def read(self):
        """Devuelve el diccionario de telemetria (o None si el plugin no esta cargado)."""
        self._ensure_thread()
        with self._lock:
            m = self._snap
            if m is None:
                return None
            pulses = {n: self._pending.get(n, 0) > 0 for n in FLIPS}
            pdet = dict(self._pdet)
            self._pending.clear()
            connected = self._connected
        return to_json(m, connected, pulses, pdet)

    def error_hint(self):
        if self.last_error == ERROR_ACCESS_DENIED:
            return "Windows no me deja leer la telemetria. Abre el juego SIN 'Ejecutar como administrador'."
        return ""


if __name__ == "__main__":      # prueba rapida:  python telemetria.py
    import json
    print("formato OK" if layout_ok() else "FORMATO DISTINTO: revisar telemetria.py", "| tamano", MAP_SIZE)
    r = Reader(events=False)
    for _ in range(6):
        d = r.read()
        print(r.status, "rev", r.rev, "|", json.dumps((d or {}).get("game")), r.error_hint())
        time.sleep(1)
