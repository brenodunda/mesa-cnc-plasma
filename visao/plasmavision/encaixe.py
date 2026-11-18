"""Encaixe do programa na chapa ou no retalho visto pela câmera.

O programa vira uma "pegada" (o material que ele consome: contorno externo cheio,
engrossado por kerf + folga). A área livre da mesa vira um mapa de bloqueio. Uma
correlação por FFT acha, para cada ângulo, todas as posições em que a pegada não
encosta em nada bloqueado; fica a melhor (mais perto do canto, ou do centro).
"""
from __future__ import annotations

import math

import cv2
import numpy as np
from scipy.signal import correlate

from .gcode import Programa


def _girar(pts: np.ndarray, ang_graus: float) -> np.ndarray:
    t = math.radians(ang_graus)
    R = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
    return pts @ R.T


def pegada(prog: Programa, ang_graus: float, res_mm: float, espessura_mm: float):
    """Máscara (linhas = Y crescente) do material usado pelo programa girado.
    Retorna (mascara uint8 0/1, (xmin, ymin) em mm do pixel [0,0])."""
    polis = [_girar(p, ang_graus) for p in prog.polilinhas_corte(passo_mm=max(res_mm / 2, 0.5))]
    if not polis:
        raise ValueError("o programa não tem movimentos de corte (G1/G2/G3)")
    todos = np.vstack(polis)
    pad = espessura_mm / 2 + 2 * res_mm
    xmin, ymin = todos.min(axis=0) - pad
    xmax, ymax = todos.max(axis=0) + pad
    w = int(math.ceil((xmax - xmin) / res_mm)) + 3
    h = int(math.ceil((ymax - ymin) / res_mm)) + 3
    tela = np.zeros((h, w), np.uint8)
    esp = max(1, int(round(espessura_mm / res_mm)))
    for p in polis:
        q = np.round((p - [xmin, ymin]) / res_mm).astype(np.int32)
        cv2.polylines(tela, [q.reshape(-1, 1, 2)], False, 1, thickness=esp)
    # tudo que não é alcançável por fora é material da peça (enche furos e o miolo)
    fora = tela.copy()
    msk = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(fora, msk, (0, 0), 2)
    cheia = (fora != 2).astype(np.uint8)
    return cheia, (float(xmin), float(ymin))


def mapa_bloqueio(mascara_util_orto: np.ndarray, calib, curso_mm, res_mm: float):
    """Converte a máscara útil da ortofoto em mapa de bloqueio em coordenadas de máquina.
    Retorna (bloqueado uint8 0/1, (X0, Y0)) com linhas = Y crescente. Fora do curso = bloqueado."""
    X0, Y0, X1, Y1 = 0.0, 0.0, float(curso_mm[0]), float(curso_mm[1])
    w = int(math.floor((X1 - X0) / res_mm)) + 1
    h = int(math.floor((Y1 - Y0) / res_mm)) + 1
    xs = X0 + np.arange(w) * res_mm
    ys = Y0 + np.arange(h) * res_mm
    gx, gy = np.meshgrid(xs, ys)
    uv = calib.mm_para_orto_px(np.column_stack([gx.ravel(), gy.ravel()]))
    mapx = uv[:, 0].reshape(h, w).astype(np.float32)
    mapy = uv[:, 1].reshape(h, w).astype(np.float32)
    util = cv2.remap(mascara_util_orto, mapx, mapy, cv2.INTER_NEAREST, borderValue=0)
    return (util == 0).astype(np.uint8), (X0, Y0)


def bloquear_programas(bloqueado: np.ndarray, origem: tuple[float, float], res_mm: float,
                       programas: list[Programa], espessura_mm: float) -> np.ndarray:
    """Marca como bloqueado o material já usado por programas cortados nesta chapa
    (peças que ficaram no lugar não aparecem como furo para a câmera)."""
    B = bloqueado.copy()
    X0, Y0 = origem
    for p in programas:
        try:
            F, (fx, fy) = pegada(p, 0.0, res_mm, espessura_mm)
        except ValueError:
            continue
        i0 = int(round((fy - Y0) / res_mm)); j0 = int(round((fx - X0) / res_mm))
        h, w = F.shape
        a0, b0 = max(0, i0), max(0, j0)
        a1, b1 = min(B.shape[0], i0 + h), min(B.shape[1], j0 + w)
        if a1 > a0 and b1 > b0:
            B[a0:a1, b0:b1] |= F[a0 - i0:a1 - i0, b0 - j0:b1 - j0]
    return B


def encaixar(prog: Programa, bloqueado: np.ndarray, origem: tuple[float, float], res_mm: float,
             angulos: list[float], kerf_mm: float = 1.6, folga_mm: float = 6.0,
             preferencia: str = "canto") -> dict | None:
    """Procura posição e ângulo em que o programa cabe na área livre.

    Retorna {angulo_graus, dx, dy, pontuacao, ...}: aplicar transformar(texto, angulo, dx, dy)."""
    X0, Y0 = origem
    B = bloqueado.astype(np.float32)
    h_b, w_b = B.shape
    centro_livre = None
    if preferencia == "centro":
        ys, xs = np.nonzero(bloqueado == 0)
        if len(xs):
            centro_livre = (xs.mean(), ys.mean())
    melhor = None
    for ang in angulos:
        F, (fx, fy) = pegada(prog, ang, res_mm, kerf_mm + 2 * folga_mm)
        h_f, w_f = F.shape
        if h_f > h_b or w_f > w_b:
            continue
        sobreposicao = correlate(B, F.astype(np.float32), mode="valid", method="fft")
        livres = np.argwhere(sobreposicao < 0.5)
        if len(livres) == 0:
            continue
        if centro_livre is None:
            score = livres[:, 0] + livres[:, 1]               # canto inferior-esquerdo
        else:
            score = np.hypot(livres[:, 1] + w_f / 2 - centro_livre[0], livres[:, 0] + h_f / 2 - centro_livre[1])
        k = int(np.argmin(score))
        a, b = livres[k]
        dx = X0 + b * res_mm - fx
        dy = Y0 + a * res_mm - fy
        cand = {"angulo_graus": float(ang), "dx": float(dx), "dy": float(dy), "pontuacao": float(score[k]),
                "posicoes_livres": int(len(livres))}
        if melhor is None or cand["pontuacao"] < melhor["pontuacao"] - 1e-9:
            melhor = cand
    return melhor


def angulos_candidatos(angulo_chapa: float, passo: float = 90.0) -> list[float]:
    passo = max(1.0, float(passo))
    n = int(round(360.0 / passo))
    return [round(angulo_chapa + i * passo, 4) for i in range(n)]


def desenhar_encaixe(orto: np.ndarray, calib, prog_transformado: Programa, cor=(255, 80, 0)) -> np.ndarray:
    out = orto.copy()
    for p in prog_transformado.polilinhas_corte(passo_mm=1.0):
        q = calib.mm_para_orto_px(p).astype(np.int32)
        cv2.polylines(out, [q.reshape(-1, 1, 2)], False, cor, 2, cv2.LINE_AA)
    for m in prog_transformado.movimentos:
        if m.tipo == 0 and m.muda_xy:
            a, b = calib.mm_para_orto_px([m.p0[:2], m.p1[:2]]).astype(int)
            cv2.line(out, tuple(a), tuple(b), (180, 180, 180), 1, cv2.LINE_AA)
    return out
