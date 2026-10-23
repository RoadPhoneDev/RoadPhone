@echo off
cd /d "%~dp0"
title Road Phone - probar el actualizador
echo === Probando la descarga del plugin desde GitHub ===
echo.
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% actualizador.py --guardar
echo.
echo Si dice "todo bien", se guardo scs-telemetry.dll en esta carpeta.
echo Si dice "fallo", copia el texto de arriba y envialo.
pause
