import itertools, sys
sys.argv=['x']
import modelo_mesa as M
from cadquery import Vector as V
parts=M.PARTS
def mov(p):
    i,g=p['id'],p['grupo']
    if i.startswith('cam_tocha'): return 'xyz'
    if g=='motores':
        return 'x' if i.startswith('motor_x') else 'xy'
    return {'portico':'x','carro_y':'xy','eixo_z':'xy','eixo_z_movel':'xyz'}.get(g,'')
for p in parts: p['mv']=mov(p)
g0,c0,z0=M.P['g'],M.P['c'],M.P['zb']
cfgs=[(-775,-525,885),(775,525,985),(-775,525,985),(775,-525,885),(0,0,885),(0,0,985)]
def place(p,g,c,z):
    s=p['shape']; m=p['mv']
    dx=(g-g0) if 'x' in m else 0; dy=(c-c0) if 'y' in m else 0; dz=(z-z0) if m=='xyz' else 0
    return s.translate(V(dx,dy,dz)) if (dx or dy or dz) else s
found={}
for (g,c,z) in cfgs:
    sh=[(p,place(p,g,c,z)) for p in parts]
    bbs=[s.BoundingBox() for _,s in sh]
    for a,b in itertools.combinations(range(len(sh)),2):
        pa,pb=sh[a][0],sh[b][0]
        if pa['mv']==pb['mv']: continue   # mesmo corpo rígido: ignorar (montagem)
        A,B=bbs[a],bbs[b]
        if A.xmax<=B.xmin or B.xmax<=A.xmin or A.ymax<=B.ymin or B.ymax<=A.ymin or A.zmax<=B.zmin or B.zmax<=A.zmin: continue
        try: v=sh[a][1].intersect(sh[b][1]).Volume()
        except Exception as e: v=-1
        if abs(v)>1:
            k=(pa['id'],pb['id']); found.setdefault(k,[]).append(((g,c,z),round(v)))
for k,v in sorted(found.items()): print(k, v[:3])
print('done', len(found))
