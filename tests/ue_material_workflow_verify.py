"""Draft isolation, explicit save choices and direct material iteration."""
import hashlib,json,uuid
from pathlib import Path
import unreal
from vfx_mesh import runtime,preview,launcher_bridge
root=Path(unreal.Paths.project_dir()).resolve()
assert root.name in ('vfx150-stock','vfx150-native')
p=runtime._panel;lib=unreal.MaterialEditingLibrary;assets=unreal.AssetToolsHelpers.get_asset_tools()
folder='/Game/MaterialWorkflow_'+uuid.uuid4().hex[:8]
source=assets.duplicate_asset('MI_Source',folder,p.current_material())
assert unreal.EditorAssetLibrary.save_loaded_asset(source)
p.pickers.set_editor_property('material',source);p.picker_changed(None);p.apply();p.material_sidebar.tick()
s=p.material_sidebar;original=lib.get_material_instance_scalar_parameter_value(source,'Roughness')

def files():
    return {f.relative_to(root).as_posix():hashlib.sha256(f.read_bytes()).hexdigest()
            for base in ('Content','Config') for f in (root/base).rglob('*') if f.is_file()}
def changed(before):
    after=files();return {k for k in before.keys()|after.keys() if before.get(k)!=after.get(k)}
def asset_file(a):return 'Content/'+a.get_path_name().split('.')[0][6:]+'.uasset'
def scalar(a):return lib.get_material_instance_scalar_parameter_value(a,'Roughness')

before=files();confirm=p.confirm
assert not s.live and s.material!=source and s.material.get_path_name().startswith('/Engine/Transient')
draft=s.material;lib.set_material_instance_scalar_parameter_value(draft,'Roughness',.82)
base=draft.get_editor_property('base_property_overrides').copy()
base.set_editor_property('override_two_sided',True);base.set_editor_property('two_sided',True)
draft.set_editor_property('base_property_overrides',base)
s.tick();assert s.state['dirty'] and '尚未保存' in str(s.save_status.get_text())
assert scalar(source)==original and files()==before and p.preview_material()==draft
choose=s.choose_save;s.choose_save=lambda path:(0,path)
assert s.save() is False and files()==before
s.choose_save=choose
p.confirm=lambda *args:False
assert s.save('replace') is False and files()==before and scalar(source)==original
assert s.save('new') is False and files()==before
p.confirm=lambda *args:True
assert s.save('replace') is True and changed(before)=={asset_file(source)}
assert abs(scalar(source)-.82)<.001 and source.get_editor_property('base_property_overrides').get_editor_property('two_sided')
assert '已保存' in str(s.save_status.get_text()) and source.get_path_name() in str(s.save_status.get_text())
before=files();lib.set_material_instance_scalar_parameter_value(draft,'Roughness',.24)
s.choose_save=lambda proposed:(2,folder+'/MI_用户命名')
assert s.save() is True
s.choose_save=choose
new=p.current_material();assert new!=source and changed(before)=={asset_file(new)}
assert new.get_name()=='MI_用户命名'
assert abs(scalar(new)-.24)<.001 and abs(scalar(source)-.82)<.001
assert new.get_editor_property('parent')==source.get_editor_property('parent')
assert new.get_editor_property('base_property_overrides').get_editor_property('two_sided')
before=files();s=p.material_sidebar;s.tick()
p.confirm=lambda *args:False
assert s.set_live(True) is False and not s.live and files()==before
p.confirm=lambda *args:True
assert s.set_live(True) and s.material==new
s.tick();lib.set_material_instance_scalar_parameter_value(s.material,'Roughness',.67);s.tick()
assert abs(scalar(new)-.67)<.001 and files()==before
assert s.set_live(False) and s.material!=new
lib.set_material_instance_scalar_parameter_value(s.material,'Roughness',.91)
assert abs(scalar(new)-.67)<.001
draft=s.material;launcher_bridge._reload();p=runtime._panel;s=p.material_sidebar;p.tick()
assert s.material==draft and not s.live and abs(scalar(s.material)-.91)<.001
assert files()==before
# Prevent replacement that would make the original instance its own ancestor.
lib.set_material_instance_parent(s.material,new)
assert not preview.native().VFX_MaterialCopy(s.material.get_path_name(),new.get_path_name())
# A rejected copy stays visibly failed and retains the editable draft.
p.confirm=lambda *args:True
try:s.save('replace');raise AssertionError('save failure was hidden')
except RuntimeError:pass
assert '保存失败' in str(s.save_status.get_text()) and abs(scalar(s.material)-.91)<.001 and files()==before
lib.set_material_instance_parent(s.material,source.get_editor_property('parent'))
# Replacing must also clear overrides, including values equal to class defaults.
base=s.material.get_editor_property('base_property_overrides').copy()
base.set_editor_property('override_two_sided',False);s.material.set_editor_property('base_property_overrides',base)
s.material.set_editor_property('scalar_parameter_values',[])
assert s.save('replace')
assert not new.get_editor_property('base_property_overrides').get_editor_property('override_two_sided')
assert not new.get_editor_property('scalar_parameter_values')
p.confirm=confirm
(root/'material-workflow.json').write_text(json.dumps({'passed':True,'draft_does_not_modify_source':True,
    'explicit_save_choices':True,'custom_instance_name':True,'cancel_writes':0,'replace_keeps_identity':True,'new_instance_copies_overrides':True,
    'save_status_and_failure_visible':True,'live_toggle_confirmed':True,'direct_iteration_no_disk_write':True,
    'turn_off_keeps_original_changes':True,'reload_keeps_draft':True,'parent_cycle_rejected':True,'cleared_overrides_copied':True,
    'native_sha256':hashlib.sha256(Path(preview.native()._name).read_bytes()).hexdigest()},indent=2))
