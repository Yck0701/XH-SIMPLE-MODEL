"""Compact editor workspace with isolated preview and explicit single-asset saves."""
import copy
import time
import traceback
import unreal
from . import geometry, engine, preview, material_sidebar

WIDGET='/VFXMeshGenerator/UI/EUW_VFXMesh'
TAB='VFXMeshGenerator.Workspace'
_panel=None
_watch=None
_blueprint=None
_next_tab_check=0.
_tab_open=False


@unreal.uclass()
class VFXUserMaterialSettings(unreal.Object):
    material=unreal.uproperty(unreal.MaterialInterface,meta={'Category':'当前材质','DisplayName':'材质 / 材质实例'})


class Panel:
    def __init__(self,widget):
        self.widget=widget;self.mesh=None;self.settings=geometry.defaults()
        self.surface={'mode':'custom','material':''};self.live=True;self.loading=False
        self.pending=None;self.geometry_dirty=False;self.surface_dirty=False
        self.history=[];self.last_applied=None;self.unsaved=False
        self.controls={};self.rows={};self.refs=[]
        self.root=widget.find_child_widget_by_name('VerticalBox_0')
        if not self.root:raise RuntimeError('插件面板模板缺失。')
        self.root.clear_children();self.build_ui();self.last_tick=time.monotonic();self.last_applied=self.snapshot()

    def obj(self,cls):
        cls=getattr(unreal,'EditorUtility'+cls.__name__,cls)
        obj=unreal.new_object(cls,outer=self.widget)
        if isinstance(obj,(unreal.SpinBox,unreal.ComboBoxString)):
            font=obj.get_editor_property('font');font.set_editor_property('size',10);obj.set_editor_property('font',font)
        if isinstance(obj,unreal.ComboBoxString):obj.set_editor_property('foreground_color',unreal.SlateColor(specified_color=unreal.LinearColor(.8,.85,.9,1)))
        self.refs.append(obj);return obj

    def add(self,parent,child,padding=1):
        slot=parent.add_child(child)
        if hasattr(slot,'set_padding'):slot.set_padding(unreal.Margin(padding,padding,padding,padding))
        return slot

    def fill(self,slot):slot.set_size(unreal.SlateChildSize(size_rule=unreal.SlateSizeRule.FILL))

    def text(self,parent,label,size=10,color=None):
        obj=self.obj(unreal.TextBlock);obj.set_text(label)
        font=obj.get_editor_property('font');font.set_editor_property('size',size);obj.set_font(font)
        obj.set_auto_wrap_text(True)
        if color:obj.set_color_and_opacity(unreal.SlateColor(specified_color=unreal.LinearColor(*color)))
        self.add(parent,obj);return obj

    def heading(self,parent,label):return self.text(parent,label,11,(.35,.8,.75,1))

    def button(self,parent,label,action,tip=''):
        b=self.obj(unreal.Button);self.text(b,label)
        if tip:b.set_tool_tip_text(tip)
        b.on_clicked.add_callable(lambda:self.safe(action));self.add(parent,b,2);return b

    def safe(self,action):
        try:return action()
        except Exception as error:
            self.pending=None;self.status.set_text(str(error));unreal.log_warning('[VFX Mesh] '+traceback.format_exc())

    def row(self,parent,label,width=90):
        row=self.obj(unreal.HorizontalBox);self.add(parent,row,1)
        box=self.obj(unreal.SizeBox);box.set_width_override(width);self.add(row,box,0);self.text(box,label)
        return row

    def spin(self,parent,key,label,value,lo,hi,target='geometry',width=90):
        row=self.row(parent,label,width);obj=self.obj(unreal.SpinBox)
        obj.set_min_value(float(lo));obj.set_max_value(float(hi));obj.set_min_slider_value(float(lo))
        obj.set_max_slider_value(float(min(hi,max(1000,value*3))))
        obj.set_value(float(value));integer=type(value) is int
        obj.set_editor_property('delta',1. if integer else .1)
        obj.set_editor_property('min_fractional_digits',0);obj.set_editor_property('max_fractional_digits',0 if integer else 2)
        obj.set_editor_property('min_desired_width',58.)
        obj.on_value_changed.add_callable(lambda v:self.change(target,key,int(round(v)) if integer else float(v)))
        self.fill(self.add(row,obj));self.controls[(target,key)]=obj;self.rows[(target,key)]=row
        return obj

    def check(self,parent,key,label,value,target='geometry'):
        obj=self.obj(unreal.CheckBox);obj.set_is_checked(value);self.text(obj,label)
        obj.on_check_state_changed.add_callable(lambda v:self.change(target,key,v))
        self.add(parent,obj);self.controls[(target,key)]=obj;self.rows[(target,key)]=obj;return obj

    def combo(self,parent,key,label,choices):
        row=self.row(parent,label);obj=self.obj(unreal.ComboBoxString)
        for _,title in choices:obj.add_option(title)
        obj.set_selected_option(dict(choices)[self.settings[key]])
        mapping={title:k for k,title in choices}
        obj.on_selection_changed.add_callable(lambda title,_:self.change('geometry',key,mapping[title]))
        self.fill(self.add(row,obj));self.controls[('geometry',key)]=obj;return obj

    def build_ui(self):
        border=self.obj(unreal.Border);border.set_padding(unreal.Margin(8,8,8,8));border.set_brush_color(unreal.LinearColor(.018,.023,.029,1))
        self.fill(self.add(self.root,border,0))
        minimum=self.obj(unreal.SizeBox);minimum.set_min_desired_width(1020);minimum.set_min_desired_height(650);border.add_child(minimum)
        body=self.obj(unreal.VerticalBox);minimum.add_child(body)
        top=self.obj(unreal.HorizontalBox);self.add(body,top)
        self.text(top,'特效网格工作台',14)
        self.button(top,'新建预览',self.new)
        self.button(top,'载入所选模型',self.load_selected)
        self.button(top,'保存模型…',self.save,'每次确认后只保存当前模型，不保存材质或其他脏资产')
        self.button(top,'定位模型',self.locate)
        self.asset_label=self.text(body,'预览草稿 · 尚未创建资产',10,(.65,.7,.75,1))
        columns=self.obj(unreal.HorizontalBox);self.fill(self.add(body,columns))
        left_box=self.obj(unreal.SizeBox);left_box.set_width_override(350);self.add(columns,left_box,3)
        scroll=self.obj(unreal.ScrollBox);left_box.add_child(scroll);self.scroll=scroll
        left=self.obj(unreal.VerticalBox);scroll.add_child(left)
        self.heading(left,'形状与尺寸 · cm')
        self.combo(left,'shape','形状',[(k,v[0]) for k,v in geometry.KINDS.items()])
        for key,field in geometry.FIELDS.items():
            if type(field[1]) is bool:self.check(left,key,field[0],field[1])
            else:self.spin(left,key,*field)
        self.heading(left,'原点与变换')
        self.combo(left,'pivot','原点',[('original','原始位置'),('center','包围盒中心'),('bottom','底部中心')])
        self.combo(left,'direction','朝向',list(zip(geometry.PRESETS,['+Z','-Z','+X','-X','+Y','-Y'])))
        self.check(left,'flip','翻转法线',False)
        for prefix,title,limit in [('r','旋转',360.),('t','位移',100000.)]:
            row=self.obj(unreal.HorizontalBox);self.add(left,row)
            self.text(row,title)
            for axis in 'xyz':
                box=self.obj(unreal.VerticalBox);self.fill(self.add(row,box,0))
                self.spin(box,prefix+axis,axis.upper(),0.,-limit,limit,width=16)
        self.heading(left,'使用自己的材质')
        self.pickers=VFXUserMaterialSettings();details=self.obj(unreal.DetailsView)
        details.set_editor_property('force_hidden_property_visibility',True);details.set_editor_property('allow_filtering',False)
        details.set_object(self.pickers);details.on_property_changed.add_callable(self.picker_changed);self.add(left,details)
        actions=self.obj(unreal.HorizontalBox);self.add(left,actions)
        self.button(actions,'使用所选材质',self.use_selected_material)
        self.button(actions,'编辑当前材质',self.edit_material,'打开该材质或材质实例的 UE 原生编辑器')
        self.material_sidebar=material_sidebar.Sidebar(self,left)
        self.heading(left,'更新与撤回')
        actions=self.obj(unreal.HorizontalBox);self.add(left,actions)
        self.check(actions,'live','实时更新',True,'options')
        self.button(actions,'应用',self.apply);self.button(actions,'撤回',self.undo)
        self.heading(left,'UE 内容目录')
        self.folder=self.obj(unreal.EditableTextBox);self.folder.set_text('/Game/VFXMeshes');self.folder.set_is_read_only(True);self.add(left,self.folder)
        actions=self.obj(unreal.HorizontalBox);self.add(left,actions)
        self.button(actions,'浏览内容目录',self.browse_folder)
        self.button(actions,'使用所选目录',self.pick_folder)
        self.name=self.obj(unreal.EditableTextBox);self.name.set_hint_text('模型名，留空自动命名');self.add(left,self.name)
        self.text(left,'在内容浏览器选文件夹，再点“使用所选目录”。已有模型保存在原位置。',9,(.6,.65,.7,1))
        right=self.obj(unreal.VerticalBox);self.fill(self.add(columns,right,5))
        actions=self.obj(unreal.HorizontalBox);self.add(right,actions)
        self.heading(actions,'实时预览')
        self.button(actions,'聚焦',lambda:self.viewer.frame())
        self.text(actions,'左键旋转 · 中键平移 · 滚轮 / 右键缩放 · F 聚焦',9,(.65,.7,.75,1))
        self.viewer=preview.Preview(self,right)
        options=self.obj(unreal.HorizontalBox);self.add(right,options)
        for key,label,value in [('wireframe','模型线框',False),('axes','坐标轴 / 原点',True),('grid','网格',True),('floor','地板',True),('environment','环境',True),('turntable','自动旋转',False)]:self.check(options,key,label,value,'preview')
        self.preview_stats=self.text(right,'预览正在准备…',10,(.65,.7,.75,1))
        self.text(right,'预览场景独立于当前关卡。保存后的模型可指定给 Niagara Mesh Renderer。',9,(.65,.7,.75,1))
        self.status=self.text(body,'拖动参数即可预览；确认保存后才会新建模型资产。',10,(.65,.85,.7,1))
        self.visibility()

    def visibility(self):
        fields=geometry.KINDS[self.settings['shape']][1]
        for key in geometry.FIELDS:self.rows[('geometry',key)].set_visibility(unreal.SlateVisibility.VISIBLE if key in fields else unreal.SlateVisibility.COLLAPSED)

    def change(self,target,key,value):
        if self.loading:return
        if target=='preview':setattr(self.viewer,key,value);return
        if target=='options':
            self.live=value
            if value:self.queue()
            return
        if self.settings[key]==value:return
        self.settings[key]=value;self.geometry_dirty=True;self.unsaved=True
        if key=='shape':self.visibility()
        self.queue()

    def current_material(self):
        path=self.surface.get('material','')
        material=unreal.load_asset(path) if path else None
        if material and not isinstance(material,unreal.MaterialInterface):raise ValueError('请选择材质或材质实例。')
        return material

    def picker_changed(self,_):
        if self.loading:return
        material=self.pickers.get_editor_property('material')
        self.surface={'mode':'custom','material':material.get_path_name() if material else ''}
        self.material_sidebar.refresh()
        self.surface_dirty=True;self.unsaved=True;self.queue()

    def use_selected_material(self):
        assets=[a for a in unreal.EditorUtilityLibrary.get_selected_assets() if isinstance(a,unreal.MaterialInterface)]
        if len(assets)!=1:raise ValueError('请在内容浏览器选中一个材质或材质实例。')
        self.pickers.set_editor_property('material',assets[0]);self.picker_changed(None)

    def edit_material(self):
        material=self.current_material()
        if not material:raise ValueError('请先指定自己的材质或材质实例。')
        unreal.get_editor_subsystem(unreal.AssetEditorSubsystem).open_editor_for_assets([material])

    def queue(self):
        if self.live:
            if self.pending is None:self.pending=time.monotonic()+.2
        else:self.status.set_text('参数待应用。')

    def snapshot(self):return copy.deepcopy((self.settings,self.surface))

    def sync_controls(self):
        self.loading=True
        try:
            labels={'shape':{k:v[0] for k,v in geometry.KINDS.items()},'pivot':{'original':'原始位置','center':'包围盒中心','bottom':'底部中心'},'direction':dict(zip(geometry.PRESETS,['+Z','-Z','+X','-X','+Y','-Y']))}
            for (target,key),control in self.controls.items():
                value=getattr(self.viewer,key) if target=='preview' else self.live if target=='options' else self.settings[key]
                if isinstance(control,unreal.SpinBox):control.set_value(float(value))
                elif isinstance(control,unreal.CheckBox):control.set_is_checked(value)
                else:control.set_selected_option(labels[key][value])
            self.pickers.set_editor_property('material',self.current_material());self.visibility()
        finally:self.loading=False
        self.material_sidebar.refresh()
        self.viewer.update(self.settings,self.current_material())

    def confirm(self,title,message):
        return unreal.EditorDialog.show_message(title,message,unreal.AppMsgType.YES_NO,unreal.AppReturnType.NO)==unreal.AppReturnType.YES

    def can_switch(self):
        return not (self.unsaved or self.geometry_dirty or self.surface_dirty) or self.confirm('切换模型','当前模型有未保存的修改。继续切换？\n本操作不会自动保存任何资产。')

    def new(self):
        if not self.can_switch():return
        self.mesh=None;self.settings=geometry.defaults();self.surface={'mode':'custom','material':''}
        self.history=[];self.last_applied=None;self.pending=None;self.unsaved=False
        self.geometry_dirty=self.surface_dirty=False;self.sync_controls();self.viewer.frame()
        self.last_applied=self.snapshot()
        self.asset_label.set_text('预览草稿 · 尚未创建资产');self.status.set_text('新建预览不会写入资产；满意后点击“保存模型…”。')

    def load_selected(self):
        mesh=engine.selected();data=engine.read(mesh)
        if not self.can_switch():return
        self.mesh=mesh;self.settings=data['geometry'];material=mesh.get_material(0)
        self.surface={'mode':'custom','material':material.get_path_name() if material else ''}
        self.pending=None;self.history=[];self.last_applied=self.snapshot();self.unsaved=False
        self.geometry_dirty=self.surface_dirty=False;self.sync_controls();self.viewer.frame()
        self.asset_label.set_text(mesh.get_path_name());self.folder.set_text(mesh.get_path_name().rsplit('/',1)[0])
        self.status.set_text('已载入。实时调参更新同一个模型；保存需单独确认。')

    def apply(self,record=True):
        self.pending=None;geometry.validate(self.settings);material=self.current_material()
        if self.mesh:
            if self.geometry_dirty:engine.update(self.mesh,self.settings,self.surface)
            if self.surface_dirty:
                with unreal.ScopedEditorTransaction('特效网格：设置当前模型材质'):
                    self.mesh.modify();self.mesh.set_material(0,material)
            if self.geometry_dirty or self.surface_dirty:engine.write_settings(self.mesh,self.settings,self.surface)
        current=self.snapshot()
        if record and self.last_applied and current!=self.last_applied:
            self.history.append(self.last_applied);self.history=self.history[-20:]
        self.last_applied=current;self.geometry_dirty=self.surface_dirty=False
        self.viewer.update(self.settings,material)
        self.status.set_text('模型已更新，尚未保存。' if self.mesh else '预览已更新，尚未创建资产。')

    def undo(self):
        self.pending=None
        if self.last_applied and self.snapshot()!=self.last_applied:
            self.settings,self.surface=copy.deepcopy(self.last_applied);self.geometry_dirty=self.surface_dirty=False
        elif self.history:
            self.settings,self.surface=copy.deepcopy(self.history.pop());self.geometry_dirty=self.surface_dirty=True
            self.apply(record=False);self.unsaved=True
        else:raise ValueError('没有可撤回的参数。')
        self.sync_controls();self.status.set_text('已撤回，未执行保存。')

    def browse_folder(self):
        unreal.EditorUtilityLibrary.sync_browser_to_folders(['/Game'])
        self.status.set_text('在内容浏览器选文件夹，再点“使用所选目录”。')

    def pick_folder(self):
        self.folder.set_text(engine.content_folder());self.status.set_text('新模型保存目录已从 UE 内容浏览器获取。')

    def save(self):
        geometry.validate(self.settings);self.current_material()
        path=self.mesh.get_path_name().split('.')[0] if self.mesh else engine.new_asset_path(self.settings,str(self.folder.get_text()),str(self.name.get_text()))
        if not self.confirm('确认保存当前模型','仅保存以下模型及其生成参数：\n'+path+'\n\n不会保存材质、关卡或其他脏资产。是否继续？'):
            self.status.set_text('已取消保存，没有写入文件。');return False
        if not self.mesh:
            folder,name=path.rsplit('/',1)
            self.mesh,_=engine.create(self.settings,folder,name,self.surface)
            self.mesh.set_material(0,self.current_material());self.last_applied=self.snapshot()
            self.pending=None;self.geometry_dirty=self.surface_dirty=False
        else:self.apply()
        engine.save(self.mesh);self.unsaved=False;self.asset_label.set_text(self.mesh.get_path_name())
        self.status.set_text('已保存当前模型。材质参数请点击侧栏“保存当前材质…”单独确认保存。');self.locate();return True

    def locate(self):
        if self.mesh:unreal.EditorAssetLibrary.sync_browser_to_objects([self.mesh.get_path_name()])

    def tick(self):
        now=time.monotonic();dt=min(now-self.last_tick,.1);self.last_tick=now
        if self.pending is not None and now>=self.pending:self.safe(self.apply)
        self.material_sidebar.tick()
        self.viewer.tick(dt)


