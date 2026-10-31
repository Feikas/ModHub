@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Primero ejecuta instalar.bat
    pause
    exit /b 1
)
echo Ejecutando las pruebas automaticas de ModHub...
".venv\Scripts\python.exe" -m pytest -q
echo.
pause
