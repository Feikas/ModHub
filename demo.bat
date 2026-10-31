@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Primero ejecuta instalar.bat
    pause
    exit /b 1
)
echo Abriendo ModHub en modo DEMO: juegos y webs de mentira en una carpeta temporal.
echo No toca tus juegos reales. Cierra la ventana para terminar.
".venv\Scripts\python.exe" "tests\ui_demo.py"
