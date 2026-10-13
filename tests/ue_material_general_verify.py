"""Exercise the native material editor proxy in isolated 1.5 test projects."""
import hashlib,json
from pathlib import Path
import unreal
from vfx_mesh import runtime,preview

root=Path(unreal.Paths.project_dir()).resolve()
assert root.name in ('vfx150-stock','vfx150-native')
p=runtime._panel;p.material_sidebar.tick();mi=p.current_material()
assert isinstance(mi,unreal.MaterialInstanceConstant)
unreal.SystemLibrary.collect_garbage()
proxies=[o for o in unreal.ObjectIterator(unreal.MaterialEditorInstanceConstant) if o.get_name().startswith('MaterialEditorInstanceConstant_')]
parent=mi.get_editor_property('parent')
proxies=[o for o in proxies if o.get_editor_property('parent')==parent]
assert proxies
# Previous details trees can live until the next Slate frame after a reload.
proxy=max(proxies,key=lambda o:int(o.get_name().rsplit('_',1)[1]))

def files():
    return {f.relative_to(root).as_posix():hashlib.sha256(f.read_bytes()).hexdigest()
            for base in ('Content','Config') for f in (root/base).rglob('*') if f.is_file()}

before=files();original=proxy.get_editor_property('base_property_overrides').copy()
base=original.copy()
base.set_editor_property('override_blend_mode',True)
base.set_editor_property('blend_mode',unreal.BlendMode.BLEND_MASKED)
base.set_editor_property('override_two_sided',True)
base.set_editor_property('two_sided',False)
base.set_editor_property('override_opacity_mask_clip_value',True)
base.set_editor_property('opacity_mask_clip_value',.42)
proxy.set_editor_property('base_property_overrides',base)
source=mi.get_editor_property('base_property_overrides')
assert source.get_editor_property('blend_mode')==unreal.BlendMode.BLEND_MASKED
assert source.get_editor_property('override_two_sided') and not source.get_editor_property('two_sided')
assert abs(source.get_editor_property('opacity_mask_clip_value')-.42)<.001
assert files()==before
confirmation=p.confirm
p.confirm=lambda *args:False
assert p.material_sidebar.save('replace') is False and files()==before
p.confirm=lambda *args:True
assert p.material_sidebar.save('replace') is True
after=files();changed={k for k in before.keys()|after.keys() if before.get(k)!=after.get(k)}
assert changed=={'Content/'+mi.get_path_name().split('.')[0][6:]+'.uasset'},changed
p.confirm=confirmation

# Disabling the override resumes the parent value after the details view refresh.
base.set_editor_property('override_two_sided',False)
proxy.set_editor_property('base_property_overrides',base);p.material_sidebar.refresh(True)
assert not mi.get_editor_property('base_property_overrides').get_editor_property('override_two_sided')
assert proxy.get_editor_property('base_property_overrides').get_editor_property('two_sided')==parent.get_editor_property('two_sided')

# Reparenting must regenerate the visible parameter objects, then restore them.
alternate=unreal.load_asset('/Engine/EngineMaterials/DefaultMaterial')
proxy.set_editor_property('parent',alternate);p.material_sidebar.refresh(True)
assert mi.get_editor_property('parent')==alternate
proxy.set_editor_property('parent',parent);p.material_sidebar.refresh(True)
assert mi.get_editor_property('parent')==parent
assert any(str(param.get_editor_property('parameter_info').name)=='Roughness'
           for group in proxy.get_editor_property('parameter_groups') for param in group.get_editor_property('parameters'))
proxy.set_editor_property('base_property_overrides',original);p.material_sidebar.refresh(True)
assert files()==after
try:proxy.get_editor_property('jx3_blend_mode');custom=True
except Exception:custom=False
assert custom==(root.name=='vfx150-native')
(root/'material-general.json').write_text(json.dumps({'passed':True,'base_overrides_propagate':True,
    'disable_restores_inheritance':True,'parent_refreshes_parameters':True,'cancel_writes':0,
    'save_only_selected_instance':True,'jx3_available':custom,
    'native_sha256':hashlib.sha256(Path(preview.native()._name).read_bytes()).hexdigest()},indent=2))
