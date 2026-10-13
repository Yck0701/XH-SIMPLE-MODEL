"""User-owned static/skeletal assets and optional transient animation playback."""
import datetime
import unreal
from .settings_types import VFXImportedModelSettings

FIELDS=('model','use_animation','animation','playing','looping','play_rate')


class Sidebar:
    def __init__(self,panel,parent):
        self.panel=panel;self.loading=False
        self.settings=VFXImportedModelSettings()
        for key,value in (('use_animation',False),('playing',True),('looping',True),('play_rate',1.)):
            self.settings.set_editor_property(key,value)
        actions=panel.obj(unreal.HorizontalBox);panel.add(parent,actions)
        panel.button(actions,'导入模型文件…',self.import_files)
        self.details=panel.obj(unreal.DetailsView)
        self.details.set_editor_property('force_hidden_property_visibility',True)
        self.details.set_editor_property('allow_filtering',False)
        self.details.set_object(self.settings)
        self.details.on_property_changed.add_callable(lambda _:panel.safe(self.changed))
        panel.add(parent,self.details)
        panel.button(parent,'动画回到开头',lambda:panel.viewer.rewind())
        panel.text(parent,'支持静态 / 骨骼网格。动画只在此预览播放；Niagara 继续引用静态网格。',9,(.65,.7,.75,1))

    def snapshot(self):return {key:self.settings.get_editor_property(key) for key in FIELDS}

    def sync(self,state):
        self.loading=True
        try:
            for key,value in state.items():self.settings.set_editor_property(key,value)
        finally:self.loading=False

    def changed(self):
        if self.loading:return
        model=self.settings.get_editor_property('model')
        if model!=self.panel.custom_mesh:
            if not model:return
            if not self.panel.load_custom(model):self.sync({'model':self.panel.custom_mesh});return
        self.panel.viewer.update_animation(self.snapshot())

    def selected(self):
        models=[a for a in unreal.EditorUtilityLibrary.get_selected_assets() if isinstance(a,(unreal.StaticMesh,unreal.SkeletalMesh))]
        if len(models)!=1:raise ValueError('请在内容浏览器选中一个静态网格或骨骼网格。')
        self.panel.load_custom(models[0])

    def import_files(self):
        p=self.panel
        if not p.can_switch():return
        folder=str(p.folder.get_text()).rstrip('/')+'/Import_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        suffix=1;base=folder
        while unreal.EditorAssetLibrary.does_directory_exist(folder):folder=base+'_'+str(suffix);suffix+=1
        if not p.confirm('确认导入自定义模型','打开 UE 原生导入窗口，导入到新目录：\n'+folder+'\n\n可在导入设置中选择骨骼网格与动画。不会覆盖原目录中的模型。是否继续？'):return
        assets=list(unreal.AssetToolsHelpers.get_asset_tools().import_assets_with_dialog(folder))
        if not assets:return
        assets=[unreal.load_asset(path) for path in unreal.EditorAssetLibrary.list_assets(folder,recursive=True,include_folder=False)]
        # Track exactly the returned new assets; never use Save All.
        p.imported_assets=[a for a in assets if a.get_path_name().startswith(folder+'/')]
        models=[a for a in assets if isinstance(a,(unreal.StaticMesh,unreal.SkeletalMesh))]
        if models:
            p.load_custom(models[0],check=False)
            animations=[a for a in assets if isinstance(a,unreal.AnimSequence)]
            if isinstance(models[0],unreal.SkeletalMesh) and animations:
                self.sync({'animation':animations[0],'use_animation':True});self.changed()
        unreal.EditorAssetLibrary.sync_browser_to_objects([a.get_path_name() for a in assets])
        p.status.set_text('导入已完成。可预览模型与动画；点击“保存模型…”确认保存本次导入的资产。')
