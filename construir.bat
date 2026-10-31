@echo off
chcp 65001 >nul
title ModHub - Construir portable e instalador
cd /d "%~dp0"
set "LOG=%~dp0construir.log"
echo === Construyendo ModHub (%date% %time%) === > "%LOG%"
echo.
echo  Construyendo ModHub.exe (tarda 2-5 minutos)...

if not exist ".venv\Scripts\python.exe" (
    echo [X] Falta el entorno: ejecuta primero instalar.bat >> "%LOG%"
    echo  [X] Ejecuta primero instalar.bat
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pyinstaller >> "%LOG%" 2>&1
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
".venv\Scripts\python.exe" -m PyInstaller --noconfirm modhub.spec >> "%LOG%" 2>&1
if not exist "dist\ModHub\ModHub.exe" (
    echo [X] PyInstaller no genero ModHub.exe >> "%LOG%"
    echo  [X] Fallo al construir. Mira construir.log
    pause
    exit /b 1
)
echo [OK] dist\ModHub\ModHub.exe >> "%LOG%"

echo  Creando la version portable...
mkdir "dist\ModHub-Portable" >nul 2>&1
xcopy "dist\ModHub" "dist\ModHub-Portable\ModHub\" /e /i /q /y >> "%LOG%" 2>&1
> "dist\ModHub-Portable\ModHub\portable.txt" echo Este archivo hace que ModHub guarde sus datos en la carpeta Datos de al lado. Borralo para usar la carpeta del usuario.
powershell -NoProfile -Command "Compress-Archive -Path 'dist\ModHub-Portable\ModHub' -DestinationPath 'dist\ModHub-Portable.zip' -Force" >> "%LOG%" 2>&1
if exist "dist\ModHub-Portable.zip" (echo [OK] dist\ModHub-Portable.zip >> "%LOG%") else (echo [X] No se pudo crear el zip portable >> "%LOG%")

echo  Creando el instalador...
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo Instalando Inno Setup con winget... >> "%LOG%"
    winget install --id JRSoftware.InnoSetup -e --silent --accept-source-agreements --accept-package-agreements >> "%LOG%" 2>&1
    if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
    if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
)
if not defined ISCC (
    echo [X] Inno Setup no disponible: solo se crea la version portable >> "%LOG%"
    goto fin
)
"%ISCC%" installer.iss >> "%LOG%" 2>&1
if exist "dist\ModHub-Setup.exe" (echo [OK] dist\ModHub-Setup.exe >> "%LOG%") else (echo [X] Fallo el instalador >> "%LOG%")

:fin
echo === FIN === >> "%LOG%"
echo.
echo  Listo. Resultado en la carpeta dist:
type "%LOG%" | findstr /b /l /c:"[OK]" /c:"[X]"
echo.
pause
