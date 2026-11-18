"""
Mesa CNC Plasma 1500 x 1000 — modelo 3D de montagem (CadQuery)
Todas as cotas em mm.  Eixos: X = comprimento (movimento do pórtico),
Y = largura (movimento do carro), Z = vertical.

Como rodar:  pip install cadquery numpy
             python modelo_mesa.py saida
Gera saida/mesa_cnc_plasma_1500x1000.step, massas.json e meshes.json (visualizador).
Premissas (SBR20, cremalheira M1 15x15, pinhão Z20, comprimentos) ficam no bloco PARÂMETROS
e nas funções sbr20_rail / rack / pinion.
"""
import math, json, base64, sys
import numpy as np
import cadquery as cq
from cadquery import Vector as V

# ------------------------------------------------------------------ PARÂMETROS
P = dict(
    # posição de exibição dos eixos móveis
    g=150.0,      # posição X do pórtico (centro)
    c=100.0,      # posição Y do carro
    zb=935.0,     # base da placa móvel Z (885..985)
    # estrutura
    H_TOP=850.0,                  # topo das vigas laterais = base dos trilhos X
    SB_Y=650.0,                   # centro das vigas laterais (= centro trilho X)
    FR_X0=-900.0, FR_X1=1040.0,   # comprimento da estrutura
    # guias
    RX0=-875.0, RX1=875.0,        # trilho/cremalheira X (1750)
    RY=600.0,                     # trilho Y de -600 a +600 (1200)
)
g, c, zb = P['g'], P['c'], P['zb']
HT = P['H_TOP']; SBY = P['SB_Y']
FB = 6.35   # barra chata 1/4" de assentamento (trilhos) e de fixação (cremalheiras)

# ------------------------------------------------------------------ CORES / MATERIAIS
COL = {
    'estrutura': (0.20, 0.22, 0.25),
    'bandeja':   (0.45, 0.47, 0.50),
    'ripa':      (0.55, 0.40, 0.30),
    'trilho':    (0.80, 0.82, 0.85),
    'suporte_al':(0.70, 0.72, 0.75),
    'bloco':     (0.88, 0.89, 0.90),
    'chapa':     (0.16, 0.42, 0.72),
    'chapa_z':   (0.95, 0.55, 0.10),
    'motor':     (0.10, 0.10, 0.11),
    'motor_fl':  (0.65, 0.66, 0.68),
    'eixo':      (0.86, 0.87, 0.89),
    'pinhao':    (0.78, 0.62, 0.25),
    'cremalheira':(0.42, 0.42, 0.44),
    'fuso':      (0.75, 0.75, 0.70),
    'tocha':     (0.75, 0.10, 0.10),
    'tocha2':    (0.12, 0.12, 0.12),
    'quadro':    (0.82, 0.83, 0.80),
    'driver':    (0.12, 0.12, 0.14),
    'fonte':     (0.72, 0.74, 0.76),
    'placa_usb': (0.75, 0.12, 0.12),
    'sensor':    (0.10, 0.45, 0.20),
    'emerg':     (0.85, 0.05, 0.05),
    'pe':        (0.30, 0.30, 0.30),
    'visao':     (0.36, 0.40, 0.46),
    'camera':    (0.08, 0.30, 0.38),
    'pi':        (0.15, 0.55, 0.30),
    'led':       (0.95, 0.93, 0.80),
    'vidro':     (0.10, 0.25, 0.12),
}
DENS = {'aco': 7.85e-6, 'al': 2.70e-6}  # kg/mm3

PARTS = []   # (id, nome, grupo, cor, shape, meta)

def add(pid, nome, grupo, cor, shape, ref='', mat='aco', massa=None, desc=''):
    PARTS.append(dict(id=pid, nome=nome, grupo=grupo, cor=COL[cor], shape=shape,
                      ref=ref, mat=mat, massa=massa, desc=desc))

# ------------------------------------------------------------------ PRIMITIVAS
def box(x0, x1, y0, y1, z0, z1):
    return cq.Workplane().box(x1-x0, y1-y0, z1-z0).translate(((x0+x1)/2, (y0+y1)/2, (z0+z1)/2)).val()

def tube(x0, x1, y0, y1, z0, z1, t, axis):
    """tubo retangular oco ao longo de 'axis' ('x','y','z')"""
    outer = box(x0, x1, y0, y1, z0, z1)
    e = 1.0
    if axis == 'x':
        inner = box(x0-e, x1+e, y0+t, y1-t, z0+t, z1-t)
    elif axis == 'y':
        inner = box(x0+t, x1-t, y0-e, y1+e, z0+t, z1-t)
    else:
        inner = box(x0+t, x1-t, y0+t, y1-t, z0-e, z1+e)
    return outer.cut(inner)

def cyl(r, h, base=(0, 0, 0), d=(0, 0, 1)):
    return cq.Solid.makeCylinder(r, h, V(*base), V(*d))

def cone(r1, r2, h, base=(0, 0, 0), d=(0, 0, 1)):
    return cq.Solid.makeCone(r1, r2, h, V(*base), V(*d))

