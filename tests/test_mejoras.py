"""Regresiones de descargas y rutas; sin conexiones a servicios externos."""
import asyncio
import os

import httpx
import pytest

from modhub.core.downloader import DownloadError, DownloadManager, DownloadState
from modhub.core.extractor import UnsafeArchive, safe_member_path
from modhub.sources.http import HttpClient


@pytest.mark.parametrize('name', ['CON', 'aux.txt', 'dir/LPT1.dll', 'COM¹.txt',
                                   'dir/file.', 'file ', 'bad?.dll', 'bad\x01.txt',
                                   'C:\\escape.dll', '..\\escape.dll'])
def test_rechaza_nombres_no_seguros_en_windows(name):
    with pytest.raises(UnsafeArchive):
        safe_member_path(name)


def test_permite_nombres_normales():
    assert str(safe_member_path('Data\\textures\\cielo.dds')) == 'Data/textures/cielo.dds'


def test_no_concatena_un_rango_incorrecto(tmp_path):
    dest = tmp_path / 'mod.zip'
    part = tmp_path / 'mod.zip.part'
    part.write_bytes(b'abc')

    async def scenario():
        async with HttpClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(206, headers={'Content-Range': 'bytes 0-2/6'}, content=b'XYZ')
        )) as http:
            dm = DownloadManager(http)
            task = await dm.wait(dm.add('https://test.invalid/mod', dest).id)
            assert task.state is DownloadState.FAILED
            assert 'rango incorrecto' in task.error
    asyncio.run(scenario())
    assert part.read_bytes() == b'abc'
    assert not dest.exists()


def test_descarga_corta_se_reanuda_aunque_la_fuente_no_de_tamano(tmp_path):
    requests = []

    def handler(req):
        requests.append(req)
        if len(requests) == 1:
            return httpx.Response(200, headers={'Content-Length': '6'}, content=b'abc')
        return httpx.Response(206, headers={'Content-Range': 'bytes 3-5/6'}, content=b'def')

    async def scenario():
        async with HttpClient(transport=httpx.MockTransport(handler)) as http:
            dm = DownloadManager(http, retry_delay=0)
            task = await dm.wait(dm.add('https://test.invalid/mod', tmp_path / 'mod').id)
            assert task.state is DownloadState.DONE
    asyncio.run(scenario())
    assert (tmp_path / 'mod').read_bytes() == b'abcdef'
    assert requests[1].headers['Range'] == 'bytes=3-'
    assert requests[0].headers['Accept-Encoding'] == 'identity'


def test_rechaza_descargas_simultaneas_al_mismo_archivo(tmp_path):
    async def scenario():
        async with HttpClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b'x'))) as http:
            dm = DownloadManager(http)
            dm.add('https://test.invalid/a', tmp_path / 'mod')
            with pytest.raises(DownloadError, match='pendiente'):
                dm.add('https://test.invalid/b', tmp_path / 'mod')
            await dm.wait_all()
    asyncio.run(scenario())


def test_error_inesperado_no_bloquea_la_cola(tmp_path):
    async def scenario():
        async with HttpClient(transport=httpx.MockTransport(lambda req: httpx.Response(200))) as http:
            dm = DownloadManager(http)
            async def broken(task):
                raise ValueError('fallo simulado')
            dm._transfer = broken
            task = await asyncio.wait_for(dm.wait(dm.add('https://test.invalid/a', tmp_path / 'a').id), 2)
            assert task.state is DownloadState.FAILED
            assert 'fallo simulado' in task.error
    asyncio.run(scenario())


def test_despliegue_usa_copia_si_no_hay_permiso_de_hardlink(tmp_path, monkeypatch):
    from modhub.core.deployer import Deployer, LinkMethod
    import modhub.core.deployer as deployer
    src = tmp_path / 'source'
    src.write_bytes(b'mod')
    def denied(*args):
        raise PermissionError('sin permiso')
    monkeypatch.setattr(deployer.os, 'link', denied)
    method = Deployer(None, allow_symlink=False)._link(src, tmp_path / 'target', LinkMethod.HARDLINK)
    assert method is LinkMethod.COPY
    assert (tmp_path / 'target').read_bytes() == b'mod'


