"""Simulação da mesa, das câmeras e do Mach3 — para testar tudo sem a máquina.

`python -m plasmavision --simular` usa estas classes no lugar das câmeras reais e
da ponte do Mach3. A simulação executa o G-code enviado (parando nos M0), move o
pórtico na imagem e tem um "operador" que acende e solta a tocha sozinho.
"""
from __future__ import annotations

import math
import threading
import time

import cv2
import numpy as np

from .calibracao import dicionario_aruco
from .gcode import interpretar


class MaquinaSimulada:
    """Imita a ponte do Mach3: executa o programa com M0, feed hold e stop."""

    def __init__(self, acelerar: float = 4.0, operador: bool = True):
        self.x = self.y = 0.0
        self.z = 0.0
        self.rodando = False          # programa em ciclo (pode estar parado num M0)
        self.movendo = False
        self.pausado = False
        self.arco = False
        self.operador = operador
        self.acelerar = acelerar
        self.passos: list = []        # [(tipo, dados)]
        self.i = 0
        self.nome = ""
        self._t_m0 = None
        self._trava = threading.Lock()
        self.cortes: list = []        # trechos cortados (para desenhar o "risco" na chapa)

    # ---- interface igual à do ClienteMach3
    def status(self) -> dict:
        return {"conectada": True, "x": round(self.x, 3), "y": round(self.y, 3), "z": round(self.z, 3),
                "movendo": self.movendo, "rodando": self.rodando, "programa": self.nome,
                "linha": self.i, "simulado": True}

    def carregar(self, nome: str, texto: str) -> dict:
        prog = interpretar(texto)
        passos = []
        pausas = {p["mov_idx"]: p for p in prog.pausas}
        for k, m in enumerate(prog.movimentos + [None]):
            if k in pausas:
                passos.append(("m0", pausas[k]["tipo"]))
            if m is None:
                break
            pts = m.pontos(2.0)
            z0, z1 = m.p0[2], m.p1[2]
            zs = np.linspace(z0, z1, len(pts))
            vel = 10000.0 if m.tipo == 0 else 1800.0
            passos.append(("mov", np.column_stack([pts, zs]), vel, m.tipo))
        with self._trava:
            self.passos, self.i, self.nome = passos, 0, nome
            self.rodando = self.pausado = self.movendo = False
        return {"ok": True, "passos": len(passos)}

    def cycle_start(self) -> dict:
        with self._trava:
            if not self.passos:
                return {"ok": False, "erro": "nenhum programa"}
            if self.i >= len(self.passos):
                self.i = 0
            if self.i < len(self.passos) and self.passos[self.i][0] == "m0":
                self.i += 1
            self.rodando, self.pausado = True, False
        return {"ok": True}

    def feed_hold(self) -> dict:
        self.pausado = True
        self.movendo = False
        return {"ok": True}

    def parar(self) -> dict:
        self.rodando = self.pausado = self.movendo = False
        self.i = 0
        return {"ok": True}

    def mdi(self, cmd: str) -> dict:
        return {"ok": True, "eco": cmd}

    # ---- execução
    def passo_tempo(self, dt: float) -> None:
        with self._trava:
            if not self.rodando or self.pausado or self.i >= len(self.passos):
                self.movendo = False
                if self.rodando and self.i >= len(self.passos):
                    self.rodando = False
                self._operador(dt)
                return
            p = self.passos[self.i]
            if p[0] == "m0":
                self.movendo = False
                self._operador(dt, p[1])
                return
            _, pts, vel, tipo = p
            alcance = vel / 60.0 * dt * self.acelerar
            atual = np.array([self.x, self.y, self.z])
            while alcance > 0 and len(pts):
                d = float(np.linalg.norm(pts[0] - atual))
                if d <= alcance:
                    if tipo != 0 and self.arco:
                        self.cortes.append((atual[:2].copy(), pts[0][:2].copy()))
                    atual = pts[0].copy(); pts = pts[1:]; alcance -= d
                else:
                    novo = atual + (pts[0] - atual) * (alcance / d)
                    if tipo != 0 and self.arco:
                        self.cortes.append((atual[:2].copy(), novo[:2].copy()))
                    atual = novo; alcance = 0
            self.x, self.y, self.z = atual
            self.movendo = True
            if len(pts) == 0:
                self.i += 1
            else:
                self.passos[self.i] = ("mov", pts, vel, tipo)
            del self.cortes[:-20000]

    def _operador(self, dt: float, esperado: str | None = None) -> None:
        if not self.operador or esperado is None:
            self._t_m0 = None
            return
        self._t_m0 = (self._t_m0 or 0.0) + dt
        if esperado == "acender" and self._t_m0 > 1.0:
            self.arco = True
        if esperado == "soltar" and self._t_m0 > 0.8:
            self.arco = False


