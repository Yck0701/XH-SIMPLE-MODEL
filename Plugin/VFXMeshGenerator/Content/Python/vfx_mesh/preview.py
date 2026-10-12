"""Native SEditorViewport + FAdvancedPreviewScene, matched to the running engine."""
import ctypes
import hashlib
import json
from pathlib import Path
import unreal
from . import engine,geometry

_dll=None


def _hash(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()


def native():
    global _dll
    if _dll:return _dll
    root=Path(unreal.Paths.engine_dir()).resolve()
    build=json.loads((root/'Binaries/Win64/UnrealEditor.modules').read_text())['BuildId']
    folder=Path(__file__).resolve().parents[3]/'Native/Win64'/build
    if not (folder/'manifest.json').is_file():raise RuntimeError('此引擎暂未包含匹配的原生预览组件，请更新插件或为当前引擎构建适配组件。')
    manifest=json.loads((folder/'manifest.json').read_text())
    for name,checksum in manifest['engine_sha256'].items():
        if _hash(root/name)!=checksum:raise RuntimeError('引擎组件与已验证的预览版本不匹配，请重新构建适配组件。')
    file=folder/'VFXNativePreview.dll'
    if _hash(file)!=manifest['dll_sha256']:raise RuntimeError('预览组件校验失败，请重新安装插件。')
    dll=ctypes.CDLL(str(file))
    for name,signature in [('VFX_Attach',[ctypes.c_wchar_p]),('VFX_SetMesh',[ctypes.c_wchar_p]*3+[ctypes.c_int]),
                          ('VFX_Options',[ctypes.c_wchar_p]+[ctypes.c_int]*5),('VFX_Focus',[ctypes.c_wchar_p]),
                          ('VFX_Camera',[ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_double)])]:
        fn=getattr(dll,name);fn.argtypes=signature;fn.restype=ctypes.c_int
    _dll=dll;return dll


class Preview:
    def __init__(self,panel,parent):
        self.panel=panel;self.dirty=True;self.frame_next=True;self.attached=False
        self.axes=True;self.grid=True;self.floor=True;self.environment=True;self.turntable=False
        self.settings=geometry.defaults();self.material=None;self.dynamic=None;self.last_flags=None
        self.host=panel.obj(unreal.NativeWidgetHost)
        slot=panel.add(parent,self.host,0);slot.set_size(unreal.SlateChildSize(size_rule=unreal.SlateSizeRule.FILL))
        self.path=self.host.get_path_name();self.dll=None;self.error=False

    def update(self,settings,material,frame=False):
        self.settings=dict(settings);self.material=material;self.dirty=True;self.frame_next|=frame

    def frame(self):
        if self.attached:self.dll.VFX_Focus(self.path)
        else:self.frame_next=True

    def tick(self,dt):
        if self.error:return
        if not self.attached:
            try:
                self.dll=native()
                if not self.dll.VFX_Attach(self.path):raise RuntimeError('无法创建原生预览视口。')
                self.attached=True
            except Exception:
                self.error=True;raise
        if self.dirty:
            self.dynamic,stats=engine.dynamic_mesh(self.settings)
            if not self.dll.VFX_SetMesh(self.path,self.dynamic.get_path_name(),self.material.get_path_name() if self.material else '',int(self.frame_next)):
                raise RuntimeError('预览网格更新失败。')
            self.panel.preview_stats.set_text('%s 顶点  ·  %s 三角形  ·  UE 原生预览场景' % (stats['vertices'],stats['triangles']))
            self.dirty=False;self.frame_next=False
        flags=tuple(map(int,(self.axes,self.grid,self.floor,self.environment,self.turntable)))
        if flags!=self.last_flags:self.dll.VFX_Options(self.path,*flags);self.last_flags=flags

    def camera(self):
        result=(ctypes.c_double*6)()
        if not self.attached or not self.dll.VFX_Camera(self.path,result):return None
        return list(result)
