# -*- mode: python ; coding: utf-8 -*-
# Receta de PyInstaller para ModHub.exe (versión de carpeta: arranca rápido y no da falsos positivos
# de antivirus como el modo de «un solo archivo»). La usa construir.bat.
import os
from pathlib import Path

root = Path(SPECPATH)
seven_dir = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip"
binaries = [(str(seven_dir / n), "tools") for n in ("7z.exe", "7z.dll") if (seven_dir / n).is_file()]
datas = [(str(root / "modhub" / "ui" / "assets"), "modhub/ui/assets"),
         (str(root / "modhub" / "db" / "schema.sql"), "modhub/db")]
if (seven_dir / "License.txt").is_file():                      # licencia LGPL de 7-Zip, obligatoria al incluirlo
    datas.append((str(seven_dir / "License.txt"), "tools"))

a = Analysis(
    [str(root / "run_modhub.pyw")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["keyring.backends.Windows", "win32timezone", "qasync", "h2"],
    excludes=["tkinter", "pytest", "tests", "numpy", "PIL", "cryptography", "yaml", "gi", "IPython", "matplotlib", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel", "PySide6.QtQuick", "PySide6.QtQml",
              "PySide6.QtQuickWidgets", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtPdf",
              "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtSql",
              "PySide6.QtDesigner", "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSensors"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ModHub", console=False,
          icon=str(root / "modhub" / "ui" / "assets" / "modhub.ico"))
coll = COLLECT(exe, a.binaries, a.datas, name="ModHub")
