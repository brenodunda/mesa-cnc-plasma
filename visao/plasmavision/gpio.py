"""Saídas físicas no Raspberry Pi (via módulo optoacoplador/relé).

- feed_hold: pulso numa entrada da placa USB configurada no Mach3 como OEM Trigger
  "Feed Hold" (pausa mesmo se a rede ou o Windows travarem);
- corta_tocha: relé com contato NF em série com o botão da tocha (abre = arco apaga);
- alarme: sirene/luz.

Fora do Raspberry (ou sem gpiozero) vira um simulador que só registra no log.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger("plasmavision.gpio")


class _SaidaFalsa:
    def __init__(self, nome): self.nome, self.value = nome, 0
    def on(self): self.value = 1; log.info("GPIO %s = 1 (simulado)", self.nome)
    def off(self): self.value = 0; log.info("GPIO %s = 0 (simulado)", self.nome)


class SaidasGPIO:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.real = False
        self.saidas = {}
        for nome in ("feed_hold", "corta_tocha", "alarme"):
            pino = cfg.get(nome)
            s = None
            if cfg.get("ativo") and pino is not None:
                try:
                    from gpiozero import OutputDevice
                    s = OutputDevice(int(pino), active_high=True, initial_value=False)
                    self.real = True
                except Exception as e:
                    log.info("gpiozero indisponível (%s); saídas simuladas", e)
            self.saidas[nome] = s or _SaidaFalsa(nome)

    def pulso(self, nome: str, segundos: float | None = None) -> None:
        s = self.saidas[nome]
        dur = self.cfg.get("pulso_s", 0.3) if segundos is None else segundos

        def _p():
            s.on(); time.sleep(dur); s.off()
        threading.Thread(target=_p, daemon=True).start()

    def ligar(self, nome: str) -> None: self.saidas[nome].on()
    def desligar(self, nome: str) -> None: self.saidas[nome].off()

    def estado(self) -> dict:
        return {k: int(v.value) for k, v in self.saidas.items()} | {"real": self.real}
