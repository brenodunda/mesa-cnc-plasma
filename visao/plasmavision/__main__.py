"""Uso:
    python -m plasmavision --config config.yaml          # na mesa
    python -m plasmavision --simular                     # sem máquina, tudo simulado
    python -m plasmavision --gerar-marcadores pasta/     # PNGs dos ArUco para imprimir
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(prog="plasmavision")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--simular", action="store_true", help="câmeras e Mach3 simulados")
    ap.add_argument("--porta", type=int)
    ap.add_argument("--gerar-marcadores", metavar="PASTA")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    from .config import carregar_config
    cfg = carregar_config(a.config if Path(a.config).exists() else None)

    if a.gerar_marcadores:
        import cv2
        from .calibracao import gerar_marcador
        pasta = Path(a.gerar_marcadores); pasta.mkdir(parents=True, exist_ok=True)
        for i in sorted(cfg["calibracao"]["marcadores_mm"]):
            cv2.imwrite(str(pasta / f"aruco_{i}.png"), gerar_marcador(i, nome_dic=cfg["calibracao"]["dicionario_aruco"]))
        print(f"marcadores salvos em {pasta} — imprima com 80 mm de lado (sem ajustar à página)")
        return

    import uvicorn
    from .sistema import Sistema
    from .web.app import criar_app
    sistema = Sistema(cfg, simular=a.simular)
    app = criar_app(sistema)
    uvicorn.run(app, host=cfg["web"]["host"], port=a.porta or cfg["web"]["porta"], log_level="warning")


if __name__ == "__main__":
    main()