def rot(s, axis, ang, center=(0, 0, 0)):
    cx, cy, cz = center
    a = {'x': (1, 0, 0), 'y': (0, 1, 0), 'z': (0, 0, 1)}[axis]
    return s.rotate(V(cx, cy, cz), V(cx+a[0], cy+a[1], cz+a[2]), ang)

def mv(s, x=0, y=0, z=0):
    return s.translate(V(x, y, z))

# ---------- componentes comerciais (geometria local) ----------
def sbr20_rail(L):
    """SBR20: base 45, centro do eixo a 27 da base. Local: comprimento em +X, base em Z=0."""
    prof = (cq.Workplane('YZ').polyline([(-22.5, 0), (22.5, 0), (22.5, 5), (8, 10), (4.5, 19),
                                         (-4.5, 19), (-8, 10), (-22.5, 5)]).close().extrude(L).val())
    sup = prof
    shaft = cyl(10, L, (0, 0, 27), (1, 0, 0))
    return sup, shaft

def sbr20uu():
    """bloco aberto SBR20UU: W48 L50 F39; topo a 23 do centro do eixo. Local: centro do eixo na origem, eixo em X."""
    b = box(-25, 25, -24, 24, -16, 23)
    slot = box(-30, 30, -7, 7, -20, -2)
    bore = cyl(10.5, 60, (-30, 0, 0), (1, 0, 0))
    b = b.cut(slot).cut(bore)
    for sx in (-17.5, 17.5):
        for sy in (-17.5, 17.5):
            b = b.cut(cyl(2.5, 8, (sx, sy, 16), (0, 0, 1)))
    return b

def sc20uu():
    """bloco fechado SC20UU: W54 L50 H50, centro do eixo a 25 da base.
    Local: base no plano X=0 (bloco em +X), eixo vertical (Z) em X=25."""
    b = box(0, 50, -27, 27, -25, 25)
    b = b.cut(cyl(10.5, 60, (25, 0, -30)))
    b = b.cut(box(40, 52, -30, -20, -30, 30)).cut(box(40, 52, 20, 30, -30, 30))  # chanfros
    return b

def sk20():
    """suporte de eixo SK20 (W60, h31, T20). Local: base no plano X=0 (corpo em -X), eixo vertical em X=-31."""
    base = box(-10, 0, -30, 30, -10, 10)
    body = box(-51, 0, -17, 17, -10, 10)
    s = base.fuse(body).cut(cyl(10, 30, (-31, 0, -15)))
    s = s.cut(box(-52, -40, -1, 1, -11, 11))  # fenda de aperto
    return s

def nema23(body_len=81.5):
    """NEMA23. Local: face do flange em Z=0, corpo em -Z, eixo Ø8 x 25 em +Z."""
    body = box(-28.5, 28.5, -28.5, 28.5, -body_len+8, -6)
    for sx in (-1, 1):
        for sy in (-1, 1):
            body = body.cut(rot(box(-6, 6, -6, 6, -body_len, 2), 'z', 45).translate(V(28.5*sx, 28.5*sy, 0)))
    fl_front = box(-28.5, 28.5, -28.5, 28.5, -6, 0)
    fl_back = box(-28.5, 28.5, -28.5, 28.5, -body_len, -body_len+8)
    fl = fl_front.fuse(fl_back)
    for sx in (-1, 1):
        for sy in (-1, 1):
            fl = fl.cut(cyl(2.6, 20, (23.57*sx, 23.57*sy, -10)))
    boss = cyl(19.05, 1.6, (0, 0, 0))
    shaft = cyl(4, 25, (0, 0, 1.6))
    conn = box(-8, 8, 28.5, 36, -body_len+10, -body_len+30)
    return body.fuse(conn), fl.fuse(boss), shaft

def gear_profile(m, z, n_pts=4):
    r = m*z/2; ra = r + m; rf = r - 1.25*m
    pts = []
    for i in range(z):
        a0 = 2*math.pi*i/z
        hw_root = math.pi/z*0.62; hw_tip = math.pi/z*0.24
        for a, rr in ((a0-math.pi/z, rf), (a0-hw_root, rf), (a0-hw_tip, ra), (a0+hw_tip, ra), (a0+hw_root, rf)):
            pts.append((rr*math.cos(a), rr*math.sin(a)))
    return pts

def pinion(m=1.0, z=20, w=15, bore=8, hub_len=0):
    g_ = cq.Workplane('XY').polyline(gear_profile(m, z)).close().extrude(w).val()
    g_ = g_.cut(cyl(bore/2, w+2, (0, 0, -1)))
    return mv(g_, z=-w/2)   # centrado em Z

