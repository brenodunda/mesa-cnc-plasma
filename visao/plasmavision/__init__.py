"""PlasmaVision — visão computacional para a mesa CNC plasma 1500 × 1000.

Roda no Raspberry Pi: alinha chapa e retalhos, encaixa o G-code, detecta o arco
para dar Cycle Start no Mach3, pausa a máquina se alguém entrar na área de corte,
mede as peças cortadas e mostra tudo num painel web ao vivo.
"""
__version__ = "0.1.0"
