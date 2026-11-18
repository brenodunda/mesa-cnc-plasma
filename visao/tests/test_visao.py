"""Testes ponta a ponta com a mesa simulada: calibração, chapa, encaixe, arco, medição, segurança."""
import math

import cv2
import numpy as np
import pytest

from plasmavision import arco as arco_mod
from plasmavision.calibracao import Calibracao, detectar_marcadores
from plasmavision.chapa import detectar_material
from plasmavision.encaixe import angulos_candidatos, encaixar, mapa_bloqueio
from plasmavision.gcode import exemplo_peca, interpretar, transformar
from plasmavision.medicao import medir, referencia_de_dxf, referencia_de_programa
from plasmavision.seguranca import MonitorSeguranca
from plasmavision.simulador import MaquinaSimulada, MesaSimulada


@pytest.fixture
def mundo(cfg):
    maq = MaquinaSimulada(acelerar=20.0)
    maq.x = 1500.0                       # pórtico estacionado no fim do curso
    mesa = MesaSimulada(cfg, maq)
    img = mesa.quadro_topo(com_portico=False)
    marc = detectar_marcadores(img, cfg["calibracao"]["dicionario_aruco"])
    ids = sorted(marc)
    assert ids == [0, 1, 2, 3]
    px = np.array([marc[i] for i in ids])
    mm = np.array([cfg["calibracao"]["marcadores_mm"][i] for i in ids])
    calib = Calibracao.de_pontos(px, mm, regiao_mm=tuple(cfg["mesa"]["regiao_orto_mm"]), escala_mm_px=1.0)
    vazia_cam = cv2.warpPerspective(mesa.orto_vazia(), mesa.H_orto_cam, mesa.tam_cam, borderValue=(30, 30, 30))
    return cfg, maq, mesa, calib, calib.ortofoto(vazia_cam)


def test_calibracao_precisa(mundo):
    cfg, maq, mesa, calib, vazia = mundo
    assert calib.erro_rms_mm < 1.0
    # um ponto qualquer: mm -> orto sintética -> câmera -> px_para_mm
    mm = np.array([[700.0, 400.0]])
    cam = cv2.perspectiveTransform(mesa.mm_para_orto(mm).reshape(-1, 1, 2).astype(np.float32),
                                   mesa.H_orto_cam).reshape(-1, 2)
    assert np.allclose(calib.px_para_mm(cam), mm, atol=1.5)


def test_detecta_chapa_girada(mundo):
    cfg, maq, mesa, calib, vazia = mundo
    orto = calib.ortofoto(mesa.quadro_topo(com_portico=False))
    r = detectar_material(orto, vazia, calib, cfg["chapa"])
    assert len(r["pecas"]) == 1
    p = r["pecas"][0]
    assert p["tipo"] == "chapa"
    assert abs(p["angulo_graus"] - 2.2) < 0.3
    assert abs(p["dimensoes_mm"][0] - 1180) < 6 and abs(p["dimensoes_mm"][1] - 860) < 6
    assert abs(p["canto_mm"][0] - 120) < 4 and abs(p["canto_mm"][1] - 80) < 4


def test_encaixe_no_retalho_em_L(mundo):
    cfg, maq, mesa, calib, vazia = mundo
    mesa.usar_retalho()
    orto = calib.ortofoto(mesa.quadro_topo(com_portico=False))
    r = detectar_material(orto, vazia, calib, cfg["chapa"])
    assert r["pecas"][0]["tipo"] == "retalho"
    texto = exemplo_peca(420, 300, 60)
    prog = interpretar(texto)
    B, orig = mapa_bloqueio(r["mascara_util"], calib, cfg["mesa"]["curso_mm"], 4.0)
    res = encaixar(prog, B, orig, 4.0, angulos_candidatos(0.0, 90.0), kerf_mm=1.6, folga_mm=6)
    assert res is not None
    novo, _ = transformar(texto, res["angulo_graus"], res["dx"], res["dy"])
    pts = np.vstack(interpretar(novo).polilinhas_corte(1.0))
    uv = calib.mm_para_orto_px(pts).round().astype(int)
    assert np.all(r["mascara_util"][uv[:, 1], uv[:, 0]] > 0), "trajetória saiu do retalho"