def rack(L, m=1.0, b=15, h=15):
    """cremalheira M1 15x15. Local: comprimento em +X (0..L), largura Y ±b/2, costas em Z=0, dentes em +Z."""
    p = math.pi*m
    hp = h - m            # linha primitiva (14)
    zr = hp - 1.25*m      # pé do dente
    pts = [(0, 0), (L, 0), (L, zr)]
    n = int(L/p)
    w_root = p/2 + 2*1.25*m*math.tan(math.radians(20))
    w_tip = p/2 - 2*m*math.tan(math.radians(20))
    x = L - p/2
    for i in range(n):
        xc = L - p/2 - i*p
        if xc - w_root/2 < 0: break
        pts += [(xc + w_root/2, zr), (xc + w_tip/2, h), (xc - w_tip/2, h), (xc - w_root/2, zr)]
    pts.append((0, zr))
    s = cq.Workplane('XZ').polyline(pts).close().extrude(-b/2).val()   # XZ extrude vai para -Y
    s2 = cq.Workplane('XZ').polyline(pts).close().extrude(b/2).val()
    return s.fuse(s2)

def microswitch():
    b = box(-10, 10, -3.2, 3.2, 0, 10)
    lever = box(-12, 6, -2, 2, 10, 11)
    return b.fuse(lever)

# ================================================================== ESTRUTURA
x0, x1 = P['FR_X0'], P['FR_X1']
for s, nm in ((1, 'dir'), (-1, 'esq')):
    y0, y1 = sorted((s*(SBY-25), s*(SBY+25)))
    add(f'viga_lat_{nm}', f'Viga lateral {nm.upper()} — tubo 100×50×3 × {x1-x0:.0f}', 'estrutura', 'estrutura',
        tube(x0, x1, y0, y1, HT-100-FB, HT-FB, 3, 'x'), ref='S-01')
    add(f'barra_trilho_{nm}', f'Barra chata 2"×1/4" de assentamento do trilho X {nm.upper()} (rosca M5)', 'estrutura', 'estrutura',
        box(x0, x1, y0, y1, HT-FB, HT), ref='S-13')
    add(f'barra_crem_{nm}', f'Barra chata 2"×1/4" de fixação da cremalheira X {nm.upper()} (rosca M5)', 'estrutura', 'estrutura',
        box(x0, x1, y0, y1, HT-100-2*FB, HT-100-FB), ref='S-13')
for xa, xb, nm in ((x0, x0+50, 'tras'), (x1-50, x1, 'frente')):
    add(f'viga_cab_{nm}', f'Travessa de cabeceira {nm} — tubo 100×50×3 × 1250', 'estrutura', 'estrutura',
        tube(xa, xb, -625, 625, HT-100-FB, HT-FB, 3, 'y'), ref='S-02')
LEG_X = [(x0, x0+50), (45, 95), (x1-50, x1)]
for i, (xa, xb) in enumerate(LEG_X):
    ztop = HT-FB if i == 1 else HT-100-FB
    for s, nm in ((1, 'dir'), (-1, 'esq')):
        y0, y1 = sorted((s*575, s*625))
        add(f'pe_{i}_{nm}', f'Pé {i+1} {nm.upper()} — tubo 50×50×2 × {ztop-40:.0f}', 'estrutura', 'estrutura',
            tube(xa, xb, y0, y1, 40, ztop, 2, 'z'), ref='S-03')
        # sapata niveladora
        foot = cyl(30, 8, ((xa+xb)/2, (y0+y1)/2, 0)).fuse(cyl(8, 32, ((xa+xb)/2, (y0+y1)/2, 8)))
        add(f'sapata_{i}_{nm}', 'Sapata niveladora M16', 'estrutura', 'pe', foot, ref='S-10')
# travessas inferiores
for s, nm in ((1, 'dir'), (-1, 'esq')):
    y0, y1 = sorted((s*575, s*625))
    add(f'trav_inf_long_a_{nm}', 'Travessa inferior longitudinal — tubo 50×50×2', 'estrutura', 'estrutura',
        tube(x0+50, 45, y0, y1, 150, 200, 2, 'x'), ref='S-04')
    add(f'trav_inf_long_b_{nm}', 'Travessa inferior longitudinal — tubo 50×50×2', 'estrutura', 'estrutura',
        tube(95, x1-50, y0, y1, 150, 200, 2, 'x'), ref='S-04')
for xa, xb, nm in ((x0, x0+50, 'tras'), (x1-50, x1, 'frente')):
    add(f'trav_inf_transv_{nm}', 'Travessa inferior transversal — tubo 50×50×2 × 1150', 'estrutura', 'estrutura',
        tube(xa, xb, -575, 575, 150, 200, 2, 'y'), ref='S-05')
# grelha de apoio da bandeja
for s, nm in ((1, 'dir'), (-1, 'esq')):
    y0, y1 = sorted((s*525, s*575))
    add(f'apoio_band_long_{nm}', 'Apoio da bandeja longitudinal — tubo 50×50×2', 'estrutura', 'estrutura',
        tube(x0+50, x1-50, y0, y1, 600, 650, 2, 'x'), ref='S-06')
for i, xc in enumerate((x0+25, -420, 70, 520, x1-25)):
    add(f'apoio_band_transv_{i}', 'Apoio da bandeja transversal — tubo 50×50×2 × 1150', 'estrutura', 'estrutura',
        tube(xc-25, xc+25, -575, 575, 650, 700, 2, 'y'), ref='S-07')
