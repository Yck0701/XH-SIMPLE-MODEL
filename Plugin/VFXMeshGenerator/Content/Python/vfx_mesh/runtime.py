"""Compact editor workspace with isolated preview and explicit single-asset saves."""
import copy
import ctypes
import time
import traceback
import unreal
from . import geometry, engine, preview, material_sidebar, environment_sidebar, model_sidebar, storage

WIDGET='/VFXMeshGenerator/UI/EUW_VFXMesh'
TAB='VFXMeshGenerator.Workspace'
_panel=None
_watch=None
_blueprint=None
_next_tab_check=0.
_tab_open=False
_ticking=False


from .settings_types import VFXUserMaterialSettings

GUIDE_TOPICS=(
    ('快速开始','四步做出特效网格',
     '1  调整模型\n在左侧选择形状，拖动尺寸参数。打开“实时更新”后，预览会跟随变化。\n\n'
     '2  选择自己的材质\n在右侧“材质细节”指定材质或材质实例，再调整下面的参数。\n\n'
     '3  分别保存\n左侧绿色按钮保存模型；右侧蓝色按钮保存材质。每次都要在弹窗中确认。\n\n'
     '4  放入 Niagara\n将保存后的静态网格指定给 Mesh Renderer，继续制作粒子效果。'),
    ('保存模型','模型保存到哪里？',
     '新模型：展开按钮上方的“保存位置”，在内容浏览器选中目标文件夹，再点“使用所选目录”。模型名可以自己填写。\n\n'
     '点击“保存生成模型…”后，核对弹窗中的资产路径并确认。保存成功后，按钮下方会显示“模型已保存”。\n\n'
     '已有模型：从内容浏览器选中本工具生成的网格，点击顶部“载入所选网格”。修改后保存会更新同一个资产，已有 Niagara 引用会保留。\n\n'
     '模型保存不会代替材质保存；两者需要分别确认。'),
    ('保存材质','材质调整与保存',
     '在右侧“材质细节”选择你的材质。默认在预览副本中调整，原材质不会随之改变。\n\n'
     '点击右侧蓝色“保存材质…”：可选择“替换原实例”，或填写名称“新增实例”。母材质和引擎材质通过新增实例保存。\n\n'
     '新增实例后，它会用于当前工作台。若要让模型记住这份材质，还需保存模型。\n\n'
     '“实时迭代原材质”会让所有引用该实例的效果同步变化；写入磁盘仍需保存确认，关闭开关不会撤销已经应用的修改。'),
    ('Niagara','让网格用于粒子效果',
     '1  先用左侧绿色按钮保存模型，得到内容浏览器中的 Static Mesh。\n\n'
     '2  在 Niagara 发射器中添加 Mesh Renderer，将该静态网格指定到 Meshes。\n\n'
     '3  重新载入同一个网格并调参，Niagara 可继续引用它；保存模型后保留本次修改。\n\n'
     '粒子颜色、粒子时间和 Renderer 覆盖材质请在 Niagara 中查看。骨骼动画只用于本工具预览。'),
    ('预览操作','观察效果与整理布局',
     '左键旋转 · 中键平移\n滚轮 / 右键拖动缩放 · F 聚焦\n\n'
     '预览底部可切换线框、坐标轴、网格与阴影。线框不透明度为 0 时隐藏，只影响预览。\n\n'
     '右侧“环境设置”可调整 HDRI、主光和曝光，仅影响当前工作台。\n\n'
     '拖动左右分隔线改变栏宽；顶部可隐藏侧栏或重置布局。教程标题可点击折叠。'),
)


