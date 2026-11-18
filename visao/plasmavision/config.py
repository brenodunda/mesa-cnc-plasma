"""Leitura da configuração (YAML) com valores padrão.

Tudo que não estiver no arquivo do usuário vem de PADRAO. As chaves são em
português e espelham o arquivo config.exemplo.yaml.
"""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

PADRAO: dict = {
    "dados": "dados",                       # pasta de calibração, referências, jobs e eventos
    "mesa": {
        "curso_mm": [1550.0, 1050.0, 100.0],   # curso da tocha em coordenadas de máquina
        "area_util_mm": [1500.0, 1000.0],
        # região que a ortofoto cobre (coordenadas de máquina, mm)
        "regiao_orto_mm": [-60.0, -60.0, 1610.0, 1110.0],
        "escala_orto_mm_px": 1.0,
        # faixa ocupada pelo pórtico em X, relativa à posição X da tocha (mm): [atrás, à frente]
        "mascara_portico_mm": [-480.0, 60.0],
    },
    "cameras": {
        "topo": {"tipo": "picamera2", "fonte": 0, "resolucao": [4608, 2592], "fps": 4,
                 "rotacao": 0},
        "tocha": {"tipo": "opencv", "fonte": 0, "resolucao": [640, 480], "fps": 30,
                  "rotacao": 0},
    },
    "calibracao": {
        "arquivo": "calibracao.json",
        "dicionario_aruco": "DICT_4X4_50",
        # centro de cada marcador em coordenadas de máquina (mm); ajuste no assistente
        "marcadores_mm": {0: [0.0, 0.0], 1: [1550.0, 0.0], 2: [1550.0, 1050.0], 3: [0.0, 1050.0]},
        "intrinseca": "",                    # arquivo .json opcional com K e distorção
    },
    "chapa": {
        "limiar_diferenca": 30,
        "area_minima_mm2": 15000.0,
        "margem_borda_mm": 8.0,
        "fecho_mm": 12.0,
    },
    "encaixe": {
        "resolucao_mm": 4.0,
        "kerf_mm": 1.6,
        "folga_mm": 6.0,
        "passo_angulo_graus": 90.0,
        "preferencia": "canto",             # canto | centro
    },
    "gcode": {
        "ij_absoluto": False,               # Mach3 padrão: I/J incrementais
    },
    "arco": {
        "roi": [0.35, 0.55, 0.30, 0.40],    # x, y, largura, altura (fração da imagem)
        "limiar_brilho": 235,
        "fracao_ligado": 0.04,
        "fracao_desligado": 0.01,
        "quadros_confirmacao": 3,
        "atraso_furacao_s": 0.6,
        "tempo_parado_s": 0.4,
        "tempo_perda_arco_s": 0.25,
        "pausar_se_arco_apagar": True,
    },
    "seguranca": {
        "ativo": True,
        "modo": "pessoa",                   # pessoa | movimento
        "modelo": "modelos/pessoa.onnx",
        "tamanho_entrada": 640,
        "confianca": 0.45,
        "cada_n_quadros": 2,
        "area_minima_movimento": 0.002,     # fração da imagem
        # polígonos da zona de exclusão em fração da imagem da câmera do topo
        # (padrão: moldura nas bordas da imagem; desenhe as suas no painel)
        "zonas": [[[0.0, 0.0], [1.0, 0.0], [1.0, 0.05], [0.0, 0.05]],
                  [[0.0, 0.95], [1.0, 0.95], [1.0, 1.0], [0.0, 1.0]],
                  [[0.0, 0.05], [0.04, 0.05], [0.04, 0.95], [0.0, 0.95]],
                  [[0.96, 0.05], [1.0, 0.05], [1.0, 0.95], [0.96, 0.95]]],
        "so_com_maquina_ativa": False,
        "acoes": ["feed_hold", "corta_tocha", "alarme"],
    },
    "medicao": {
        "tolerancia_mm": 1.0,
        "metodo": "diferenca",              # diferenca (usa mesa vazia) | otsu
        "escala_mm_px": 0.5,
    },
    "gpio": {
        "ativo": True,
        "feed_hold": 17,                    # pulso para a entrada OEM "Feed Hold" da placa USB
        "corta_tocha": 27,                  # relé NF em série com o botão da tocha
        "alarme": 22,                       # sirene/luz
        "pulso_s": 0.3,
    },
    "mach3": {
        "url": "http://192.168.0.50:8765",
        "token": "troque-este-token",
        "intervalo_s": 0.2,
        "simulado": False,
    },
    "web": {
        "host": "0.0.0.0",
        "porta": 8080,
        "usuario": "admin",
        "senha": "",
        "qualidade_jpeg": 75,
        "largura_stream": 1280,
        # quem pode mandar a máquina ANDAR (cycle start, enviar job). Pausar e parar são liberados.
        "redes_de_comando": ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
    },
}


def _mesclar(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "marcadores_mm":
            out[k] = _mesclar(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def carregar_config(caminho: str | Path | None = None, extra: dict | None = None) -> dict:
    cfg = copy.deepcopy(PADRAO)
    if caminho:
        p = Path(caminho)
        if p.exists():
            with open(p, encoding="utf-8") as f:
                cfg = _mesclar(cfg, yaml.safe_load(f) or {})
    if extra:
        cfg = _mesclar(cfg, extra)
    cfg["calibracao"]["marcadores_mm"] = {int(k): [float(a) for a in v]
                                          for k, v in cfg["calibracao"]["marcadores_mm"].items()}
    base = Path(caminho).parent if caminho else Path.cwd()
    dados = Path(cfg["dados"])
    cfg["dados"] = str(dados if dados.is_absolute() else (base / dados))
    Path(cfg["dados"]).mkdir(parents=True, exist_ok=True)
    return cfg


def caminho_dados(cfg: dict, *partes: str) -> Path:
    p = Path(cfg["dados"]).joinpath(*partes)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
