#!/usr/bin/env bash
# Gera modelos/pessoa.onnx (detector de pessoas) a partir do YOLOv8n.
# Rode num PC (não precisa ser o Pi) e copie o arquivo para visao/modelos/.
# ATENÇÃO À LICENÇA: o YOLOv8 (Ultralytics) é AGPL-3.0. Para uso interno na sua
# oficina tudo bem; se for distribuir o sistema junto com o modelo, leia a licença
# ou troque por um modelo Apache/MIT no mesmo formato (ex.: YOLOX-nano).
set -euo pipefail
python3 -m pip install --upgrade ultralytics onnx
python3 - <<'PY'
from ultralytics import YOLO
m = YOLO("yolov8n.pt")
m.export(format="onnx", imgsz=640, opset=12, simplify=True)
PY
mkdir -p modelos && mv yolov8n.onnx modelos/pessoa.onnx
echo "modelo salvo em modelos/pessoa.onnx"