# bandeja d'água
TX0, TX1, TY = -840.0, 980.0, 570.0
tray = box(TX0, TX1, -TY, TY, 700, 830).cut(box(TX0+2, TX1-2, -TY+2, TY-2, 702, 840))
add('bandeja', f'Bandeja d’água — chapa #14 (2 mm) {TX1-TX0:.0f}×{2*TY:.0f}×130', 'estrutura', 'bandeja', tray, ref='S-08')
# suportes das ripas (barra chata com rasgos)
SLAT_X = [ -800 + 80*i for i in range(23) ]
for s, nm in ((1, 'dir'), (-1, 'esq')):
    yc = s*545
    hold = box(TX0+10, TX1-10, yc-1.5, yc+1.5, 702, 805)
    for xs in SLAT_X:
        hold = hold.cut(box(xs-2, xs+2, yc-3, yc+3, 790, 810))
    add(f'sup_ripas_{nm}', 'Suporte das ripas — barra chata 1/8"×4" com rasgos a cada 80', 'estrutura', 'ripa',
        hold, ref='S-09')
for i, xs in enumerate(SLAT_X):
    add(f'ripa_{i:02d}', 'Ripa — barra chata 1/8"×2" × 1120', 'estrutura', 'ripa',
        box(xs-1.5, xs+1.5, -560, 560, 790, 840), ref='S-11')

# ================================================================== GUIAS X (fixas)
RX0, RX1 = P['RX0'], P['RX1']
LX = RX1 - RX0
for s, nm in ((1, 'dir'), (-1, 'esq')):
    sup, shaft = sbr20_rail(LX)
    add(f'trilho_x_sup_{nm}', f'Suporte do trilho X {nm.upper()} (SBR20 × {LX:.0f})', 'guias_x', 'suporte_al',
        mv(sup, RX0, s*SBY, HT), ref='K-05', mat='al')
    add(f'trilho_x_eixo_{nm}', f'Eixo Ø20 do trilho X {nm.upper()}', 'guias_x', 'trilho',
        mv(shaft, RX0, s*SBY, HT), ref='K-05')
    r = rack(LX)
    r = rot(r, 'x', 180)                    # dentes para baixo
    add(f'crem_x_{nm}', f'Cremalheira X {nm.upper()} M1 15×15 × {LX:.0f} (dentes p/ baixo)', 'guias_x', 'cremalheira',
        mv(r, RX0, s*662.5, HT-100-2*FB), ref='K-06')
    # fim de curso / home X
    sw = mv(microswitch(), -886, s*640, HT)
    add(f'fc_x_{nm}', f'Fim de curso / home X{"1" if s>0 else "2"}', 'guias_x', 'sensor', sw, ref='E-08')

# ================================================================== PÓRTICO (gantry) – local g=0
def G(s_):  # move para posição do pórtico
    return mv(s_, x=g)

for s, nm in ((1, 'dir'), (-1, 'esq')):
    y0, y1 = sorted((s*615, s*695))
    add(f'base_portico_{nm}', 'Placa base do pórtico — aço 10 mm 250×80', 'portico', 'chapa',
        G(box(-125, 125, y0, y1, 900, 910)), ref='P-01')
    for bx in (-75, 75):
        b = mv(sbr20uu(), bx, s*SBY, HT+27)
        add(f'bloco_x_{nm}_{"a" if bx<0 else "b"}', 'Pillow block aberto SBR20UU (X)', 'portico', 'bloco', G(b), ref='K-07', mat='al')
    # placa do motor X (pendurada) com rasgos de ajuste de pré-carga
    y0, y1 = sorted((s*685, s*693))
    hp = box(-100, 100, y0, y1, 675, 900)
    zc = HT-100-2*FB-14-10   # linha primitiva - raio primitivo
    hp = hp.cut(box(-19.5, 19.5, y0-1, y1+1, zc-19.5, zc+19.5+8))
    for sx in (-1, 1):
        for sz in (-1, 1):
            hp = hp.cut(box(23.57*sx-2.7, 23.57*sx+2.7, y0-1, y1+1, zc+23.57*sz-2.7, zc+23.57*sz+2.7+8))
    add(f'placa_motor_x_{nm}', 'Placa do motor X — aço 8 mm 200×210 (rasgos p/ pré-carga)', 'portico', 'chapa', G(hp), ref='P-02')
    # motor X
    body, fl, sh = nema23()
    tr = []
    for part in (body, fl, sh):
        q = rot(part, 'x', 90 if s > 0 else -90)   # eixo do motor aponta para dentro (-Y no lado dir)
        q = mv(q, 0, s*693, zc)
        tr.append(q)
    add(f'motor_x_{nm}_corpo', f'Motor NEMA23 25 kgf·cm — eixo X{"1" if s>0 else "2"}', 'motores', 'motor', G(tr[0]), ref='K-01', massa=1.14*0.85)
    add(f'motor_x_{nm}_flange', f'Motor X{"1" if s>0 else "2"} — flanges', 'motores', 'motor_fl', G(tr[1]), ref='K-01', massa=1.14*0.15)
    add(f'motor_x_{nm}_eixo', f'Motor X{"1" if s>0 else "2"} — eixo Ø8', 'motores', 'eixo', G(tr[2]), ref='K-01', massa=0.0)
    pn = rot(pinion(), 'x', 90)
    add(f'pinhao_x_{nm}', 'Pinhão M1 Z20 furo 8 (X)', 'portico', 'pinhao', G(mv(pn, 0, s*662.5, zc)), ref='K-06')
    # montante
    y0, y1 = sorted((s*645, s*655))
    add(f'montante_{nm}', 'Montante do pórtico — aço 10 mm 200×80', 'portico', 'chapa', G(box(-100, 100, y0, y1, 910, 990)), ref='P-03')
    y0, y1 = sorted((s*600, s*700))
    add(f'flange_viga_{nm}', 'Flange de apoio da viga — aço 10 mm 200×100', 'portico', 'chapa', G(box(-100, 100, y0, y1, 990, 1000)), ref='P-04')
    for gx in (-95, 95):
        tri = (cq.Workplane('YZ').polyline([(s*655, 910), (s*695, 910), (s*655, 985)]).close().extrude(6).val())
        add(f'mao_francesa_{nm}_{gx}', 'Mão-francesa — aço 6 mm', 'portico', 'chapa', G(mv(tri, gx-3, 0, 0)), ref='P-05')

