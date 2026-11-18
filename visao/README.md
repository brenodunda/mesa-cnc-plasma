# PlasmaVision

Visão computacional para a mesa CNC plasma, rodando num Raspberry Pi 5 com o Mach3 num PC Windows.

## Módulos

| Arquivo | O que faz |
| --- | --- |
| `camera.py` | Captura em thread (Picamera2, USB/OpenCV, RTSP/HTTP, arquivo, simulada) |
| `calibracao.py` | Homografia imagem → mm de máquina por marcadores ArUco; ortofoto; lente opcional |
| `chapa.py` | Chapa e retalhos por diferença com a mesa vazia; ângulo, canto, polígono e área útil |
| `gcode.py` | Leitura de G-code (G0–G3, G90/G91, G20/G21, I/J e R), M0 da tocha, rotação + deslocamento |
| `encaixe.py` | “Pegada” do programa e busca por FFT da posição/ângulo livre; bloqueia o que já foi cortado |
| `arco.py` | Detector de arco (brilho na região da tocha) e máquina de estados do Cycle Start automático |
| `seguranca.py` | Pessoa (ONNX formato YOLO) ou movimento (MOG2) em zonas; alarme com trava e foto |
| `medicao.py` | Contorno e furos da peça em mm, comparação com DXF ou com o G-code |
| `mach3.py` | Cliente HTTP da ponte do Mach3 |
| `gpio.py` | Saídas Feed Hold, corta-tocha e alarme (gpiozero) |
| `simulador.py` | Mesa, câmeras, operador e Mach3 simulados |
| `sistema.py` | Orquestrador (laço de 30 Hz) |
| `web/` | Painel FastAPI + página única, vídeo MJPEG |

## Instalação no Raspberry Pi

```bash
git clone https://github.com/<seu-usuario>/mesa-cnc-plasma.git
cd mesa-cnc-plasma/visao
./scripts/instalar_pi.sh          # apt + venv + requisitos + serviço systemd + marcadores ArUco
nano config.yaml                  # IP e token do Mach3, senha do painel
sudo systemctl start plasmavision
journalctl -u plasmavision -f     # ver o log
```

Imprima os marcadores de `visao/marcadores/` em tamanho real (80 mm de lado, sem “ajustar à página”) e cole em chapinhas.

## Padrão do G-code

A automação da tocha precisa de um M0 em cada furação e outro em cada fim de contorno. No pós-processador do SheetCAM/Fusion, troque o M3 por `M0 (ACENDA ...)` e o M5 por `M0 (SOLTE ...)`. A palavra ACENDA/SOLTE no comentário define o tipo da parada; sem ela, o sistema alterna começando por “acender”. Exemplo em `gcode.exemplo_peca()`.

## API do painel

| Rota | Faz |
| --- | --- |
| `GET /api/status` | Estado completo (Mach3, arco, automação, segurança, câmeras, calibração) |
| `GET /stream/topo`, `/stream/tocha` | Vídeo MJPEG com sobreposições |
| `POST /api/mach3/{cycle_start,feed_hold,stop}` | Comandos (Cycle Start só das `redes_de_comando`) |
| `POST /api/chapa/capturar`, `/api/chapa/nova` | Detectar material / zerar histórico de cortes |
| `POST /api/job` (arquivo), `/api/job/posicionar`, `/api/job/enviar` | Fluxo de corte |
| `POST /api/arco/{armar,desarmar,ajustes}` | Automação da tocha |
| `POST /api/seguranca/{reconhecer,zonas,ativo}` | Monitor de área |
| `POST /api/medicao/{referencia,medir}` | Medição |
| `POST /api/calibracao/{vazia,registrar,calcular}`, `GET /api/calibracao/marcadores` | Calibração |

## Acesso remoto

Use [Tailscale](https://tailscale.com): instale no Pi (`curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`) e no celular/PC. Nada de abrir porta no roteador. Defina `web.senha` no `config.yaml`. Por padrão, fora da rede local o painel não manda a máquina andar (veja `redes_de_comando`).

## Testes

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## Limites conhecidos

- Segurança por câmera não é dispositivo de segurança certificado (NR-12 / ISO 13849).
- Os códigos OEM do Mach3 variam entre versões: ajuste em `mach3_ponte/ponte_config.json`.
- Precisão de medição limitada pela câmera do topo (≈0,55 mm/px). Para mais precisão, use uma estação de medição com câmera mais perto e outra calibração.
- Luz muito diferente da captura da “mesa vazia” atrapalha a detecção da chapa: use as barras de LED e refaça a referência se mudar a iluminação.
