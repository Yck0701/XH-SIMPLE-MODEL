"""Execute only inside the disposable VFX120 UE test project."""
import hashlib
import json
import uuid
from pathlib import Path
import unreal
from vfx_mesh import runtime,engine

root=Path(unreal.Paths.project_dir()).resolve()
assert root.name in ('vfx120','vfx120-stock','vfx120-native','vfx130-stock','vfx130-native','vfx140-stock','vfx140-native','vfx150-stock','vfx150-native'), 'This test must not run in a user project'
def files():
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for base in ('Content','Config') for p in (root/base).rglob('*') if p.is_file()}

p=runtime._panel
original_confirm=p.confirm;p.confirm=lambda *args:True;p.new();p.confirm=original_confirm
folder='/Game/SaveIsolation_'+uuid.uuid4().hex[:8]
tools=unreal.AssetToolsHelpers.get_asset_tools()
material=tools.create_asset('M_Unrelated',folder,unreal.Material,unreal.MaterialFactoryNew())
unreal.EditorAssetLibrary.save_loaded_asset(material)
material.set_editor_property('two_sided',True)
other,_=engine.create(engine.geometry.defaults(),folder,'SM_Unrelated',{})
engine.save(other)
other_settings=engine.geometry.defaults();other_settings['width']=311.
engine.update(other,other_settings)
baseline=files()
level=unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
actors_before=set(str(a) for a in unreal.GameplayStatics.get_all_actors_of_class(level,unreal.Actor))
p.settings['shape']='sphere';p.settings['radius']=115.;p.geometry_dirty=True
p.surface={'mode':'custom','material':material.get_path_name()};p.surface_dirty=True;p.apply();p.viewer.tick(.016)
assert p.mesh is None and files()==baseline
assert p.viewer.attached and p.viewer.camera() is not None
assert actors_before==set(str(a) for a in unreal.GameplayStatics.get_all_actors_of_class(level,unreal.Actor))
p.folder.set_text(folder);p.name.set_text('SM_Target')
confirm=p.confirm;p.confirm=lambda *args:False
assert p.save() is False and files()==baseline and p.mesh is None
p.confirm=lambda *args:True
assert p.save() is True
after=files();changed={key for key in baseline.keys()|after.keys() if baseline.get(key)!=after.get(key)}
target='Content/'+folder.split('/')[-1]+'/SM_Target.uasset'
assert changed=={target},changed
mesh=p.mesh;path=mesh.get_path_name();on_disk=after[target]
p.settings['radius']=169.;p.geometry_dirty=True;p.apply();p.viewer.tick(.016)
assert p.mesh==mesh and p.mesh.get_path_name()==path
assert files()==after,'Live update wrote files'
p.confirm=lambda *args:False
assert p.save() is False and files()==after
p.confirm=lambda *args:True
assert p.save() is True
final=files();changed_again={key for key in after if after[key]!=final[key]}
assert changed_again=={target},changed_again
assert p.mesh.get_material(0)==material
p.confirm=confirm
unreal.EditorUtilityLibrary.sync_browser_to_folders([folder])
assert engine.content_folder()==folder
dirty=[str(v) for v in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()]
assert any('M_Unrelated' in v for v in dirty) and any('SM_Unrelated' in v for v in dirty),dirty
report={'passed':True,'new_preview_writes':0,'cancel_writes':0,'saved_files':sorted(changed),
        'repeat_saved_files':sorted(changed_again),'unrelated_material_and_mesh_still_dirty':True,
        'level_actor_count_unchanged':True,'preview_world_isolated':True,'same_mesh_reference':True,
        'material':material.get_path_name(),'ue_content_folder':engine.content_folder()}
(root/'save-isolation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
