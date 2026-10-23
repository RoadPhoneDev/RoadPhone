@echo off
cd /d "%~dp0"
title Road Phone - empaquetar version
echo === Road Phone: empaquetando la version para publicar ===
echo.
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% empaquetar.py
echo.
pause