class MesaSimulada:
    """Desenha o que as câmeras veriam: ortofoto sintética -> perspectiva da câmera."""

    def __init__(self, cfg: dict, maquina: MaquinaSimulada):
        self.cfg = cfg
        self.maq = maquina
        xmin, ymin, xmax, ymax = cfg["mesa"]["regiao_orto_mm"]
        self.regiao = (xmin, ymin, xmax, ymax)
        self.w, self.h = int(xmax - xmin), int(ymax - ymin)
        self.tam_cam = (1600, 1000)
        # homografia ortofoto(px) -> câmera(px), com perspectiva leve
        src = np.float32([[0, 0], [self.w, 0], [self.w, self.h], [0, self.h]])
        dst = np.float32([[95, 70], [1525, 52], [1560, 958], [60, 940]])
        self.H_orto_cam = cv2.getPerspectiveTransform(src, dst)
        self.chapas = [self._retangulo(120, 80, 1180, 860, 2.2)]
        self.pessoa = False
        self.fundo = self._fundo()

    def _retangulo(self, x, y, w, h, ang):
        t = math.radians(ang)
        R = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
        pts = np.array([[0, 0], [w, 0], [w, h], [0, h]], float) @ R.T + [x, y]
        return pts

    def usar_retalho(self):
        """Troca a chapa por um retalho em L (para testar o encaixe)."""
        L = np.array([[200, 100], [1300, 100], [1300, 420], [700, 420], [700, 900], [200, 900]], float)
        self.chapas = [L]

    def mm_para_orto(self, pts):
        xmin, _, _, ymax = self.regiao
        p = np.asarray(pts, float).reshape(-1, 2)
        return np.column_stack([p[:, 0] - xmin, ymax - p[:, 1]])

    def _fundo(self):
        img = np.full((self.h, self.w, 3), (40, 42, 45), np.uint8)
        # ripas da mesa (barras de 3 mm a cada 80 mm, cor de ferrugem)
        for x in np.arange(-40, 1600, 80):
            u = int(self.mm_para_orto([[x, 0]])[0, 0])
            cv2.rectangle(img, (u - 2, 0), (u + 2, self.h), (45, 75, 125), -1)
        # marcadores ArUco de calibração (80 mm)
        dic = dicionario_aruco(self.cfg["calibracao"]["dicionario_aruco"])
        for i, (x, y) in self.cfg["calibracao"]["marcadores_mm"].items():
            m = cv2.aruco.generateImageMarker(dic, int(i), 80)
            m = cv2.copyMakeBorder(m, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
            u, v = self.mm_para_orto([[x, y]])[0].astype(int)
            h2 = m.shape[0] // 2
            y0, x0 = v - h2, u - h2
            if 0 <= y0 and y0 + m.shape[0] <= self.h and 0 <= x0 and x0 + m.shape[1] <= self.w:
                img[y0:y0 + m.shape[0], x0:x0 + m.shape[1]] = cv2.cvtColor(m, cv2.COLOR_GRAY2BGR)
        return img

    def orto_vazia(self) -> np.ndarray:
        return self.fundo.copy()

    def quadro_topo(self, com_portico: bool = True) -> np.ndarray:
        img = self.fundo.copy()
        for ch in self.chapas:
            cv2.fillPoly(img, [self.mm_para_orto(ch).astype(np.int32)], (150, 152, 156))
        # risco do corte (kerf escuro)
        for a, b in self.maq.cortes[-6000:]:
            pa, pb = self.mm_para_orto([a, b]).astype(int)
            cv2.line(img, tuple(pa), tuple(pb), (25, 25, 25), 2)
        cam = cv2.warpPerspective(img, self.H_orto_cam, self.tam_cam, borderValue=(30, 30, 30))
        if com_portico:
            cam = self._portico(cam)
        if self.pessoa:
            cv2.ellipse(cam, (1560, 500), (60, 160), 0, 0, 360, (60, 90, 160), -1)
            cv2.circle(cam, (1560, 320), 45, (120, 150, 200), -1)
        ruido = np.random.default_rng().normal(0, 3, cam.shape).astype(np.int16)
        return np.clip(cam.astype(np.int16) + ruido, 0, 255).astype(np.uint8)

    def _portico(self, cam):
        x = self.maq.x
        faixa = self.cfg["mesa"]["mascara_portico_mm"]
        x0, x1 = x + faixa[0] + 60, x - 120
        cantos = np.array([[x0, -60], [x1, -60], [x1, 1110], [x0, 1110]], float)
        p = cv2.perspectiveTransform(self.mm_para_orto(cantos).reshape(-1, 1, 2).astype(np.float32),
                                     self.H_orto_cam).reshape(-1, 2).astype(np.int32)
        cv2.fillPoly(cam, [p], (55, 60, 70))
        t = cv2.perspectiveTransform(self.mm_para_orto([[self.maq.x, self.maq.y]]).reshape(-1, 1, 2).astype(np.float32),
                                     self.H_orto_cam).reshape(2).astype(int)
        cv2.rectangle(cam, (t[0] - 18, t[1] - 30), (t[0] + 18, t[1] + 30), (40, 120, 230), -1)
        if self.maq.arco:
            cv2.circle(cam, tuple(t), 14, (255, 255, 255), -1)
            cv2.circle(cam, tuple(t), 26, (255, 200, 120), 3)
        return cam

    def quadro_tocha(self) -> np.ndarray:
        img = np.full((480, 640, 3), 18, np.uint8)
        cv2.rectangle(img, (290, 0), (350, 230), (60, 60, 70), -1)     # corpo
        cv2.rectangle(img, (305, 230), (335, 290), (30, 90, 160), -1)  # bico
        if self.maq.arco:
            cv2.ellipse(img, (320, 330), (70, 50), 0, 0, 360, (255, 255, 255), -1)
            cv2.ellipse(img, (320, 330), (110, 80), 0, 0, 360, (255, 220, 180), 6)
        else:
            cv2.line(img, (200, 340), (440, 340), (70, 70, 70), 3)        # chapa
        return img
