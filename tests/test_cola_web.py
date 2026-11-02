"""Nexus con cuenta gratuita: una página cada vez, un clic por mod y lo demás solo (como Vortex)."""
from __future__ import annotations

import httpx
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from modhub.ui.web_queue import WebItem, WebQueue

QApplication.instance() or QApplication([])


def item(n, game=1):
    return WebItem(game, "nexus", str(n), f"f{n}", f"Mod {n}",
                   f"https://www.nexusmods.com/skyrimspecialedition/mods/{n}?tab=files&file_id=f{n}&nmm=1")


def make(protocol_ok=True):
    opened, notes = [], []
    q = WebQueue(lambda t, l="info", a=None: notes.append((t, l, [x[0] for x in (a or [])])),
                 opener=opened.append, protocol_ok=lambda s: protocol_ok, next_delay_ms=0)
    return q, opened, notes


def pump():
    for _ in range(5):
        QCoreApplication.processEvents()


def test_abre_las_paginas_de_una_en_una():
    q, opened, notes = make()
    for n in (1, 2, 3):
        q.add(item(n))
    assert opened == [item(1).page_url] and len(q) == 3            # solo la primera
    assert "nmm=1" in opened[0] and "file_id=f1" in opened[0]      # la página del archivo, no la ficha
    got = q.take("nexus", "1", "f1")                                # llegó su nxm://
    assert got.name == "Mod 1"
    pump()
    assert opened[-1] == item(2).page_url and q.current.mod_id == "2"
    q.skip()
    assert opened[-1] == item(3).page_url
    assert q.take("nexus", "3", "f3") is not None
    pump()
    assert len(q) == 0 and len(opened) == 3


def test_reconoce_el_enlace_aunque_lo_pulses_en_otra_pestana():
    q, opened, _ = make()
    q.add(item(1))
    q.add(item(2))
    assert q.take("nexus", "2", "f2").name == "Mod 2"               # estaba en cola, no era el actual
    assert q.current.mod_id == "1" and not q.pending
    assert q.take("nexus", "99", "x") is None                       # enlace que nadie esperaba


def test_no_duplica_y_avisa_si_nxm_no_es_de_modhub():
    q, opened, notes = make(protocol_ok=False)
    q.add(item(1))
    q.add(item(1))                                                  # pedirlo otra vez: se reabre, no se duplica
    assert len(q) == 1 and opened == [item(1).page_url] * 2
    assert any("nxm://" in t and l == "warn" for t, l, _ in notes)


def test_flujo_de_cuenta_gratuita_va_a_la_cola():
    from modhub.sources import DownloadPolicy
    from modhub.sources.base import DownloadTicket, InterceptMethod
    from modhub.core.installer import InstallResult
    from modhub.ui.flow import InstallFlow

    class Ctx:
        class events:
            class intercept_event:
                @staticmethod
                def emit(_t):
                    pass

    q, opened, notes = make()
    flow = InstallFlow(Ctx(), lambda *a, **k: None, lambda _m: None, None, q)
    t = DownloadTicket(policy=DownloadPolicy.BROWSER_INTERCEPT, source="nexus", mod_id="5", file_id="f5",
                       intercept=InterceptMethod.PROTOCOL, page_url=item(5).page_url, note="pulsa")
    for _ in range(2):
        flow.handle_result(InstallResult("needs_user", ticket=t), 7, "Mod 5")
    assert len(q) == 1 and q.current.game_id == 7 and opened == [item(5).page_url, item(5).page_url]


def test_enlace_esperado_se_instala_en_su_juego_sin_preguntar(tmp_path, monkeypatch):
    from modhub.ui.context import AppContext
    from modhub.ui.main_window import MainWindow
    ctx = AppContext(tmp_path / "data", use_keyring=False,
                     http_transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    gids = []
    for name in ("A", "B"):                                         # dos juegos de Nexus: normalmente preguntaría
        root = tmp_path / name
        root.mkdir()
        (root / f"{name}.exe").write_bytes(b"x")
        gids.append(ctx.add_game_folder(str(root), name, source_ids={"nexus": "skyrimspecialedition"}))

    class Reg:
        available = False

    w = MainWindow(ctx, registrar=Reg(), start_watcher=False)
    called = {}

    async def fake_install(match, game_id):
        called["game"], called["key"] = game_id, match.request.context.get("key")
        from modhub.core.installer import InstallResult
        return InstallResult("installed", message="")
    monkeypatch.setattr(ctx, "install_from_protocol", fake_install)
    monkeypatch.setattr("modhub.ui.main_window.ChooseGameDialog",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debía preguntar")))
    import asyncio

    def run_now(coro, on_ok=None, on_error=None, on_finally=None):
        res = asyncio.run(coro)
        if on_ok:
            on_ok(res)
    monkeypatch.setattr("modhub.ui.main_window.spawn", run_now)
    w.flow.handle_result = lambda *a, **k: None
    w.web_queue.opener = lambda _u: None
    w.web_queue.add(WebItem(gids[1], "nexus", "266", "1000", "SkyUI", "https://x"))
    w.handle_url("nxm://skyrimspecialedition/mods/266/files/1000?key=abc&expires=9&user_id=1")
    assert called == {"game": gids[1], "key": "abc"}
    assert w.web_queue.current is None
