"""Cliente da ponte do Mach3 (programa mach3_ponte/ponte_mach3.py rodando no PC do Mach3).

Só usa a biblioteca padrão. Um thread consulta /status a cada `intervalo_s` e guarda
o último estado; os comandos são POST com o token no cabeçalho X-Token.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger("plasmavision.mach3")


class ClienteMach3:
    def __init__(self, url: str, token: str, intervalo_s: float = 0.2, timeout_s: float = 1.5):
        self.url = url.rstrip("/")
        self.token = token
        self.intervalo = intervalo_s
        self.timeout = timeout_s
        self.ultimo: dict = {"conectada": False}
        self._parar = threading.Event()
        threading.Thread(target=self._laco, name="mach3", daemon=True).start()

    def _req(self, metodo: str, caminho: str, corpo: dict | None = None) -> dict:
        dados = None if corpo is None else json.dumps(corpo).encode()
        r = urllib.request.Request(self.url + caminho, data=dados, method=metodo,
                                   headers={"X-Token": self.token, "Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode() or "{}")

    def _laco(self) -> None:
        while not self._parar.is_set():
            try:
                s = self._req("GET", "/status")
                s["conectada"] = True
                self.ultimo = s
            except Exception as e:
                self.ultimo = {"conectada": False, "erro": str(e)}
            time.sleep(self.intervalo)

    def status(self) -> dict:
        return dict(self.ultimo)

    def _cmd(self, caminho: str, corpo: dict | None = None) -> dict:
        try:
            return self._req("POST", caminho, corpo or {})
        except urllib.error.HTTPError as e:
            return {"ok": False, "erro": f"HTTP {e.code}: {e.read().decode(errors='ignore')}"}
        except Exception as e:
            return {"ok": False, "erro": str(e)}

    def cycle_start(self) -> dict: return self._cmd("/cycle_start")
    def feed_hold(self) -> dict: return self._cmd("/feed_hold")
    def parar(self) -> dict: return self._cmd("/stop")
    def mdi(self, cmd: str) -> dict: return self._cmd("/mdi", {"cmd": cmd})

    def carregar(self, nome: str, texto: str) -> dict:
        return self._cmd("/load", {"nome": nome, "gcode": texto})

    def passo_tempo(self, dt: float) -> None:   # compatível com a simulação
        pass
