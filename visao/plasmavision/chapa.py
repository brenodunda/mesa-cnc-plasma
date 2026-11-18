"""Detecção de chapa e retalhos sobre a mesa.

Compara a ortofoto atual com a ortofoto da MESA VAZIA (capturada na calibração).
Onde há diferença há material. Furos de peças já cortadas mostram as ripas de novo
e somem da máscara sozinhos, então o retalho fica com o formato real do que sobrou.
"""
from __future__ import annotations

import math

import cv2
import numpy as np


def _mascara_diferenca(orto: np.ndarray, vazia: np.ndarray, limiar: float) -> np.ndarray:
    a = cv2.cvtColor(cv2.GaussianBlur(orto, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.int16)
    b = cv2.cvtColor(cv2.GaussianBlur(vazia, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.int16)
    d = np.abs(a - b)
    # luminância pesa menos (sombras), cor pesa mais
    dist = 0.6 * d[..., 0] + 1.2 * d[..., 1] + 1.2 * d[..., 2]
    return (dist > limiar).astype(np.uint8) * 255


def faixa_portico(calib, x_tocha_mm: float, faixa: tuple[float, float]) -> np.ndarray:
    """Máscara (255 = ignorar) da faixa da ortofoto coberta pelo pórtico."""
    w, h = calib.tamanho_orto()
    m = np.zeros((h, w), np.uint8)
    x0, x1 = x_tocha_mm + faixa[0], x_tocha_mm + faixa[1]
    u0, u1 = calib.mm_para_orto_px([[x0, 0], [x1, 0]])[:, 0]
    m[:, max(0, int(u0)):max(0, int(math.ceil(u1)))] = 255
    return m


def _angulo_e_canto(box_mm: np.ndarray) -> tuple[float, np.ndarray, float, float]:
    """Dado o retângulo (4 cantos em mm), devolve ângulo da aresta mais próxima de X
    (graus, -45..45], canto inferior-esquerdo e dimensões (ao longo de X, ao longo de Y)."""
    def norm(a: float) -> float:
        while a <= -90: a += 180
        while a > 90: a -= 180
        return a
    e0, e1 = box_mm[1] - box_mm[0], box_mm[2] - box_mm[1]
    a0 = norm(math.degrees(math.atan2(e0[1], e0[0])))
    a1 = norm(math.degrees(math.atan2(e1[1], e1[0])))
    l0, l1 = float(np.hypot(*e0)), float(np.hypot(*e1))
    if abs(a0) <= abs(a1):
        ang, comp_x, comp_y = a0, l0, l1
    else:
        ang, comp_x, comp_y = a1, l1, l0
    canto = box_mm[np.argmin(box_mm[:, 0] + box_mm[:, 1])]
    return ang, canto, comp_x, comp_y


def detectar_material(orto: np.ndarray, vazia: np.ndarray, calib, cfg_chapa: dict,
                      ignorar: np.ndarray | None = None) -> dict:
    """Retorna peças de material encontradas e a máscara útil (255 = dá para cortar)."""
    s = calib.escala_mm_px
    m = _mascara_diferenca(orto, vazia, cfg_chapa["limiar_diferenca"])
    valido = cv2.cvtColor(orto, cv2.COLOR_BGR2GRAY) > 0           # fora da imagem = preto
    m[~valido] = 0
    if ignorar is not None:
        m[ignorar > 0] = 0
    k_fecho = max(3, int(round(cfg_chapa["fecho_mm"] / s)) | 1)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_fecho, k_fecho)))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))

    contornos, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    area_min_px = cfg_chapa["area_minima_mm2"] / (s * s)
    pecas = []
    util = np.zeros_like(m)
    if hier is not None:
        hier = hier[0]
        for i, c in enumerate(contornos):
            if hier[i][3] != -1:            # é furo; tratado pelo pai
                continue
            area = cv2.contourArea(c)
            if area < area_min_px:
                continue
            cv2.drawContours(util, contornos, i, 255, -1)
            filho = hier[i][2]
            furos = 0
            while filho != -1:
                cv2.drawContours(util, contornos, filho, 0, -1)
                furos += 1
                filho = hier[filho][0]
            rect = cv2.minAreaRect(c)
            box_mm = calib.orto_px_para_mm(cv2.boxPoints(rect))
            ang, canto, lx, ly = _angulo_e_canto(box_mm)
            area_rect = rect[1][0] * rect[1][1]
            poli = cv2.approxPolyDP(c, 2.5 / s, True).reshape(-1, 2)
            pecas.append({
                "tipo": "chapa" if (area / max(area_rect, 1) > 0.96 and furos == 0) else "retalho",
                "angulo_graus": round(ang, 3),
                "canto_mm": [round(float(canto[0]), 2), round(float(canto[1]), 2)],
                "dimensoes_mm": [round(lx, 1), round(ly, 1)],
                "area_m2": round(area * s * s / 1e6, 4),
                "furos": furos,
                "poligono_mm": calib.orto_px_para_mm(poli).round(1).tolist(),
                "box_mm": box_mm.round(2).tolist(),
            })
    margem = cfg_chapa["margem_borda_mm"]
    if margem > 0:
        k = max(3, int(round(2 * margem / s)) | 1)
        util = cv2.erode(util, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    pecas.sort(key=lambda p: -p["area_m2"])
    return {"pecas": pecas, "mascara_util": util, "mascara_bruta": m}


def desenhar_pecas(orto: np.ndarray, resultado: dict, calib) -> np.ndarray:
    out = orto.copy()
    verde = np.zeros_like(out); verde[resultado["mascara_util"] > 0] = (60, 200, 60)
    out = cv2.addWeighted(out, 1.0, verde, 0.35, 0)
    for i, p in enumerate(resultado["pecas"]):
        poli = calib.mm_para_orto_px(p["poligono_mm"]).astype(np.int32)
        cv2.polylines(out, [poli], True, (0, 140, 255), 2)
        c = calib.mm_para_orto_px([p["canto_mm"]])[0].astype(int)
        cv2.circle(out, tuple(c), 8, (0, 0, 255), -1)
        txt = f"{i+1}: {p['tipo']} {p['dimensoes_mm'][0]:.0f}x{p['dimensoes_mm'][1]:.0f} {p['angulo_graus']:+.2f} graus"
        cv2.putText(out, txt, (int(c[0]) + 12, int(c[1]) - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
    return out
