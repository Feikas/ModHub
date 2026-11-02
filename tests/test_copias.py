"""Actualización de mods con copia cuando los hardlinks no están disponibles."""
import os
from test_fase3 import Env, write


def test_actualiza_copia_con_mismo_tamano_y_fecha(tmp_path):
    env = Env(tmp_path)
    env.db.execute("UPDATE games SET deploy_method='copy' WHERE id=?", (env.game_id,))
    env.mod('A', {'a.txt': 'v1'})
    assert env.dep.deploy_game(env.game_id).ok
    src = env.staging / 'mods/A/a.txt'
    timestamp = src.stat().st_mtime_ns
    src.unlink()
    write(src, 'v2')
    os.utime(src, ns=(timestamp, timestamp))
    result = env.dep.deploy_game(env.game_id)
    assert result.ok and result.removed == 1 and result.added == 1
    assert (env.root / 'a.txt').read_text() == 'v2'


def test_no_borra_una_copia_que_el_usuario_edito(tmp_path):
    """Editar un archivo copiado conserva el inodo: aun así se detecta y no se borra al desactivar."""
    env = Env(tmp_path)
    env.db.execute("UPDATE games SET deploy_method='copy' WHERE id=?", (env.game_id,))
    mid = env.mod('A', {'config.ini': 'original'})
    assert env.dep.deploy_game(env.game_id).ok
    cfg = env.root / 'config.ini'
    with open(cfg, 'a') as fh:                       # el usuario lo edita en su sitio
        fh.write('\nmi_ajuste=1')
    env.db.execute("UPDATE mods SET state='disabled' WHERE id=?", (mid,))
    result = env.dep.deploy_game(env.game_id)
    assert cfg.exists() and 'mi_ajuste=1' in cfg.read_text()
    assert any('fuera de ModHub' in w for w in result.warnings)


def test_desactivar_una_copia_sin_tocar_la_quita(tmp_path):
    env = Env(tmp_path)
    env.db.execute("UPDATE games SET deploy_method='copy' WHERE id=?", (env.game_id,))
    mid = env.mod('A', {'a.txt': 'v1'})
    assert env.dep.deploy_game(env.game_id).ok
    env.db.execute("UPDATE mods SET state='disabled' WHERE id=?", (mid,))
    result = env.dep.deploy_game(env.game_id)
    assert result.removed == 1 and not (env.root / 'a.txt').exists()