def test_filtros_instalados_no_permiten_acciones_sobre_filas_ocultas(tmp_path):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from test_fase4 import _ui
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    loop, ctx, ids, win = _ui(tmp_path)
    try:
        gid = ids['Lethal Company']
        ctx.db.execute('INSERT INTO mods (game_id, source, source_mod_id, name, state) VALUES (?,?,?,?,?)',
                       (gid, 'local', 'test', 'Mi Mod', 'disabled'))
        view = win.pages['installed']
        view.game.select_game(gid)
        view.refresh()
        view.table.selectRow(0)
        assert view.selected_mod_id() is not None
        view.search.setText('inexistente')
        assert view.table.isRowHidden(0)
        assert view.selected_mod_id() is None
        assert not view.uninstall_btn.isEnabled()
        view.search.clear()
        view.state_filter.setCurrentIndex(1)
        assert view.table.isRowHidden(0)
        view.state_filter.setCurrentIndex(2)
        assert not view.table.isRowHidden(0)
        assert view.visible_count.text() == '1 / 1 mods'
    finally:
        win.close()
        loop.close()


def test_conexion_que_se_corta_a_ratos_acaba_la_descarga(tmp_path):
    """Cada intento avanza 2 bytes y se corta: aunque haya más cortes que reintentos, termina."""
    data = b'0123456789abcdef'

    def handler(req):
        start = int(req.headers.get('Range', 'bytes=0-')[6:-1] or 0)
        chunk = data[start:start + 2]
        headers = {'Content-Length': str(len(data) - start)}
        if start:
            headers['Content-Range'] = f'bytes {start}-{len(data) - 1}/{len(data)}'
        return httpx.Response(206 if start else 200, headers=headers, content=chunk)

    async def scenario():
        async with HttpClient(transport=httpx.MockTransport(handler)) as http:
            dm = DownloadManager(http, retry_delay=0, max_retries=2)
            return await dm.wait(dm.add('https://test.invalid/mod', tmp_path / 'mod').id)
    task = asyncio.run(scenario())
    assert task.state is DownloadState.DONE and (tmp_path / 'mod').read_bytes() == data


def test_conexion_que_nunca_avanza_se_rinde(tmp_path):
    async def scenario():
        async with HttpClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)), max_retries=0) as http:
            dm = DownloadManager(http, retry_delay=0, max_retries=2)
            return await dm.wait(dm.add('https://test.invalid/mod', tmp_path / 'mod').id)
    task = asyncio.run(scenario())
    assert task.state is DownloadState.FAILED and task.attempts == 3


def test_instalaciones_a_medias_se_ofrecen_al_abrir(tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from modhub.ui.context import AppContext
    ctx = AppContext(tmp_path / 'data', use_keyring=False,
                     http_transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    game = tmp_path / 'Juego'
    (game / 'Juego.exe').parent.mkdir(parents=True)
    (game / 'Juego.exe').write_bytes(b'x')
    gid = ctx.add_game_folder(str(game), 'Juego')
    ctx.db.execute("""INSERT INTO mods (game_id, source, source_mod_id, source_file_id, name, state)
                      VALUES (?,?,?,?,?,?)""", (gid, 'nexus', '42', '7', 'Mod grande', 'downloading'))
    rows = ctx.interrupted_installs()
    assert [(r['name'], r['game_name']) for r in rows] == [('Mod grande', 'Juego')]
    ctx.discard_interrupted(rows[0]['id'])
    assert ctx.interrupted_installs() == []
    assert ctx.db.one("SELECT state FROM mods WHERE id=?", (rows[0]['id'],))['state'] == 'error'
