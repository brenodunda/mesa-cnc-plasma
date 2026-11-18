"""Monitor de área: pausa a máquina se alguém entrar na zona de exclusão.

ATENÇÃO: isto é um recurso de AUXÍLIO, não um dispositivo de segurança certificado
(NR-12 / ISO 13849). O botão de emergência, a proteção física e o procedimento de
trabalho continuam obrigatórios.

Dois modos:
- "pessoa": detector de pessoas em ONNX (formato YOLO: saída [1, 84, N] estilo v8
  ou [1, N, 85] estilo v5/YOLOX), rodando no OpenCV DNN;
- "movimento": subtração de fundo (MOG2) dentro da zona — não precisa de modelo,
  mas também dispara com fumaça e faíscas; use zonas FORA da área de corte.
"""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np


class DetectorPessoas:
    def __init__(self, caminho_modelo: str, tamanho: int = 640, confianca: float = 0.45):
        self.rede = cv2.dnn.readNetFromONNX(str(caminho_modelo))
        self.tamanho = tamanho
        self.confianca = confianca

    def detectar(self, img: np.ndarray) -> list[tuple[int, int, int, int, float]]:
        h, w = img.shape[:2]
        s = self.tamanho
        escala = min(s / w, s / h)
        nw, nh = int(round(w * escala)), int(round(h * escala))
        tela = np.full((s, s, 3), 114, np.uint8)
        tela[:nh, :nw] = cv2.resize(img, (nw, nh))
        blob = cv2.dnn.blobFromImage(tela, 1 / 255.0, (s, s), swapRB=True, crop=False)
        self.rede.setInput(blob)
        out = self.rede.forward()
        out = np.squeeze(out)
        if out.ndim != 2:
            return []
        if out.shape[0] in (84, 85) and out.shape[1] not in (84, 85):   # [84, N] (v8) -> [N, 84]
            out = out.T
        if out.shape[1] == 85:            # v5/YOLOX: cx cy w h obj classes...
            conf = out[:, 4] * out[:, 5]
        else:                             # v8: cx cy w h classes...
            conf = out[:, 4]
        sel = conf >= self.confianca
        caixas, notas = [], []
        for (cx, cy, bw, bh), c in zip(out[sel, :4], conf[sel]):
            x = (cx - bw / 2) / escala
            y = (cy - bh / 2) / escala
            caixas.append([int(x), int(y), int(bw / escala), int(bh / escala)])
            notas.append(float(c))
        idx = cv2.dnn.NMSBoxes(caixas, notas, self.confianca, 0.45) if caixas else []
        return [(*caixas[i], notas[i]) for i in np.array(idx).ravel()]


class DetectorMovimento:
    def __init__(self, area_minima: float = 0.002):
        self.sub = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=32, detectShadows=False)
        self.area_minima = area_minima
        self.quadros = 0
        self.aquecimento = 25          # quadros só para aprender o fundo

    def detectar(self, img: np.ndarray, mascara: np.ndarray | None = None):
        peq = cv2.resize(img, (640, int(640 * img.shape[0] / img.shape[1])))
        fg = self.sub.apply(peq)
        self.quadros += 1
        if self.quadros <= self.aquecimento:
            return []
        if mascara is not None:
            fg[cv2.resize(mascara, (fg.shape[1], fg.shape[0]), interpolation=cv2.INTER_NEAREST) == 0] = 0
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        cont, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        k = img.shape[1] / 640
        amin = self.area_minima * fg.size
        return [(int(x * k), int(y * k), int(w * k), int(h * k), 1.0)
                for x, y, w, h in (cv2.boundingRect(c) for c in cont if cv2.contourArea(c) >= amin)]


class MonitorSeguranca:
    def __init__(self, cfg: dict, pasta_eventos: str | Path):
        self.cfg = cfg
        self.pasta = Path(pasta_eventos); self.pasta.mkdir(parents=True, exist_ok=True)
        self.alarme = False
        self.motivo = ""
        self.deteccoes: list = []
        self.eventos: list[dict] = []
        self._n = 0
        self.modo = cfg["modo"]
        self.detector = None
        self.erro = ""
        if self.modo == "pessoa":
            if Path(cfg["modelo"]).exists():
                try:
                    self.detector = DetectorPessoas(cfg["modelo"], cfg["tamanho_entrada"], cfg["confianca"])
                except Exception as e:  # modelo inválido
                    self.erro = f"modelo de pessoa não carregou ({e}); usando movimento"
            else:
                self.erro = f"modelo {cfg['modelo']} não encontrado; usando movimento"
            if self.detector is None:
                self.modo = "movimento"
        if self.detector is None:
            self.detector = DetectorMovimento(cfg["area_minima_movimento"])

    def mascara_zonas(self, w: int, h: int) -> np.ndarray:
        m = np.zeros((h, w), np.uint8)
        for z in self.cfg["zonas"]:
            pts = (np.array(z, np.float64) * [w, h]).astype(np.int32)
            cv2.fillPoly(m, [pts], 255)
        return m

    def atualizar(self, img: np.ndarray | None, maquina_ativa: bool = True,
                  ignorar: np.ndarray | None = None) -> bool:
        """Processa um quadro. Retorna True se um NOVO alarme disparou neste quadro.

        `ignorar` (255 = ignorar) é a faixa do pórtico na imagem; só vale no modo
        movimento — no modo pessoa uma pessoa perto do pórtico continua contando."""
        if img is None or not self.cfg["ativo"]:
            return False
        self._n += 1
        if self._n % max(1, self.cfg["cada_n_quadros"]):
            return False
        h, w = img.shape[:2]
        zona = self.mascara_zonas(w, h)
        if isinstance(self.detector, DetectorMovimento):
            if ignorar is not None:
                zona = zona.copy(); zona[ignorar > 0] = 0
            caixas = self.detector.detectar(img, zona)
        else:
            caixas = self.detector.detectar(img)
        dentro = []
        for (x, y, bw, bh, c) in caixas:
            x0, y0 = max(0, x), max(0, y)
            x1, y1 = min(w, x + bw), min(h, y + bh)
            if x1 > x0 and y1 > y0 and np.any(zona[y0:y1, x0:x1]):
                dentro.append((x, y, bw, bh, c))
        self.deteccoes = dentro
        if dentro and not self.alarme and (maquina_ativa or not self.cfg["so_com_maquina_ativa"]):
            self.alarme = True
            self.motivo = "pessoa na área" if self.modo == "pessoa" else "movimento na área"
            ts = time.strftime("%Y%m%d-%H%M%S")
            arq = self.pasta / f"alarme-{ts}.jpg"
            cv2.imwrite(str(arq), self.desenhar(img))
            self.eventos.insert(0, {"hora": time.strftime("%d/%m %H:%M:%S"), "motivo": self.motivo,
                                    "imagem": arq.name})
            del self.eventos[50:]
            return True
        return False

    def reconhecer(self) -> None:
        self.alarme = False
        self.motivo = ""

    def desenhar(self, img: np.ndarray) -> np.ndarray:
        out = img.copy()
        h, w = out.shape[:2]
        cor_zona = (0, 0, 255) if self.alarme else (0, 200, 255)
        for z in self.cfg["zonas"]:
            pts = (np.array(z, np.float64) * [w, h]).astype(np.int32)
            cv2.polylines(out, [pts], True, cor_zona, max(2, w // 600))
        for (x, y, bw, bh, c) in self.deteccoes:
            cv2.rectangle(out, (x, y), (x + bw, y + bh), (0, 0, 255), max(2, w // 500))
            cv2.putText(out, f"{c:.2f}", (x, max(20, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        return out
