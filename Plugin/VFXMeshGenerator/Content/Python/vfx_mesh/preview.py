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
    dll.VFX_Wireframe.argtypes=[ctypes.c_wchar_p,ctypes.c_int];dll.VFX_Wireframe.restype=ctypes.c_int
    dll.VFX_SetComponent.argtypes=[ctypes.c_wchar_p]*3+[ctypes.c_int];dll.VFX_SetComponent.restype=ctypes.c_int
    dll.VFX_Shadow.argtypes=[ctypes.c_wchar_p,ctypes.c_int];dll.VFX_Shadow.restype=ctypes.c_int
    dll.VFX_Layout.argtypes=[ctypes.c_wchar_p]*4+[ctypes.POINTER(ctypes.c_double)];dll.VFX_Layout.restype=ctypes.c_int
    dll.VFX_LayoutSizes.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_double),ctypes.c_int];dll.VFX_LayoutSizes.restype=ctypes.c_int
    dll.VFX_WireOpacity.argtypes=[ctypes.c_wchar_p,ctypes.c_double];dll.VFX_WireOpacity.restype=ctypes.c_int
    for name,args in [('VFX_EnvironmentGet',[ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_double),ctypes.POINTER(ctypes.c_wchar),ctypes.c_int]),
                      ('VFX_EnvironmentSet',[ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_double),ctypes.c_wchar_p])]:
        fn=getattr(dll,name);fn.argtypes=args;fn.restype=ctypes.c_int
    dll.VFX_GlobalParameter.argtypes=[ctypes.c_wchar_p,ctypes.c_int,ctypes.c_wchar_p];dll.VFX_GlobalParameter.restype=ctypes.c_int
    for name,args in [('VFX_MaterialDetails',[ctypes.c_wchar_p]*2),('VFX_MaterialRefresh',[ctypes.c_wchar_p,ctypes.c_int]),('VFX_MaterialRevision',[ctypes.c_wchar_p])]:
        fn=getattr(dll,name);fn.argtypes=args;fn.restype=ctypes.c_int
    dll.VFX_MaterialDraft.argtypes=[ctypes.c_wchar_p];dll.VFX_MaterialDraft.restype=ctypes.c_wchar_p
    dll.VFX_MaterialCopy.argtypes=[ctypes.c_wchar_p]*2;dll.VFX_MaterialCopy.restype=ctypes.c_int
    dll.VFX_MaterialSaveDialog.argtypes=[ctypes.c_wchar_p]*3+[ctypes.c_int]*2+[ctypes.POINTER(ctypes.c_wchar),ctypes.c_int];dll.VFX_MaterialSaveDialog.restype=ctypes.c_int
    _dll=dll;return dll