class Panel:
    def __init__(self,widget):
        self.widget=widget;self.mesh=None;self.settings=geometry.defaults()
        self.custom_mesh=None;self.imported_assets=[]
        self.surface={'mode':'custom','material':''};self.live=True;self.loading=True
        self.pending=None;self.geometry_dirty=False;self.surface_dirty=False
        self.history=[];self.last_applied=None;self.unsaved=False
        self.busy=False;self.last_error=None
        self.controls={};self.rows={};self.refs=[]
        self.sections={};self.left_visible=True;self.right_visible=True;self.sidebar_tab='material'
        self.layout_attached=False;self.layout_sizes=[.21,.54,.25]
        self.guide_index=0;self._save_state=None
        self.root=widget.find_child_widget_by_name('VerticalBox_0')
        if not self.root:raise RuntimeError('插件面板模板缺失。')
        self.root.clear_children();self.build_ui();self.loading=False;self.last_tick=time.monotonic();self.last_applied=self.snapshot()

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

    def text(self,parent,label,size=10,color=None,wrap=True):
        obj=self.obj(unreal.TextBlock);obj.set_text(label)
        font=obj.get_editor_property('font');font.set_editor_property('size',size);obj.set_font(font)
        obj.set_auto_wrap_text(wrap)
        if not wrap:
            obj.set_tool_tip_text(label)
            obj.set_editor_property('text_overflow_policy',unreal.TextOverflowPolicy.ELLIPSIS)
        if color:obj.set_color_and_opacity(unreal.SlateColor(specified_color=unreal.LinearColor(*color)))
        self.add(parent,obj);return obj

    def heading(self,parent,label):return self.text(parent,label,11,(.35,.8,.75,1),False)

    def section(self,parent,key,title,expanded=True):
        button=self.obj(unreal.Button);self.add(parent,button,2)
        label=self.text(button,('- ' if expanded else '+ ')+title,11,(.35,.8,.75,1),False)
        body=self.obj(unreal.VerticalBox);self.add(parent,body,2)
        self.sections[key]=(body,label,title)
        self.expand_section(key,expanded)
        button.on_clicked.add_callable(lambda:self.expand_section(key,body.get_visibility()==unreal.SlateVisibility.COLLAPSED))
        return body

    def expand_section(self,key,expanded):
        body,label,title=self.sections[key]
        body.set_visibility(unreal.SlateVisibility.VISIBLE if expanded else unreal.SlateVisibility.COLLAPSED)
        label.set_text(('- ' if expanded else '+ ')+title)

    def switch_sidebar(self,tab):
        self.sidebar_tab=tab;self.sidebar_switcher.set_active_widget_index(0 if tab=='material' else 1)
        for key,button in self.sidebar_buttons.items():
            button.set_background_color(unreal.LinearColor(.08,.3,.3,1) if key==tab else unreal.LinearColor(.1,.12,.15,1))

    def button(self,parent,label,action,tip=''):
        b=self.obj(unreal.Button);self.text(b,label,wrap=False)
        if tip:b.set_tool_tip_text(tip)
        b.on_clicked.add_callable(lambda:self.safe(action));self.add(parent,b,2);return b

    def primary_button(self,parent,label,action,color,tip):
        frame=self.obj(unreal.SizeBox);frame.set_min_desired_height(42);self.add(parent,frame,3)
        # A plain UButton keeps the explicit colors instead of the editor theme's dark tint.
        b=unreal.new_object(unreal.Button,outer=self.widget);self.refs.append(b);frame.add_child(b)
        style=b.get_editor_property('widget_style')
        for key,factor in (('normal',1.),('hovered',1.18),('pressed',.78),('disabled',0.)):
            tint=tuple(min(1.,v*factor) for v in color) if factor else (.10,.12,.14)
            style.set_editor_property(key,unreal.SlateBrush(draw_as=unreal.SlateBrushDrawType.BOX,
                tint_color=unreal.SlateColor(specified_color=unreal.LinearColor(*tint,1))))
        b.set_style(style);b.set_background_color(unreal.LinearColor(1,1,1,1))
        text=self.text(b,label,13,(1,1,1,1),False)
        font=text.get_editor_property('font');font.set_editor_property('typeface_font_name','Bold');text.set_font(font)
        text.slot.set_padding(unreal.Margin(10,7,10,7))
        b.set_tool_tip_text(tip);b.on_clicked.add_callable(lambda:self.safe(action))
        return b

    def build_guide(self,parent):
        guide=self.section(parent,'guide','使用教程 · 点击主题查看',True)
        topics=self.obj(unreal.WrapBox);topics.set_inner_slot_padding(unreal.Vector2D(2,3));self.add(guide,topics)
        self.guide_buttons=[self.button(topics,title,lambda i=i:self.show_guide(i)) for i,(title,_,_) in enumerate(GUIDE_TOPICS)]
        card=self.obj(unreal.Border);card.set_padding(unreal.Margin(10,10,10,10))
        card.set_brush_color(unreal.LinearColor(.027,.036,.044,1));self.add(guide,card,3)
        content=self.obj(unreal.VerticalBox);card.add_child(content)
        self.guide_title=self.text(content,'',11,(.5,.9,.8,1),False)
        self.guide_body=self.text(content,'',10,(.8,.84,.88,1))
        self.show_guide(0)

    def show_guide(self,index):
        self.guide_index=index;_,title,body=GUIDE_TOPICS[index]
        self.guide_title.set_text(title);self.guide_body.set_text(body)
        for i,button in enumerate(self.guide_buttons):
            button.set_background_color(unreal.LinearColor(*((.25,.65,.58,1) if i==index else (.3,.34,.4,1))))

    def refresh_save_state(self):
        state=(bool(self.custom_mesh),len(self.imported_assets),bool(self.mesh),self.unsaved,self.geometry_dirty,self.surface_dirty)
        if state==self._save_state:return
        self._save_state=state
        if self.custom_mesh:
            text='● 本次导入资产尚未保存' if self.imported_assets else '已有自定义模型：仅预览，无需保存网格'
        elif not self.mesh:text='● 预览草稿 · 点击上方按钮创建模型资产'
        elif self.unsaved or self.geometry_dirty or self.surface_dirty:text='● 模型有修改 · 尚未保存到磁盘'
        else:text='✓ 模型已保存 · 材质请在右侧单独保存'
        self.model_save_status.set_text(text)
        color=(1.,.78,.32,1.) if text.startswith('●') else (.5,.9,.65,1.)
        self.model_save_status.set_color_and_opacity(unreal.SlateColor(specified_color=unreal.LinearColor(*color)))

    def safe(self,action):
        if self.busy:return
        self.busy=True
        try:
            result=action();self.last_error=None;return result
        except Exception as error:
            self.pending=None;self.status.set_text(str(error))
            if str(error)!=self.last_error:unreal.log_warning('[VFX Mesh] '+traceback.format_exc())
            self.last_error=str(error)
        finally:self.busy=False

    def dispose(self):
        dll=preview.native()
        for host in (self.viewer.host,self.material_sidebar.host,self.columns):
            dll.VFX_ReleaseHost(host.get_path_name())
        # UMG delegates hold Python closures. Break those references after the
        # new panel has copied state, so old widget trees can be collected.
        for widget in self.refs:
            for name in ('on_clicked','on_value_changed','on_selection_changed',
                         'on_check_state_changed','on_property_changed'):
                delegate=getattr(widget,name,None)
                if delegate is not None:delegate.clear()
        self.refs.clear()

    def row(self,parent,label,width=120):
        row=self.obj(unreal.HorizontalBox);self.add(parent,row,1)
        box=self.obj(unreal.SizeBox);box.set_width_override(width);self.add(row,box,0);self.text(box,label,wrap=False)
        return row

    def spin(self,parent,key,label,value,lo,hi,target='geometry',width=120):
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
        obj=self.obj(unreal.CheckBox);obj.set_is_checked(value);self.text(obj,label,wrap=False)
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
        minimum=self.obj(unreal.SizeBox);minimum.set_min_desired_width(1180);minimum.set_min_desired_height(720);border.add_child(minimum)
        body=self.obj(unreal.VerticalBox);minimum.add_child(body)
        top=self.obj(unreal.HorizontalBox);self.add(body,top)
        self.text(top,'特效网格工作台',14,wrap=False)
        self.button(top,'新建参数模型',self.new,'新建可调整形状的预览草稿；保存确认后才创建资产')
        self.button(top,'载入所选网格',self.load_selected,'从内容浏览器载入所选网格：本工具生成的模型可继续调参，其他模型进入自定义预览')
        self.locate_button=self.button(top,'定位模型',self.locate,'在内容浏览器定位当前已存在的网格资产')
        self.button(top,'重置布局',self.reset_layout)
        self.fill(self.add(top,self.obj(unreal.Spacer)))
        self.check(top,'left_visible','模型参数',True,'layout');self.check(top,'right_visible','材质 / 环境',True,'layout')
        self.asset_label=self.text(body,'预览草稿 · 尚未创建资产',10,(.65,.7,.75,1),False)
        columns=self.obj(unreal.NativeWidgetHost);self.fill(self.add(body,columns));self.columns=columns
        left_box=self.obj(unreal.SizeBox);self.left_box=left_box
        scroll=self.obj(unreal.ScrollBox);left_box.add_child(scroll);self.scroll=scroll
        left=self.obj(unreal.VerticalBox);scroll.add_child(left)
        custom=self.section(left,'custom','自定义模型 / 动画',False)
        self.model_sidebar=model_sidebar.Sidebar(self,custom)
        actions=self.obj(unreal.HorizontalBox);self.add(left,actions)
        self.geometry_actions=actions
        self.check(actions,'live','实时更新',True,'options');self.button(actions,'应用',self.apply);self.button(actions,'撤回',self.undo)
        shape=self.section(left,'shape','形状与尺寸 · cm')
        self.combo(shape,'shape','形状',[(k,v[0]) for k,v in geometry.KINDS.items()])
        for key,field in geometry.FIELDS.items():
            if type(field[1]) is bool:self.check(shape,key,field[0],field[1])
            else:self.spin(shape,key,*field)
        transform=self.section(left,'transform','原点与变换')
        self.combo(transform,'pivot','原点',[('original','原始位置'),('center','包围盒中心'),('bottom','底部中心')])
        self.combo(transform,'direction','朝向',list(zip(geometry.PRESETS,['+Z','-Z','+X','-X','+Y','-Y'])))
        self.check(transform,'flip','翻转法线',False)
        for prefix,title,limit in [('r','旋转',360.),('t','位移',100000.)]:
            row=self.obj(unreal.HorizontalBox);self.add(transform,row)
            self.text(row,title)
            for axis in 'xyz':
                box=self.obj(unreal.VerticalBox);self.fill(self.add(row,box,0))
                self.spin(box,prefix+axis,axis.upper(),0.,-limit,limit,width=16)
        save=self.section(left,'save','保存位置 · UE 内容目录',False)
        self.folder=self.obj(unreal.EditableTextBox);self.folder.set_text('/Game/VFXMeshes');self.folder.set_is_read_only(True);self.add(save,self.folder)
        actions=self.obj(unreal.HorizontalBox);self.add(save,actions)
        self.button(actions,'浏览内容目录',self.browse_folder)
        self.button(actions,'使用所选目录',self.pick_folder)
        self.name=self.obj(unreal.EditableTextBox);self.name.set_hint_text('模型名，留空自动命名');self.add(save,self.name)
        self.text(save,'在内容浏览器选文件夹，再点“使用所选目录”。已有模型保存在原位置。',9,(.6,.65,.7,1))
        self.save_button=self.primary_button(left,'保存生成模型…',self.save,(.045,.34,.22),'核对弹窗中的目标路径并确认；模型与材质分别保存。')
        self.model_save_status=self.text(left,'',10,(1.,.78,.32,1.))
        self.build_guide(left)
        right=self.obj(unreal.VerticalBox);self.center_box=right
        actions=self.obj(unreal.HorizontalBox);self.add(right,actions)
        self.heading(actions,'实时预览')
        self.button(actions,'聚焦',lambda:self.viewer.frame())
        self.text(actions,'左键旋转 · 中键平移 · 滚轮 / 右键缩放 · F 聚焦',9,(.65,.7,.75,1))
        self.viewer=preview.Preview(self,right)
        options=self.obj(unreal.WrapBox);self.add(right,options)
        options.set_inner_slot_padding(unreal.Vector2D(4,3))
        self.check(options,'wireframe','线框',False,'preview')
        opacity=self.spin(options,'wire_opacity','不透明度 %',self.viewer.wire_opacity,0,100,'preview',70)
        opacity_tip='拖动或输入 0–100：0 完全隐藏，100 完全不透明。默认 35，仅影响预览线框。'
        opacity.set_tool_tip_text(opacity_tip)
        slider_box=self.obj(unreal.SizeBox);slider_box.set_width_override(90);self.add(options,slider_box,4)
        self.opacity_slider=self.obj(unreal.Slider);slider_box.add_child(self.opacity_slider)
        self.opacity_slider.set_min_value(0.);self.opacity_slider.set_max_value(100.);self.opacity_slider.set_step_size(1.)
        self.opacity_slider.set_value(float(self.viewer.wire_opacity));self.opacity_slider.set_tool_tip_text(opacity_tip)
        self.opacity_slider.on_value_changed.add_callable(lambda value:self.change('preview','wire_opacity',int(round(value))))
        self.controls[('preview','wireframe')].set_tool_tip_text('透视显示基础网格的全部三角布线（含背面），避免与材质表面发生深度冲突。仅用于预览。')
        for key,label,value in [('axes','坐标轴',True),('grid','网格',True),('turntable','旋转',False),('cast_shadow','投射阴影',True)]:self.check(options,key,label,value,'preview')
        self.controls[('preview','cast_shadow')].set_tool_tip_text('控制当前模型在预览中的投射阴影。Niagara 的阴影请在 Mesh Renderer 中设置。')
        self.preview_stats=self.text(right,'预览正在准备…',10,(.65,.7,.75,1))
        material_box=self.obj(unreal.SizeBox);self.right_box=material_box
        sidebar=self.obj(unreal.VerticalBox);material_box.add_child(sidebar)
        tabs=self.obj(unreal.HorizontalBox);self.add(sidebar,tabs)
        self.sidebar_buttons={key:self.button(tabs,title,lambda key=key:self.switch_sidebar(key)) for key,title in [('material','材质细节'),('environment','环境设置')]}
        material_save_area=self.obj(unreal.VerticalBox);self.add(sidebar,material_save_area,0)
        self.sidebar_switcher=self.obj(unreal.WidgetSwitcher);self.fill(self.add(sidebar,self.sidebar_switcher,0))
        material_column=self.obj(unreal.VerticalBox);self.sidebar_switcher.add_child(material_column)
        self.pickers=VFXUserMaterialSettings();details=self.obj(unreal.DetailsView)
        details.set_editor_property('force_hidden_property_visibility',True);details.set_editor_property('allow_filtering',False)
        details.set_object(self.pickers);details.on_property_changed.add_callable(self.picker_changed);self.add(material_column,details)
        self.material_sidebar=material_sidebar.Sidebar(self,material_column,material_save_area)
        environment_column=self.obj(unreal.VerticalBox);self.sidebar_switcher.add_child(environment_column)
        self.environment_sidebar=environment_sidebar.Sidebar(self,environment_column)
        self.switch_sidebar(self.sidebar_tab)
        self.status=self.text(body,'拖动参数即可预览；确认保存后才会新建模型资产。',10,(.65,.85,.7,1))
        self.visibility()

    def ensure_layout(self):
        dll=preview.native()
        if self.layout_attached:
            values=(ctypes.c_double*3)()
            if dll.VFX_LayoutSizes(self.columns.get_path_name(),values,0):self.layout_sizes=list(values);return
        if not dll.VFX_Layout(self.columns.get_path_name(),self.left_box.get_path_name(),self.center_box.get_path_name(),self.right_box.get_path_name(),(ctypes.c_double*3)(*self.layout_sizes)):
            raise RuntimeError('无法创建可拖动分栏。')
        self.layout_attached=True

    def layout_snapshot(self):
        values=(ctypes.c_double*3)()
        if self.layout_attached and preview.native().VFX_LayoutSizes(self.columns.get_path_name(),values,0):return list(values)
        return self.layout_sizes[:]

    def reset_layout(self):
        self.layout_sizes=[.21,.54,.25]
        if self.layout_attached:preview.native().VFX_LayoutSizes(self.columns.get_path_name(),(ctypes.c_double*3)(*self.layout_sizes),1)
        for key in ('left_visible','right_visible'):self.change('layout',key,True);self.controls[('layout',key)].set_is_checked(True)

    def visibility(self):
        fields=geometry.KINDS[self.settings['shape']][1]
        for key in geometry.FIELDS:self.rows[('geometry',key)].set_visibility(unreal.SlateVisibility.VISIBLE if key in fields else unreal.SlateVisibility.COLLAPSED)
        for key in ('shape','transform'):self.sections[key][0].set_is_enabled(not self.custom_mesh)
        self.geometry_actions.set_visibility(unreal.SlateVisibility.COLLAPSED if self.custom_mesh else unreal.SlateVisibility.VISIBLE)
        if hasattr(self,'save_button'):
            self.save_button.set_visibility(unreal.SlateVisibility.VISIBLE if not self.custom_mesh or self.imported_assets else unreal.SlateVisibility.COLLAPSED)
            self.save_button.get_parent().set_visibility(self.save_button.get_visibility())
            self.save_button.get_child_at(0).set_text('保存导入资产…' if self.custom_mesh else '保存生成模型…')
            self.locate_button.set_is_enabled(bool(self.custom_mesh or self.mesh))
            self.refresh_save_state()
        self.controls[('preview','wireframe')].set_is_enabled(not isinstance(self.custom_mesh,unreal.SkeletalMesh))

    def change(self,target,key,value):
        if self.loading:return
        if target=='layout':
            setattr(self,key,value)
            (self.left_box if key=='left_visible' else self.right_box).set_visibility(unreal.SlateVisibility.VISIBLE if value else unreal.SlateVisibility.COLLAPSED)
            return
        if target=='preview':
            setattr(self.viewer,key,value)
            if key=='wire_opacity':
                self.loading=True
                try:
                    self.controls[('preview',key)].set_value(float(value));self.opacity_slider.set_value(float(value))
                finally:self.loading=False
            return
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

    def preview_material(self):
        return self.material_sidebar.material or self.current_material()

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
                value=getattr(self.viewer,key) if target=='preview' else getattr(self,key) if target=='layout' else self.live if target=='options' else self.settings[key]
                if isinstance(control,unreal.SpinBox):control.set_value(float(value))
                elif isinstance(control,unreal.CheckBox):control.set_is_checked(value)
                else:control.set_selected_option(labels[key][value])
            self.opacity_slider.set_value(float(self.viewer.wire_opacity))
            self.pickers.set_editor_property('material',self.current_material());self.visibility()
        finally:self.loading=False
        self.material_sidebar.refresh()
        self.viewer.update(self.settings,self.preview_material())

    def confirm(self,title,message):
        return unreal.EditorDialog.show_message(title,message,unreal.AppMsgType.YES_NO,unreal.AppReturnType.NO)==unreal.AppReturnType.YES

    def can_switch(self):
        return not (self.unsaved or self.geometry_dirty or self.surface_dirty or self.imported_assets) or self.confirm('切换模型','当前模型或本次导入资产有未保存的修改。继续切换？\n本操作不会自动保存任何资产。')

    def new(self):
        if not self.can_switch():return
        self.mesh=None;self.custom_mesh=None;self.imported_assets=[];self.model_sidebar.sync({'model':None,'animation':None,'use_animation':False})
        self.settings=geometry.defaults();self.surface={'mode':'custom','material':''}
        self.history=[];self.last_applied=None;self.pending=None;self.unsaved=False
        self.geometry_dirty=self.surface_dirty=False;self.sync_controls();self.viewer.frame()
        self.last_applied=self.snapshot()
        self.expand_section('shape',True);self.expand_section('transform',True)
        self.expand_section('custom',False)
        self.asset_label.set_text('预览草稿 · 尚未创建资产');self.status.set_text('新建预览不会写入资产；满意后点击“保存模型…”。')

    def load_selected(self):
        selected=[a for a in unreal.EditorUtilityLibrary.get_selected_assets() if isinstance(a,(unreal.StaticMesh,unreal.SkeletalMesh))]
        if len(selected)!=1:raise ValueError('请在内容浏览器选中一个静态网格或骨骼网格。')
        mesh=selected[0]
        if not isinstance(mesh,unreal.StaticMesh) or not unreal.EditorAssetLibrary.get_metadata_tag(mesh,engine.TAG):
            return self.load_custom(mesh)
        data=engine.read(mesh)
        if not self.can_switch():return
        self.custom_mesh=None;self.imported_assets=[];self.model_sidebar.sync({'model':None,'animation':None,'use_animation':False})
        self.mesh=mesh;self.settings=data['geometry'];material=mesh.get_material(0)
        self.surface={'mode':'custom','material':material.get_path_name() if material else ''}
        self.pending=None;self.history=[];self.last_applied=self.snapshot();self.unsaved=False
        self.geometry_dirty=self.surface_dirty=False;self.sync_controls();self.viewer.frame()
        self.asset_label.set_text(mesh.get_path_name());self.folder.set_text(mesh.get_path_name().rsplit('/',1)[0])
        self.status.set_text('已载入。实时调参更新同一个模型；保存需单独确认。')

    def show_custom(self):
        self.change('layout','left_visible',True);self.controls[('layout','left_visible')].set_is_checked(True)
        self.expand_section('custom',True)

    def load_custom(self,model,check=True):
        if not isinstance(model,(unreal.StaticMesh,unreal.SkeletalMesh)):raise ValueError('请选择静态网格或骨骼网格。')
        if check and not self.can_switch():return False
        if model not in self.imported_assets:self.imported_assets=[]
        self.mesh=None;self.custom_mesh=model
        material=model.get_material(0) if isinstance(model,unreal.StaticMesh) else (model.materials[0].material_interface if model.materials else None)
        self.surface={'mode':'custom','material':material.get_path_name() if material else ''}
        self.history=[];self.pending=None;self.unsaved=False;self.geometry_dirty=self.surface_dirty=False
        self.model_sidebar.sync({'model':model,'animation':None,'use_animation':False})
        self.viewer.update_animation(self.model_sidebar.snapshot());self.sync_controls();self.viewer.frame();self.show_custom()
        self.expand_section('shape',False);self.expand_section('transform',False)
        self.asset_label.set_text('自定义模型预览 · '+model.get_path_name())
        self.status.set_text('已载入自定义模型。材质替换和动画仅用于此预览，不改写原网格；材质实例参数可单独确认保存。')
        return True

    def apply(self,record=True):
        self.pending=None;geometry.validate(self.settings);material=self.current_material()
        if self.custom_mesh:
            self.viewer.update(self.settings,self.preview_material());self.surface_dirty=False;self.unsaved=False
            self.status.set_text('自定义模型预览已更新。原网格未改动。');return
        if self.mesh:
            storage.content_path(self.mesh.get_path_name())
            if self.geometry_dirty:engine.update(self.mesh,self.settings,self.surface,prepared=self.viewer.prepare(self.settings))
            if self.surface_dirty:
                with unreal.ScopedEditorTransaction('特效网格：设置当前模型材质'):
                    self.mesh.modify();self.mesh.set_material(0,material)
            if self.surface_dirty and not self.geometry_dirty:engine.write_settings(self.mesh,self.settings,self.surface)
        current=self.snapshot()
        if record and self.last_applied and current!=self.last_applied:
            self.history.append(self.last_applied);self.history=self.history[-20:]
        self.last_applied=current;self.geometry_dirty=self.surface_dirty=False
        self.viewer.update(self.settings,self.preview_material())
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
        if self.custom_mesh:
            assets=list(self.imported_assets)
            if not assets:raise ValueError('当前为已有自定义模型的预览，无需重新保存网格。材质参数请在右侧单独保存。')
            for asset in assets:storage.content_path(asset.get_path_name())
            paths='\n'.join(a.get_path_name() for a in assets)
            if not self.confirm('确认保存本次导入资产','仅保存本次导入的以下资产：\n'+paths+'\n\n不会保存关卡或其他脏资产。是否继续？'):return False
            for asset in assets:
                if not unreal.EditorAssetLibrary.save_loaded_asset(asset):raise RuntimeError('保存失败：'+asset.get_path_name())
            self.imported_assets=[];self.visibility();self.status.set_text('已保存本次导入的指定资产。');return True
        geometry.validate(self.settings);self.current_material()
        path=self.mesh.get_path_name().split('.')[0] if self.mesh else engine.new_asset_path(self.settings,str(self.folder.get_text()),str(self.name.get_text()))
        if not self.confirm('确认保存当前模型','仅保存以下模型及其生成参数：\n'+path+'\n\n不会保存材质、关卡或其他脏资产。是否继续？'):
            self.status.set_text('已取消保存，没有写入文件。');return False
        self.unsaved=True
        if not self.mesh:
            folder,name=path.rsplit('/',1)
            self.mesh,_=engine.create(self.settings,folder,name,self.surface)
            self.mesh.set_material(0,self.current_material());self.last_applied=self.snapshot()
            self.pending=None;self.geometry_dirty=self.surface_dirty=False
        else:self.apply()
        engine.save(self.mesh);self.unsaved=False;self.asset_label.set_text('已保存模型 · '+self.mesh.get_path_name());self.visibility()
        self.status.set_text('已保存当前模型。材质参数请点击侧栏“保存材质…”单独确认保存。');self.locate();return True

    def locate(self):
        asset=self.custom_mesh or self.mesh
        if asset:unreal.EditorAssetLibrary.sync_browser_to_objects([asset.get_path_name()])

    def tick(self):
        self.ensure_layout()
        now=time.monotonic();dt=min(now-self.last_tick,.1);self.last_tick=now
        if self.pending is not None and now>=self.pending:self.apply()
        self.material_sidebar.tick()
        self.viewer.tick(dt)
        self.environment_sidebar.tick()
        self.refresh_save_state()