# viga do pórtico
add('viga_portico', 'Viga do pórtico — tubo 100×50×3 × 1400', 'portico', 'estrutura', G(tube(-25, 25, -700, 700, 1000, 1100, 3, 'y')), ref='S-12')
for s in (1, -1):
    y0, y1 = sorted((s*700, s*703))
    add(f'tampa_viga_{"d" if s>0 else "e"}', 'Tampa da viga — aço 3 mm', 'portico', 'estrutura', G(box(-25, 25, y0, y1, 1000, 1100)), ref='S-12')
RY = P['RY']
# trilho Y superior
sup, shaft = sbr20_rail(2*RY)
for nm, part, cor, mat in (('sup', sup, 'suporte_al', 'al'), ('eixo', shaft, 'trilho', 'aco')):
    q = rot(part, 'z', 90); q = mv(q, 0, -RY, 1100+FB)
    add(f'trilho_y1_{nm}', f'Trilho Y superior SBR20 × {2*RY:.0f} — {nm}', 'portico', cor, G(q), ref='K-05', mat=mat)
# trilho Y frontal (base na face +X da viga)
sup, shaft = sbr20_rail(2*RY)
for nm, part, cor, mat in (('sup', sup, 'suporte_al', 'al'), ('eixo', shaft, 'trilho', 'aco')):
    q = rot(part, 'z', 90); q = rot(q, 'y', 90); q = mv(q, 25, -RY, 1050)
    add(f'trilho_y2_{nm}', f'Trilho Y frontal SBR20 × {2*RY:.0f} — {nm}', 'portico', cor, G(q), ref='K-05', mat=mat)
# cremalheira Y sob a viga
r = rack(2*RY); r = rot(r, 'x', 180); r = rot(r, 'z', 90)
add('crem_y', f'Cremalheira Y M1 15×15 × {2*RY:.0f} (sob a viga, dentes p/ baixo)', 'portico', 'cremalheira',
    G(mv(r, -17.5, -RY, 1000-FB)), ref='K-06')
add('barra_trilho_y', 'Barra chata 2"×1/4" de assentamento do trilho Y superior', 'portico', 'estrutura', G(box(-25, 25, -RY, RY, 1100, 1100+FB)), ref='S-13')
add('barra_crem_y', 'Barra chata 2"×1/4" de fixação da cremalheira Y', 'portico', 'estrutura', G(box(-25, 25, -RY, RY, 1000-FB, 1000)), ref='S-13')
add('fc_y', 'Fim de curso / home Y', 'portico', 'sensor', G(mv(rot(microswitch(), 'z', 90), 0, -612, 1100+FB)), ref='E-08')

# ================================================================== CARRO Y – local c=0
def C(s_):
    return mv(s_, x=g, y=c)

for by in (-50, 50):
    b = rot(sbr20uu(), 'z', 90); b = mv(b, 0, by, 1127+FB)
    add(f'bloco_y_sup_{by}', 'Pillow block aberto SBR20UU (Y superior)', 'carro_y', 'bloco', C(b), ref='K-07', mat='al')
    b = rot(sbr20uu(), 'z', 90); b = rot(b, 'y', 90); b = mv(b, 52, by, 1050)
    add(f'bloco_y_fr_{by}', 'Pillow block aberto SBR20UU (Y frontal)', 'carro_y', 'bloco', C(b), ref='K-07', mat='al')
