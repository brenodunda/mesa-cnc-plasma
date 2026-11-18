import math

import numpy as np

from plasmavision.gcode import exemplo_peca, interpretar, transformar


def _rt(p, ang, dx, dy):
    t = math.radians(ang)
    return (p[0] * math.cos(t) - p[1] * math.sin(t) + dx, p[0] * math.sin(t) + p[1] * math.cos(t) + dy)


def test_transformacao_preserva_geometria_e_arcos():
    txt = exemplo_peca(200, 120, 40)
    a = interpretar(txt)
    novo, avisos = transformar(txt, 37.0, 412.5, -18.25)
    b = interpretar(novo)
    assert not avisos
    assert len(a.movimentos) == len(b.movimentos)
    primeiro = next(i for i, m in enumerate(a.movimentos) if m.muda_xy)
    for ma, mb in list(zip(a.movimentos, b.movimentos))[primeiro:]:
        esp = _rt(ma.p1, 37.0, 412.5, -18.25)
        assert np.allclose(mb.p1[:2], esp, atol=1e-3)
        assert abs(mb.p1[2] - ma.p1[2]) < 1e-9          # Z intocado
        if ma.centro is not None:
            assert np.allclose(mb.centro, _rt(ma.centro, 37.0, 412.5, -18.25), atol=1e-3)
    assert b.sequencia_tocha() == ["acender", "soltar", "acender", "soltar"]


def test_linha_so_com_y_ganha_x_e_modo_incremental():
    txt = "G21 G90\nG0 X10 Y0\nG1 Y50 F1000\nG91\nG1 X20\nG90\nG1 X0 Y0\n"
    novo, _ = transformar(txt, 90.0, 0.0, 0.0, cabecalho=False)
    b = interpretar(novo)
    pts = [m.p1[:2] for m in b.movimentos]
    assert np.allclose(pts[0], (0, 10), atol=1e-6)      # (10,0) girado 90°
    assert np.allclose(pts[1], (-50, 10), atol=1e-6)    # (10,50)
    assert np.allclose(pts[2], (-50, 30), atol=1e-6)    # +20 em X vira +20 em Y
    assert np.allclose(pts[3], (0, 0), atol=1e-6)


def test_polilinhas_e_limites():
    p = interpretar(exemplo_peca(200, 120, 40, kerf=1.6))
    xmin, ymin, xmax, ymax = p.limites_corte()
    assert xmin < 0 and ymin < 0
    assert abs(xmax - 200.8) < 0.01 and abs(ymax - 120.8) < 0.01
    assert len(p.polilinhas_corte()) == 2


def test_g53_nao_e_transformado():
    txt = "G90\nG53 G0 X0 Y0\nG1 X10 Y10\n"
    novo, avisos = transformar(txt, 10, 5, 5, cabecalho=False)
    assert "G53 G0 X0 Y0" in novo and avisos
