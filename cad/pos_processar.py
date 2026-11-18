import json, sys
src, dst = sys.argv[1], sys.argv[2]
d = json.load(open(src))

MASS_FIX = {'quadro': 12.0, 'quadro_porta': 3.0, 'emergencia': 0.1, 'chave_geral': 0.2,
            'fonte_1': 1.0, 'fonte_2': 1.0, 'driver_1': 0.35, 'driver_2': 0.35, 'driver_3': 0.35,
            'driver_4': 0.35, 'placa_usb': 0.1, 'trilho_din': 0.3}
GVEC = {'estrutura': (0, 0, 0), 'guias_x': (0, 0, 140), 'portico': (0, 0, 420), 'carro_y': (320, 0, 720),
        'eixo_z': (620, 0, 720), 'eixo_z_movel': (900, 0, 720), 'eletrica': (-450, 0, 0),
        'visao': (0, 0, 380)}

def info(p):
    i, g = p['id'], p['grupo']
    if g == 'motores':
        if i.startswith('motor_x_dir'): return 'x', (0, 300, 420)
        if i.startswith('motor_x_esq'): return 'x', (0, -300, 420)
        if i.startswith('motor_y'):     return 'xy', (60, 0, 720)
        if i.startswith('motor_z'):     return 'xy', (620, 0, 880)
    if i.startswith('cam_tocha'):
        return 'xyz', (900, 120, 720)
    mov = {'portico': 'x', 'carro_y': 'xy', 'eixo_z': 'xy', 'eixo_z_movel': 'xyz'}.get(g, '')
    v = list(GVEC.get(g, (0, 0, 0)))
    if i == 'quadro_porta': v[0] -= 250
    if i in ('emergencia', 'chave_geral'): v[0] -= 250
    if i.startswith('pinhao_x_dir'): v = [0, 120, 420]
    if i.startswith('pinhao_x_esq'): v = [0, -120, 420]
    if i == 'pinhao_y': v = [140, 0, 720]
    if i.startswith('fc_x'): v = [-120, 0, 140]
    return mov, tuple(v)

for p in d['parts']:
    if p['id'] in MASS_FIX: p['massa'] = MASS_FIX[p['id']]
    p['mov'], p['exp'] = info(p)
json.dump(d, open(dst, 'w'), separators=(',', ':'))
tot = {}
for p in d['parts']:
    tot[p['grupo']] = tot.get(p['grupo'], 0) + p['massa']
print({k: round(v, 1) for k, v in tot.items()})
mov = sum(p['massa'] for p in d['parts'] if p['mov'])
movxy = sum(p['massa'] for p in d['parts'] if p['mov'] in ('xy', 'xyz'))
movz = sum(p['massa'] for p in d['parts'] if p['mov'] == 'xyz')
print('massa móvel X (pórtico completo): %.1f kg | carro Y completo: %.1f kg | Z móvel: %.1f kg' % (mov, movxy, movz))
