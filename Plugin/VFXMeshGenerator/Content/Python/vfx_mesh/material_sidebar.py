"""Native UE material parameter details with explicit single-instance saves."""
import unreal
from . import engine,preview
LIB=unreal.MaterialEditingLibrary


class Sidebar:
    def __init__(self,panel,parent):
        self.panel=panel;self.material=None;self.path=None;self.attached=False
        self.show_hidden=False;self.revision=0
        actions=panel.obj(unreal.HorizontalBox);panel.add(parent,actions)
        panel.button(actions,'刷新参数',lambda:self.refresh(True))
        panel.button(actions,'保存当前材质…',self.save)
        self.hidden=panel.obj(unreal.CheckBox);panel.text(self.hidden,'显示未激活参数')
        self.hidden.on_check_state_changed.add_callable(self.hidden_changed);panel.add(parent,self.hidden)
        self.info=panel.text(parent,'选择材质实例，在右侧原生细节栏调整。',9,(.65,.7,.75,1))
        self.create_button=panel.button(parent,'创建材质实例…',self.create_instance)
        self.host=panel.obj(unreal.NativeWidgetHost);panel.fill(panel.add(parent,self.host,0))
        self.host_path=self.host.get_path_name()
        self.refresh()

    def hidden_changed(self,value):
        self.show_hidden=value
        if self.attached:preview.native().VFX_MaterialRefresh(self.host_path,int(value))

    def refresh(self,force=False):
        material=self.panel.current_material();path=material.get_path_name() if material else ''
        changed=path!=self.path;self.material=material;self.path=path
        editable=isinstance(material,unreal.MaterialInstanceConstant) and path.startswith('/Game/')
        self.create_button.set_visibility(unreal.SlateVisibility.COLLAPSED if editable or not material else unreal.SlateVisibility.VISIBLE)
        self.hidden.set_is_enabled(editable)
        self.info.set_text('参数分组与控件沿用 UE 材质细节栏。更改会影响所有引用此实例的对象；保存需单独确认。' if editable else '请创建当前工程的材质实例后调整参数。' if material else '选择材质实例，在右侧原生细节栏调整。')
        if changed:self.attached=False
        if self.attached and force:preview.native().VFX_MaterialRefresh(self.host_path,int(self.show_hidden))

    def flush(self):
        # UE property handles apply directly to the in-memory instance.
        pass

    def tick(self):
        dll=preview.native()
        if not self.attached:
            editable=isinstance(self.material,unreal.MaterialInstanceConstant) and self.path.startswith('/Game/')
            if not dll.VFX_MaterialDetails(self.host_path,self.path if editable else ''):
                raise RuntimeError('无法创建 UE 原生材质细节栏。')
            self.attached=True;self.revision=dll.VFX_MaterialRevision(self.host_path)
            if self.show_hidden:dll.VFX_MaterialRefresh(self.host_path,1)
        revision=dll.VFX_MaterialRevision(self.host_path)
        if revision!=self.revision:
            self.revision=revision;self.panel.status.set_text('材质参数已实时更新，尚未保存。')

    def save(self):
        material=self.material
        if not isinstance(material,unreal.MaterialInstanceConstant):raise ValueError('请选择材质实例，或先创建一个材质实例。')
        path=material.get_path_name()
        if not path.startswith('/Game/'):raise ValueError('请使用当前工程 Content 下的材质实例。')
        if not self.panel.confirm('确认保存当前材质实例','仅保存以下材质实例：\n'+path+'\n\n不会保存模型、母材质、关卡或其他资产。是否继续？'):
            self.panel.status.set_text('已取消材质保存，没有写入文件。');return False
        self.flush()
        if not unreal.EditorAssetLibrary.save_loaded_asset(material):raise RuntimeError('材质实例保存失败。')
        self.panel.status.set_text('已保存当前材质实例；模型需要时请单独保存。');return True

    def create_instance(self):
        parent=self.panel.current_material()
        if not parent:raise ValueError('请先选择一个母材质。')
        path=engine.new_asset_path(self.panel.settings,str(self.panel.folder.get_text()),'MI_'+parent.get_name())
        if not self.panel.confirm('确认创建材质实例','父材质：'+parent.get_path_name()+'\n新实例：'+path+'\n\n创建并保存该实例，然后用于当前模型。不会保存模型或修改父材质。是否继续？'):return False
        folder,name=path.rsplit('/',1)
        material=unreal.AssetToolsHelpers.get_asset_tools().create_asset(name,folder,unreal.MaterialInstanceConstant,unreal.MaterialInstanceConstantFactoryNew())
        if not material:raise RuntimeError('创建材质实例失败。')
        LIB.set_material_instance_parent(material,parent)
        if not unreal.EditorAssetLibrary.save_loaded_asset(material):raise RuntimeError('新材质实例保存失败。')
        self.panel.pickers.set_editor_property('material',material);self.panel.picker_changed(None)
        self.panel.status.set_text('材质实例已创建。可在侧栏勾选参数覆盖并调整；模型尚未保存。')
        return True
