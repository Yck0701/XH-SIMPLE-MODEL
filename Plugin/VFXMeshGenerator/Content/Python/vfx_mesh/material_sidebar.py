"""Project material instance parameters, edited in memory and explicitly saved."""
import time
import unreal
from . import engine,preview


@unreal.uclass()
class VFXScalarValue(unreal.Object):
    value=unreal.uproperty(float,meta={'Category':'参数','DisplayName':'数值'})

@unreal.uclass()
class VFXVectorValue(unreal.Object):
    value=unreal.uproperty(unreal.LinearColor,meta={'Category':'参数','DisplayName':'颜色'})

@unreal.uclass()
class VFXTextureValue(unreal.Object):
    value=unreal.uproperty(unreal.Texture,meta={'Category':'参数','DisplayName':'贴图'})

@unreal.uclass()
class VFXSwitchValue(unreal.Object):
    value=unreal.uproperty(bool,meta={'Category':'参数','DisplayName':'开关'})


KINDS=(('scalar','数值',VFXScalarValue),('vector','颜色 / 向量',VFXVectorValue),
       ('texture','贴图',VFXTextureValue),('static_switch','静态开关',VFXSwitchValue))
LIB=unreal.MaterialEditingLibrary


class Sidebar:
    def __init__(self,panel,parent):
        self.panel=panel;self.material=None;self.path=None;self.pending={};self.deadline=0.
        self.bindings={};self.generated=[];self.loading=False
        actions=panel.obj(unreal.HorizontalBox);panel.add(parent,actions)
        panel.button(actions,'刷新参数',lambda:self.refresh(True))
        panel.button(actions,'保存当前材质…',self.save)
        self.info=panel.text(parent,'选择材质实例即可在这里调整参数。',9,(.65,.7,.75,1))
        limit=panel.obj(unreal.SizeBox);limit.set_max_desired_height(300);panel.add(parent,limit)
        self.scroll=panel.obj(unreal.ScrollBox);limit.add_child(self.scroll)
        self.rows=panel.obj(unreal.VerticalBox);self.scroll.add_child(self.rows)
        self.refresh()

    def get(self,kind,name):
        return getattr(LIB,'get_material_instance_'+kind+'_parameter_value')(self.material,name)

    def refresh(self,force=False):
        self.flush()
        material=self.panel.current_material();path=material.get_path_name() if material else ''
        if path==self.path and not force:return
        self.material=material;self.path=path;self.loading=True
        self.rows.clear_children();self.bindings.clear()
        stale=set(self.generated);self.panel.refs[:]=[o for o in self.panel.refs if o not in stale];self.generated=[]
        start=len(self.panel.refs)
        try:
            p=self.panel
            if not material:
                self.info.set_text('选择材质实例即可在这里调整参数。');return
            if not isinstance(material,unreal.MaterialInstanceConstant) or not path.startswith('/Game/'):
                self.info.set_text('请创建当前工程的材质实例，即可在侧栏实时调参并单独保存。')
                p.button(self.rows,'创建材质实例…',self.create_instance)
                return
            count=0;skipped=0
            for kind,title,cls in KINDS:
                names=getattr(LIB,'get_'+kind+'_parameter_names')(material)
                if not names:continue
                p.heading(self.rows,title)
                for name in sorted(set(names),key=str):
                    if not preview.native().VFX_GlobalParameter(path,KINDS.index((kind,title,cls)),str(name)):
                        skipped+=1;continue
                    key=(kind,str(name));obj=cls();obj.set_editor_property('value',self.get(kind,name))
                    row=p.obj(unreal.HorizontalBox);p.add(self.rows,row)
                    override=p.obj(unreal.CheckBox)
                    enabled=LIB.is_material_instance_parameter_overridden(material,name)
                    override.set_is_checked(enabled);override.set_tool_tip_text('勾选以覆盖父材质参数；取消恢复继承值')
                    p.add(row,override)
                    view=p.obj(unreal.SinglePropertyView);view.set_property_name('value');view.set_name_override(str(name));view.set_object(obj)
                    view.set_is_enabled(enabled);view.set_tool_tip_text(str(name));p.fill(p.add(row,view))
                    self.bindings[key]=(obj,view,override)
                    view.on_property_changed.add_callable(self.value_handler(key))
                    override.on_check_state_changed.add_callable(self.override_handler(key))
                    count+=1
            message='勾选左侧覆盖后编辑；更改会影响所有引用此实例的对象。' if count else '此实例没有可编辑的常用参数，请在原生材质编辑器中检查。'
            if skipped:message+=' 分层或重名参数请用“编辑当前材质”调整。'
            self.info.set_text(message)
        finally:
            self.generated=self.panel.refs[start:];self.loading=False

    def value_handler(self,key):
        def changed(_):self.panel.safe(lambda:self.change(key))
        return changed

    def override_handler(self,key):
        def changed(value):self.panel.safe(lambda:self.override(key,value))
        return changed

    def change(self,key):
        if self.loading:return
        obj,_,_=self.bindings[key]
        self.pending[key]=obj.get_editor_property('value')
        if not self.deadline:self.deadline=time.monotonic()+.15
        self.panel.status.set_text('材质参数待更新，尚未保存。')

    def flush(self):
        if not self.pending:return
        material=self.material
        if not isinstance(material,unreal.MaterialInstanceConstant):raise ValueError('请先选择材质实例。')
        values=dict(self.pending)
        with unreal.ScopedEditorTransaction('特效网格：调整材质实例参数'):
            material.modify()
            for (kind,name),value in values.items():
                # Some engine builds report False even after a successful setter.
                getattr(LIB,'set_material_instance_'+kind+'_parameter_value')(material,name,value)
        self.pending.clear();self.deadline=0.
        self.panel.status.set_text('材质参数已实时更新，尚未保存。')

    def override(self,key,enabled):
        if self.loading:return
        self.flush()
        with unreal.ScopedEditorTransaction('特效网格：材质参数覆盖'):
            self.material.modify()
            if not LIB.set_material_instance_parameter_override(self.material,key[1],enabled):
                raise ValueError('该参数无法在侧栏覆盖，请在原生材质编辑器中调整。')
        obj,view,_=self.bindings[key]
        self.loading=True
        try:obj.set_editor_property('value',self.get(*key));view.set_is_enabled(enabled)
        finally:self.loading=False
        self.panel.status.set_text('材质参数已更新，尚未保存。')

    def tick(self):
        if self.deadline and time.monotonic()>=self.deadline:self.flush()

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
