@echo off
chcp 65001 >nul
title ModHub - Instalacion
cd /d "%~dp0"
echo.
echo  ==============================================
echo     ModHub - instalacion de dependencias
echo  ==============================================
echo.

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :nopython
goto :havepython

:nopython
echo  [X] No se encuentra Python. Instala Python 3.12 desde https://www.python.org/downloads/
echo      y marca la casilla "Add python.exe to PATH" al instalar.
pause
exit /b 1

:havepython
if not exist ".venv\Scripts\python.exe" (
    echo  Creando entorno aislado en .venv ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo  [X] No se pudo crear el entorno de Python.
        pause
        exit /b 1
    )
)

echo  Instalando librerias (puede tardar unos minutos la primera vez) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt > instalar.log 2>&1
set "PIPERR=%errorlevel%"
type instalar.log
if not "%PIPERR%"=="0" cmd /c exit 1
if errorlevel 1 (
    echo.
    echo  [X] Fallo la instalacion de librerias. Revisa tu conexion e intentalo de nuevo.
    pause
    exit /b 1
)

where 7z >nul 2>nul
if errorlevel 1 if not exist "%ProgramFiles%\7-Zip\7z.exe" (
    echo.
    echo  [!] No se ha encontrado 7-Zip. Sin el, los mods en .rar no se podran abrir.
    echo      Descargalo gratis en https://www.7-zip.org/
)

echo.
echo  [OK] Todo listo. Ahora puedes abrir:
echo       - demo.bat      (prueba segura con juegos de mentira)
echo       - ejecutar.bat  (ModHub de verdad)
echo.
pause