class Preview:
    def __init__(self,panel,parent):
        self.panel=panel;self.dirty=True;self.frame_next=True;self.attached=False
        self.axes=True;self.grid=True;self.floor=True;self.environment=True;self.turntable=False;self.wireframe=False
        self.wire_opacity=35;self.last_opacity=None
        self.cast_shadow=True;self.last_shadow=None
        self.custom_asset=None;self.component=None;self.component_asset=None;self.animation_state=None;self.last_animation=None
        self.settings=geometry.defaults();self.material=None;self.dynamic=None;self.last_flags=None
        self.host=panel.obj(unreal.NativeWidgetHost)
        slot=panel.add(parent,self.host,0);slot.set_size(unreal.SlateChildSize(size_rule=unreal.SlateSizeRule.FILL))
        self.path=self.host.get_path_name();self.dll=None;self.error=False

    def update(self,settings,material,frame=False):
        self.settings=dict(settings);self.material=material;self.dirty=True;self.frame_next|=frame
        self.custom_asset=self.panel.custom_mesh

    def update_animation(self,state):
        asset=state['model'];animation=state['animation']
        if state['use_animation'] and animation and isinstance(asset,unreal.SkeletalMesh):
            if asset.get_editor_property('skeleton')!=animation.get_editor_property('skeleton'):
                raise ValueError('动画和模型的骨架不一致，请选择同骨架动画或先在 UE 中重定向。')
        self.animation_state=state

    def rewind(self):
        if isinstance(self.component,unreal.SkeletalMeshComponent):self.component.set_position(0.,False)

    def custom_preview(self):
        asset=self.custom_asset
        if self.component_asset!=asset:
            self.component_asset=asset;self.last_animation=None
            if isinstance(asset,unreal.StaticMesh):
                self.component=unreal.new_object(unreal.StaticMeshComponent)
                self.component.set_static_mesh(asset)
                self.dynamic=unreal.DynamicMesh()
                _,outcome=unreal.GeometryScript_AssetUtils.copy_mesh_from_static_mesh(asset,self.dynamic,
                    unreal.GeometryScriptCopyMeshFromAssetOptions(),unreal.GeometryScriptMeshReadLOD())
                if outcome!=unreal.GeometryScriptOutcomePins.SUCCESS:self.dynamic=None
            else:
                self.component=unreal.new_object(unreal.SkeletalMeshComponent)
                self.component.set_skeletal_mesh_asset(asset);self.dynamic=None
        # Only slot zero is preview-overridden. All other slots retain user materials.
        self.component.set_material(0,self.material)
        if not self.dll.VFX_SetComponent(self.path,self.component.get_path_name(),self.dynamic.get_path_name() if self.dynamic else '',int(self.frame_next)):
            raise RuntimeError('无法载入自定义模型预览。')
        if isinstance(self.component,unreal.SkeletalMeshComponent):
            self.component.set_update_animation_in_editor(True)
            self.component.set_editor_property('visibility_based_anim_tick_option',unreal.VisibilityBasedAnimTickOption.ALWAYS_TICK_POSE_AND_REFRESH_BONES)
        self.panel.preview_stats.set_text('自定义 '+('骨骼网格 · 动画仅用于预览；拓扑叠加不可用' if isinstance(asset,unreal.SkeletalMesh) else '静态网格')+' · '+asset.get_name())

    def frame(self):
        if self.dirty:self.frame_next=True
        elif self.attached:self.dll.VFX_Focus(self.path)
        else:self.frame_next=True

    def tick(self,dt):
        if self.error:return
        self.panel.ensure_layout()
        if self.attached and self.camera() is None:
            # UMG can release and rebuild Slate resources during editor changes.
            # Reattach without losing the local environment or model settings.
            environment=self.panel.environment_sidebar
            environment.pending=environment.snapshot();environment.initialized=False
            self.attached=False;self.dirty=True;self.last_flags=None;self.last_opacity=None;self.last_shadow=None
        if not self.attached:
            try:
                self.dll=native()
                if not self.dll.VFX_Attach(self.path):raise RuntimeError('无法创建原生预览视口。')
                self.attached=True
            except Exception:
                self.error=True;raise
        if self.dirty:
            if self.custom_asset:self.custom_preview()
            else:
                self.dynamic,stats=engine.dynamic_mesh(self.settings)
                if not self.dll.VFX_SetMesh(self.path,self.dynamic.get_path_name(),self.material.get_path_name() if self.material else '',int(self.frame_next)):
                    raise RuntimeError('预览网格更新失败。')
                self.component=None;self.component_asset=None;self.last_animation=None
                self.panel.preview_stats.set_text('%s 顶点  ·  %s 三角形  ·  UE 原生预览场景' % (stats['vertices'],stats['triangles']))
            self.dirty=False;self.frame_next=False
        if isinstance(self.component,unreal.SkeletalMeshComponent) and self.animation_state!=self.last_animation:
            state=self.animation_state
            if state:
                animation=state['animation'] if state['use_animation'] else None
                if not self.last_animation or animation!=(self.last_animation['animation'] if self.last_animation['use_animation'] else None):
                    self.component.set_animation_mode(unreal.AnimationMode.ANIMATION_SINGLE_NODE)
                    self.component.set_animation(animation);self.component.set_position(0.,False)
                self.component.play(state['looping']);self.component.set_editor_property('pause_anims',not state['playing'] or not animation)
                self.component.set_play_rate(state['play_rate'])
                self.last_animation=dict(state)
        flags=tuple(map(int,(self.axes,self.grid,self.floor,self.environment,self.turntable,self.wireframe)))
        if flags!=self.last_flags:
            self.dll.VFX_Options(self.path,*flags[:5]);self.dll.VFX_Wireframe(self.path,flags[5]);self.last_flags=flags
        if self.wire_opacity!=self.last_opacity:
            self.dll.VFX_WireOpacity(self.path,self.wire_opacity/100.);self.last_opacity=self.wire_opacity
        if self.cast_shadow!=self.last_shadow:
            self.dll.VFX_Shadow(self.path,int(self.cast_shadow));self.last_shadow=self.cast_shadow

    def camera(self):
        result=(ctypes.c_double*6)()
        if not self.attached or not self.dll.VFX_Camera(self.path,result):return None
        return list(result)
