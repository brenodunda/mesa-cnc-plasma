"""Orquestrador: liga câmeras, visão, automação da tocha, segurança e Mach3."""
from __future__ import annotations

import collections
import logging
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from . import gcode
from .arco import AutomacaoTocha, DetectorArco
from .calibracao import Calibracao, detectar_marcadores
from .camera import Camera
from .chapa import desenhar_pecas, detectar_material, faixa_portico
from .config import caminho_dados
from .encaixe import angulos_candidatos, bloquear_programas, desenhar_encaixe, encaixar, mapa_bloqueio
from .gpio import SaidasGPIO
from .mach3 import ClienteMach3
from .medicao import desenhar_medicao, medir, referencia_de_dxf, referencia_de_programa
from .seguranca import MonitorSeguranca

log = logging.getLogger("plasmavision")


def _jpeg(img: np.ndarray, largura: int | None = None, q: int = 75) -> bytes:
    if largura and img.shape[1] > largura:
        img = cv2.resize(img, (largura, int(img.shape[0] * largura / img.shape[1])), interpolation=cv2.INTER_AREA)
    ok, b = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    return b.tobytes() if ok else b""


class Sistema:
    def __init__(self, cfg: dict, simular: bool = False):
        self.cfg = cfg
        self.simular = simular
        self.eventos = collections.deque(maxlen=200)
        self.trava = threading.RLock()
        self.mesa = None
        if simular:
            from .simulador import MaquinaSimulada, MesaSimulada
            self.maq = MaquinaSimulada(acelerar=3.0, operador=True)
            self.maq.x, self.maq.y = 0.0, 0.0          # estacionado no home
            self.mesa = MesaSimulada(cfg, self.maq)
            for c in ("topo", "tocha"):
                cfg["cameras"][c] = dict(cfg["cameras"][c], tipo="simulada", fps=8 if c == "topo" else 20)
        else:
            m = cfg["mach3"]
            self.maq = ClienteMach3(m["url"], m["token"], m["intervalo_s"])
        self.cam = {n: Camera(n, cfg["cameras"][n], self.mesa).iniciar() for n in ("topo", "tocha")}
        self.gpio = SaidasGPIO(cfg["gpio"])
        self.det_arco = DetectorArco(cfg["arco"])
        self.aut = AutomacaoTocha(cfg["arco"])
        self.seg = MonitorSeguranca(cfg["seguranca"], caminho_dados(cfg, "eventos", "x").parent)
        if self.seg.erro:
            self.registrar(self.seg.erro)
        self.calib: Calibracao | None = None
        self.vazia_cam: np.ndarray | None = None
        self.marcadores_mm: dict[int, list[float]] = dict(cfg["calibracao"]["marcadores_mm"])
        self._carregar_calibracao()
        self.material: dict | None = None
        self.job: dict | None = None
        self.cortados: list = []          # programas já enviados para a chapa atual
        self.ref_medicao: dict | None = None
        self.ultima_medicao: dict | None = None
        self.previas: dict[str, bytes] = {}
        self.jpeg: dict[str, bytes] = {"topo": b"", "tocha": b""}
        self._t_quadro = {"topo": 0.0, "tocha": 0.0}
        self._parar = threading.Event()
        threading.Thread(target=self._laco, name="sistema", daemon=True).start()
        if simular:
            threading.Thread(target=self._auto_calibrar_sim, daemon=True).start()

    # ------------------------------------------------------------ utilidades
    def registrar(self, msg: str) -> None:
        log.info(msg)
        self.eventos.appendleft({"hora": time.strftime("%H:%M:%S"), "msg": msg})

    def _carregar_calibracao(self) -> None:
        arq = caminho_dados(self.cfg, self.cfg["calibracao"]["arquivo"])
        if arq.exists():
            try:
                self.calib = Calibracao.carregar(arq)
                for k, v in self.calib.pontos.items():
                    self.marcadores_mm[int(k)] = v["mm"]
            except Exception as e:
                self.registrar(f"calibração inválida: {e}")
        vazia = caminho_dados(self.cfg, "mesa_vazia_camera.png")
        if vazia.exists():
            self.vazia_cam = cv2.imread(str(vazia))

    def _auto_calibrar_sim(self) -> None:
        time.sleep(1.5)
        try:
            self.vazia_cam = cv2.warpPerspective(self.mesa.orto_vazia(), self.mesa.H_orto_cam, self.mesa.tam_cam,
                                                 borderValue=(30, 30, 30))
            cv2.imwrite(str(caminho_dados(self.cfg, "mesa_vazia_camera.png")), self.vazia_cam)
            self.calcular_calibracao(self.vazia_cam)
            self.registrar("simulação: calibração automática feita")
        except Exception as e:
            self.registrar(f"simulação: calibração falhou: {e}")

    def quadro(self, nome: str) -> np.ndarray | None:
        return self.cam[nome].ler()[0]

    # ------------------------------------------------------------ laço principal
    def _laco(self) -> None:
        t_ant = time.monotonic()
        t_jpeg = {"topo": 0.0, "tocha": 0.0}
        while not self._parar.is_set():
            agora = time.monotonic()
            dt, t_ant = agora - t_ant, agora
            try:
                self.maq.passo_tempo(dt)
                st = self.maq.status()
                q_tocha, tt = self.cam["tocha"].ler()
                if tt != self._t_quadro["tocha"] and q_tocha is not None:
                    self._t_quadro["tocha"] = tt
                    self.det_arco.atualizar(q_tocha)
                q_topo, tp = self.cam["topo"].ler()
                novo_topo = tp != self._t_quadro["topo"] and q_topo is not None
                if novo_topo:
                    self._t_quadro["topo"] = tp
                    ativa = bool(st.get("movendo") or st.get("rodando"))
                    if self.seg.atualizar(q_topo, ativa, self._mascara_portico_cam(q_topo, st)):
                        self._acionar_seguranca()
                acoes = self.aut.passo(self.det_arco.ligado, bool(st.get("movendo")), bool(st.get("conectada")),
                                       self.seg.alarme)
                for a in acoes:
                    self._executar(a, origem="automação da tocha")
                # vídeo para o painel (~6 qps no topo, ~12 na tocha)
                if novo_topo and agora - t_jpeg["topo"] > 0.16:
                    t_jpeg["topo"] = agora
                    self.jpeg["topo"] = _jpeg(self._sobrepor_topo(q_topo, st), self.cfg["web"]["largura_stream"],
                                              self.cfg["web"]["qualidade_jpeg"])
                if q_tocha is not None and agora - t_jpeg["tocha"] > 0.08:
                    t_jpeg["tocha"] = agora
                    self.jpeg["tocha"] = _jpeg(self.det_arco.desenhar(q_tocha), 800, self.cfg["web"]["qualidade_jpeg"])
            except Exception as e:  # o laço nunca pode morrer
                log.exception("erro no laço: %s", e)
            time.sleep(0.03)

    def _executar(self, acao: str, origem: str = "") -> dict:
        if acao == "cycle_start":
            if self.seg.alarme:
                return {"ok": False, "erro": "alarme de segurança ativo"}
            r = self.maq.cycle_start()
        elif acao == "feed_hold":
            r = self.maq.feed_hold()
            self.gpio.pulso("feed_hold")
        elif acao == "stop":
            r = self.maq.parar()
            self.gpio.pulso("feed_hold")
        else:
            return {"ok": False, "erro": f"ação desconhecida {acao}"}
        self.registrar(f"{acao} ({origem})" + ("" if r.get("ok", True) else f" — falhou: {r.get('erro')}"))
        return r

    def comando(self, acao: str, origem: str = "painel") -> dict:
        if acao in ("feed_hold", "stop"):
            self.aut.pausa_externa("pausa pelo painel" if acao == "feed_hold" else "parada pelo painel")
            if acao == "stop":
                self.aut.desarmar("parada pelo painel")
        return self._executar(acao, origem)

    def _mascara_portico_cam(self, img: np.ndarray, st: dict) -> np.ndarray | None:
        """Faixa do pórtico (com folga) projetada na imagem do topo, para o modo movimento."""
        if self.seg.modo != "movimento" or self.calib is None or not st.get("conectada") or "x" not in st:
            return None
        f = self.cfg["mesa"]["mascara_portico_mm"]
        x = float(st["x"])
        x0, x1 = x + f[0] - 150, x + f[1] + 150
        cantos = self.calib.mm_para_px([[x0, -500], [x1, -500], [x1, 1550], [x0, 1550]]).astype(np.int32)
        m = np.zeros(img.shape[:2], np.uint8)
        cv2.fillPoly(m, [cantos], 255)
        return m

    def _acionar_seguranca(self) -> None:
        self.registrar(f"ALARME DE SEGURANÇA: {self.seg.motivo}")
        acoes = self.cfg["seguranca"]["acoes"]
        if "feed_hold" in acoes:
            self.maq.feed_hold(); self.gpio.pulso("feed_hold")
        if "corta_tocha" in acoes:
            self.gpio.ligar("corta_tocha")
        if "alarme" in acoes:
            self.gpio.ligar("alarme")
        self.aut.pausa_externa("alarme de segurança")

    def reconhecer_seguranca(self) -> None:
        self.seg.reconhecer()
        self.gpio.desligar("corta_tocha"); self.gpio.desligar("alarme")
        self.registrar("alarme de segurança reconhecido")

    def _sobrepor_topo(self, img: np.ndarray, st: dict) -> np.ndarray:
        out = self.seg.desenhar(img)
        if self.calib is not None and self.calib.pronta:
            try:
                # área útil e posição da tocha
                x1, y1 = self.cfg["mesa"]["curso_mm"][:2]
                cant = self.calib.mm_para_px([[0, 0], [x1, 0], [x1, y1], [0, y1]]).astype(np.int32)
                cv2.polylines(out, [cant], True, (0, 140, 255), 2, cv2.LINE_AA)
                if st.get("conectada") and "x" in st:
                    p = self.calib.mm_para_px([[st["x"], st["y"]]])[0].astype(int)
                    cv2.drawMarker(out, tuple(p), (0, 0, 255) if self.det_arco.ligado else (255, 255, 0),
                                   cv2.MARKER_CROSS, 40, 3)
                if self.job and self.job.get("prog_final") is not None:
                    for pl in self.job["prog_final"].polilinhas_corte(3.0):
                        q = self.calib.mm_para_px(pl).astype(np.int32)
                        cv2.polylines(out, [q.reshape(-1, 1, 2)], False, (255, 120, 0), 2, cv2.LINE_AA)
            except Exception:
                pass
        if self.seg.alarme:
            cv2.putText(out, "ALARME: " + self.seg.motivo, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 255), 4)
        return out

    # ------------------------------------------------------------ calibração
    def capturar_vazia(self) -> dict:
        q = self.quadro("topo")
        if q is None:
            raise RuntimeError("câmera do topo sem imagem")
        self.vazia_cam = q
        cv2.imwrite(str(caminho_dados(self.cfg, "mesa_vazia_camera.png")), q)
        self.registrar("referência da mesa vazia capturada")
        return {"ok": True}

    def ver_marcadores(self) -> dict:
        q = self.quadro("topo")
        if q is None:
            raise RuntimeError("câmera do topo sem imagem")
        m = detectar_marcadores(q, self.cfg["calibracao"]["dicionario_aruco"])
        vis = q.copy()
        for i, c in m.items():
            cv2.circle(vis, tuple(c.astype(int)), 18, (0, 0, 255), 3)
            cv2.putText(vis, f"ID {i}", tuple((c + [22, -22]).astype(int)), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
        self.previas["marcadores"] = _jpeg(vis, 1280)
        return {"encontrados": {int(k): [round(float(v[0]), 1), round(float(v[1]), 1)] for k, v in m.items()},
                "mm": {int(k): v for k, v in self.marcadores_mm.items()}}

    def registrar_marcador(self, id_: int, x: float | None = None, y: float | None = None) -> dict:
        if x is None or y is None:
            st = self.maq.status()
            if not st.get("conectada"):
                raise RuntimeError("sem Mach3: digite X e Y")
            x, y = st["x"], st["y"]
        self.marcadores_mm[int(id_)] = [float(x), float(y)]
        self.registrar(f"marcador {id_} = X{x:.2f} Y{y:.2f}")
        return {"ok": True, "mm": self.marcadores_mm}

    def calcular_calibracao(self, img: np.ndarray | None = None) -> dict:
        q = img if img is not None else self.quadro("topo")
        if q is None:
            raise RuntimeError("câmera do topo sem imagem")
        m = detectar_marcadores(q, self.cfg["calibracao"]["dicionario_aruco"])
        ids = [i for i in sorted(m) if i in self.marcadores_mm]
        if len(ids) < 4:
            raise RuntimeError(f"preciso de 4 marcadores com posição; vi {sorted(m)}")
        c = self.cfg
        calib = Calibracao.de_pontos(np.array([m[i] for i in ids]), np.array([self.marcadores_mm[i] for i in ids]),
                                     regiao_mm=tuple(c["mesa"]["regiao_orto_mm"]),
                                     escala_mm_px=c["mesa"]["escala_orto_mm_px"], tamanho_img=(q.shape[1], q.shape[0]))
        calib.pontos = {i: {"px": m[i].round(2).tolist(), "mm": self.marcadores_mm[i]} for i in ids}
        intr = c["calibracao"].get("intrinseca")
        if intr and Path(intr).exists():
            import json
            d = json.loads(Path(intr).read_text())
            calib.K, calib.dist = np.array(d["K"]), np.array(d["dist"])
            calib = Calibracao.de_pontos(np.array([m[i] for i in ids]), np.array([self.marcadores_mm[i] for i in ids]),
                                         K=calib.K, dist=calib.dist, regiao_mm=calib.regiao_mm,
                                         escala_mm_px=calib.escala_mm_px, tamanho_img=calib.tamanho_img)
            calib.pontos = {i: {"px": m[i].round(2).tolist(), "mm": self.marcadores_mm[i]} for i in ids}
        calib.salvar(caminho_dados(c, c["calibracao"]["arquivo"]))
        self.calib = calib
        self.registrar(f"calibração calculada com {len(ids)} marcadores, erro {calib.erro_rms_mm:.2f} mm")
        if self.vazia_cam is not None:
            self.previas["orto"] = _jpeg(calib.ortofoto(self.vazia_cam), 1280)
        return {"ok": True, "erro_rms_mm": round(calib.erro_rms_mm, 3), "ids": ids}

    def _exigir_calibracao(self):
        if self.calib is None or not self.calib.pronta:
            raise RuntimeError("faça a calibração primeiro (aba Calibração)")
        if self.vazia_cam is None:
            raise RuntimeError("capture a mesa vazia primeiro (aba Calibração)")

    # ------------------------------------------------------------ chapa e job
    def nova_chapa(self) -> dict:
        n = len(self.cortados)
        self.cortados.clear()
        self.registrar(f"chapa nova: histórico de {n} corte(s) apagado")
        return {"ok": True}

    def capturar_chapa(self) -> dict:
        self._exigir_calibracao()
        q = self.quadro("topo")
        orto = self.calib.ortofoto(q)
        vazia = self.calib.ortofoto(self.vazia_cam)
        st = self.maq.status()
        ignorar = None
        if st.get("conectada") and "x" in st:
            ignorar = faixa_portico(self.calib, float(st["x"]), self.cfg["mesa"]["mascara_portico_mm"])
        r = detectar_material(orto, vazia, self.calib, self.cfg["chapa"], ignorar)
        self.material = r | {"orto": orto}
        self.previas["chapa"] = _jpeg(desenhar_pecas(orto, r, self.calib), 1400)
        self.registrar(f"chapa: {len(r['pecas'])} peça(s) de material encontradas")
        return {"pecas": r["pecas"]}

    def carregar_job(self, nome: str, texto: str) -> dict:
        prog = gcode.interpretar(texto, self.cfg["gcode"]["ij_absoluto"])
        lim = prog.limites_corte()
        if lim is None:
            raise ValueError("o arquivo não tem movimentos de corte")
        self.job = {"nome": nome, "texto": texto, "prog": prog, "prog_final": None, "texto_final": None,
                    "transf": None}
        self.registrar(f"job {nome} carregado: {len(prog.pausas)} M0, {len(prog.movimentos)} movimentos")
        return {"nome": nome, "limites_mm": [round(v, 2) for v in lim],
                "tamanho_mm": [round(lim[2] - lim[0], 1), round(lim[3] - lim[1], 1)],
                "sequencia_tocha": prog.sequencia_tocha(), "avisos": prog.avisos}

    def posicionar(self, modo_angulo: str = "90", folga_mm: float | None = None, kerf_mm: float | None = None,
                   preferencia: str | None = None, indice_peca: int = 0) -> dict:
        if not self.job:
            raise RuntimeError("carregue um G-code primeiro")
        if not self.material:
            raise RuntimeError("capture a chapa primeiro")
        e = self.cfg["encaixe"]
        folga = e["folga_mm"] if folga_mm is None else folga_mm
        kerf = e["kerf_mm"] if kerf_mm is None else kerf_mm
        pecas = self.material["pecas"]
        ang0 = pecas[indice_peca]["angulo_graus"] if pecas else 0.0
        passo = {"chapa": 360.0, "90": 90.0, "45": 45.0, "15": 15.0}.get(str(modo_angulo), e["passo_angulo_graus"])
        B, orig = mapa_bloqueio(self.material["mascara_util"], self.calib, self.cfg["mesa"]["curso_mm"], e["resolucao_mm"])
        if self.cortados:
            B = bloquear_programas(B, orig, e["resolucao_mm"], self.cortados, kerf + 2 * folga)
        r = encaixar(self.job["prog"], B, orig, e["resolucao_mm"], angulos_candidatos(ang0, passo), kerf, folga,
                     preferencia or e["preferencia"])
        if r is None:
            self.previas["encaixe"] = self.previas.get("chapa", b"")
            raise RuntimeError("a peça não cabe na área livre com esses ângulos e folga")
        texto, avisos = gcode.transformar(self.job["texto"], r["angulo_graus"], r["dx"], r["dy"],
                                          ij_absoluto=self.cfg["gcode"]["ij_absoluto"])
        pf = gcode.interpretar(texto, self.cfg["gcode"]["ij_absoluto"])
        self.job.update(texto_final=texto, prog_final=pf, transf=r)
        vis = desenhar_pecas(self.material["orto"], self.material, self.calib)
        for pc in self.cortados:
            vis = desenhar_encaixe(vis, self.calib, pc, cor=(120, 120, 120))
        self.previas["encaixe"] = _jpeg(desenhar_encaixe(vis, self.calib, pf), 1400)
        self.registrar(f"job posicionado: {r['angulo_graus']:+.2f}°, X{r['dx']:+.1f} Y{r['dy']:+.1f}")
        return r | {"avisos": avisos}

    def enviar_job(self, armar_arco: bool = True) -> dict:
        if not self.job or not self.job.get("texto_final"):
            raise RuntimeError("posicione o job primeiro")
        nome = Path(self.job["nome"]).stem + "_pv.tap"
        caminho_dados(self.cfg, "jobs", nome).write_text(self.job["texto_final"], encoding="utf-8")
        r = self.maq.carregar(nome, self.job["texto_final"])
        if r.get("ok", True) is False:
            raise RuntimeError(r.get("erro", "falha ao enviar"))
        if armar_arco:
            seq = self.job["prog_final"].sequencia_tocha()
            if seq:
                self.aut.armar(seq)
                self.registrar(f"automação da tocha armada: {len(seq)} M0")
            else:
                self.registrar("o programa não tem M0: automação da tocha não armada")
        self.cortados.append(self.job["prog_final"])
        self.registrar(f"job enviado ao Mach3 como {nome}")
        return {"ok": True, "arquivo": nome}

    # ------------------------------------------------------------ medição
    def carregar_referencia_dxf(self, caminho: Path) -> dict:
        self.ref_medicao = referencia_de_dxf(caminho)
        r = self.ref_medicao
        return {"furos": len(r["furos"]), "recortes": len(r["recortes"])}

    def medir(self, roi_mm: list[float] | None = None, usar: str = "dxf") -> dict:
        self._exigir_calibracao()
        s = self.cfg["medicao"]["escala_mm_px"]
        q = self.quadro("topo")
        orto = self.calib.ortofoto(q, escala=s)
        vazia = self.calib.ortofoto(self.vazia_cam, escala=s)
        roi_px = None
        if roi_mm:
            x0, y0, x1, y1 = roi_mm
            a = self.calib.mm_para_orto_px([[min(x0, x1), max(y0, y1)]], escala=s)[0]
            b = self.calib.mm_para_orto_px([[max(x0, x1), min(y0, y1)]], escala=s)[0]
            roi_px = (int(a[0]), int(a[1]), int(b[0] - a[0]), int(b[1] - a[1]))
        ref = None
        if usar == "dxf":
            ref = self.ref_medicao
        elif usar == "job" and self.job:
            ref = referencia_de_programa(self.job["prog"])
        res = medir(orto, s, lambda uv: self.calib.orto_px_para_mm(uv, escala=s), vazia=vazia, referencia=ref,
                    tolerancia_mm=self.cfg["medicao"]["tolerancia_mm"], metodo=self.cfg["medicao"]["metodo"],
                    roi_px=roi_px)
        vis = desenhar_medicao(orto, res, lambda mm: self.calib.mm_para_orto_px(mm, escala=s))
        if roi_px:
            x, y, w, h = roi_px
            vis = vis[max(0, y - 40):y + h + 40, max(0, x - 40):x + w + 40]
        self.previas["medicao"] = _jpeg(vis, 1400)
        self.ultima_medicao = res
        self.registrar(f"medição: {res['dimensoes_mm'][0]:.1f} × {res['dimensoes_mm'][1]:.1f} mm")
        return res

    # ------------------------------------------------------------ estado
    def estado(self) -> dict:
        st = self.maq.status()
        return {
            "mach3": st,
            "arco": {"ligado": self.det_arco.ligado, "metrica": round(self.det_arco.metrica, 4)},
            "automacao": self.aut.info(),
            "seguranca": {"alarme": self.seg.alarme, "motivo": self.seg.motivo, "modo": self.seg.modo,
                          "ativo": self.cfg["seguranca"]["ativo"], "eventos": self.seg.eventos[:10],
                          "zonas": self.cfg["seguranca"]["zonas"]},
            "cameras": {n: {"fps": round(c.fps_medido, 1), "erro": c.erro, "ok": c.quadro is not None}
                        for n, c in self.cam.items()},
            "calibracao": {"pronta": bool(self.calib and self.calib.pronta),
                           "erro_rms_mm": None if not self.calib else self.calib.erro_rms_mm,
                           "mesa_vazia": self.vazia_cam is not None},
            "cortes_na_chapa": len(self.cortados),
            "job": None if not self.job else {"nome": self.job["nome"], "posicionado": self.job["transf"] is not None,
                                              "transf": self.job["transf"]},
            "gpio": self.gpio.estado(),
            "eventos": list(self.eventos)[:25],
            "simulado": self.simular,
        }
