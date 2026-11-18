"""Calibração da câmera do topo: imagem (px) ↔ coordenadas de máquina (mm).

Usa marcadores ArUco colocados sobre a mesa em posições conhecidas. O assistente
do painel leva a tocha até o centro de cada marcador e lê X/Y do Mach3, então as
posições ficam exatamente no sistema de coordenadas da máquina.

Convenção da ortofoto (imagem "vista de cima" em escala):
    pixel (u, v)  ->  X = xmin + u * s,   Y = ymax - v * s
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np


def dicionario_aruco(nome: str = "DICT_4X4_50"):
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, nome))


def detectar_marcadores(img: np.ndarray, nome_dic: str = "DICT_4X4_50") -> dict[int, np.ndarray]:
    """Retorna {id: centro_px (2,)} dos marcadores encontrados."""
    cinza = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    det = cv2.aruco.ArucoDetector(dicionario_aruco(nome_dic), params)
    cantos, ids, _ = det.detectMarkers(cinza)
    out: dict[int, np.ndarray] = {}
    if ids is None:
        return out
    for c, i in zip(cantos, ids.ravel()):
        out[int(i)] = c.reshape(4, 2).mean(axis=0)
    return out


def gerar_marcador(id_: int, lado_px: int = 600, nome_dic: str = "DICT_4X4_50", borda_px: int = 60) -> np.ndarray:
    """Imagem para imprimir (marcador + borda branca)."""
    m = cv2.aruco.generateImageMarker(dicionario_aruco(nome_dic), id_, lado_px)
    m = cv2.copyMakeBorder(m, borda_px, borda_px, borda_px, borda_px, cv2.BORDER_CONSTANT, value=255)
    cv2.putText(m, f"ID {id_}", (borda_px, borda_px - 15), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 0, 2)
    return m


@dataclass
class Calibracao:
    H: np.ndarray | None = None                 # px (sem distorção) -> mm
    K: np.ndarray | None = None
    dist: np.ndarray | None = None
    tamanho_img: tuple[int, int] | None = None   # (largura, altura)
    regiao_mm: tuple[float, float, float, float] = (-60.0, -60.0, 1610.0, 1110.0)
    escala_mm_px: float = 1.0
    erro_rms_mm: float | None = None
    pontos: dict = field(default_factory=dict)  # {id: {"px": [u,v], "mm": [x,y]}}

    # ---------- construção
    @classmethod
    def de_pontos(cls, px: np.ndarray, mm: np.ndarray, **kw) -> "Calibracao":
        px = np.asarray(px, np.float64).reshape(-1, 2)
        mm = np.asarray(mm, np.float64).reshape(-1, 2)
        if len(px) < 4:
            raise ValueError("são necessários pelo menos 4 pontos")
        metodo = cv2.RANSAC if len(px) > 4 else 0
        H, _ = cv2.findHomography(px, mm, metodo, 3.0)
        if H is None:
            raise ValueError("homografia não encontrada: pontos colineares?")
        c = cls(H=H, **kw)
        c.erro_rms_mm = float(np.sqrt(np.mean(np.sum((c.px_para_mm(px) - mm) ** 2, axis=1))))
        return c

    @property
    def pronta(self) -> bool:
        return self.H is not None

    # ---------- conversões
    def _sem_distorcao(self, px: np.ndarray) -> np.ndarray:
        if self.K is None or self.dist is None:
            return px
        return cv2.undistortPoints(px.reshape(-1, 1, 2), self.K, self.dist, P=self.K).reshape(-1, 2)

    def px_para_mm(self, px) -> np.ndarray:
        p = self._sem_distorcao(np.asarray(px, np.float64).reshape(-1, 2))
        return cv2.perspectiveTransform(p.reshape(-1, 1, 2), self.H).reshape(-1, 2)

    def mm_para_px(self, mm) -> np.ndarray:
        """mm -> px na imagem SEM distorção (suficiente para desenhar sobre o vídeo)."""
        Hi = np.linalg.inv(self.H)
        return cv2.perspectiveTransform(np.asarray(mm, np.float64).reshape(-1, 1, 2), Hi).reshape(-1, 2)

    # ---------- ortofoto
    def matriz_mm_para_orto(self, escala: float | None = None) -> np.ndarray:
        s = escala or self.escala_mm_px
        xmin, _, _, ymax = self.regiao_mm
        return np.array([[1 / s, 0, -xmin / s], [0, -1 / s, ymax / s], [0, 0, 1]], np.float64)

    def tamanho_orto(self, escala: float | None = None) -> tuple[int, int]:
        s = escala or self.escala_mm_px
        xmin, ymin, xmax, ymax = self.regiao_mm
        return int(round((xmax - xmin) / s)), int(round((ymax - ymin) / s))

    def ortofoto(self, img: np.ndarray, escala: float | None = None, interp=cv2.INTER_LINEAR) -> np.ndarray:
        if self.K is not None and self.dist is not None:
            img = cv2.undistort(img, self.K, self.dist)
        M = self.matriz_mm_para_orto(escala) @ self.H
        return cv2.warpPerspective(img, M, self.tamanho_orto(escala), flags=interp,
                                   borderMode=cv2.BORDER_CONSTANT, borderValue=0)

    def orto_px_para_mm(self, uv, escala: float | None = None) -> np.ndarray:
        s = escala or self.escala_mm_px
        uv = np.asarray(uv, np.float64).reshape(-1, 2)
        xmin, _, _, ymax = self.regiao_mm
        return np.column_stack([xmin + uv[:, 0] * s, ymax - uv[:, 1] * s])

    def mm_para_orto_px(self, mm, escala: float | None = None) -> np.ndarray:
        s = escala or self.escala_mm_px
        mm = np.asarray(mm, np.float64).reshape(-1, 2)
        xmin, _, _, ymax = self.regiao_mm
        return np.column_stack([(mm[:, 0] - xmin) / s, (ymax - mm[:, 1]) / s])

    # ---------- persistência
    def salvar(self, caminho: str | Path) -> None:
        d = {
            "H": None if self.H is None else self.H.tolist(),
            "K": None if self.K is None else self.K.tolist(),
            "dist": None if self.dist is None else self.dist.ravel().tolist(),
            "tamanho_img": self.tamanho_img,
            "regiao_mm": list(self.regiao_mm),
            "escala_mm_px": self.escala_mm_px,
            "erro_rms_mm": self.erro_rms_mm,
            "pontos": {str(k): v for k, v in self.pontos.items()},
        }
        Path(caminho).write_text(json.dumps(d, indent=1), encoding="utf-8")

    @classmethod
    def carregar(cls, caminho: str | Path) -> "Calibracao":
        d = json.loads(Path(caminho).read_text(encoding="utf-8"))
        arr = lambda v: None if v is None else np.array(v, np.float64)
        return cls(H=arr(d.get("H")), K=arr(d.get("K")), dist=arr(d.get("dist")),
                   tamanho_img=tuple(d["tamanho_img"]) if d.get("tamanho_img") else None,
                   regiao_mm=tuple(d.get("regiao_mm", (-60, -60, 1610, 1110))),
                   escala_mm_px=float(d.get("escala_mm_px", 1.0)),
                   erro_rms_mm=d.get("erro_rms_mm"),
                   pontos={int(k): v for k, v in d.get("pontos", {}).items()})


def calibrar_intrinseca(imagens: list[np.ndarray], padrao=(9, 6), lado_mm: float = 25.0):
    """Calibração da lente com tabuleiro de xadrez (opcional, melhora lentes grande-angulares).
    Retorna (K, dist, erro_rms_px)."""
    objp = np.zeros((padrao[0] * padrao[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0:padrao[0], 0:padrao[1]].T.reshape(-1, 2) * lado_mm
    obj, img_pts, tam = [], [], None
    for im in imagens:
        g = im if im.ndim == 2 else cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
        tam = g.shape[::-1]
        ok, cantos = cv2.findChessboardCorners(g, padrao)
        if ok:
            cantos = cv2.cornerSubPix(g, cantos, (11, 11), (-1, -1),
                                      (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-3))
            obj.append(objp)
            img_pts.append(cantos)
    if len(obj) < 5:
        raise ValueError(f"tabuleiro encontrado em só {len(obj)} imagens; use pelo menos 5")
    rms, K, dist, _, _ = cv2.calibrateCamera(obj, img_pts, tam, None, None)
    return K, dist, rms
