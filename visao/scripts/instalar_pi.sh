#!/usr/bin/env bash
# Instala o PlasmaVision no Raspberry Pi OS (Bookworm, 64 bits).
set -euo pipefail
cd "$(dirname "$0")/.."
sudo apt update
sudo apt install -y python3-venv python3-picamera2 python3-gpiozero python3-lgpio
# --system-site-packages: deixa o venv enxergar picamera2 e gpiozero instalados pelo apt
python3 -m venv --system-site-packages .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
[ -f config.yaml ] || cp config.exemplo.yaml config.yaml
mkdir -p modelos
.venv/bin/python -m plasmavision --gerar-marcadores marcadores
sudo cp scripts/plasmavision.service /etc/systemd/system/
sudo sed -i "s#/home/pi/mesa-cnc-plasma/visao#$(pwd)#g; s#User=pi#User=$(whoami)#" /etc/systemd/system/plasmavision.service
sudo systemctl daemon-reload
sudo systemctl enable plasmavision
echo
echo "Pronto. Edite config.yaml (IP do Mach3, token, senha) e rode: sudo systemctl start plasmavision"
echo "Painel: http://$(hostname -I | awk '{print $1}'):8080"
echo "Marcadores ArUco para imprimir: $(pwd)/marcadores"
