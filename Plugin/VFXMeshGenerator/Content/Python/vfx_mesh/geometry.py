"""Pure geometry; centimeters, Z-up, outward analytic normals, per-vertex UV seams."""
import math

FIELDS = {
    'width':('宽度',100.,.01,100000.),'length':('长度',100.,.01,100000.),
    'height':('高度',100.,.01,100000.),'divisions':('网格分段',1,1,64),
    'segments':('圆周分段',32,3,128),'rings':('纵向 / 径向分段',16,1,128),
    'radius':('半径',100.,.01,100000.),'inner':('内半径',40.,.01,100000.),
    'outer':('外半径',100.,.01,100000.),'topRadius':('上半径',50.,0.,100000.),
    'bottomRadius':('下半径',100.,0.,100000.),'top':('顶盖',False),'bottom':('底盖',False),
    'spiralStart':('起始半径',100.,.01,100000.),'spiralEnd':('结束半径',100.,.01,100000.),
    'spiralWidth':('面片带宽',30.,.01,100000.),'spiralHeight':('螺旋高度',200.,0.,100000.),
    'spiralTurns':('旋转圈数',2.5,.1,32.),'spiralSegments':('长度分段',128,3,512),
    'spiralRows':('宽度分段',1,1,32),'spiralReverse':('反向盘绕',False),
}
KINDS = {
    'plane':('平面',['width','length','divisions']),
    'box':('立方体',['width','length','height']),
    'sphere':('球',['radius','segments','rings']),
    'hemi':('半球',['radius','segments','rings','bottom']),
    'cylinder':('圆柱',['radius','height','segments','rings','top','bottom']),
    'frustum':('圆锥 / 圆台',['topRadius','bottomRadius','height','segments','rings','top','bottom']),
    'ring':('平面圆环',['inner','outer','segments','rings']),
    'cross':('十字面片',['width','height','divisions']),
    'spiral':('螺旋面片',['spiralStart','spiralEnd','spiralWidth','spiralHeight','spiralTurns','spiralSegments','spiralRows','spiralReverse']),
}
PRESETS={'zp':(0,0,0),'zn':(180,0,0),'xp':(0,90,0),'xn':(0,-90,0),'yp':(-90,0,0),'yn':(90,0,0)}

def defaults():
    return dict({k:v[1] for k,v in FIELDS.items()},shape='plane',flip=False,pivot='original',direction='zp',rx=0.,ry=0.,rz=0.,tx=0.,ty=0.,tz=0.)

def validate(settings):
    p=defaults();p.update(settings)
    if p['shape'] not in KINDS:raise ValueError('不支持的形状')
    if p['pivot'] not in ('original','center','bottom') or p['direction'] not in PRESETS:raise ValueError('原点或朝向无效')
    for key in KINDS[p['shape']][1]:
        field=FIELDS[key];value=p[key]
        if type(field[1]) is bool:
            if type(value) is not bool:raise ValueError(field[0]+'必须是开关')
        elif not isinstance(value,(float,int)) or not math.isfinite(value) or not field[2]<=value<=field[3]:
            raise ValueError(field[0]+'超出有效范围')
        elif type(field[1]) is int:
            if int(value)!=value:raise ValueError(field[0]+'必须为整数')
            p[key]=int(value)
    for key in ('rx','ry','rz','tx','ty','tz'):
        if not isinstance(p[key],(float,int)) or not math.isfinite(p[key]) or abs(p[key])>1000000:raise ValueError('旋转或位移无效')
    if p['shape']=='sphere' and p['rings']<2:raise ValueError('球的纵向分段至少为 2')
    if p['shape']=='ring' and p['outer']<=p['inner']:raise ValueError('外半径必须大于内半径')
    if p['shape']=='frustum' and max(p['topRadius'],p['bottomRadius'])<=0:raise ValueError('上下半径不能同时为 0')
    if p['shape']=='spiral':
        if min(p['spiralStart'],p['spiralEnd'])<=p['spiralWidth']/2:raise ValueError('起止半径必须大于带宽一半')
        if p['spiralSegments']<math.ceil(p['spiralTurns']*8):raise ValueError('长度分段至少为圈数的 8 倍')
    return p

def rotate(point,angles):
    x,y,z=point;a,b,c=map(math.radians,angles)
    y,z=y*math.cos(a)-z*math.sin(a),y*math.sin(a)+z*math.cos(a)
    x,z=x*math.cos(b)+z*math.sin(b),-x*math.sin(b)+z*math.cos(b)
    x,y=x*math.cos(c)-y*math.sin(c),x*math.sin(c)+y*math.cos(c)
    return (x,y,z)