def _restore(panel,old):
    if hasattr(old,'material_sidebar'):old.material_sidebar.flush()
    for key in ('mesh','settings','surface','live','history','last_applied','geometry_dirty','surface_dirty','pending','unsaved'):
        if hasattr(old,key):setattr(panel,key,getattr(old,key))
    if hasattr(old,'viewer'):
        for key in ('axes','grid','floor','environment','turntable','wireframe'):
            if hasattr(old.viewer,key):setattr(panel.viewer,key,getattr(old.viewer,key))
    if panel.mesh:
        material=panel.mesh.get_material(0)
        if panel.surface.get('mode')!='custom':panel.surface={'mode':'custom','material':material.get_path_name() if material else ''}
        panel.asset_label.set_text(panel.mesh.get_path_name())
    panel.folder.set_text(old.folder.get_text());panel.name.set_text(old.name.get_text());panel.sync_controls()


def open_panel():
    global _panel,_blueprint,_tab_open
    bp=unreal.load_asset(WIDGET)
    if not bp:raise RuntimeError('找不到工作台，请在启动器重新安装插件。')
    widget=unreal.get_editor_subsystem(unreal.EditorUtilitySubsystem).spawn_and_register_tab_with_id(bp,TAB)
    _blueprint=bp;_tab_open=True
    if _panel is None or _panel.widget!=widget:
        old=_panel;_panel=Panel(widget)
        if old:_restore(_panel,old)
    return _panel