add('carro_y_topo', 'Placa superior do carro Y — aço 10 mm 112×180', 'carro_y', 'chapa', C(box(-37, 75, -90, 90, 1150+FB, 1160+FB)), ref='P-06')
fp = box(75, 85, -90, 90, 1003, 1340)
add('carro_y_frontal', 'Placa frontal do carro Y / base do eixo Z — aço 10 mm 180×337', 'carro_y', 'chapa', C(fp), ref='P-07')
rp = box(-37, -31, -60, 60, 930, 1150+FB)
zc = 1000-FB-14-10
rp = rp.cut(box(-38, -30, -19.5, 19.5, zc-19.5, zc+19.5+8))
for sy in (-1, 1):
    for sz in (-1, 1):
        rp = rp.cut(box(-38, -30, 23.57*sy-2.7, 23.57*sy+2.7, zc+23.57*sz-2.7, zc+23.57*sz+2.7+8))
add('carro_y_traseira', 'Placa do motor Y — aço 6 mm 120×215 (rasgos p/ pré-carga)', 'carro_y', 'chapa', C(rp), ref='P-08')
for gy in (-85, 80):
    tri = cq.Workplane('XZ').polyline([(75, 1160+FB), (75, 1230+FB), (35, 1160+FB)]).close().extrude(-5).val()
    add(f'carro_y_mf_{gy}', 'Mão-francesa do carro — aço 5 mm', 'carro_y', 'chapa', C(mv(tri, 0, gy, 0)), ref='P-09')
body, fl, sh = nema23()
tr = [mv(rot(p_, 'y', 90), -37, 0, zc) for p_ in (body, fl, sh)]
add('motor_y_corpo', 'Motor NEMA23 25 kgf·cm — eixo Y', 'motores', 'motor', C(tr[0]), ref='K-01', massa=1.14*0.85)
add('motor_y_flange', 'Motor Y — flanges', 'motores', 'motor_fl', C(tr[1]), ref='K-01', massa=1.14*0.15)
add('motor_y_eixo', 'Motor Y — eixo Ø8', 'motores', 'eixo', C(tr[2]), ref='K-01', massa=0.0)
add('pinhao_y', 'Pinhão M1 Z20 furo 8 (Y)', 'carro_y', 'pinhao', C(mv(rot(pinion(), 'y', 90), -17.5, 0, zc)), ref='K-06')

# ================================================================== EIXO Z
for zcen in (1030, 1140):
    for sy in (-45, 45):
        b = mv(sc20uu(), 85, sy, zcen)
        add(f'bloco_z_{zcen}_{sy}', 'Pillow block fechado SC20UU (Z)', 'eixo_z', 'bloco', C(b), ref='K-08', mat='al')
add('mancal_inf_z', 'Suporte inferior do fuso — aço 8 mm', 'eixo_z', 'chapa', C(box(75, 125, -12, 12, 995, 1003)), ref='P-10')
add('rolamento_inf_z', 'Rolamento 608ZZ c/ alojamento', 'eixo_z', 'bloco', C(cyl(11, 7, (110, 0, 1003))), ref='Z-06')
add('suporte_motor_z', 'Suporte do motor Z — aço 10 mm 60×80 (ou suporte L do kit)', 'eixo_z', 'chapa',
    C(box(85, 145, -40, 40, 1330, 1340).cut(cyl(19.5, 20, (110, 0, 1325)))), ref='P-11')
for gy in (-85, 80):
    tri = cq.Workplane('XZ').polyline([(85, 1330), (145, 1330), (85, 1270)]).close().extrude(-5).val()
    add(f'mf_motor_z_{gy}', 'Mão-francesa do suporte Z — aço 5 mm', 'eixo_z', 'chapa', C(mv(tri, 0, gy, 0)), ref='P-11')
body, fl, sh = nema23()
tr = [mv(rot(p_, 'x', 180), 110, 0, 1340) for p_ in (body, fl, sh)]
add('motor_z_corpo', 'Motor NEMA23 25 kgf·cm — eixo Z', 'motores', 'motor', C(tr[0]), ref='K-01', massa=1.14*0.85)
add('motor_z_flange', 'Motor Z — flanges', 'motores', 'motor_fl', C(tr[1]), ref='K-01', massa=1.14*0.15)
add('motor_z_eixo', 'Motor Z — eixo Ø8', 'motores', 'eixo', C(tr[2]), ref='K-01', massa=0.0)
add('acopl_z', 'Acoplamento flexível 8×8 mm', 'eixo_z', 'motor_fl', C(cyl(10, 30, (110, 0, 1300))), ref='Z-05')
add('fuso_z', 'Fuso trapezoidal T8 passo 2 × 300', 'eixo_z', 'fuso', C(cyl(4, 300, (110, 0, 1008))), ref='Z-03')
add('fc_z', 'Fim de curso / home Z', 'eixo_z', 'sensor', C(mv(rot(microswitch(), 'y', 90), 92, 62, 1300)), ref='E-08')
# --- conjunto móvel Z (zb)
def Zm(s_):
    return mv(s_, x=g, y=c, z=zb)