def _restore(panel,old):
    if hasattr(old,'layout_snapshot'):panel.layout_sizes=old.layout_snapshot()
    if hasattr(old,'material_sidebar'):old.material_sidebar.flush()
    if hasattr(old,'material_sidebar'):
        panel.material_sidebar.show_hidden=old.material_sidebar.show_hidden
        panel.material_sidebar.hidden.set_is_checked(old.material_sidebar.show_hidden)
    for key in ('mesh','custom_mesh','imported_assets','settings','surface','live','history','last_applied','geometry_dirty','surface_dirty','pending','unsaved'):
        if hasattr(old,key):setattr(panel,key,getattr(old,key))
    if hasattr(old,'viewer'):
        for key in ('axes','grid','floor','environment','turntable','wireframe','wire_opacity','cast_shadow'):
            if hasattr(old.viewer,key):setattr(panel.viewer,key,getattr(old.viewer,key))
    if hasattr(old,'model_sidebar'):
        state=old.model_sidebar.snapshot();panel.model_sidebar.sync(state);panel.viewer.update_animation(state)
    if hasattr(old,'environment_sidebar'):
        panel.environment_sidebar.pending=old.environment_sidebar.snapshot()
        panel.environment_sidebar.defaults=copy.deepcopy(old.environment_sidebar.defaults)
    for key in ('left_visible','right_visible'):
        if hasattr(old,key):panel.change('layout',key,getattr(old,key))
    for key,(body,_,_) in getattr(old,'sections',{}).items():
        if key in panel.sections:panel.expand_section(key,body.get_visibility()!=unreal.SlateVisibility.COLLAPSED)
    panel.switch_sidebar(getattr(old,'sidebar_tab','material'))
    panel.show_guide(getattr(old,'guide_index',0))
    if panel.mesh:
        material=panel.mesh.get_material(0)
        if panel.surface.get('mode')!='custom':panel.surface={'mode':'custom','material':material.get_path_name() if material else ''}
        panel.asset_label.set_text(panel.mesh.get_path_name())
    panel.folder.set_text(old.folder.get_text());panel.name.set_text(old.name.get_text());panel.sync_controls()
    if hasattr(old.material_sidebar,'restore'):
        panel.material_sidebar.restore(old.material_sidebar)
        panel.viewer.update(panel.settings,panel.preview_material())


