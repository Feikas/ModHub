"""Instancia única: si ModHub ya está abierto, la segunda copia le pasa el enlace y se cierra.

Dos piezas de Qt:
  • QLockFile  -> decide quién es la instancia principal (sobrevive a cierres bruscos: detecta
                  el bloqueo de un proceso que ya no existe).
  • QLocalServer / QLocalSocket -> canal de mensajes (en Windows es una tubería con nombre).
"""
from __future__ import annotations

import getpass
import re
import time
from pathlib import Path

from PySide6.QtCore import QDir, QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def _channel_name(app_id: str) -> str:
    user = re.sub(r"[^A-Za-z0-9_-]", "_", getpass.getuser() or "user")
    return f"{app_id}-{user}"


class SingleInstanceGuard(QObject):
    message_received = Signal(str)

    def __init__(self, app_id: str = "ModHub", lock_dir: Path | str | None = None, parent=None):
        super().__init__(parent)
        self.name = _channel_name(app_id)
        lock_dir = Path(lock_dir) if lock_dir else Path(QDir.tempPath())
        self._lock = QLockFile(str(lock_dir / f"{self.name}.lock"))
        self._lock.setStaleLockTime(0)          # si el proceso dueño murió, el bloqueo se considera viejo
        self._server: QLocalServer | None = None

    # --- instancia principal ----------------------------------------------
    def acquire(self) -> bool:
        """True si somos la primera copia (y empezamos a escuchar)."""
        if not self._lock.tryLock(100):
            return False
        QLocalServer.removeServer(self.name)    # restos de un cierre brusco (Linux/macOS)
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self._server.listen(self.name):
            raise RuntimeError(f"No se pudo abrir el canal de mensajes: {self._server.errorString()}")
        self._server.newConnection.connect(self._on_connection)
        return True

    def _on_connection(self) -> None:
        while self._server and self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(sock.deleteLater)
            if sock.bytesAvailable():
                self._read(sock)

    def _read(self, sock: QLocalSocket) -> None:
        data = bytes(sock.readAll()).decode("utf-8", errors="replace")
        for line in data.splitlines():
            if line.strip():
                self.message_received.emit(line.strip())

    # --- instancia secundaria ---------------------------------------------
    def send(self, message: str, *, attempts: int = 10, timeout_ms: int = 1000) -> bool:
        """Envía `message` a la copia principal. Reintenta un poco por si aún está arrancando."""
        for _ in range(attempts):
            sock = QLocalSocket()
            sock.connectToServer(self.name)
            if sock.waitForConnected(timeout_ms):
                sock.write((message.replace("\n", " ") + "\n").encode("utf-8"))
                sock.flush()
                sock.waitForBytesWritten(timeout_ms)
                sock.disconnectFromServer()
                return True
            time.sleep(0.2)
        return False

    def release(self) -> None:
        if self._server:
            # Desconectar antes de cerrar: si no, un mensaje tardío llamaría a un objeto ya destruido.
            for sock in self._server.findChildren(QLocalSocket):
                try:
                    sock.readyRead.disconnect()
                    sock.disconnected.disconnect()
                except (RuntimeError, TypeError):
                    pass
                sock.abort()
            self._server.newConnection.disconnect()
            self._server.close()
            self._server.deleteLater()
            self._server = None
        if self._lock.isLocked():
            self._lock.unlock()
