"""Fontes de imagem com captura em thread (sempre o quadro mais recente).

Tipos: picamera2 (câmera CSI do Raspberry Pi), opencv (USB), rtsp/http (câmera IP
ou celular), arquivo (imagem/vídeo) e simulada.
"""
from __future__ import annotations

import logging
import threading
import time

import cv2
import numpy as np

log = logging.getLogger("plasmavision.camera")


class Camera:
    def __init__(self, nome: str, cfg: dict, simulador=None):
        self.nome = nome
        self.cfg = cfg
        self.sim = simulador
        self.quadro: np.ndarray | None = None
        self.t_quadro = 0.0
        self.erro = ""
        self.fps_medido = 0.0
        self._parar = threading.Event()
        self._fonte = None
        self._thread = threading.Thread(target=self._laco, name=f"cam-{nome}", daemon=True)

    def iniciar(self) -> "Camera":
        self._thread.start()
        return self

    def parar(self) -> None:
        self._parar.set()

    def ler(self) -> tuple[np.ndarray | None, float]:
        q = self.quadro
        return (None if q is None else q.copy()), self.t_quadro

    # ------------------------------------------------------------------
    def _abrir(self):
        tipo = self.cfg["tipo"]
        w, h = self.cfg.get("resolucao", [1280, 720])
        if tipo == "simulada":
            return "sim"
        if tipo == "picamera2":
            from picamera2 import Picamera2  # só existe no Raspberry Pi OS
            cam = Picamera2(int(self.cfg.get("fonte", 0)))
            conf = cam.create_video_configuration(main={"size": (int(w), int(h)), "format": "RGB888"},
                                                  buffer_count=2)
            cam.configure(conf)
            cam.start()
            return cam
        if tipo == "arquivo":
            caminho = str(self.cfg["fonte"])
            img = cv2.imread(caminho)
            if img is not None:
                return ("img", img)
            cap = cv2.VideoCapture(caminho)
            return cap
        fonte = self.cfg.get("fonte", 0)
        if tipo == "opencv" and str(fonte).isdigit():
            fonte = int(fonte)
        cap = cv2.VideoCapture(fonte)
        if tipo == "opencv":
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(w))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(h))
            cap.set(cv2.CAP_PROP_FPS, float(self.cfg.get("fps", 30)))
        if not cap.isOpened():
            raise RuntimeError(f"não abriu a câmera {fonte}")
        return cap

    def _capturar(self) -> np.ndarray | None:
        f = self._fonte
        if f == "sim":
            return self.sim.quadro_topo() if self.nome == "topo" else self.sim.quadro_tocha()
        if isinstance(f, tuple) and f[0] == "img":
            return f[1].copy()
        if hasattr(f, "capture_array"):
            return f.capture_array("main")
        ok, q = f.read()
        if not ok and self.cfg["tipo"] == "arquivo":
            f.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, q = f.read()
        return q if ok else None

    def _laco(self) -> None:
        espera = 1.0 / max(0.5, float(self.cfg.get("fps", 10)))
        falhas = 0
        t_fps, n_fps = time.monotonic(), 0
        while not self._parar.is_set():
            if self._fonte is None:
                try:
                    self._fonte = self._abrir()
                    self.erro = ""
                except Exception as e:
                    self.erro = str(e)
                    log.warning("câmera %s: %s", self.nome, e)
                    time.sleep(3.0)
                    continue
            t0 = time.monotonic()
            try:
                q = self._capturar()
            except Exception as e:
                q, self.erro = None, str(e)
            if q is None:
                falhas += 1
                if falhas > 20:
                    log.warning("câmera %s sem imagem, reabrindo", self.nome)
                    self._fechar(); falhas = 0
                time.sleep(0.1)
                continue
            falhas = 0
            rot = int(self.cfg.get("rotacao", 0))
            if rot == 180:
                q = cv2.rotate(q, cv2.ROTATE_180)
            elif rot == 90:
                q = cv2.rotate(q, cv2.ROTATE_90_CLOCKWISE)
            elif rot == 270:
                q = cv2.rotate(q, cv2.ROTATE_90_COUNTERCLOCKWISE)
            self.quadro, self.t_quadro = q, time.time()
            n_fps += 1
            if time.monotonic() - t_fps >= 2.0:
                self.fps_medido = n_fps / (time.monotonic() - t_fps)
                t_fps, n_fps = time.monotonic(), 0
            resto = espera - (time.monotonic() - t0)
            if resto > 0:
                time.sleep(resto)
        self._fechar()

    def _fechar(self) -> None:
        f, self._fonte = self._fonte, None
        try:
            if hasattr(f, "release"):
                f.release()
            elif hasattr(f, "stop"):
                f.stop()
        except Exception:
            pass
