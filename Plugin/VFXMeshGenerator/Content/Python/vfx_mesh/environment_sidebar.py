"""Transient preview lighting controls; never edit shared UE profiles or assets."""
import ctypes
import time
import unreal
from . import preview


from .settings_types import VFXPreviewEnvironmentSettings


# Keep the array ABI explicit and shared with Environment.h (20 double values).
FIELDS={0:'sky_intensity',1:'light_intensity',2:'sky_rotation',3:'light_pitch',4:'light_yaw',
        8:'exposure_bias',9:'bloom',10:'auto_exposure',14:'background_intensity',
        15:'show_environment',16:'show_floor',17:'post_processing',18:'floor_height',19:'sky_lighting'}
BOOLS={10,15,16,17,19}


class Sidebar:
    def __init__(self,panel,parent):
        self.panel=panel;self.loading=False;self.initialized=False;self.pending=None
        self.defaults=None;self.last=None;self.next_poll=0.
        actions=panel.obj(unreal.HorizontalBox);panel.add(parent,actions)
        panel.button(actions,'重置环境',self.reset,'恢复打开工作台时的 UE 预览环境')
        panel.text(parent,'仅调整此预览。HDRI 需使用 Texture Cube；K + 拖动旋转环境，L + 拖动旋转主光。',9,(.65,.7,.75,1))
        self.settings=VFXPreviewEnvironmentSettings()
        self.details=panel.obj(unreal.DetailsView)
        self.details.set_editor_property('force_hidden_property_visibility',True)
        self.details.set_editor_property('allow_filtering',True)
        self.details.set_object(self.settings);self.details.on_property_changed.add_callable(self.changed)
        panel.fill(panel.add(parent,self.details,0))

    def read(self):
        values=(ctypes.c_double*20)();path=ctypes.create_unicode_buffer(2048)
        if not preview.native().VFX_EnvironmentGet(self.panel.viewer.path,values,path,len(path)):
            raise RuntimeError('无法读取预览环境。')
        return (list(values),path.value)

    def snapshot(self):
        if not self.initialized:return self.pending
        v=[0.]*20
        for i,key in FIELDS.items():v[i]=float(self.settings.get_editor_property(key))
        for start,key in [(5,'light_color'),(11,'background_color')]:
            c=self.settings.get_editor_property(key);v[start:start+3]=[c.r,c.g,c.b]
        cube=self.settings.get_editor_property('environment_cube')
        return (v,cube.get_path_name() if cube else '')

    def sync(self,state):
        values,path=state;self.loading=True
        try:
            for i,key in FIELDS.items():self.settings.set_editor_property(key,bool(values[i]) if i in BOOLS else values[i])
            for start,key in [(5,'light_color'),(11,'background_color')]:
                self.settings.set_editor_property(key,unreal.LinearColor(*values[start:start+3],1))
            cube=self.settings.get_editor_property('environment_cube')
            if (cube.get_path_name() if cube else '')!=path:
                self.settings.set_editor_property('environment_cube',unreal.load_asset(path) if path else None)
            self.panel.viewer.environment=bool(values[15]);self.panel.viewer.floor=bool(values[16])
            self.last=state
        finally:self.loading=False

    def apply(self,state):
        values,path=state
        if not preview.native().VFX_EnvironmentSet(self.panel.viewer.path,(ctypes.c_double*20)(*values),path):
            raise ValueError('环境设置无效，请选择有效的 Texture Cube。')
        self.sync(self.read())

    def changed(self,_):
        if self.loading or not self.initialized:return
        self.panel.safe(lambda:self.apply(self.snapshot()))

    def reset(self):
        if self.defaults:
            self.apply(self.defaults);self.panel.status.set_text('已重置此预览环境。')

    def tick(self):
        if not self.panel.viewer.attached:return
        if not self.initialized:
            if self.defaults is None:self.defaults=self.read()
            self.initialized=True
            if self.pending:self.apply(self.pending);self.pending=None
            else:self.sync(self.defaults)
        if time.monotonic()>=self.next_poll:
            self.next_poll=time.monotonic()+.2
            state=self.read()
            if state!=self.last:self.sync(state)
