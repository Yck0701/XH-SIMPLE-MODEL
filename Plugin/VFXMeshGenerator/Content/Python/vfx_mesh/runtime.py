"""Native dockable editor panel. Python is only needed while authoring."""
import copy
import time
import traceback
import unreal
from . import geometry, engine, materials

WIDGET = '/VFXMeshGenerator/UI/EUW_VFXMesh'
_panel = None
_watch = None
_blueprint = None
_next_tab_check = 0.
_tab_open = False


@unreal.uclass()
class VFXMeshSurfaceSettings(unreal.Object):
    main_texture = unreal.uproperty(unreal.Texture2D, meta={'Category':'贴图', 'DisplayName':'主贴图'})
    mask_texture = unreal.uproperty(unreal.Texture2D, meta={'Category':'贴图', 'DisplayName':'遮罩贴图（R 通道）'})
    material = unreal.uproperty(unreal.MaterialInterface, meta={'Category':'自定义材质', 'DisplayName':'材质'})


class Panel:
    def __init__(self, widget):
        self.widget = widget
        self.mesh = None
        self.settings = geometry.defaults()
        self.surface = materials.defaults()
        self.live = True
        self.loading = False
        self.pending = None
        self.geometry_dirty = False
        self.surface_dirty = False
        self.history = []
        self.last_applied = None
        self.controls = {}
        self.rows = {}
        self.refs = []
        self.root = widget.find_child_widget_by_name('VerticalBox_0')
        if not self.root: raise RuntimeError('插件面板模板缺失。')
        self.root.clear_children()
        self.build_ui()

    def obj(self, cls):
        cls = getattr(unreal, 'EditorUtility' + cls.__name__, cls)
        obj = unreal.new_object(cls, outer=self.widget)
        if isinstance(obj,(unreal.SpinBox,unreal.ComboBoxString)):
            font=obj.get_editor_property('font');font.set_editor_property('size',11);obj.set_editor_property('font',font)
        if isinstance(obj,unreal.ComboBoxString):
            obj.set_editor_property('foreground_color',unreal.SlateColor(specified_color=unreal.LinearColor(.8,.8,.8,1)))
        self.refs.append(obj)
        return obj

    def add(self, parent, child, padding=3):
        slot = parent.add_child(child)
        if hasattr(slot, 'set_padding'): slot.set_padding(unreal.Margin(padding,padding,padding,padding))
        return slot

    def text(self, parent, label, size=11, color=None):
        w = self.obj(unreal.TextBlock)
        w.set_text(label)
        font = w.get_editor_property('font'); font.set_editor_property('size', size); w.set_font(font)
        w.set_auto_wrap_text(True)
        if color: w.set_color_and_opacity(unreal.SlateColor(specified_color=unreal.LinearColor(*color)))
        self.add(parent, w)
        return w

    def heading(self, parent, label):
        return self.text(parent, label, 12, (.35,.72,1.,1.))

    def button(self, parent, label, action, tip=''):
        b = self.obj(unreal.Button)
        self.text(b, label)
        if tip: b.set_tool_tip_text(tip)
        b.on_clicked.add_callable(lambda: self.safe(action))
        self.add(parent, b, 2)
        return b

    def safe(self, action):
        try: return action()
        except Exception as error:
            self.pending = None
            self.status.set_text(str(error))
            unreal.log_warning('[VFX Mesh] ' + traceback.format_exc())

    def row(self, parent, label):
        row = self.obj(unreal.HorizontalBox); self.add(parent, row, 1)
        box = self.obj(unreal.SizeBox); box.set_width_override(145); self.add(row, box, 1)
        self.text(box, label)
        return row

    def spin(self, parent, key, label, value, lo, hi, target):
        row = self.row(parent, label)
        spin = self.obj(unreal.SpinBox)
        spin.set_min_value(float(lo)); spin.set_max_value(float(hi))
        spin.set_min_slider_value(float(lo)); spin.set_max_slider_value(float(min(hi, max(1000, value*3))))
        spin.set_value(float(value))
        integer = type(value) is int
        spin.set_editor_property('delta', 1. if integer else .1)
        spin.set_editor_property('min_fractional_digits', 0)
        spin.set_editor_property('max_fractional_digits', 0 if integer else 3)
        spin.set_editor_property('min_desired_width',130.)
        spin.on_value_changed.add_callable(lambda v: self.change(target, key, int(round(v)) if integer else float(v)))
        slot = self.add(row, spin, 1); slot.set_size(unreal.SlateChildSize(size_rule=unreal.SlateSizeRule.FILL))
        self.controls[(target,key)] = spin; self.rows[(target,key)] = row
        return spin

    def check(self, parent, key, label, value, target):
        check = self.obj(unreal.CheckBox); check.set_is_checked(value)
        self.text(check, label)
        check.on_check_state_changed.add_callable(lambda v: self.change(target, key, v))
        self.add(parent, check)
        self.controls[(target,key)] = check; self.rows[(target,key)] = check
        return check

    def combo(self, parent, key, label, choices, target):
        row = self.row(parent, label); box = self.obj(unreal.ComboBoxString)
        for _, text in choices: box.add_option(text)
        data = self.settings if target=='geometry' else self.surface
        box.set_selected_option(dict(choices)[data[key]])
        mapping = {label:value for value,label in choices}
        box.on_selection_changed.add_callable(lambda label, _: self.change(target,key,mapping[label]))
        slot=self.add(row,box);slot.set_size(unreal.SlateChildSize(size_rule=unreal.SlateSizeRule.FILL))
        self.controls[(target,key)]=box
        return box

    def build_ui(self):
        border=self.obj(unreal.Border);border.set_padding(unreal.Margin(10,10,10,10));border.set_brush_color(unreal.LinearColor(.016,.019,.024,1))
        self.add(self.root,border,0)
        body=self.obj(unreal.VerticalBox);border.add_child(body)
        self.text(body,'VFX 特效网格',18)
        self.text(body,'调参后更新原资产 · Niagara 网格只需引用一次',10,(.65,.7,.75,1))
        actions=self.obj(unreal.HorizontalBox);self.add(body,actions)
        self.button(actions,'新建模型',self.create,'生成新的静态网格，不覆盖同名资产')
        self.button(actions,'载入所选',self.load_selected,'载入内容浏览器中所选模型的生成参数')
        self.button(actions,'保存',self.save,'保存当前网格及本工具创建的材质实例')
        self.asset_label=self.text(body,'尚未新建或载入模型',10)
        actions=self.obj(unreal.HorizontalBox);self.add(body,actions)
        self.button(actions,'查看模型',self.preview)
        self.button(actions,'定位资产',self.locate)
        self.button(actions,'撤回上次调参',self.undo)
        self.status=self.text(body,'选择形状并新建模型，然后在 Niagara Mesh Renderer 中引用该资产。',10,(.65,.8,.65,1))
        scroll=self.obj(unreal.ScrollBox)
        self.scroll=scroll
        slot=self.add(body,scroll);slot.set_size(unreal.SlateChildSize(size_rule=unreal.SlateSizeRule.FILL))
        # Ensure the main container fills the tab, giving the scroll box a finite height.
        root_slot=border.get_editor_property('slot')
        root_slot.set_size(unreal.SlateChildSize(size_rule=unreal.SlateSizeRule.FILL))
        content=self.obj(unreal.VerticalBox);scroll.add_child(content)
        self.heading(content,'形状与尺寸 · cm')
        self.combo(content,'shape','形状',[(k,v[0]) for k,v in geometry.KINDS.items()],'geometry')
        for key, field in geometry.FIELDS.items():
            if type(field[1]) is bool:self.check(content,key,field[0],field[1],'geometry')
            else:self.spin(content,key,*field,'geometry')
        self.heading(content,'原点与朝向')
        self.combo(content,'pivot','原点',[('original','原始位置'),('center','包围盒中心'),('bottom','底部中心')],'geometry')
        self.combo(content,'direction','主轴方向',[(k,v) for k,v in zip(geometry.PRESETS,['+Z','-Z','+X','-X','+Y','-Y'])],'geometry')
        self.check(content,'flip','翻转法线（朝内 / 朝外）',False,'geometry')
        for key,label in [('rx','旋转 X'),('ry','旋转 Y'),('rz','旋转 Z')]:self.spin(content,key,label,0.,-360.,360.,'geometry')
        for key,label in [('tx','位移 X'),('ty','位移 Y'),('tz','位移 Z')]:self.spin(content,key,label,0.,-100000.,100000.,'geometry')
        self.heading(content,'材质与贴图')
        self.combo(content,'mode','材质模式',[('generated','工具材质（主贴图 × 遮罩）'),('custom','自定义 UE 材质')],'surface')
        self.pickers=VFXMeshSurfaceSettings()
        details=self.obj(unreal.DetailsView)
        details.set_editor_property('force_hidden_property_visibility',True)
        details.set_editor_property('allow_filtering',False)
        details.set_object(self.pickers);details.on_property_changed.add_callable(self.picker_changed)
        self.add(content,details)
        self.text(content,'遮罩读取 R 通道：黑色透明、白色显示。自定义材质模式使用你自己的 UE 材质参数。',10)
        self.generated_box=self.obj(unreal.VerticalBox);self.add(content,self.generated_box,0)
        for prefix,label in [('Main','主贴图 UV'),('Mask','遮罩 UV')]:
            self.heading(self.generated_box,label)
            flips=self.obj(unreal.HorizontalBox);self.add(self.generated_box,flips)
            for key,title,value in materials.UV_FIELDS:
                if type(value) is bool:self.check(flips,prefix+key,title,value,'surface')
                else:self.spin(self.generated_box,prefix+key,title,value,-360. if key=='Angle' else -10.,360. if key=='Angle' else 10.,'surface')
        self.spin(self.generated_box,'Opacity','不透明度',1.,0.,1.,'surface')
        self.spin(self.generated_box,'Brightness','亮度',1.,0.,100.,'surface')
        self.heading(content,'更新与保存')
        self.check(content,'live','实时更新（约 0.25 秒间隔）',True,'options')
        self.button(content,'立即应用参数',self.apply)
        self.folder=self.obj(unreal.EditableTextBox);self.folder.set_text('/Game/VFXMeshes');self.add(content,self.folder)
        self.folder.set_tool_tip_text('新模型的内容路径，例如 /Game/VFXMeshes')
        self.name=self.obj(unreal.EditableTextBox);self.name.set_hint_text('新模型名称（留空自动命名）');self.add(content,self.name)
        self.text(content,'生成参数随模型保存。实时修改不会自动保存到磁盘；满意后点击“保存”。',10)
        self.visibility()

    def visibility(self):
        visible=unreal.SlateVisibility.VISIBLE;hidden=unreal.SlateVisibility.COLLAPSED
        fields=geometry.KINDS[self.settings['shape']][1]
        for key in geometry.FIELDS:self.rows[('geometry',key)].set_visibility(visible if key in fields else hidden)
        self.generated_box.set_visibility(visible if self.surface['mode']=='generated' else hidden)

    def change(self,target,key,value):
        if self.loading:return
        if target=='options':
            self.live=value
            if value and self.mesh:self.queue()
            return
        data=self.settings if target=='geometry' else self.surface
        if data[key]==value:return
        data[key]=value
        if target=='geometry':self.geometry_dirty=True
        else:self.surface_dirty=True
        if key in ('shape','mode'):self.visibility()
        self.queue()

    def picker_changed(self, _):
        if self.loading:return
        for key in ('main_texture','mask_texture','material'):
            asset=self.pickers.get_editor_property(key)
            self.surface[key]=asset.get_path_name() if asset else ''
        self.surface_dirty=True;self.queue()

    def queue(self):
        if self.mesh and self.live:
            if self.pending is None:self.pending=time.monotonic()+.25
        elif self.mesh:self.status.set_text('参数已修改，点击“立即应用参数”更新。')

    def snapshot(self):return copy.deepcopy((self.settings,self.surface))

    def sync_controls(self):
        self.loading=True
        try:
            labels={'shape':{k:v[0] for k,v in geometry.KINDS.items()},'pivot':{'original':'原始位置','center':'包围盒中心','bottom':'底部中心'},
                'direction':dict(zip(geometry.PRESETS,['+Z','-Z','+X','-X','+Y','-Y'])),
                'mode':{'generated':'工具材质（主贴图 × 遮罩）','custom':'自定义 UE 材质'}}
            for (target,key),control in self.controls.items():
                if target=='options':
                    control.set_is_checked(self.live)
                    continue
                value=(self.settings if target=='geometry' else self.surface)[key]
                if isinstance(control,unreal.SpinBox):control.set_value(float(value))
                elif isinstance(control,unreal.CheckBox):control.set_is_checked(value)
                else:control.set_selected_option(labels[key][value])
            for key in ('main_texture','mask_texture','material'):
                self.pickers.set_editor_property(key,unreal.load_asset(self.surface[key]) if self.surface[key] else None)
            self.visibility()
        finally:self.loading=False

    def create(self):
        materials.validate(self.surface)
        if self.mesh and (self.geometry_dirty or self.surface_dirty):self.apply()
        mesh,stats=engine.create(self.settings,str(self.folder.get_text()),str(self.name.get_text()),self.surface)
        self.mesh=mesh
        materials.apply(mesh,self.surface)
        engine.write_settings(mesh,self.settings,self.surface)
        self.history=[];self.last_applied=self.snapshot()
        self.pending=None;self.geometry_dirty=self.surface_dirty=False
        self.report(stats,'已新建，尚未保存')
        self.locate();self.preview()

    def load_selected(self):
        mesh=engine.selected();data=engine.read(mesh)
        self.pending=None;self.mesh=mesh;self.settings=data['geometry'];self.surface=materials.defaults();self.surface.update(data.get('surface',{}))
        self.history=[];self.last_applied=self.snapshot();self.geometry_dirty=self.surface_dirty=False
        self.sync_controls();self.asset_label.set_text(mesh.get_path_name())
        self.status.set_text('已载入生成参数；修改后将更新此资产及所有引用。')

    def apply(self,record=True):
        self.pending=None
        if not self.mesh:raise ValueError('请先新建模型，或载入所选模型。')
        geometry.validate(self.settings)
        materials.validate(self.surface)
        stats=None
        if self.geometry_dirty:stats=engine.update(self.mesh,self.settings,self.surface)
        if self.surface_dirty:materials.apply(self.mesh,self.surface)
        engine.write_settings(self.mesh,self.settings,self.surface)
        current=self.snapshot()
        if record and self.last_applied and current!=self.last_applied:
            self.history.append(self.last_applied);self.history=self.history[-20:]
        self.last_applied=current;self.geometry_dirty=self.surface_dirty=False
        self.report(stats,'已实时更新，尚未保存')

    def undo(self):
        self.pending=None
        if self.last_applied and self.snapshot()!=self.last_applied:
            self.settings,self.surface=copy.deepcopy(self.last_applied)
            self.geometry_dirty=self.surface_dirty=False
            self.sync_controls();self.status.set_text('已撤回尚未应用的参数。');return
        if not self.history:raise ValueError('没有可撤回的调参记录。')
        self.settings,self.surface=copy.deepcopy(self.history.pop())
        self.geometry_dirty=self.surface_dirty=True
        self.apply(record=False);self.sync_controls();self.status.set_text('已撤回上次调参，尚未保存。')

    def report(self,stats,message):
        self.asset_label.set_text(self.mesh.get_path_name())
        suffix=(' · %s 顶点 / %s 三角形' % (stats['vertices'],stats['triangles'])) if stats else ''
        self.status.set_text(message+suffix)

    def save(self):
        self.apply();engine.save(self.mesh);self.status.set_text('模型、材质和生成参数已保存。')

    def locate(self):
        if self.mesh:unreal.EditorAssetLibrary.sync_browser_to_objects([self.mesh.get_path_name()])

    def preview(self):
        if self.mesh:unreal.get_editor_subsystem(unreal.AssetEditorSubsystem).open_editor_for_assets([self.mesh])

    def tick(self):
        if self.pending is not None and time.monotonic()>=self.pending:self.safe(self.apply)


