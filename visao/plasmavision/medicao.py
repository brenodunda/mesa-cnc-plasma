"""Medição de peças cortadas pela câmera do topo.

A peça é colocada sobre as ripas (ou sobre uma placa de fundo claro) dentro do campo
da câmera. A ortofoto em escala fina dá o contorno em mm; ele é alinhado ao desenho
(DXF) ou ao contorno do programa e comparado: dimensões, furos e desvio do contorno.

Precisão esperada com a Câmera 3 Wide a ~1 m da chapa: ±0,5 a 1 mm. Para medir melhor,
use uma estação de medição com câmera mais perto (mesmo código, outra calibração).
"""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np


# ---------------------------------------------------------------- referência
def _anel_fechado(pts: np.ndarray, tol: float = 0.5) -> bool:
    return len(pts) > 2 and float(np.hypot(*(pts[0] - pts[-1]))) <= tol


def _area(p: np.ndarray) -> float:
    x, y = p[:, 0], p[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _circulo(p: np.ndarray) -> tuple[float, float, float] | None:
    """Se o anel for um círculo, devolve (cx, cy, diâmetro)."""
    c = p.mean(axis=0)
    r = np.hypot(*(p - c).T)
    if r.mean() > 0 and r.std() / r.mean() < 0.02:
        return float(c[0]), float(c[1]), float(2 * r.mean())
    return None


def _encadear(trechos: list[np.ndarray], tol: float = 0.05) -> list[np.ndarray]:
    """Junta trechos abertos (linhas, arcos) pelas pontas até formar anéis."""
    abertos = [t for t in trechos if len(t) > 1]
    aneis = []
    while abertos:
        atual = abertos.pop(0)
        mudou = True
        while mudou and not _anel_fechado(atual, tol):
            mudou = False
            for i, t in enumerate(abertos):
                if np.hypot(*(t[0] - atual[-1])) <= tol:
                    atual = np.vstack([atual, t[1:]]); abertos.pop(i); mudou = True; break
                if np.hypot(*(t[-1] - atual[-1])) <= tol:
                    atual = np.vstack([atual, t[::-1][1:]]); abertos.pop(i); mudou = True; break
        if _anel_fechado(atual, tol):
            aneis.append(atual)
    return aneis


def referencia_de_aneis(aneis: list[np.ndarray]) -> dict:
    aneis = [a for a in aneis if len(a) > 2]
    if not aneis:
        raise ValueError("nenhum contorno fechado na referência")
    aneis.sort(key=_area, reverse=True)
    externo = aneis[0]
    furos, recortes = [], []
    for a in aneis[1:]:
        c = _circulo(a)
        (furos.append(c) if c else recortes.append(a))
    return {"externo": externo, "furos": furos, "recortes": recortes}


def referencia_de_dxf(caminho: str | Path) -> dict:
    import ezdxf
    from ezdxf import path as ezpath
    doc = ezdxf.readfile(str(caminho))
    trechos, aneis = [], []
    for e in doc.modelspace():
        if e.dxftype() not in ("LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE", "SPLINE", "ELLIPSE"):
            continue
        try:
            p = ezpath.make_path(e)
        except Exception:
            continue
        pts = np.array([(v.x, v.y) for v in p.flattening(0.05)], np.float64)
        if len(pts) < 2:
            continue
        (aneis if _anel_fechado(pts, 1e-3) else trechos).append(pts)
    aneis += _encadear(trechos)
    return referencia_de_aneis(aneis)


def referencia_de_programa(prog) -> dict:
    """Usa a trajetória do programa (já compensada pelo kerf no CAM) como desenho."""
    aneis = []
    for p in prog.polilinhas_corte(passo_mm=0.5):
        # descarta a entrada: o anel é o trecho que volta perto de um ponto anterior
        for i in range(len(p)):
            d = np.hypot(*(p[i + 2:] - p[i]).T) if i + 2 < len(p) else np.array([])
            j = np.nonzero(d < 1.0)[0]
            if len(j):
                aneis.append(p[i:i + 2 + int(j[-1]) + 1]); break
        else:
            aneis.append(p)
    return referencia_de_aneis(aneis)


# ---------------------------------------------------------------- medição
def segmentar(orto: np.ndarray, vazia: np.ndarray | None, metodo: str = "diferenca") -> np.ndarray:
    if metodo == "diferenca" and vazia is not None:
        a = cv2.cvtColor(cv2.GaussianBlur(orto, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.int16)
        b = cv2.cvtColor(cv2.GaussianBlur(vazia, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.int16)
        d = np.abs(a - b)
        dist = np.clip(0.6 * d[..., 0] + 1.2 * d[..., 1] + 1.2 * d[..., 2], 0, 255).astype(np.uint8)
        # Otsu põe o limiar no meio do degrau da borda (borda real), não no pé do borrão
        _, m = cv2.threshold(dist, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    else:
        g = cv2.cvtColor(orto, cv2.COLOR_BGR2GRAY)
        _, m = cv2.threshold(cv2.GaussianBlur(g, (5, 5), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if np.count_nonzero(m) > m.size / 2:
            m = 255 - m
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return m


def _alinhar(pts: np.ndarray, ref_ext: np.ndarray):
    """Transformação rígida que leva os pontos medidos (mm) ao sistema do desenho.
    Testa as 4 orientações do retângulo mínimo e espelhamento; fica a de menor erro."""
    rm = cv2.minAreaRect(pts.astype(np.float32))
    rr = cv2.minAreaRect(ref_ext.astype(np.float32))
    cm, cr = np.array(rm[0]), np.array(rr[0])
    melhor = None
    for espelho in (False, True):
        p = pts.copy()
        if espelho:
            p[:, 0] = 2 * cm[0] - p[:, 0]
        r_m = cv2.minAreaRect(p.astype(np.float32))
        for k in range(4):
            ang = math.radians(rr[2] - r_m[2] + 90 * k)
            R = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]])
            q = (p - np.array(r_m[0])) @ R.T + cr
            erro = float(np.mean(_dist_pontos_poligono(q[::max(1, len(q) // 400)], ref_ext)))
            if melhor is None or erro < melhor[0]:
                melhor = (erro, espelho, R, np.array(r_m[0]), cr, cm)
    return melhor


def _dist_pontos_poligono(p: np.ndarray, poli: np.ndarray) -> np.ndarray:
    a = poli
    b = np.roll(poli, -1, axis=0)
    ab = b - a
    L2 = np.maximum((ab ** 2).sum(axis=1), 1e-12)
    ap = p[:, None, :] - a[None, :, :]
    t = np.clip((ap * ab[None]).sum(axis=2) / L2[None], 0, 1)
    proj = a[None] + t[..., None] * ab[None]
    return np.sqrt(((p[:, None, :] - proj) ** 2).sum(axis=2)).min(axis=1)


def medir(orto: np.ndarray, escala_mm_px: float, orto_para_mm, vazia: np.ndarray | None = None,
          referencia: dict | None = None, tolerancia_mm: float = 1.0, metodo: str = "diferenca",
          roi_px: tuple[int, int, int, int] | None = None) -> dict:
    """orto_para_mm: função (N,2 px da ortofoto) -> (N,2 mm)."""
    img, viz = orto, vazia
    ox = oy = 0
    if roi_px:
        x, y, w, h = roi_px
        img = orto[y:y + h, x:x + w]
        viz = None if vazia is None else vazia[y:y + h, x:x + w]
        ox, oy = x, y
    m = segmentar(img, viz, metodo)
    cont, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hier is None:
        raise ValueError("nenhuma peça encontrada")
    hier = hier[0]
    externos = [i for i in range(len(cont)) if hier[i][3] == -1]
    i_ext = max(externos, key=lambda i: cv2.contourArea(cont[i]))
    para_mm = lambda c: orto_para_mm(c.reshape(-1, 2).astype(np.float64) + [ox, oy])
    ext_mm = para_mm(cont[i_ext])
    furos_mm = []
    f = hier[i_ext][2]
    while f != -1:
        if cv2.contourArea(cont[f]) * escala_mm_px ** 2 > 3.0:
            furos_mm.append(para_mm(cont[f]))
        f = hier[f][0]
    rect = cv2.minAreaRect(ext_mm.astype(np.float32))
    L, W = sorted(rect[1], reverse=True)
    res = {
        "dimensoes_mm": [round(L, 2), round(W, 2)],
        "area_mm2": round(_area(ext_mm), 1),
        "furos": [],
        "contorno_mm": ext_mm[:: max(1, len(ext_mm) // 300)].round(2).tolist(),
        "escala_mm_px": escala_mm_px,
    }
    for fm in furos_mm:
        c = fm.mean(axis=0)
        d_eq = 2 * math.sqrt(_area(fm) / math.pi) - 0.5 * escala_mm_px
        res["furos"].append({"centro_mm": c.round(2).tolist(), "diametro_mm": round(d_eq, 2)})
    if referencia is not None:
        ref_ext = referencia["externo"]
        erro, esp, R, cm, cr, cm0 = _alinhar(ext_mm, ref_ext)

        def leva(p):
            p = np.asarray(p, np.float64).reshape(-1, 2).copy()
            if esp:
                p[:, 0] = 2 * cm0[0] - p[:, 0]
            return (p - cm) @ R.T + cr

        ext_al = leva(ext_mm)
        d = _dist_pontos_poligono(ext_al, ref_ext)
        rr = cv2.minAreaRect(ref_ext.astype(np.float32))
        Lr, Wr = sorted(rr[1], reverse=True)
        comp = {
            "dimensoes_ref_mm": [round(Lr, 2), round(Wr, 2)],
            "desvio_dimensoes_mm": [round(L - Lr, 2), round(W - Wr, 2)],
            "desvio_contorno_medio_mm": round(float(d.mean()), 2),
            "desvio_contorno_max_mm": round(float(d.max()), 2),
            "espelhada": bool(esp),
            "furos": [],
        }
        usados = set()
        for fm in furos_mm:
            c_al = leva(fm.mean(axis=0))[0]
            d_eq = 2 * math.sqrt(_area(fm) / math.pi) - 0.5 * escala_mm_px
            if not referencia["furos"]:
                continue
            dists = [math.hypot(c_al[0] - fx, c_al[1] - fy) for fx, fy, _ in referencia["furos"]]
            j = int(np.argmin(dists))
            usados.add(j)
            fx, fy, fd = referencia["furos"][j]
            comp["furos"].append({"diametro_ref_mm": round(fd, 2), "diametro_mm": round(d_eq, 2),
                                  "desvio_diametro_mm": round(d_eq - fd, 2),
                                  "desvio_posicao_mm": round(dists[j], 2)})
        comp["furos_faltando"] = len(referencia["furos"]) - len(usados)
        piores = [abs(x) for x in comp["desvio_dimensoes_mm"]] + [comp["desvio_contorno_max_mm"]] + \
                 [abs(f["desvio_diametro_mm"]) for f in comp["furos"]] + [f["desvio_posicao_mm"] for f in comp["furos"]]
        comp["aprovada"] = bool(max(piores) <= tolerancia_mm and comp["furos_faltando"] == 0)
        comp["tolerancia_mm"] = tolerancia_mm
        res["comparacao"] = comp
    return res


def desenhar_medicao(orto: np.ndarray, res: dict, mm_para_orto) -> np.ndarray:
    out = orto.copy()
    ext = mm_para_orto(np.array(res["contorno_mm"])).astype(np.int32)
    ok = res.get("comparacao", {}).get("aprovada", True)
    cor = (0, 180, 0) if ok else (0, 0, 255)
    cv2.polylines(out, [ext.reshape(-1, 1, 2)], True, cor, 2, cv2.LINE_AA)
    for f in res["furos"]:
        c = mm_para_orto(np.array([f["centro_mm"]]))[0].astype(int)
        cv2.drawMarker(out, tuple(c), cor, cv2.MARKER_CROSS, 18, 2)
        cv2.putText(out, f"d{f['diametro_mm']:.1f}", (int(c[0]) + 8, int(c[1]) - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, cor, 2)
    L, W = res["dimensoes_mm"]
    x0, y0 = ext.min(axis=0)
    cv2.putText(out, f"{L:.1f} x {W:.1f} mm", (int(x0), max(20, int(y0) - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, cor, 2)
    return out