def test_peca_grande_nao_cabe(mundo):
    cfg, maq, mesa, calib, vazia = mundo
    orto = calib.ortofoto(mesa.quadro_topo(com_portico=False))
    r = detectar_material(orto, vazia, calib, cfg["chapa"])
    prog = interpretar(exemplo_peca(1400, 950, 50))
    B, orig = mapa_bloqueio(r["mascara_util"], calib, cfg["mesa"]["curso_mm"], 5.0)
    assert encaixar(prog, B, orig, 5.0, angulos_candidatos(2.2, 90.0)) is None


def test_ciclo_completo_com_arco_automatico(cfg):
    maq = MaquinaSimulada(acelerar=30.0, operador=True)
    mesa = MesaSimulada(cfg, maq)
    texto, _ = transformar(exemplo_peca(200, 120, 40), 0, 300, 300)
    prog = interpretar(texto)
    maq.carregar("t.tap", texto)
    det = arco_mod.DetectorArco(cfg["arco"])
    aut = arco_mod.AutomacaoTocha(cfg["arco"])
    aut.armar(prog.sequencia_tocha())
    maq.cycle_start()                          # o operador dá o primeiro Cycle Start
    n_cs, t = 0, 0.0
    for _ in range(6000):
        dt = 0.05; t += dt
        maq.passo_tempo(dt)
        arco = det.atualizar(mesa.quadro_tocha())
        for a in aut.passo(arco, maq.movendo, True, False, agora=t):
            if a == "cycle_start":
                maq.cycle_start(); n_cs += 1
        if aut.estado == "concluido":
            break
    assert aut.estado == "concluido", aut.info()
    assert n_cs == 4
    comprimento = sum(float(np.hypot(*(b - a))) for a, b in maq.cortes)
    assert comprimento > 600, comprimento          # perímetro + furo foram cortados com arco


def test_arco_apagando_no_corte_pausa(cfg):
    aut = arco_mod.AutomacaoTocha(cfg["arco"])
    aut.armar(["acender", "soltar"])
    t = 0.0
    seq = [(False, True)] * 5 + [(False, False)] * 20 + [(True, False)] * 30 + [(True, True)] * 10 + [(False, True)] * 10
    acoes = []
    for arco, mov in seq:
        t += 0.05
        acoes += aut.passo(arco, mov, True, False, agora=t)
    assert acoes == ["cycle_start", "feed_hold"]
    assert aut.estado == "arco_perdido"


def test_seguranca_por_movimento(cfg, tmp_path):
    c = dict(cfg["seguranca"]); c["modo"] = "movimento"; c["cada_n_quadros"] = 1
    c["zonas"] = [[[0.85, 0.0], [1.0, 0.0], [1.0, 1.0], [0.85, 1.0]]]
    mon = MonitorSeguranca(c, tmp_path)
    maq = MaquinaSimulada(); mesa = MesaSimulada(cfg, maq)
    for _ in range(40):
        assert not mon.atualizar(mesa.quadro_topo())
    mesa.pessoa = True
    disparou = any(mon.atualizar(mesa.quadro_topo()) for _ in range(5))
    assert disparou and mon.alarme and mon.eventos


def _peca_sintetica(escala=0.5, sup=8):
    """Ortofoto 0,5 mm/px com peça 200 x 120 girada 12° e furo Ø40, desenhada com
    superamostragem (bordas com cobertura de área correta, como numa câmera real)."""
    w, h = int(400 / escala), int(300 / escala)
    e2 = escala / sup
    para_px = lambda p: np.column_stack([p[:, 0] / e2, (300 - p[:, 1]) / e2]) - 0.5
    grande = np.zeros((h * sup, w * sup), np.uint8)
    ang = math.radians(12)
    R = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]])
    ret = np.array([[0, 0], [200, 0], [200, 120], [0, 120]], float) @ R.T + [100, 80]
    cv2.fillPoly(grande, [np.round(para_px(ret) * 16).astype(np.int32)], 255, cv2.LINE_8, shift=4)
    cc = para_px(np.array([[100, 60]]) @ R.T + [100, 80])[0]
    cv2.circle(grande, (int(round(cc[0] * 16)), int(round(cc[1] * 16))), int(round(20 / e2 * 16)), 0, -1, cv2.LINE_8, shift=4)
    cobertura = cv2.resize(grande, (w, h), interpolation=cv2.INTER_AREA).astype(np.float32)[..., None] / 255
    fundo = np.full((h, w, 3), (40, 42, 45), np.uint8)
    for u in range(0, w, int(80 / escala)):
        cv2.rectangle(fundo, (u, 0), (u + 4, h), (45, 75, 125), -1)
    img = (fundo * (1 - cobertura) + np.array([160, 160, 165]) * cobertura).round().astype(np.uint8)
    orto_para_mm = lambda uv: np.column_stack([(uv[:, 0] + 0.5) * escala, 300 - (uv[:, 1] + 0.5) * escala])
    return img, fundo, orto_para_mm


