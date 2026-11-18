"""Painel web (FastAPI): vídeo ao vivo, comandos e assistentes.

Acesso remoto: use Tailscale (ver docs/acesso-remoto.md). Quem está fora das
`redes_de_comando` pode ver tudo e PAUSAR/PARAR, mas não pode mandar a máquina andar.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import secrets
import tempfile
import time
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from ..config import caminho_dados

ESTATICO = Path(__file__).parent / "static"


def criar_app(sistema) -> FastAPI:
    cfg = sistema.cfg
    app = FastAPI(title="PlasmaVision", docs_url=None, redoc_url=None)
    basic = HTTPBasic(auto_error=False)
    redes = [ipaddress.ip_network(r) for r in cfg["web"]["redes_de_comando"]]

    def autenticar(cred: HTTPBasicCredentials | None = Depends(basic)):
        senha = cfg["web"].get("senha") or ""
        if not senha:
            return
        if cred is None or not (secrets.compare_digest(cred.username, cfg["web"]["usuario"]) and
                                secrets.compare_digest(cred.password, senha)):
            raise HTTPException(401, "login necessário", headers={"WWW-Authenticate": "Basic"})

    def pode_comandar(req: Request) -> bool:
        try:
            ip = ipaddress.ip_address(req.client.host)
        except Exception:
            return False
        return any(ip in r for r in redes)

    def exigir_local(req: Request):
        if not pode_comandar(req):
            raise HTTPException(403, "comando de movimento só pela rede da oficina (redes_de_comando)")

    def tratar(f, *a, **k):
        try:
            return f(*a, **k)
        except (RuntimeError, ValueError) as e:
            raise HTTPException(400, str(e))

    A = [Depends(autenticar)]

    @app.get("/", response_class=HTMLResponse, dependencies=A)
    def inicio():
        return (ESTATICO / "index.html").read_text(encoding="utf-8")

    @app.get("/stream/{cam}", dependencies=A)
    async def stream(cam: str):
        if cam not in sistema.jpeg:
            raise HTTPException(404)

        async def gerar():
            ultimo = None
            while True:
                b = sistema.jpeg.get(cam)
                if b and b is not ultimo:
                    ultimo = b
                    yield b"--quadro\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(b)).encode() + b"\r\n\r\n" + b + b"\r\n"
                await asyncio.sleep(0.05)
        return StreamingResponse(gerar(), media_type="multipart/x-mixed-replace; boundary=quadro")

    @app.get("/foto/{cam}.jpg", dependencies=A)
    def foto(cam: str):
        b = sistema.jpeg.get(cam)
        if not b:
            raise HTTPException(404)
        return Response(b, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/previa/{nome}.jpg", dependencies=A)
    def previa(nome: str):
        b = sistema.previas.get(nome)
        if not b:
            raise HTTPException(404, "ainda não há imagem")
        return Response(b, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/evento/{arquivo}", dependencies=A)
    def evento(arquivo: str):
        p = caminho_dados(cfg, "eventos", Path(arquivo).name)
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p)

    @app.get("/api/status", dependencies=A)
    def status(req: Request):
        return sistema.estado() | {"pode_comandar": pode_comandar(req)}

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        senha = cfg["web"].get("senha") or ""
        if senha and sock.query_params.get("chave") != senha:
            await sock.close(code=4401); return
        await sock.accept()
        try:
            while True:
                await sock.send_text(json.dumps(sistema.estado(), default=str))
                await asyncio.sleep(0.25)
        except (WebSocketDisconnect, RuntimeError):
            pass

    # ---- máquina
    @app.post("/api/mach3/{acao}", dependencies=A)
    def mach3(acao: str, req: Request):
        if acao not in ("cycle_start", "feed_hold", "stop"):
            raise HTTPException(404)
        if acao == "cycle_start":
            exigir_local(req)
        return sistema.comando(acao, origem=f"painel {req.client.host}")

    # ---- chapa e job
    @app.post("/api/chapa/capturar", dependencies=A)
    def chapa():
        return tratar(sistema.capturar_chapa)

    @app.post("/api/chapa/nova", dependencies=A)
    def chapa_nova():
        return sistema.nova_chapa()

    @app.post("/api/job", dependencies=A)
    async def job(arquivo: UploadFile = File(...)):
        texto = (await arquivo.read()).decode("utf-8", errors="replace")
        return tratar(sistema.carregar_job, arquivo.filename or "job.tap", texto)

    @app.post("/api/job/exemplo", dependencies=A)
    def job_exemplo(largura: float = 200, altura: float = 120, furo: float = 40):
        from ..gcode import exemplo_peca
        return tratar(sistema.carregar_job, f"teste_{int(largura)}x{int(altura)}.tap", exemplo_peca(largura, altura, furo))

    @app.post("/api/job/posicionar", dependencies=A)
    async def posicionar(req: Request):
        p = await req.json() if (await req.body()) else {}
        return tratar(sistema.posicionar, str(p.get("angulos", "90")), p.get("folga_mm"), p.get("kerf_mm"),
                      p.get("preferencia"), int(p.get("peca", 0)))

    @app.post("/api/job/enviar", dependencies=A)
    async def enviar(req: Request):
        exigir_local(req)
        p = await req.json() if (await req.body()) else {}
        return tratar(sistema.enviar_job, bool(p.get("armar_arco", True)))

    @app.get("/api/job/gcode", dependencies=A)
    def baixar_gcode():
        if not sistema.job or not sistema.job.get("texto_final"):
            raise HTTPException(404, "nenhum job posicionado")
        return Response(sistema.job["texto_final"], media_type="text/plain",
                        headers={"Content-Disposition": f'attachment; filename="{Path(sistema.job["nome"]).stem}_pv.tap"'})

    # ---- arco
    @app.post("/api/arco/{acao}", dependencies=A)
    async def arco(acao: str, req: Request):
        if acao == "armar":
            exigir_local(req)
            if not sistema.job:
                raise HTTPException(400, "carregue um job")
            prog = sistema.job.get("prog_final") or sistema.job["prog"]
            return tratar(lambda: (sistema.aut.armar(prog.sequencia_tocha()), sistema.aut.info())[1])
        if acao == "desarmar":
            sistema.aut.desarmar("desarmado pelo painel"); return sistema.aut.info()
        if acao == "ajustes":
            p = await req.json()
            for k in ("limiar_brilho", "fracao_ligado", "fracao_desligado", "atraso_furacao_s", "roi"):
                if k in p:
                    cfg["arco"][k] = p[k]
            return cfg["arco"]
        raise HTTPException(404)

    # ---- segurança
    @app.post("/api/seguranca/reconhecer", dependencies=A)
    def reconhecer():
        sistema.reconhecer_seguranca(); return {"ok": True}

    @app.post("/api/seguranca/zonas", dependencies=A)
    async def zonas(req: Request):
        p = await req.json()
        z = p.get("zonas")
        if not isinstance(z, list) or not all(len(poli) >= 3 for poli in z):
            raise HTTPException(400, "zonas inválidas")
        cfg["seguranca"]["zonas"] = z
        return {"ok": True}

    @app.post("/api/seguranca/ativo", dependencies=A)
    async def seg_ativo(req: Request):
        cfg["seguranca"]["ativo"] = bool((await req.json()).get("ativo", True))
        sistema.registrar(f"monitor de segurança {'ligado' if cfg['seguranca']['ativo'] else 'DESLIGADO'}")
        return {"ativo": cfg["seguranca"]["ativo"]}

    # ---- medição
    @app.post("/api/medicao/referencia", dependencies=A)
    async def referencia(arquivo: UploadFile = File(...)):
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as t:
            t.write(await arquivo.read())
        return tratar(sistema.carregar_referencia_dxf, Path(t.name))

    @app.post("/api/medicao/medir", dependencies=A)
    async def medir_(req: Request):
        p = await req.json() if (await req.body()) else {}
        return tratar(sistema.medir, p.get("roi_mm"), p.get("usar", "dxf"))

    # ---- calibração
    @app.post("/api/calibracao/vazia", dependencies=A)
    def cal_vazia():
        return tratar(sistema.capturar_vazia)

    @app.get("/api/calibracao/marcadores", dependencies=A)
    def cal_marc():
        return tratar(sistema.ver_marcadores)

    @app.post("/api/calibracao/registrar", dependencies=A)
    async def cal_reg(req: Request):
        p = await req.json()
        return tratar(sistema.registrar_marcador, int(p["id"]), p.get("x"), p.get("y"))

    @app.post("/api/calibracao/calcular", dependencies=A)
    def cal_calc():
        return tratar(sistema.calcular_calibracao)

    # ---- simulação
    @app.post("/api/sim/{acao}", dependencies=A)
    def sim(acao: str):
        if not sistema.simular:
            raise HTTPException(404)
        m = sistema.mesa
        if acao == "pessoa":
            m.pessoa = not m.pessoa
        elif acao == "retalho":
            m.usar_retalho()
        elif acao == "chapa":
            m.chapas = [m._retangulo(120, 80, 1180, 860, 2.2)]; m.maq.cortes.clear()
        elif acao == "operador":
            m.maq.operador = not m.maq.operador
        return {"pessoa": m.pessoa, "operador": m.maq.operador}

    return app