def open_panel():
    global _panel,_blueprint,_tab_open
    bp=unreal.load_asset(WIDGET)
    if not bp:raise RuntimeError('找不到工作台，请在启动器重新安装插件。')
    widget=unreal.get_editor_subsystem(unreal.EditorUtilitySubsystem).spawn_and_register_tab_with_id(bp,TAB)
    _blueprint=bp;_tab_open=True
    if _panel is None or _panel.widget!=widget:
        old=_panel;_panel=Panel(widget)
        if old:
            _restore(_panel,old)
            if hasattr(old,'dispose'):old.dispose()
    return _panel


def _tick_impl():
    global _panel,_next_tab_check,_tab_open
    if _blueprint and time.monotonic()>=_next_tab_check:
        _next_tab_check=time.monotonic()+.5
        widget=unreal.get_editor_subsystem(unreal.EditorUtilitySubsystem).find_utility_widget_from_blueprint(_blueprint)
        _tab_open=bool(widget)
        if widget and (_panel is None or widget!=_panel.widget):
            old=_panel;_panel=Panel(widget)
            if old:
                _restore(_panel,old)
                if hasattr(old,'dispose'):old.dispose()
        if not widget and _panel:
            _panel.pending=None
            if hasattr(_panel,'material_sidebar'):_panel.safe(_panel.material_sidebar.flush)
    if _panel and _tab_open:_panel.safe(_panel.tick)


def _tick(_):
    global _ticking
    if _ticking or (_panel and _panel.busy):return
    _ticking=True
    try:_tick_impl()
    finally:_ticking=False


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
