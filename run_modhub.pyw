"""Lanzador sin consola para Windows (doble clic o enlaces del navegador).

Windows ejecuta:  pythonw.exe run_modhub.pyw "nxm://…"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from modhub.main import main  # noqa: E402

sys.exit(main())