def build(settings):
    p=validate(settings);kind=p['shape'];vertices=[];normals=[];uv=[];faces=[]
    def vert(P,T,N):
        length=math.sqrt(sum(x*x for x in N))
        if length<1e-15:raise ValueError('无效法线')
        vertices.append(tuple(P));uv.append(tuple(T));normals.append(tuple(x/length for x in N));return len(vertices)-1
    def tri(a,b,c):
        A,B,C=(vertices[i] for i in (a,b,c));u=[B[i]-A[i] for i in range(3)];w=[C[i]-A[i] for i in range(3)]
        cr=(u[1]*w[2]-u[2]*w[1],u[2]*w[0]-u[0]*w[2],u[0]*w[1]-u[1]*w[0])
        if sum(x*x for x in cr)<1e-24:return
        if sum(cr[i]*sum(normals[k][i] for k in (a,b,c)) for i in range(3))<0:b,c=c,b
        faces.append((a,b,c))
    def grid(nx,ny,fn):
        ids=[]
        for j in range(ny+1):
            for i in range(nx+1):
                u,t=i/nx,j/ny;P,N=fn(u,t);ids.append(vert(P,(u,t),N))
        for j in range(ny):
            for i in range(nx):
                k=j*(nx+1)+i;tri(ids[k],ids[k+1],ids[k+nx+1]);tri(ids[k+1],ids[k+nx+2],ids[k+nx+1])
    def cap(radius,z,sign):
        if radius<=0:return
        center=vert((0,0,z),(.5,.5),(0,0,sign));ids=[]
        for i in range(p['segments']+1):
            angle=i/p['segments']*math.tau;x,y=math.cos(angle),math.sin(angle)
            ids.append(vert((radius*x,radius*y,z),(.5+.5*x,.5+.5*y),(0,0,sign)))
        for i in range(p['segments']):tri(center,ids[i],ids[i+1])
    if kind=='plane':grid(p['divisions'],p['divisions'],lambda u,t:(((u-.5)*p['width'],(t-.5)*p['length'],0),(0,0,1)))
    elif kind=='box':
        dims=[p['width'],p['length'],p['height']]
        for axis in range(3):
            for sign in (-1,1):
                def box(u,t):
                    a,b=(axis+1)%3,(axis+2)%3;P=[0,0,0];N=[0,0,0]
                    P[axis]=sign*dims[axis]/2;P[a]=(u-.5)*dims[a];P[b]=(t-.5)*dims[b];N[axis]=sign;return P,N
                grid(1,1,box)
    elif kind in ('sphere','hemi'):
        def sphere(u,t):
            a=u*math.tau;b=t*math.pi/2 if kind=='hemi' else (t-.5)*math.pi
            r=math.cos(b);r=0. if abs(r)<1e-14 else r
            N=(r*math.cos(a),r*math.sin(a),math.sin(b));return tuple(x*p['radius'] for x in N),N
        grid(p['segments'],p['rings'],sphere)
        if kind=='hemi' and p['bottom']:cap(p['radius'],0,-1)
    elif kind in ('cylinder','frustum'):
        rb=p['radius'] if kind=='cylinder' else p['bottomRadius'];rt=p['radius'] if kind=='cylinder' else p['topRadius']
        def cylinder(u,t):
            a=u*math.tau;r=rb+(rt-rb)*t;x,y=math.cos(a),math.sin(a)
            return (r*x,r*y,(t-.5)*p['height']),(x,y,(rb-rt)/p['height'])
        grid(p['segments'],p['rings'],cylinder)
        if p['top']:cap(rt,p['height']/2,1)
        if p['bottom']:cap(rb,-p['height']/2,-1)
    elif kind=='ring':
        def ring(u,t):
            a=u*math.tau;r=p['inner']+(p['outer']-p['inner'])*t
            return (r*math.cos(a),r*math.sin(a),0),(0,0,1)
        grid(p['segments'],p['rings'],ring)
    elif kind=='cross':
        grid(p['divisions'],p['divisions'],lambda u,t:(((u-.5)*p['width'],0,(t-.5)*p['height']),(0,-1,0)))
        grid(p['divisions'],p['divisions'],lambda u,t:((0,(u-.5)*p['width'],(t-.5)*p['height']),(1,0,0)))
    elif kind=='spiral':
        omega=p['spiralTurns']*math.tau*(-1 if p['spiralReverse'] else 1)
        def spiral(u,t):
            a=u*omega;r=p['spiralStart']+(p['spiralEnd']-p['spiralStart'])*u+(t-.5)*p['spiralWidth'];c,s=math.cos(a),math.sin(a)
            return (r*c,r*s,(u-.5)*p['spiralHeight']),(p['spiralHeight']*s,-p['spiralHeight']*c,r*omega)
        grid(p['spiralSegments'],p['spiralRows'],spiral)
    # Remove unused pole seam vertices while preserving the UV seams used by faces.
    used=sorted(set(i for face in faces for i in face));mapping={old:new for new,old in enumerate(used)}
    vertices=[vertices[i] for i in used];normals=[normals[i] for i in used];uv=[uv[i] for i in used];faces=[tuple(mapping[i] for i in f) for f in faces]
    lo=[min(v[i] for v in vertices) for i in range(3)];hi=[max(v[i] for v in vertices) for i in range(3)]
    pivot=[0.,0.,0.]
    if p['pivot']!='original':
        pivot=[(lo[i]+hi[i])/2 for i in range(3)]
        if p['pivot']=='bottom':pivot[2]=lo[2]
    angles=[p[k] for k in ('rx','ry','rz')];offset=[p[k] for k in ('tx','ty','tz')]
    vertices=[tuple(x+offset[i] for i,x in enumerate(rotate(rotate([v[i]-pivot[i] for i in range(3)],PRESETS[p['direction']]),angles))) for v in vertices]
    normals=[rotate(rotate(n,PRESETS[p['direction']]),angles) for n in normals]
    if p['flip']:normals=[tuple(-x for x in n) for n in normals];faces=[(a,c,b) for a,b,c in faces]
    return dict(vertices=vertices,normals=normals,uv=uv,faces=faces)
