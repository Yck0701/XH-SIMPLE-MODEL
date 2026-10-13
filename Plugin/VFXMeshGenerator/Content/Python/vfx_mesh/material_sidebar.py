"""Native material details, transient drafts and explicit targeted saves."""
import ctypes
import datetime
import unreal
from . import engine,preview,storage


class Sidebar:
    def __init__(self,panel,parent):
        self.panel=panel;self.source=None;self.material=None;self.path=None;self.attached=False
        self.show_hidden=False;self.revision=0;self.cache={};self.state=None;self.loading=False
        actions=panel.obj(unreal.HorizontalBox);panel.add(parent,actions)
        panel.button(actions,'刷新参数',lambda:self.refresh(True))
        self.save_button=panel.button(actions,'保存材质…',self.save)
        self.live_box=panel.obj(unreal.CheckBox);panel.text(self.live_box,'实时迭代原材质',wrap=False)
        self.live_box.set_tool_tip_text('开启后直接修改原材质实例，所有引用即时更新；写入磁盘仍需保存确认。关闭不会撤销已应用的修改。')
        self.live_box.on_check_state_changed.add_callable(lambda value:panel.safe(lambda:self.set_live(value)))
        panel.add(parent,self.live_box)
        self.hidden=panel.obj(unreal.CheckBox);panel.text(self.hidden,'显示未激活参数',wrap=False)
        self.hidden.on_check_state_changed.add_callable(self.hidden_changed);panel.add(parent,self.hidden)
        self.info=panel.text(parent,'请选择材质或材质实例。',9,(.65,.7,.75,1))
        self.save_status=panel.text(parent,'尚未保存',10,(.65,.85,.7,1))
        self.host=panel.obj(unreal.NativeWidgetHost);panel.fill(panel.add(parent,self.host,0))
        self.host_path=self.host.get_path_name();self.refresh()

    @property
    def live(self):return bool(self.state and self.state['live'])

    def can_replace(self):
        return isinstance(self.source,unreal.MaterialInstanceConstant) and self.source.get_path_name().startswith('/Game/')

    def draft(self,source):
        path=preview.native().VFX_MaterialDraft(source.get_path_name())
        result=unreal.find_object(None,path) if path else None
        if not isinstance(result,unreal.MaterialInstanceConstant):raise RuntimeError('无法创建材质预览副本。')
        return result

    def message(self,text):
        if self.state:self.state['message']=text
        self.save_status.set_text(text)
        color=(1.,.35,.2,1.) if '失败' in text else (.5,.9,.65,1.) if text.startswith('✓') else (1.,.8,.35,1.) if text.startswith('●') else (.7,.75,.8,1.)
        self.save_status.set_color_and_opacity(unreal.SlateColor(specified_color=unreal.LinearColor(*color)))

    def mark_changed(self):
        self.state['dirty']=True
        self.message('● 原材质已实时修改，尚未保存到磁盘。' if self.live else '● 预览副本有修改，尚未保存；此模式仅影响预览。')

    def hidden_changed(self,value):
        self.show_hidden=value
        if self.attached:preview.native().VFX_MaterialRefresh(self.host_path,int(value))

    def refresh(self,force=False):
        source=self.panel.current_material();path=source.get_path_name() if source else ''
        changed=path!=self.path;self.source=source;self.path=path
        if changed:
            if source and path not in self.cache:
                self.cache[path]={'draft':self.draft(source),'live':False,'dirty':False,'message':'预览副本就绪；调整后点击“保存材质…”。'}
            self.state=self.cache.get(path);self.attached=False
        self.material=(source if self.live else self.state['draft']) if self.state else None
        self.loading=True
        try:self.live_box.set_is_checked(self.live)
        finally:self.loading=False
        self.live_box.set_is_enabled(self.can_replace())
        self.hidden.set_is_enabled(bool(source));self.save_button.set_is_enabled(bool(source))
        self.info.set_text('直接迭代原材质实例：所有引用同步更新；保存只写入该实例。' if self.live else
            '预览副本：调整只影响此预览。保存可替换原实例或新增实例。' if self.can_replace() else
            '母材质 / 引擎材质通过实例预览；保存时新增工程材质实例。' if source else '请选择材质或材质实例。')
        self.message(self.state['message'] if self.state else '尚未选择材质')
        if self.attached and force:preview.native().VFX_MaterialRefresh(self.host_path,int(self.show_hidden))

    def set_live(self,value):
        if self.loading or value==self.live:return
        if value and not self.can_replace():raise ValueError('实时迭代需要当前工程的材质实例；母材质请先新增实例。')
        if value and not self.panel.confirm('启用实时迭代原材质',
            '将预览参数应用到：\n'+self.source.get_path_name()+'\n\n所有引用此实例的效果都会立即更新。此步不写入磁盘；关闭开关不会撤销已应用的修改。是否继续？'):
            self.refresh();return False
        if value:
            storage.content_path(self.source.get_path_name())
            if not preview.native().VFX_MaterialCopy(self.material.get_path_name(),self.source.get_path_name()):raise RuntimeError('应用原材质失败。')
        else:self.state['draft']=self.draft(self.source)
        self.state['live']=bool(value);self.attached=False;self.refresh()
        self.mark_changed();self.panel.viewer.update(self.panel.settings,self.material)
        return True

    def flush(self):pass  # Native property handles immediately update the active editing object.

    def tick(self):
        if not self.panel.layout_attached:self.panel.ensure_layout()
        dll=preview.native();revision=dll.VFX_MaterialRevision(self.host_path) if self.attached else -1
        if revision<0:self.attached=False
        if not self.attached:
            if not dll.VFX_MaterialDetails(self.host_path,self.material.get_path_name() if self.material else ''):
                raise RuntimeError('无法创建 UE 原生材质细节栏。')
            self.attached=True;self.revision=dll.VFX_MaterialRevision(self.host_path)
            if self.show_hidden:dll.VFX_MaterialRefresh(self.host_path,1)
            revision=self.revision
        if revision!=self.revision:
            self.revision=revision
            if self.state:self.mark_changed()

    def restore(self,old):
        self.cache=old.cache;self.path=None;self.refresh();self.attached=False

    def cancel_save(self):
        self.message('已取消保存；调整保留，未写入文件。')
        return False

    def new_path(self):
        try:folder=engine.content_folder()
        except ValueError:folder=self.source.get_path_name().rsplit('/',1)[0] if self.path.startswith('/Game/') else str(self.panel.folder.get_text())
        return engine.new_asset_path(self.panel.settings,folder,self.source.get_name()+'_Instance')

    def choose_save(self,path):
        self.tick()
        chosen=ctypes.create_unicode_buffer(2048)
        result=preview.native().VFX_MaterialSaveDialog(self.host_path,self.source.get_path_name(),path,int(self.can_replace()),int(self.live),chosen,len(chosen))
        return result,chosen.value

    def save(self,mode=None):
        if not self.material:raise ValueError('请先选择材质或材质实例。')
        confirmed=False;new_path=None
        if mode is None:
            choice,new_path=self.choose_save(self.new_path())
            if choice==0:return self.cancel_save()
            if choice not in (1,2):raise RuntimeError('无法打开材质保存确认窗口。')
            mode='replace' if choice==1 else 'new';confirmed=True
        if mode not in ('replace','new'):raise ValueError('未知材质保存方式。')
        if mode=='replace' and not self.can_replace():raise ValueError('母材质不能被实例覆盖，请选择新增实例。')
        if mode=='replace':path=self.source.get_path_name().split('.')[0]
        else:path=new_path or self.new_path()
        storage.content_path(path)
        if mode=='new':
            folder,name=path.rsplit('/',1)
            if engine.new_asset_path(self.panel.settings,folder,name)!=path:raise ValueError('该名称已被使用，请重新打开保存窗口并更改名称。')
        title='确认替换原材质实例' if mode=='replace' else '确认新增材质实例'
        detail='替换参数并保存原实例，保留所有现有引用。' if mode=='replace' else '保存当前调整为新实例，并用于工作台。模型引用需要单独保存。'
        if self.live and mode=='new':detail+='\n实时迭代已对原实例生效；另存不会撤销原实例的内存修改。'
        if not confirmed and not self.panel.confirm(title,path+'\n\n'+detail+'\n仅保存这个实例，不保存模型、母材质、关卡或其他资产。是否继续？'):
            return self.cancel_save()
        try:
            storage.content_path(path)
            target=self.source
            if mode=='new':
                folder,name=path.rsplit('/',1)
                target=unreal.AssetToolsHelpers.get_asset_tools().create_asset(name,folder,unreal.MaterialInstanceConstant,unreal.MaterialInstanceConstantFactoryNew())
                if not target:raise RuntimeError('创建新材质实例失败。')
            if target!=self.material and not preview.native().VFX_MaterialCopy(self.material.get_path_name(),target.get_path_name()):
                raise RuntimeError('复制材质参数失败。')
            if not unreal.EditorAssetLibrary.save_loaded_asset(target):raise RuntimeError('保存失败，请检查目录权限或源代码管理状态。')
            if mode=='new':
                self.panel.pickers.set_editor_property('material',target);self.panel.picker_changed(None)
            self.state['dirty']=False
            if self.attached:self.revision=preview.native().VFX_MaterialRevision(self.host_path)
            self.message('✓ 已保存 · '+datetime.datetime.now().strftime('%H:%M:%S')+'\n'+target.get_path_name())
            self.panel.status.set_text('材质保存成功：'+target.get_path_name())
            unreal.EditorAssetLibrary.sync_browser_to_objects([target.get_path_name()])
            return True
        except Exception as error:
            self.message('保存失败：'+str(error)+'\n调整仍保留，可修正后重试。');raise

    def create_instance(self):return self.save('new')
