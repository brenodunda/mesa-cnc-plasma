"""Detecção do arco de plasma pela câmera da tocha e automação do Cycle Start.

Fluxo com a tocha acionada à mão (G-code com M0 em cada furação e em cada fim de
contorno):
  1. a máquina para no M0 "ACENDA";
  2. você aperta o botão da tocha; a câmera vê o arco, espera o tempo de furação e
     manda Cycle Start;
  3. a máquina corta e para no M0 "SOLTE"; você solta o botão; a câmera vê o arco
     apagar e manda Cycle Start para ir à próxima furação;
  4. se o arco apagar no meio do corte, a máquina recebe Feed Hold na hora.

A automação só arma quando um job é enviado pelo painel (ela conhece a sequência
de M0 do programa) e se desarma no último M0. Com alarme de segurança ativo ela
nunca manda a máquina andar.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np


class DetectorArco:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.ligado = False
        self._cont = 0
        self.metrica = 0.0

    def roi(self, frame: np.ndarray) -> tuple[int, int, int, int]:
        h, w = frame.shape[:2]
        x, y, rw, rh = self.cfg["roi"]
        return int(x * w), int(y * h), max(1, int(rw * w)), max(1, int(rh * h))

    def atualizar(self, frame: np.ndarray | None) -> bool:
        if frame is None:
            return self.ligado
        x, y, w, h = self.roi(frame)
        g = frame[y:y + h, x:x + w]
        if g.ndim == 3:
            g = cv2.cvtColor(g, cv2.COLOR_BGR2GRAY)
        self.metrica = float(np.count_nonzero(g >= self.cfg["limiar_brilho"])) / g.size
        alvo = self.metrica >= self.cfg["fracao_ligado"] if not self.ligado else self.metrica > self.cfg["fracao_desligado"]
        if alvo != self.ligado:
            self._cont += 1
            if self._cont >= self.cfg["quadros_confirmacao"]:
                self.ligado = alvo
                self._cont = 0
        else:
            self._cont = 0
        return self.ligado

    def desenhar(self, frame: np.ndarray) -> np.ndarray:
        out = frame.copy()
        x, y, w, h = self.roi(frame)
        cor = (0, 0, 255) if self.ligado else (0, 200, 0)
        cv2.rectangle(out, (x, y), (x + w, y + h), cor, 2)
        txt = f"ARCO {'LIGADO' if self.ligado else 'desligado'}  {100*self.metrica:.1f}%"
        cv2.putText(out, txt, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, cor, 2)
        return out


@dataclass
class AutomacaoTocha:
    cfg: dict
    sequencia: list[str] = field(default_factory=list)   # ['acender','soltar',...]
    indice: int = 0
    estado: str = "desarmado"
    mensagem: str = ""
    _t_parado: float | None = None
    _t_arco: float | None = None
    _t_comando: float | None = None
    _t_sem_arco: float | None = None
    _viu_movimento: bool = False

    def armar(self, sequencia: list[str]) -> None:
        if not sequencia:
            raise ValueError("o programa não tem M0 de tocha; use o pós-processador com M0")
        self.sequencia, self.indice = list(sequencia), 0
        self.estado, self.mensagem = "aguardando_inicio", "dê Cycle Start para ir à primeira furação"
        self._t_parado = self._t_arco = self._t_comando = self._t_sem_arco = None
        self._viu_movimento = False

    def desarmar(self, motivo: str = "desarmado") -> None:
        self.estado, self.mensagem = "desarmado", motivo

    def pausa_externa(self, motivo: str) -> None:
        """Pausa vinda de fora (botão Pausar, alarme de segurança). Evita que a automação
        confunda a parada com um M0 do programa."""
        if self.estado in ("desarmado", "concluido", "aguardando_inicio"):
            return
        cortando = self.indice > 0 and self.sequencia[self.indice - 1] == "acender"
        if cortando:
            self.estado, self.mensagem = "arco_perdido", f"{motivo}: acenda a tocha para retomar o corte"
        else:
            self.estado, self.mensagem = "aguardando_inicio", f"{motivo}: dê Cycle Start para continuar"
            self._viu_movimento = False
        self._t_arco = None

    @property
    def esperado(self) -> str | None:
        return self.sequencia[self.indice] if self.indice < len(self.sequencia) else None

    def info(self) -> dict:
        return {"estado": self.estado, "mensagem": self.mensagem, "indice": self.indice,
                "total": len(self.sequencia), "esperado": self.esperado}

    def passo(self, arco: bool, movendo: bool, conectada: bool, bloqueado: bool,
              agora: float | None = None) -> list[str]:
        """Avança a máquina de estados. Retorna ações: 'cycle_start', 'feed_hold'."""
        agora = time.monotonic() if agora is None else agora
        acoes: list[str] = []
        if self.estado in ("desarmado", "concluido"):
            return acoes
        if not conectada:
            self.mensagem = "sem comunicação com o Mach3"
            return acoes
        c = self.cfg
        # cronômetros comuns
        if movendo:
            self._t_parado = None
            self._viu_movimento = True
        elif self._t_parado is None:
            self._t_parado = agora
        parado = (not movendo) and self._t_parado is not None and (agora - self._t_parado) >= c["tempo_parado_s"]

        # perda de arco durante o corte
        cortando = self.estado == "aguardando_m0" and self.indice > 0 and self.sequencia[self.indice - 1] == "acender"
        if cortando and movendo and not arco and c.get("pausar_se_arco_apagar", True):
            if self._t_sem_arco is None:
                self._t_sem_arco = agora
            elif agora - self._t_sem_arco >= c["tempo_perda_arco_s"]:
                acoes.append("feed_hold")
                self.estado, self.mensagem = "arco_perdido", "arco apagou no corte: máquina pausada. Acenda de novo para continuar"
                self._t_arco = None
                return acoes
        else:
            self._t_sem_arco = None

        if self.estado == "aguardando_inicio":
            if self._viu_movimento:
                self.estado, self.mensagem = "aguardando_m0", "indo para a furação"
            return acoes

        if self.estado == "aguardando_movimento":
            if movendo:
                self.estado = "aguardando_m0"
                self.mensagem = "cortando" if self.sequencia[self.indice - 1] == "acender" else "indo para a próxima furação"
            elif self._t_comando and agora - self._t_comando > 3.0:
                self.estado, self.mensagem = "erro", "Cycle Start enviado e a máquina não andou"
            return acoes

        if self.estado in ("aguardando_m0", "arco_perdido") and parado:
            if self.estado == "aguardando_m0" and self.esperado is None:
                self.estado, self.mensagem = "concluido", "programa concluído"
                return acoes
            quer_arco = True if self.estado == "arco_perdido" else (self.esperado == "acender")
            if quer_arco:
                self.mensagem = "ACENDA A TOCHA" if self.estado != "arco_perdido" else self.mensagem
                if arco:
                    if self._t_arco is None:
                        self._t_arco = agora
                    elif agora - self._t_arco >= c["atraso_furacao_s"] and not bloqueado:
                        acoes.append("cycle_start")
                        if self.estado != "arco_perdido":
                            self.indice += 1
                        self._seguir(agora)
                else:
                    self._t_arco = None
            else:
                self.mensagem = "SOLTE A TOCHA"
                if not arco and not bloqueado:
                    acoes.append("cycle_start")
                    self.indice += 1
                    self._seguir(agora)
            if bloqueado and self.estado not in ("aguardando_movimento",):
                self.mensagem = "alarme de segurança ativo: automação travada"
        return acoes

    def _seguir(self, agora: float) -> None:
        self._t_comando = agora
        self._t_arco = None
        self._t_parado = None
        self.estado = "aguardando_movimento"
        if self.indice >= len(self.sequencia) and self.sequencia and self.sequencia[-1] == "soltar":
            self.mensagem = "último contorno: retornando"
