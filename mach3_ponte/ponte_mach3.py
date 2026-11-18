"""Ponte HTTP para o Mach3 — roda no PC (Windows) onde o Mach3 está aberto.

O Raspberry Pi (PlasmaVision) conversa com esta ponte pela rede:
    GET  /status          -> posição, se está movendo, se o programa está rodando
    POST /cycle_start     -> botão Cycle Start
    POST /feed_hold       -> botão Feed Hold (pausa)
    POST /stop            -> botão Stop
    POST /load            -> {"nome": "...", "gcode": "..."} grava e carrega o arquivo no Mach3
    POST /mdi             -> {"cmd": "G53 G0 Z0"} executa um comando MDI
Todas as rotas pedem o cabeçalho X-Token igual ao --token.

O Mach3 publica a automação COM "Mach4.Document" (nome histórico); daí se pega o
objeto de script, que tem as mesmas funções dos macros VB (DoOEMButton, GetOEMDRO,
GetOEMLED, IsMoving, Code, LoadFile).

Uso:
    pip install pywin32
    python ponte_mach3.py --token MEU_TOKEN --porta 8765
    python ponte_mach3.py --simular        # sem Mach3, para testar a rede
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Códigos OEM do Mach3 (confira na lista "OEM Codes" da sua versão; dá para trocar em ponte_config.json)
CODIGOS = {
    "botao_cycle_start": 1000,
    "botao_feed_hold": 1001,
    "botao_stop": 1003,
    "dro_x": 800, "dro_y": 801, "dro_z": 802,              # coordenadas de trabalho (DROs principais)
    "dro_maq_x": 83, "dro_maq_y": 84, "dro_maq_z": 85,     # coordenadas de máquina
    "led_rodando": 804,                                     # programa em execução
    "usar_coordenadas_maquina": True,                       # a calibração da câmera usa coordenadas de máquina
}


class Mach3COM:
    """Acesso ao Mach3 por COM. Todas as chamadas passam por um único thread (COM é apartment-threaded)."""

    def __init__(self, codigos: dict):
        self.c = codigos
        self.scr = None
        self.trava = threading.Lock()
        self.erro = ""

    def _conectar(self):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        mach = win32com.client.GetObject(Class="Mach4.Document")
        for nome in ("GetScriptDispatch", "GetScriptObject"):
            f = getattr(mach, nome, None)
            if f is not None:
                try:
                    self.scr = f()
                    break
                except Exception:
                    continue
        if self.scr is None:
            raise RuntimeError("Mach3 aberto, mas o objeto de script não respondeu")

    def _chamar(self, fn):
        with self.trava:
            for tentativa in (1, 2):
                try:
                    if self.scr is None:
                        self._conectar()
                    r = fn(self.scr)
                    self.erro = ""
                    return r
                except Exception as e:
                    self.scr = None
                    self.erro = str(e)
                    if tentativa == 2:
                        raise

    def status(self) -> dict:
        c = self.c

        def f(s):
            if c["usar_coordenadas_maquina"]:
                x, y, z = s.GetOEMDRO(c["dro_maq_x"]), s.GetOEMDRO(c["dro_maq_y"]), s.GetOEMDRO(c["dro_maq_z"])
            else:
                x, y, z = s.GetOEMDRO(c["dro_x"]), s.GetOEMDRO(c["dro_y"]), s.GetOEMDRO(c["dro_z"])
            try:
                movendo = bool(s.IsMoving())
            except Exception:
                movendo = None
            try:
                rodando = bool(s.GetOEMLED(c["led_rodando"]))
            except Exception:
                rodando = None
            return {"x": float(x), "y": float(y), "z": float(z), "movendo": movendo, "rodando": rodando}
        return self._chamar(f)

    def botao(self, codigo: int) -> None:
        self._chamar(lambda s: s.DoOEMButton(int(codigo)))

    def mdi(self, cmd: str) -> None:
        self._chamar(lambda s: s.Code(cmd))

    def carregar(self, caminho: str) -> None:
        self._chamar(lambda s: s.LoadFile(caminho))


class Mach3Simulado:
    def __init__(self):
        self.x = self.y = self.z = 0.0
        self.movendo = False
        self.rodando = False
        self.erro = ""

    def status(self): return {"x": self.x, "y": self.y, "z": self.z, "movendo": self.movendo, "rodando": self.rodando}
    def botao(self, codigo): self.rodando = codigo == CODIGOS["botao_cycle_start"]
    def mdi(self, cmd): pass
    def carregar(self, caminho): pass


class Movendo:
    """Se IsMoving() não existir na sua versão, deduz 'movendo' pela variação da posição."""

    def __init__(self):
        self.ultimo = None
        self.t_mudou = 0.0

    def __call__(self, st: dict) -> bool:
        p = (round(st["x"], 3), round(st["y"], 3), round(st["z"], 3))
        if p != self.ultimo:
            self.ultimo, self.t_mudou = p, time.monotonic()
        return time.monotonic() - self.t_mudou < 0.25


def criar_servidor(mach, token: str, pasta: Path, codigos: dict, porta: int) -> ThreadingHTTPServer:
    detector_mov = Movendo()

    class H(BaseHTTPRequestHandler):
        server_version = "PonteMach3/1.0"

        def log_message(self, fmt, *a):
            if not self.path.startswith("/status"):
                print(time.strftime("%H:%M:%S"), self.client_address[0], fmt % a)

        def _resp(self, cod: int, d: dict):
            b = json.dumps(d).encode()
            self.send_response(cod)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _ok_token(self) -> bool:
            if self.headers.get("X-Token") != token:
                self._resp(401, {"ok": False, "erro": "token inválido"})
                return False
            return True

        def _corpo(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

        def do_GET(self):
            if not self._ok_token():
                return
            if self.path.startswith("/status"):
                try:
                    st = mach.status()
                    if st.get("movendo") is None:
                        st["movendo"] = detector_mov(st)
                    self._resp(200, st)
                except Exception as e:
                    self._resp(503, {"ok": False, "erro": f"Mach3 não respondeu: {e}"})
            else:
                self._resp(404, {"ok": False})

        def do_POST(self):
            if not self._ok_token():
                return
            try:
                d = self._corpo()
                if self.path == "/cycle_start":
                    mach.botao(codigos["botao_cycle_start"])
                elif self.path == "/feed_hold":
                    mach.botao(codigos["botao_feed_hold"])
                elif self.path == "/stop":
                    mach.botao(codigos["botao_stop"])
                elif self.path == "/mdi":
                    mach.mdi(str(d["cmd"]))
                elif self.path == "/load":
                    nome = Path(str(d.get("nome", "job.tap"))).name
                    caminho = pasta / nome
                    caminho.write_text(str(d["gcode"]), encoding="utf-8")
                    try:
                        mach.carregar(str(caminho))
                        self._resp(200, {"ok": True, "arquivo": str(caminho)})
                    except Exception as e:
                        self._resp(200, {"ok": True, "arquivo": str(caminho),
                                         "aviso": f"arquivo salvo; carregue no Mach3 à mão ({e})"})
                    return
                else:
                    self._resp(404, {"ok": False}); return
                self._resp(200, {"ok": True})
            except Exception as e:
                self._resp(500, {"ok": False, "erro": str(e)})

    return ThreadingHTTPServer(("0.0.0.0", porta), H)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--porta", type=int, default=int(os.environ.get("PONTE_PORTA", 8765)))
    ap.add_argument("--token", default=os.environ.get("PONTE_TOKEN", ""))
    ap.add_argument("--pasta", default=os.environ.get("PONTE_PASTA", r"C:\PlasmaVision\jobs"))
    ap.add_argument("--config", default=str(Path(__file__).with_name("ponte_config.json")))
    ap.add_argument("--simular", action="store_true")
    a = ap.parse_args()
    if not a.token:
        raise SystemExit("defina --token (o mesmo do config.yaml do Raspberry)")
    codigos = dict(CODIGOS)
    if Path(a.config).exists():
        codigos.update(json.loads(Path(a.config).read_text(encoding="utf-8")))
    pasta = Path(a.pasta); pasta.mkdir(parents=True, exist_ok=True)
    mach = Mach3Simulado() if a.simular else Mach3COM(codigos)
    srv = criar_servidor(mach, a.token, pasta, codigos, a.porta)
    print(f"Ponte Mach3 em http://0.0.0.0:{a.porta}  (jobs em {pasta}){'  [SIMULADO]' if a.simular else ''}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
