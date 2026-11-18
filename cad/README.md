# CAD da mesa

`modelo_mesa.py` gera a montagem inteira em CadQuery a partir das premissas no topo do arquivo.

```bash
pip install cadquery numpy
python modelo_mesa.py saida                          # STEP + massas + malhas
python pos_processar.py saida/meshes.json ../docs/modelo-3d/meshes.json   # malhas do visualizador
python verificar_interferencias.py                   # confere colisões nos extremos de curso
```

- `export/mesa_cnc_plasma_1500x1000_STEP.zip`: montagem com 177 peças nomeadas pelo código da lista de materiais.
- Cotas de componentes comerciais (SBR20UU, SC20UU, SK20, NEMA23) são aproximadas: confira nas peças reais antes de furar.
- Eixos do modelo: X = comprimento, Y = largura, Z = altura. Coordenada de máquina = coordenada do modelo + (775 − 176) em X e + 525 em Y.