for sy in (-45, 45):
    add(f'eixo_z_{sy}', 'Eixo retificado Ø20 × 300 (Z)', 'eixo_z_movel', 'eixo', Zm(cyl(10, 300, (110, sy, 0))), ref='Z-01')
    for zz in (10, 290):
        add(f'sk20_{sy}_{zz}', 'Suporte de eixo SK20', 'eixo_z_movel', 'bloco', Zm(mv(sk20(), 141, sy, zz)), ref='Z-02', mat='al')
add('placa_z_movel', 'Placa móvel Z (porta-tocha) — aço 10 mm 160×300', 'eixo_z_movel', 'chapa_z', Zm(box(141, 151, -80, 80, 0, 300)), ref='P-12')
add('suporte_castanha', 'Suporte da castanha — aço 8 mm', 'eixo_z_movel', 'chapa_z', Zm(box(99, 141, -15, 15, 130, 138).cut(cyl(5.5, 20, (110, 0, 120)))), ref='P-13')
nut = cyl(11, 3.5, (110, 0, 138)).fuse(cyl(5.1, 15, (110, 0, 141.5))).cut(cyl(4, 30, (110, 0, 130)))
add('castanha', 'Castanha T8 (latão) c/ flange', 'eixo_z_movel', 'pinhao', Zm(nut), ref='Z-03')
for zz in (15, 90):
    cl = box(151, 200, -25, 25, zz, zz+25).cut(cyl(16.2, 40, (176, 0, zz-5)))
    cl = cl.cut(box(185, 201, -1, 1, zz-1, zz+26))
    add(f'abracadeira_tocha_{zz}', 'Abraçadeira da tocha Ø32 — alumínio/aço', 'eixo_z_movel', 'chapa_z', Zm(cl), ref='P-14')
torch = cyl(16, 215, (176, 0, -15)).fuse(cone(7, 16, 30, (176, 0, -45)))
add('tocha', 'Tocha de plasma (corpo Ø32 — referência)', 'eixo_z_movel', 'tocha', Zm(torch), ref='T-01', massa=1.0)
add('tocha_bico', 'Bico/capa da tocha', 'eixo_z_movel', 'tocha2', Zm(cyl(7, 12, (176, 0, -57)).fuse(cyl(3.5, 2, (176, 0, -59)))), ref='T-01', massa=0.0)
add('tocha_cabo', 'Cabo da tocha (saída)', 'eixo_z_movel', 'tocha2', Zm(cyl(9, 120, (176, 0, 200))), ref='T-01', massa=0.2)

# ================================================================== QUADRO ELÉTRICO
QX0, QX1 = -1110.0, -910.0
cab = box(QX0, QX1, -250, 250, 350, 750).cut(box(QX0-1, QX1-2, -248, 248, 352, 748))
add('quadro', 'Quadro elétrico 500×400×200 (aço)', 'eletrica', 'quadro', cab, ref='E-01')
for sy in (-200, 200):
    add(f'quadro_sup_{sy}', 'Suporte do quadro — barra chata 2"×1/4"', 'eletrica', 'estrutura', box(-910, -900, sy-25, sy+25, 150, 760), ref='E-01')
add('quadro_porta', 'Porta do quadro', 'eletrica', 'quadro', box(QX0-2, QX0, -250, 250, 350, 750), ref='E-01')
add('emergencia', 'Botão de emergência (cogumelo)', 'eletrica', 'emerg', cyl(20, 22, (QX0-2, 150, 690), (-1, 0, 0)).fuse(cyl(14, 14, (QX0-2, 150, 690), (-1, 0, 0))), ref='E-05')
add('chave_geral', 'Chave geral rotativa', 'eletrica', 'tocha2', cyl(22, 10, (QX0-2, 150, 600), (-1, 0, 0)).fuse(box(QX0-32, QX0-12, 145, 155, 585, 615)), ref='E-04')
for i, yc in enumerate((-125, 125)):
    add(f'fonte_{i+1}', f'Fonte chaveada 48 V 10 A nº{i+1}', 'eletrica', 'fonte', box(-970, -920, yc-107.5, yc+107.5, 610, 725), ref='K-03')
for i, yc in enumerate((-150, -80, -10, 60)):
    nm = ['X1', 'X2 (A)', 'Y', 'Z'][i]
    add(f'driver_{i+1}', f'Driver DM556 — {nm}', 'eletrica', 'driver', box(-995, -920, yc-16, yc+16, 440, 558), ref='K-02')
add('placa_usb', 'Placa controladora USB Mach3 (4 eixos)', 'eletrica', 'placa_usb', box(-932, -920, 120, 230, 440, 520), ref='K-04')
add('trilho_din', 'Trilho DIN + bornes', 'eletrica', 'fonte', box(-930, -920, -220, 220, 380, 395), ref='E-06')

