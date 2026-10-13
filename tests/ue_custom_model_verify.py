"""Import an engine FBX fixture, play skeletal animation, and check file isolation."""
import hashlib,json,time,traceback,uuid
from pathlib import Path
import unreal
from vfx_mesh import runtime,geometry,launcher_bridge,preview
root=Path(unreal.Paths.project_dir()).resolve()
assert root.name in ('vfx150-stock','vfx150-native')
assert not geometry.defaults()['top'] and not geometry.defaults()['bottom']
p=runtime._panel;confirm=p.confirm;p.confirm=lambda *args:True
original_material=p.current_material()

def files():
    return {f.relative_to(root).as_posix():hashlib.sha256(f.read_bytes()).hexdigest()
            for base in ('Content','Config') for f in (root/base).rglob('*') if f.is_file()}

before=files();cube=unreal.load_asset('/Engine/BasicShapes/Cube')
assert p.load_custom(cube);assert p.viewer.frame_next;p.viewer.tick(.016)
assert isinstance(p.viewer.component,unreal.StaticMeshComponent) and p.viewer.dynamic.get_triangle_count()>0
assert p.save_button.get_visibility()==unreal.SlateVisibility.COLLAPSED
component=p.viewer.component
p.change('preview','cast_shadow',False);p.viewer.tick(.016)
assert not component.get_editor_property('cast_shadow')
p.pickers.set_editor_property('material',original_material);p.picker_changed(None);p.apply();p.viewer.tick(.016)
assert p.viewer.component==component and p.mesh is None and p.custom_mesh==cube
assert cube.get_material(0)!=original_material
assert files()==before

# Import only into the isolated test project, using the engine's FBX test fixture.
folder='/Game/AnimationTest_'+uuid.uuid4().hex[:8]
task=unreal.AssetImportTask();task.set_editor_property('filename','F:/UE_5.8/Engine/Content/FbxEditorAutomation/Keys_0_30_60_90_Rate_1Hz.fbx')
task.set_editor_property('destination_path',folder);task.set_editor_property('automated',True)
task.set_editor_property('save',False);task.set_editor_property('replace_existing',False)
task.set_editor_property('factory',unreal.FbxFactory())
options=unreal.FbxImportUI();options.set_editor_property('automated_import_should_detect_type',False)
options.set_editor_property('mesh_type_to_import',unreal.FBXImportType.FBXIT_SKELETAL_MESH)
options.set_editor_property('import_as_skeletal',True);options.set_editor_property('import_mesh',True)
options.set_editor_property('import_animations',True);options.set_editor_property('import_materials',False)
options.set_editor_property('import_textures',False);options.set_editor_property('create_physics_asset',False)
task.set_editor_property('options',options)
unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
assets=[unreal.load_asset(path) for path in unreal.EditorAssetLibrary.list_assets(folder,recursive=True,include_folder=False)]
skeletal=next(a for a in assets if isinstance(a,unreal.SkeletalMesh))
animation=next(a for a in assets if isinstance(a,unreal.AnimSequence))
p.imported_assets=assets;assert p.load_custom(skeletal)
assert p.save_button.get_visibility()==unreal.SlateVisibility.VISIBLE
assert str(p.save_button.get_child_at(0).get_text())=='保存导入资产…'
p.model_sidebar.sync({'animation':animation,'use_animation':True,'playing':True,'looping':True,'play_rate':1.})
p.model_sidebar.changed();p.viewer.tick(.016)
component=p.viewer.component
assert isinstance(component,unreal.SkeletalMeshComponent)
assert not component.get_editor_property('cast_shadow')
before=files();p.confirm=lambda *args:False
assert p.save() is False and files()==before
p.confirm=lambda *args:True;assert p.save() is True
assert p.save_button.get_visibility()==unreal.SlateVisibility.COLLAPSED
allowed={'Content/'+a.get_path_name().split('.')[0][6:]+'.uasset' for a in assets}
after=files();assert {k for k in before.keys()|after.keys() if before.get(k)!=after.get(k)}<=allowed
before=after;p.confirm=confirm;start=time.monotonic();stage=0;position=component.get_position()

def observe(_):
    global stage,start,position,p,component
    try:
        if time.monotonic()-start<1.:return
        start=time.monotonic()
        if stage==0:
            assert component.get_position()!=position,('animation not advancing',component.get_position())
            p.model_sidebar.sync({'playing':False});p.model_sidebar.changed();p.viewer.tick(.016)
            position=component.get_position();stage=1;return
        if stage==1:
            assert component.get_position()==position,'pause did not hold position'
            p.model_sidebar.sync({'playing':True,'play_rate':2.});p.model_sidebar.changed();p.viewer.tick(.016)
            assert component.get_play_rate()==2.
            p.viewer.rewind();assert component.get_position()==0.
            launcher_bridge._reload();p=runtime._panel;p.viewer.tick(.016);p.environment_sidebar.tick()
            assert p.custom_mesh==skeletal and not p.viewer.cast_shadow
            assert p.model_sidebar.snapshot()['animation']==animation and p.model_sidebar.snapshot()['play_rate']==2.
            component=p.viewer.component;position=component.get_position();stage=2;return
        assert component.get_position()!=position,'animation stopped after reload'
        unreal.SystemLibrary.collect_garbage()
        assert files()==before
        (root/'custom-model.json').write_text(json.dumps({'passed':True,'caps_default_off':True,'static_mesh_preview':True,
            'original_mesh_unchanged':True,'shadow_switch':True,'fbx_skeletal_and_animation_import':True,
            'animation_advances':True,'pause_holds':True,'play_rate_and_rewind':True,'reload_and_gc':True,
            'cancel_writes':0,'import_save_isolated':True,'skeletal':skeletal.get_path_name(),'animation':animation.get_path_name(),
            'native_sha256':hashlib.sha256(Path(preview.native()._name).read_bytes()).hexdigest()},indent=2))
        unreal.unregister_slate_post_tick_callback(handle)
    except Exception:
        (root/'custom-model-failure.txt').write_text(traceback.format_exc(),encoding='utf8')
        unreal.unregister_slate_post_tick_callback(handle)
handle=unreal.register_slate_post_tick_callback(observe)