def _tick(_):
    global _panel,_next_tab_check,_tab_open
    if _blueprint and time.monotonic()>=_next_tab_check:
        _next_tab_check=time.monotonic()+.5
        widget=unreal.get_editor_subsystem(unreal.EditorUtilitySubsystem).find_utility_widget_from_blueprint(_blueprint)
        _tab_open=bool(widget)
        if widget and (_panel is None or widget!=_panel.widget):
            old=_panel;_panel=Panel(widget)
            if old:_restore(_panel,old)
        if not widget and _panel:
            _panel.pending=None
            if hasattr(_panel,'material_sidebar'):_panel.safe(_panel.material_sidebar.flush)
    if _panel and _tab_open:_panel.safe(_panel.tick)


def startup():
    global _watch,_blueprint
    _blueprint=unreal.load_asset(WIDGET)
    menus=unreal.ToolMenus.get()
    root=menus.extend_menu('LevelEditor.MainMenu')
    direct=root.add_sub_menu('VFXMeshGenerator','VFXMeshGenerator','VFXMeshWorkspace','特效网格','独立特效网格工作台')
    entry=unreal.ToolMenuEntry(name='VFXMeshGenerator.OpenWorkspace',type=unreal.MultiBlockType.MENU_ENTRY)
    entry.set_label('打开独立工作台')
    entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON,'','from vfx_mesh.runtime import open_panel; open_panel()')
    direct.add_menu_entry('Workspace',entry)
    for menu_name,kind in [('LevelEditor.MainMenu.Tools',unreal.MultiBlockType.MENU_ENTRY),('LevelEditor.LevelEditorToolBar.User',unreal.MultiBlockType.TOOL_BAR_BUTTON)]:
        menu=menus.extend_menu(menu_name);entry=unreal.ToolMenuEntry(name='VFXMeshGenerator.Open',type=kind)
        entry.set_label('特效网格');entry.set_tool_tip('打开独立特效网格工作台：调参、预览、材质与确认保存')
        entry.set_icon('EditorStyle','ClassIcon.StaticMesh')
        entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON,'','from vfx_mesh.runtime import open_panel; open_panel()')
        menu.add_menu_entry('VFXMeshGenerator',entry)
    menus.refresh_all_widgets()
    if _watch is None:_watch=unreal.register_slate_post_tick_callback(_tick)
