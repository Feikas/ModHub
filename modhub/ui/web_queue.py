"""Cola de descargas «vía web» (Nexus con cuenta gratuita), como hace Vortex.

Nexus no deja descargar automáticamente a las cuentas gratuitas: hay que pulsar «Slow download» en su web.
Lo que sí se puede hacer, igual que Vortex, es que eso sea UN clic por mod y que todo lo demás vaya solo:

1. ModHub abre la página de descarga de ESE archivo (no la ficha general).
2. Pulsas «Slow download»; Nexus envía el enlace nxm:// a ModHub.
3. ModHub lo reconoce (sabe para qué juego lo pediste), descarga, instala y activa.
4. Si había más mods pedidos (varios mods, dependencias…), abre la página del siguiente.

Se abre una página cada vez para no llenarte el navegador de pestañas.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal

from .theme import SOURCE_NAMES
from .widgets import open_url


@dataclass
class WebItem:
    game_id: int
    source: str
    mod_id: str
    file_id: str | None
    name: str
    page_url: str

    def matches(self, source: str, mod_id: str, file_id: str | None) -> bool:
        if self.source != source or str(self.mod_id) != str(mod_id):
            return False
        return not self.file_id or not file_id or str(self.file_id) == str(file_id)


class WebQueue(QObject):
    changed = Signal()

    def __init__(self, notify, *, opener=open_url, protocol_ok=None, next_delay_ms: int = 1500):
        super().__init__()
        self.notify, self.opener = notify, opener
        self.protocol_ok = protocol_ok or (lambda source: True)   # ¿el enlace nxm:// llega a ModHub?
        self.next_delay_ms = next_delay_ms
        self.pending: deque[WebItem] = deque()
        self.current: WebItem | None = None

    # ── consulta ────────────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self.pending) + (self.current is not None)

    def items(self) -> list[WebItem]:
        return ([self.current] if self.current else []) + list(self.pending)

    # ── cola ────────────────────────────────────────────────────────────
    def add(self, item: WebItem) -> None:
        if any(i.matches(item.source, item.mod_id, item.file_id) and i.game_id == item.game_id
               for i in self.items()):
            if self.current and self.current.matches(item.source, item.mod_id, item.file_id):
                self.reopen()                                  # ya era el actual: se vuelve a abrir
            return
        self.pending.append(item)
        if self.current is None:
            self._open_next()
        else:
            self.notify(f"«{item.name}» en cola ({len(self.pending)} esperando). Se abrirá su página cuando "
                        "termines con la actual.", "info", self._actions())
        self.changed.emit()

    def take(self, source: str, mod_id: str, file_id: str | None) -> WebItem | None:
        """Llegó un enlace de la web: devuelve el mod que lo esperaba (y pasa al siguiente)."""
        if self.current and self.current.matches(source, mod_id, file_id):
            item, self.current = self.current, None
            QTimer.singleShot(self.next_delay_ms, self._open_next)
            self.changed.emit()
            return item
        for item in list(self.pending):                         # lo pulsó en otra pestaña que ya tenía abierta
            if item.matches(source, mod_id, file_id):
                self.pending.remove(item)
                self.changed.emit()
                return item
        return None

    def skip(self) -> None:
        if self.current:
            self.notify(f"«{self.current.name}» saltado.", "info")
        self.current = None
        self._open_next()

    def clear(self) -> None:
        n = len(self)
        self.pending.clear()
        self.current = None
        self.changed.emit()
        if n:
            self.notify(f"Cola vaciada ({n} descargas por la web canceladas).", "info")

    def reopen(self) -> None:
        if self.current:
            self.opener(self.current.page_url)

    # ── interno ─────────────────────────────────────────────────────────
    def _open_next(self) -> None:
        if self.current is not None or not self.pending:
            self.changed.emit()
            return
        self.current = self.pending.popleft()
        web = SOURCE_NAMES.get(self.current.source, self.current.source)
        more = f" Después quedan {len(self.pending)}." if self.pending else ""
        if not self.protocol_ok(self.current.source):
            self.notify(f"Para que el botón de {web} mande las descargas a ModHub, activa «nxm://» en "
                        "Ajustes → Enlaces. Si no, se abrirá Vortex u otro gestor.", "warn")
        self.notify(f"«{self.current.name}»: pulsa «Slow download» en la web de {web} y ModHub seguirá solo.{more}",
                    "info", self._actions())
        self.opener(self.current.page_url)
        self.changed.emit()

    def _actions(self):
        acts = [("Volver a abrir", self.reopen), ("Saltar", self.skip)]
        if len(self) > 1:
            acts.append((f"Cancelar todos ({len(self)})", self.clear))
        return acts
