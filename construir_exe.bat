@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Road Phone - crear el .exe
echo === Road Phone: creando el .exe ===
echo.

rem --- 1) estan todos los archivos?
set FALTA=
for %%F in (launcher.py puente.py telemetria.py actualizador.py) do if not exist "%%F" set FALTA=!FALTA! %%F
if not "!FALTA!"=="" goto faltan

rem --- 2) Python
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY goto nopython
echo Usando: %PY%
%PY% --version
echo.

rem --- 3) herramientas
%PY% -m pip install --upgrade pyinstaller pillow
if errorlevel 1 goto fallo

rem --- 4) icono: convierte icono.png a .ico si hace falta
if not exist icono.png goto sinconv
if exist icono.ico goto sinconv
echo Convirtiendo icono.png a icono.ico...
%PY% -c "from PIL import Image; im=Image.open('icono.png').convert('RGBA'); s=max(im.size); c=Image.new('RGBA',(s,s),(0,0,0,0)); c.paste(im,((s-im.width)//2,(s-im.height)//2)); c.save('icono.ico',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
:sinconv

rem --- 5) opciones
set OPTS=--noconfirm --clean --windowed --name RoadPhone --hidden-import puente --hidden-import telemetria --hidden-import actualizador
if exist icono.ico set OPTS=!OPTS! --icon icono.ico --add-data "icono.ico;."
if exist fondo.png set OPTS=!OPTS! --add-data "fondo.png;."
if exist fondo.jpg set OPTS=!OPTS! --add-data "fondo.jpg;."
if exist plugin\scs-telemetry.dll set OPTS=!OPTS! --add-data "plugin\scs-telemetry.dll;."
if exist plugin\scs-telemetry.dll echo Incluyendo el plugin de plugin\scs-telemetry.dll
if not exist plugin\scs-telemetry.dll echo Aviso: no hay plugin\scs-telemetry.dll - se usara el que ya este instalado en el juego
echo.

rem --- 6) construir
%PY% -m PyInstaller !OPTS! launcher.py
if errorlevel 1 goto fallo
if not exist "dist\RoadPhone\RoadPhone.exe" goto fallo

echo.
echo Listo. Comparte la carpeta completa:  dist\RoadPhone
echo Dentro esta RoadPhone.exe (esa es la que se abre).
pause
exit /b 0

:faltan
echo.
echo FALTAN ARCHIVOS en esta carpeta:!FALTA!
echo Ponlos junto a construir_exe.bat y vuelve a ejecutarlo.
pause
exit /b 1

:nopython
echo.
echo No encontre Python. Instalalo desde python.org y marca "Add Python to PATH".
pause
exit /b 1

:fallo
echo.
echo Algo fallo. Copia el texto de arriba (o una captura) y envialo.
pause
exit /b 1