def test_medicao_com_dxf(tmp_path):
    import ezdxf
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (200, 0), (200, 120), (0, 120)], close=True)
    msp.add_circle((100, 60), 20)
    arq = tmp_path / "peca.dxf"; doc.saveas(arq)
    ref = referencia_de_dxf(arq)
    img, fundo, o2m = _peca_sintetica()
    res = medir(img, 0.5, o2m, vazia=fundo, referencia=ref, tolerancia_mm=1.0)
    L, W = res["dimensoes_mm"]
    assert abs(L - 200) < 1.0 and abs(W - 120) < 1.0
    assert len(res["furos"]) == 1 and abs(res["furos"][0]["diametro_mm"] - 40) < 1.0
    assert res["comparacao"]["aprovada"], res["comparacao"]


def test_referencia_do_programa():
    ref = referencia_de_programa(interpretar(exemplo_peca(200, 120, 40, kerf=0.0)))
    assert len(ref["furos"]) == 1 and abs(ref["furos"][0][2] - 40) < 0.5


def test_segundo_encaixe_nao_sobrepoe_o_primeiro(mundo):
    from plasmavision.encaixe import bloquear_programas, pegada
    cfg, maq, mesa, calib, vazia = mundo
    orto = calib.ortofoto(mesa.quadro_topo(com_portico=False))
    r = detectar_material(orto, vazia, calib, cfg["chapa"])
    texto = exemplo_peca(300, 200, 60)
    prog = interpretar(texto)
    B, orig = mapa_bloqueio(r["mascara_util"], calib, cfg["mesa"]["curso_mm"], 4.0)
    a = encaixar(prog, B, orig, 4.0, [0.0], 1.6, 6.0)
    pa = interpretar(transformar(texto, a["angulo_graus"], a["dx"], a["dy"])[0])
    B2 = bloquear_programas(B, orig, 4.0, [pa], 1.6 + 12.0)
    b = encaixar(prog, B2, orig, 4.0, [0.0], 1.6, 6.0)
    pb = interpretar(transformar(texto, b["angulo_graus"], b["dx"], b["dy"])[0])
    la, lb = pa.limites_corte(), pb.limites_corte()
    separadas = la[2] < lb[0] or lb[2] < la[0] or la[3] < lb[1] or lb[3] < la[1]
    assert separadas, (la, lb)


def test_decodifica_saida_yolo_v8():
    from plasmavision.seguranca import DetectorPessoas

    class RedeFalsa:
        def setInput(self, b): self.b = b
        def forward(self):
            out = np.zeros((1, 84, 50), np.float32)
            out[0, :4, 0] = [320, 240, 100, 200]    # caixa no espaço 640x640
            out[0, 4, 0] = 0.9                      # classe 0 = pessoa
            out[0, :4, 1] = [100, 100, 50, 50]; out[0, 5, 1] = 0.95   # outra classe: ignorar
            return out
    d = DetectorPessoas.__new__(DetectorPessoas)
    d.rede, d.tamanho, d.confianca = RedeFalsa(), 640, 0.5
    caixas = d.detectar(np.zeros((960, 1280, 3), np.uint8))   # escala 0,5
    assert len(caixas) == 1
    x, y, w, h, c = caixas[0]
    assert (x, y, w, h) == (540, 280, 200, 400) and c > 0.89


def test_config_exemplo_carrega():
    from pathlib import Path
    from plasmavision.config import carregar_config
    import tempfile, shutil
    tmp = Path(tempfile.mkdtemp())
    shutil.copy(Path(__file__).parents[1] / "config.exemplo.yaml", tmp / "config.yaml")
    c = carregar_config(tmp / "config.yaml")
    assert c["calibracao"]["marcadores_mm"][2] == [1550.0, 1050.0]
    assert c["cameras"]["topo"]["tipo"] == "picamera2"
    assert len(c["seguranca"]["zonas"]) == 4          # padrão mantido
