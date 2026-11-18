# Ponte Mach3

Programa pequeno que roda no PC do Mach3 e deixa o Raspberry (PlasmaVision) ler a posição da máquina e apertar Cycle Start, Feed Hold e Stop pela rede.

## Instalar

1. Instale o [Python 3](https://www.python.org/downloads/windows/) marcando “Add python.exe to PATH”.
2. No Prompt de Comando: `pip install pywin32`
3. Copie esta pasta para `C:\PlasmaVision\mach3_ponte`.
4. Edite `iniciar_ponte.bat` e troque `troque-este-token` pelo mesmo token do `config.yaml` do Raspberry.
5. Abra o Mach3 e depois dê dois cliques em `iniciar_ponte.bat`.
6. Libere a porta 8765 no Firewall do Windows (rede privada).
7. Para iniciar com o Windows: atalho do `.bat` em `shell:startup`.

Teste no navegador do próprio PC não funciona (precisa do cabeçalho X-Token). Teste pelo Raspberry:

```bash
curl -H "X-Token: SEU_TOKEN" http://IP_DO_PC:8765/status
```

Sem o Mach3 (só para testar a rede): `python ponte_mach3.py --simular --token SEU_TOKEN`.

## Como funciona

O Mach3 registra no Windows o objeto de automação `Mach4.Document` (nome herdado). A ponte pega o objeto de script dele, que tem as mesmas funções dos macros: `DoOEMButton`, `GetOEMDRO`, `GetOEMLED`, `IsMoving`, `Code` e `LoadFile`.

Os códigos usados estão em `ponte_config.json`:

| Chave | Padrão | O que é |
| --- | --- | --- |
| `botao_cycle_start` | 1000 | Botão OEM Cycle Start |
| `botao_feed_hold` | 1001 | Botão OEM Feed Hold |
| `botao_stop` | 1003 | Botão OEM Stop |
| `dro_maq_x/y/z` | 83/84/85 | DROs de coordenada de máquina |
| `dro_x/y/z` | 800/801/802 | DROs de coordenada de trabalho |
| `led_rodando` | 804 | LED de programa em execução |
| `usar_coordenadas_maquina` | true | A calibração da câmera usa coordenadas de máquina |

Se a sua versão do Mach3 usar outros números, troque no arquivo; não precisa mexer no código. Se `IsMoving` não existir, a ponte deduz o movimento pela variação da posição.

## Se o Mach3 não aparecer

- Abra o Mach3 **antes** da ponte e uma vez “como administrador” para ele registrar o COM.
- Python e Mach3 rodando com o mesmo usuário e o mesmo nível de permissão.
- Se mesmo assim falhar, o arquivo é salvo em `C:\PlasmaVision\jobs` e você carrega no Mach3 à mão; o resto (posição, botões) continua valendo.
