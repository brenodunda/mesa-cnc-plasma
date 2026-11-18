"""Teste de integração: sistema simulado + API do painel, do jeito que o navegador usa."""
import time

import pytest
from fastapi.testclient import TestClient

from plasmavision.sistema import Sistema
from plasmavision.web.app import criar_app


@pytest.fixture(scope="module")
def cliente(tmp_path_factory):
    from plasmavision.config import carregar_config
    cfg = carregar_config(None, {"dados": str(tmp_path_factory.mktemp("dados"))})
    s = Sistema(cfg, simular=True)
    for _ in range(100):
        if s.calib is not None and s.vazia_cam is not None and s.cam["topo"].quadro is not None:
            break
        time.sleep(0.1)
    app = criar_app(s)
    return TestClient(app, client=("192.168.0.20", 50000)), s, app


def test_fluxo_de_corte_pela_api(cliente):
    c, s, _ = cliente
    st = c.get("/api/status").json()
    assert st["calibracao"]["pronta"] and st["simulado"]
    pecas = c.post("/api/chapa/capturar").json()["pecas"]
    assert pecas and pecas[0]["tipo"] == "chapa"
    job = c.post("/api/job/exemplo?largura=200&altura=120&furo=40").json()
    assert job["sequencia_tocha"] == ["acender", "soltar", "acender", "soltar"]
    pos = c.post("/api/job/posicionar", json={"angulos": "90"}).json()
    assert "dx" in pos
    assert c.post("/api/job/enviar", json={"armar_arco": True}).json()["ok"]
    assert c.get("/api/status").json()["automacao"]["estado"] == "aguardando_inicio"
    assert c.get("/previa/encaixe.jpg").status_code == 200
    g = c.get("/api/job/gcode")
    assert g.status_code == 200 and "PlasmaVision" in g.text


def test_comando_de_fora_da_rede_e_bloqueado(cliente):
    _, s, app = cliente
    de_fora = TestClient(app, client=("100.101.102.103", 50000))     # ex.: celular pelo Tailscale
    assert de_fora.post("/api/mach3/cycle_start").status_code == 403
    assert de_fora.post("/api/job/enviar", json={}).status_code == 403
    assert de_fora.post("/api/mach3/feed_hold").status_code == 200   # pausar é sempre permitido
    assert de_fora.get("/api/status").json()["pode_comandar"] is False
