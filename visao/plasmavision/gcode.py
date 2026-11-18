"""Leitura, geometria e transformação de G-code de plasma (dialeto Mach3).

- interpretar(): extrai movimentos (G0/G1/G2/G3), pausas M0/M1 e avisos;
- Programa.polilinhas_corte(): trajetória de corte em mm para encaixe e simulação;
- transformar(): gira e desloca o programa no plano XY (arcos I/J inclusos),
  para casar o programa com a chapa encontrada pela câmera.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

_PALAVRA = re.compile(r"([A-Za-z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")
_COMENTARIO = re.compile(r"\([^)]*\)|;.*$")
_NAO_MOVIMENTO = {4.0, 10.0, 28.0, 30.0, 53.0, 92.0, 92.1, 92.2, 92.3}


def _separar(linha: str):
    """Divide a linha em pedaços: ('c', comentário) ou ('w', letra, texto_numero)."""
    partes, pos = [], 0
    s = linha.rstrip("\r\n")
    while pos < len(s):
        m_c = _COMENTARIO.match(s, pos)
        if m_c:
            partes.append(("c", m_c.group(0))); pos = m_c.end(); continue
        m_w = _PALAVRA.match(s, pos)
        if m_w:
            partes.append(("w", m_w.group(1).upper(), m_w.group(2))); pos = m_w.end(); continue
        partes.append(("c", s[pos])); pos += 1
    return partes


def _num(t: str) -> float:
    return float(t)


def _fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


@dataclass
class Movimento:
    tipo: int                  # 0, 1, 2, 3
    p0: tuple                  # (x, y, z) mm
    p1: tuple
    centro: tuple | None = None
    linha: int = 0

    @property
    def muda_xy(self) -> bool:
        return abs(self.p1[0] - self.p0[0]) > 1e-9 or abs(self.p1[1] - self.p0[1]) > 1e-9 or self.tipo in (2, 3)

    def pontos(self, passo_mm: float = 1.0) -> np.ndarray:
        """Pontos XY ao longo do movimento (inclui início e fim)."""
        a, b = np.array(self.p0[:2]), np.array(self.p1[:2])
        if self.tipo in (0, 1) or self.centro is None:
            return np.vstack([a, b])
        c = np.array(self.centro)
        r = float(np.hypot(*(a - c)))
        t0 = math.atan2(a[1] - c[1], a[0] - c[0])
        t1 = math.atan2(b[1] - c[1], b[0] - c[0])
        if self.tipo == 2:     # horário
            while t1 >= t0: t1 -= 2 * math.pi
        else:                  # anti-horário
            while t1 <= t0: t1 += 2 * math.pi
        if np.allclose(a, b):  # círculo completo
            t1 = t0 + (-2 * math.pi if self.tipo == 2 else 2 * math.pi)
        n = max(4, int(abs(t1 - t0) * r / max(passo_mm, 0.1)) + 1)
        t = np.linspace(t0, t1, n)
        return np.column_stack([c[0] + r * np.cos(t), c[1] + r * np.sin(t)])


@dataclass
class Programa:
    linhas: list[str]
    movimentos: list[Movimento] = field(default_factory=list)
    pausas: list[dict] = field(default_factory=list)   # {linha, msg, tipo, mov_idx}
    avisos: list[str] = field(default_factory=list)
    fator_mm: float = 1.0

    def polilinhas_corte(self, passo_mm: float = 1.0) -> list[np.ndarray]:
        """Trechos de corte contínuos (G1/G2/G3 com movimento em XY), em mm."""
        out, atual = [], []
        for m in self.movimentos:
            if m.tipo == 0 or not m.muda_xy:
                if m.tipo == 0 and len(atual) > 1:
                    out.append(np.vstack(atual)); atual = []
                continue
            pts = m.pontos(passo_mm)
            if atual and not np.allclose(atual[-1][-1], pts[0], atol=1e-6):
                out.append(np.vstack(atual)); atual = []
            atual.append(pts if not atual else pts[1:])
        if atual and sum(len(a) for a in atual) > 1:
            out.append(np.vstack(atual))
        return out

    def limites_corte(self) -> tuple[float, float, float, float] | None:
        polis = self.polilinhas_corte()
        if not polis:
            return None
        p = np.vstack(polis)
        return float(p[:, 0].min()), float(p[:, 1].min()), float(p[:, 0].max()), float(p[:, 1].max())

    def sequencia_tocha(self) -> list[str]:
        return [p["tipo"] for p in self.pausas]


def _tipo_pausa(msg: str, anterior: str | None) -> str:
    m = msg.upper()
    if any(k in m for k in ("ACEND", "LIGUE", "LIGAR", "FURA", "TORCH ON")):
        return "acender"
    if any(k in m for k in ("SOLT", "APAG", "DESLIG", "TORCH OFF")):
        return "soltar"
    return "soltar" if anterior == "acender" else "acender"


def interpretar(texto: str, ij_absoluto: bool = False) -> Programa:
    linhas = texto.splitlines()
    prog = Programa(linhas=linhas)
    mov_modal: int | None = None
    absoluto = True
    fator = 1.0
    ijabs = ij_absoluto
    pos = [0.0, 0.0, 0.0]
    for n, linha in enumerate(linhas):
        partes = _separar(linha)
        palavras = [(p[1], _num(p[2])) for p in partes if p[0] == "w"]
        comentarios = " ".join(p[1].strip("();") for p in partes if p[0] == "c" and len(p[1]) > 1).strip()
        gs = [v for l, v in palavras if l == "G"]
        ms = [v for l, v in palavras if l == "M"]
        nao_mov = [g for g in gs if g in _NAO_MOVIMENTO]
        for g in gs:
            if g in (0, 1, 2, 3): mov_modal = int(g)
            elif g == 90: absoluto = True
            elif g == 91: absoluto = False
            elif g == 20: fator = 25.4
            elif g == 21: fator = 1.0
            elif g == 90.1: ijabs = True
            elif g == 91.1: ijabs = False
        prog.fator_mm = fator
        if any(m in (0, 1) for m in ms):
            ant = prog.pausas[-1]["tipo"] if prog.pausas else None
            prog.pausas.append({"linha": n, "msg": comentarios, "tipo": _tipo_pausa(comentarios, ant),
                                "mov_idx": len(prog.movimentos)})
        eixos = {l: v for l, v in palavras if l in "XYZIJR"}
        if nao_mov:
            if any(g in (28.0, 30.0, 53.0, 92.0, 10.0) for g in nao_mov) and any(k in eixos for k in "XY"):
                prog.avisos.append(f"linha {n+1}: G{int(nao_mov[0])} não é transformado ({linha.strip()})")
            continue
        if not any(k in eixos for k in "XYZ") or mov_modal is None:
            continue
        novo = list(pos)
        for i, k in enumerate("XYZ"):
            if k in eixos:
                v = eixos[k] * fator
                novo[i] = v if absoluto else pos[i] + v
        centro = None
        if mov_modal in (2, 3):
            if "R" in eixos:
                centro = _centro_por_raio(pos, novo, eixos["R"] * fator, mov_modal == 2)
            else:
                i_ = eixos.get("I", 0.0) * fator
                j_ = eixos.get("J", 0.0) * fator
                centro = (i_, j_) if ijabs else (pos[0] + i_, pos[1] + j_)
        prog.movimentos.append(Movimento(mov_modal, tuple(pos), tuple(novo), centro, n))
        pos = novo
    return prog


def _centro_por_raio(p0, p1, r, horario):
    a, b = np.array(p0[:2]), np.array(p1[:2])
    d = float(np.hypot(*(b - a)))
    if d < 1e-9 or abs(r) < d / 2:
        return tuple(((a + b) / 2).tolist())
    h = math.sqrt(r * r - (d / 2) ** 2)
    meio = (a + b) / 2
    perp = np.array([-(b - a)[1], (b - a)[0]]) / d
    # R positivo = arco menor; horário + arco menor -> centro à direita do trajeto
    sinal = -1 if horario else 1
    if r < 0:
        sinal = -sinal
    c = meio + sinal * h * perp
    return tuple(c.tolist())


def _rot(v, cos_t, sin_t):
    return (v[0] * cos_t - v[1] * sin_t, v[0] * sin_t + v[1] * cos_t)


def transformar(texto: str, angulo_graus: float, dx: float, dy: float,
                pivo: tuple[float, float] = (0.0, 0.0), ij_absoluto: bool = False,
                cabecalho: bool = True) -> tuple[str, list[str]]:
    """Gira o programa de `angulo_graus` em torno de `pivo` e desloca de (dx, dy).

    Retorna (texto_novo, avisos). Coordenadas em mm (ou na unidade do programa para dx/dy
    se ele estiver em polegadas — G20)."""
    th = math.radians(angulo_graus)
    c, s = math.cos(th), math.sin(th)
    avisos: list[str] = []
    saida: list[str] = []
    if cabecalho:
        saida.append(f"(PlasmaVision: girado {angulo_graus:+.3f} graus, deslocado X{dx:+.2f} Y{dy:+.2f})")
    mov_modal: int | None = None
    absoluto = True
    ijabs = ij_absoluto
    pos = [0.0, 0.0]                 # posição no sistema ORIGINAL
    for n, linha in enumerate(texto.splitlines()):
        partes = _separar(linha)
        palavras = [(p[1], _num(p[2])) for p in partes if p[0] == "w"]
        gs = [v for l, v in palavras if l == "G"]
        for g in gs:
            if g in (0, 1, 2, 3): mov_modal = int(g)
            elif g == 90: absoluto = True
            elif g == 91: absoluto = False
            elif g == 90.1: ijabs = True
            elif g == 91.1: ijabs = False
        eixos = {l: v for l, v in palavras if l in "XYIJ"}
        if any(g in _NAO_MOVIMENTO for g in gs) or mov_modal is None or not eixos:
            if any(g in _NAO_MOVIMENTO for g in gs) and any(k in eixos for k in "XY"):
                avisos.append(f"linha {n+1} mantida sem transformar: {linha.strip()}")
            saida.append(linha)
            continue
        # alvo no sistema original
        alvo = list(pos)
        if "X" in eixos: alvo[0] = eixos["X"] if absoluto else pos[0] + eixos["X"]
        if "Y" in eixos: alvo[1] = eixos["Y"] if absoluto else pos[1] + eixos["Y"]
        novos: dict[str, float] = {}
        if "X" in eixos or "Y" in eixos:
            if absoluto:
                r = _rot((alvo[0] - pivo[0], alvo[1] - pivo[1]), c, s)
                novos["X"], novos["Y"] = r[0] + pivo[0] + dx, r[1] + pivo[1] + dy
            else:
                r = _rot((alvo[0] - pos[0], alvo[1] - pos[1]), c, s)
                novos["X"], novos["Y"] = r
        if mov_modal in (2, 3) and ("I" in eixos or "J" in eixos):
            i_, j_ = eixos.get("I", 0.0), eixos.get("J", 0.0)
            if ijabs:
                r = _rot((i_ - pivo[0], j_ - pivo[1]), c, s)
                novos["I"], novos["J"] = r[0] + pivo[0] + dx, r[1] + pivo[1] + dy
            else:
                novos["I"], novos["J"] = _rot((i_, j_), c, s)
        saida.append(_reescrever(partes, novos))
        pos = alvo
    return "\n".join(saida) + "\n", avisos


def _reescrever(partes, novos: dict[str, float]) -> str:
    out, feitos = [], set()
    for p in partes:
        if p[0] == "c":
            out.append(p[1]); continue
        l = p[1]
        if l in novos:
            out.append(f"{l}{_fmt(novos[l])}"); feitos.add(l)
            par = {"X": "Y", "I": "J"}.get(l)
            if par and par in novos and par not in feitos and not any(q[0] == "w" and q[1] == par for q in partes):
                out.append(f" {par}{_fmt(novos[par])}"); feitos.add(par)
        else:
            out.append(f"{l}{p[2]}")
        out.append(" ")
    for l in ("X", "Y", "I", "J"):
        if l in novos and l not in feitos:
            out.append(f"{l}{_fmt(novos[l])} ")
    return re.sub(r"\s+", " ", "".join(out)).strip()


def exemplo_peca(largura=200.0, altura=120.0, furo_d=40.0, kerf=1.6, entrada=4.0) -> str:
    """Programa de teste: retângulo com cantos retos e um furo no centro, já no padrão M0."""
    k = kerf / 2
    r = furo_d / 2 - k
    cx, cy = largura / 2, altura / 2
    L = [
        "(peca de teste PlasmaVision)", "G21 G90 G17", "G0 Z15",
        f"G0 X{_fmt(cx + r - entrada)} Y{_fmt(cy)}", "G0 Z3.8",
        "M0 (ACENDA A TOCHA, ESPERE FURAR E DE CYCLE START)",
        "G1 Z1.5 F300", f"G1 X{_fmt(cx + r)} Y{_fmt(cy)} F1500",
        f"G3 X{_fmt(cx + r)} Y{_fmt(cy)} I{_fmt(-r)} J0",
        "M0 (SOLTE A TOCHA E DE CYCLE START)", "G0 Z15",
        f"G0 X{_fmt(-k - entrada)} Y{_fmt(-k)}", "G0 Z3.8",
        "M0 (ACENDA A TOCHA, ESPERE FURAR E DE CYCLE START)",
        "G1 Z1.5 F300", f"G1 X{_fmt(-k)} Y{_fmt(-k)} F1800",
        f"G1 X{_fmt(-k)} Y{_fmt(altura + k)}", f"G1 X{_fmt(largura + k)}",
        f"G1 Y{_fmt(-k)}", f"G1 X{_fmt(-k + 0.5)}",
        "M0 (SOLTE A TOCHA E DE CYCLE START)", "G0 Z15", "M30",
    ]
    return "\n".join(L) + "\n"