# ================================================================== VISÃO COMPUTACIONAL
# pórtico fixo da câmera na estação dos pés centrais (X 45..95), por fora dos motores X
for s, nm in ((1, 'dir'), (-1, 'esq')):
    y0, y1 = sorted((s*825, s*875))
    add(f'poste_cam_{nm}', f'Poste da câmera {nm.upper()} — tubo 50×50×2 × 1890', 'visao', 'visao',
        tube(45, 95, y0, y1, 40, 1930, 2, 'z'), ref='V-01')
    add(f'sapata_cam_{nm}', 'Sapata niveladora M16 (poste da câmera)', 'visao', 'pe',
        cyl(30, 8, (70, s*850, 0)).fuse(cyl(8, 32, (70, s*850, 8))), ref='S-10')
    y0, y1 = sorted((s*625, s*825))
    for zt in (150, 560):
        add(f'tirante_cam_{nm}_{zt}', 'Tirante poste ↔ pé central — tubo 50×50×2 × 200', 'visao', 'visao',
            tube(45, 95, y0, y1, zt, zt+50, 2, 'y'), ref='V-02')
add('travessa_cam', 'Travessa da câmera — tubo 50×50×2 × 1750', 'visao', 'visao', tube(45, 95, -875, 875, 1930, 1980, 2, 'y'), ref='V-03')
add('camera_topo', 'Câmera do topo — Raspberry Pi Camera Module 3 Wide em caixa', 'visao', 'camera',
    box(40, 100, -30, 30, 1885, 1930).fuse(cyl(9, 12, (70, 0, 1873))), ref='V-04')
add('lente_topo', 'Lente da câmera do topo', 'visao', 'vidro', cyl(6, 2, (70, 0, 1871)), ref='V-04')
add('raspberry', 'Raspberry Pi 5 em caixa IP54 (sobre a travessa)', 'visao', 'pi', box(10, 130, 60, 180, 1980, 2025), ref='V-05')
for s in (1, -1):
    y0, y1 = sorted((s*150, s*700))
    add(f'led_{"d" if s>0 else "e"}', 'Barra de LED 24 V (iluminação difusa)', 'visao', 'led', box(55, 85, y0, y1, 1915, 1930), ref='V-06')
# câmera da tocha (anda com o Z)
add('cam_tocha_braco', 'Braço da câmera da tocha — alumínio 8 mm', 'visao', 'chapa_z', Zm(box(151, 236, 70, 120, 12, 20)), ref='V-07')
add('cam_tocha', 'Câmera USB da tocha (olhando o bico)', 'visao', 'camera', Zm(box(200, 236, 94, 130, -26, 12)), ref='V-08')
add('cam_tocha_lente', 'Lente da câmera da tocha', 'visao', 'vidro', Zm(cyl(7, 8, (218, 94, -8), (0, -1, 0))), ref='V-08')
add('cam_tocha_filtro', 'Filtro de solda tonalidade 10–11 + vidro de sacrifício', 'visao', 'vidro', Zm(box(205, 231, 82, 84, -22, 4)), ref='V-09')
add('modulo_gpio', 'Módulo optoacoplador + 2 relés (saídas do Raspberry)', 'eletrica', 'pi', box(-940, -920, -235, -165, 395, 440), ref='V-10')

# ================================================================== SAÍDA
if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else 'out'
    import os; os.makedirs(out, exist_ok=True)
    # massas
    tot = {}
    for p_ in PARTS:
        if p_['massa'] is None:
            p_['massa'] = p_['shape'].Volume() * DENS[p_['mat']]
        tot[p_['grupo']] = tot.get(p_['grupo'], 0) + p_['massa']
    json.dump({k: round(v, 2) for k, v in tot.items()}, open(f'{out}/massas.json', 'w'), indent=1)
    print('massas por grupo (kg):', {k: round(v, 1) for k, v in tot.items()})
    # STEP
    from cadquery.occ_impl.exporters.assembly import exportAssembly
    assy = cq.Assembly(name='Mesa_CNC_Plasma_1500x1000')
    for p_ in PARTS:
        assy.add(p_['shape'], name=p_['id'], color=cq.Color(*p_['cor']))
    exportAssembly(assy, f'{out}/mesa_cnc_plasma_1500x1000.step')
    # malhas p/ visualizador
    meshes = []
    for p_ in PARTS:
        tol = 0.25 if p_['grupo'] not in ('estrutura',) else 0.5
        vs, ts = p_['shape'].tessellate(tol, 0.35)
        pos = np.array([[v.x, v.y, v.z] for v in vs], dtype=np.float32)
        idx = np.array(ts, dtype=np.uint32).ravel()
        meshes.append(dict(id=p_['id'], nome=p_['nome'], grupo=p_['grupo'], ref=p_['ref'],
                           cor=[round(x, 3) for x in p_['cor']], massa=round(p_['massa'], 3),
                           pos=base64.b64encode(pos.tobytes()).decode(),
                           idx=base64.b64encode(idx.astype(np.uint32).tobytes()).decode(),
                           nv=len(pos), nt=len(idx)//3))
    json.dump(dict(params=P, parts=meshes), open(f'{out}/meshes.json', 'w'))
    print('peças:', len(PARTS), ' triângulos:', sum(m['nt'] for m in meshes))
