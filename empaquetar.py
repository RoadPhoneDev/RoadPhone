# -*- coding: utf-8 -*-
"""Road Phone - empaqueta dist\\RoadPhone en release\\RoadPhone-<version>.zip y calcula su hash (sha256).
La version se lee de APP_VERSION en launcher.py, asi nunca se desincroniza."""
import hashlib
import os
import re
import sys
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(AQUI, "dist", "RoadPhone")
SALIDA = os.path.join(AQUI, "release")


def leer_version():
    with open(os.path.join(AQUI, "launcher.py"), encoding="utf-8") as f:
        m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
    if not m:
        raise SystemExit("ERROR: no encontre APP_VERSION en launcher.py")
    return m.group(1)


def main():
    version = leer_version()
    if not os.path.isfile(os.path.join(DIST, "RoadPhone.exe")):
        print("ERROR: no existe dist\\RoadPhone\\RoadPhone.exe. Ejecuta primero construir_exe.bat")
        return 1
    os.makedirs(SALIDA, exist_ok=True)
    zip_path = os.path.join(SALIDA, "RoadPhone-%s.zip" % version)
    if os.path.exists(zip_path):
        os.remove(zip_path)
    n = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for raiz, _dirs, archivos in os.walk(DIST):
            for nombre in archivos:
                ruta = os.path.join(raiz, nombre)
                z.write(ruta, os.path.join("RoadPhone", os.path.relpath(ruta, DIST)))
                n += 1
    with open(zip_path, "rb") as f:
        h = hashlib.sha256(f.read()).hexdigest()
    with open(zip_path + ".sha256", "w") as f:
        f.write("%s  %s\n" % (h, os.path.basename(zip_path)))
    print("Version:  %s" % version)
    print("Archivo:  %s (%d archivos, %.1f MB)" % (zip_path, n, os.path.getsize(zip_path) / 1048576.0))
    print("sha256:   %s" % h)
    print("")
    print("Siguiente paso: en GitHub > Releases > Draft a new release")
    print("  Etiqueta: v%s   |   Adjunta el .zip de la carpeta release" % version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