def open_panel():
    global _panel, _blueprint, _tab_open
    bp=unreal.load_asset(WIDGET)
    if not bp:raise RuntimeError('找不到 VFX 网格面板，请检查插件是否启用。')
    widget=unreal.get_editor_subsystem(unreal.EditorUtilitySubsystem).spawn_and_register_tab(bp)
    _blueprint=bp;_tab_open=True
    if _panel is None or _panel.widget!=widget:
        old=_panel;_panel=Panel(widget)
        if old and old.mesh:
            _panel.mesh=old.mesh;_panel.settings=old.settings;_panel.surface=old.surface
            _panel.last_applied=old.last_applied;_panel.history=old.history
            _panel.geometry_dirty=old.geometry_dirty;_panel.surface_dirty=old.surface_dirty
            _panel.sync_controls();_panel.asset_label.set_text(old.mesh.get_path_name())
    return _panel


def _tick(_):
    global _panel, _next_tab_check, _tab_open
    if _blueprint and time.monotonic()>=_next_tab_check:
        _next_tab_check=time.monotonic()+.5
        # Utility widget instances are recreated when the editor changes maps.
        widget=unreal.get_editor_subsystem(unreal.EditorUtilitySubsystem).find_utility_widget_from_blueprint(_blueprint)
        _tab_open=bool(widget)
        if widget and _panel is None:_panel=Panel(widget)
        if widget and widget!=_panel.widget:
            old=_panel;_panel=Panel(widget)
            if old.mesh:
                _panel.mesh=old.mesh;_panel.settings=old.settings;_panel.surface=old.surface
                _panel.last_applied=old.last_applied;_panel.sync_controls()
                _panel.asset_label.set_text(old.mesh.get_path_name())
        if not widget and _panel:_panel.pending=None
    if _panel and _tab_open:_panel.tick()


def startup():
    global _watch, _blueprint
    _blueprint=unreal.load_asset(WIDGET)
    menus=unreal.ToolMenus.get()
    menu=menus.extend_menu('LevelEditor.MainMenu.Tools')
    entry=unreal.ToolMenuEntry(name='VFXMeshGenerator.Open',type=unreal.MultiBlockType.MENU_ENTRY)
    entry.set_label('VFX 特效网格生成器')
    entry.set_tool_tip('编辑器实时调参，直接更新 Niagara 引用的静态网格')
    entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON,'','from vfx_mesh.runtime import open_panel; open_panel()')
    menu.add_menu_entry('VFXMeshGenerator',entry)
    menus.refresh_all_widgets()
    if _watch is None:_watch=unreal.register_slate_post_tick_callback(_tick)
