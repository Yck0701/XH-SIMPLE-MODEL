"""Native material details fixture and save checks in isolated 1.4 editors only."""
import copy,hashlib,json,uuid
from pathlib import Path
import unreal
from vfx_mesh import runtime,launcher_bridge
root=Path(unreal.Paths.project_dir()).resolve()
assert root.name in ('vfx140-stock','vfx140-native')
def files():
    return {f.relative_to(root).as_posix():hashlib.sha256(f.read_bytes()).hexdigest()
            for base in ('Content','Config') for f in (root/base).rglob('*') if f.is_file()}
def changed(before):
    after=files();return {k for k in before.keys()|after.keys() if before.get(k)!=after.get(k)}
def asset_file(asset):return 'Content/'+asset.get_path_name().split('.')[0][6:]+'.uasset'
p=runtime._panel;lib=unreal.MaterialEditingLibrary
folder='/Game/NativeDetails_'+uuid.uuid4().hex[:8]
assets=unreal.AssetToolsHelpers.get_asset_tools()
parent=assets.create_asset('M_NativeDetails',folder,unreal.Material,unreal.MaterialFactoryNew())
def expression(cls,name,group,priority):
    e=lib.create_material_expression(parent,cls,0,0)
    e.set_editor_property('parameter_name',name);e.set_editor_property('group',group)
    e.set_editor_property('sort_priority',priority);return e
scalar=expression(unreal.MaterialExpressionScalarParameter,'Roughness','01 全局参数',20)
scalar.set_editor_property('default_value',.5);scalar.set_editor_property('slider_min',0.);scalar.set_editor_property('slider_max',1.)
color=expression(unreal.MaterialExpressionVectorParameter,'Tint','01 全局参数',0)
color.set_editor_property('default_value',unreal.LinearColor(.03,.25,.65,1))
texture=expression(unreal.MaterialExpressionTextureSampleParameter2D,'MainTexture','02 主贴图',0)
texture.set_editor_property('texture',unreal.load_asset('/Engine/EngineResources/DefaultTexture'))
switch=expression(unreal.MaterialExpressionStaticSwitchParameter,'UseTexture','02 主贴图',10)
switch.set_editor_property('default_value',False)
assert lib.connect_material_expressions(texture,'',switch,'True')
assert lib.connect_material_expressions(color,'',switch,'False')
assert lib.connect_material_property(switch,'',unreal.MaterialProperty.MP_BASE_COLOR)
assert lib.connect_material_property(scalar,'',unreal.MaterialProperty.MP_ROUGHNESS)
lib.recompile_material(parent);assert unreal.EditorAssetLibrary.save_loaded_asset(parent)
mi=assets.create_asset('MI_NativeDetails',folder,unreal.MaterialInstanceConstant,unreal.MaterialInstanceConstantFactoryNew())
lib.set_material_instance_parent(mi,parent);assert unreal.EditorAssetLibrary.save_loaded_asset(mi)
parent.set_editor_property('two_sided',True)
p.pickers.set_editor_property('material',mi);p.picker_changed(None);p.apply();p.material_sidebar.tick()
assert p.material_sidebar.attached
saved=files();lib.set_material_instance_scalar_parameter_value(mi,'Roughness',.64)
p.material_sidebar.refresh(True)
assert files()==saved
confirm=p.confirm;p.confirm=lambda *args:False
assert p.material_sidebar.save() is False and files()==saved
p.confirm=lambda *args:True
assert p.material_sidebar.save() is True
assert changed(saved)=={asset_file(mi)},changed(saved)
saved=files();lib.set_material_instance_scalar_parameter_value(mi,'Roughness',.31)
p.material_sidebar.refresh(True);p.settings['radius']=173.;p.geometry_dirty=True;p.apply()
assert p.save() is True and changed(saved)=={asset_file(p.mesh)}
dirty=unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()
assert any('MI_NativeDetails' in str(d) for d in dirty) and any('M_NativeDetails' in str(d) for d in dirty)
saved=files();p.pickers.set_editor_property('material',parent);p.picker_changed(None);p.material_sidebar.tick()
p.folder.set_text(folder);p.confirm=lambda *args:False
assert p.material_sidebar.create_instance() is False and files()==saved
p.confirm=lambda *args:True
assert p.material_sidebar.create_instance() is True
assert changed(saved)=={asset_file(p.current_material())}
p.pickers.set_editor_property('material',mi);p.picker_changed(None);p.apply();p.material_sidebar.tick()
p.material_sidebar.hidden_changed(True)
p.viewer.wireframe=True;p.change('preview','wire_opacity',23);p.sync_controls();p.viewer.tick(.016)
settings=copy.deepcopy(p.settings);mesh=p.mesh;saved=files()
p.confirm=confirm
launcher_bridge._reload();p=runtime._panel;p.material_sidebar.tick();p.viewer.tick(.016)
assert p.viewer.wireframe and p.mesh==mesh and p.settings==settings and p.current_material()==mi
assert p.viewer.wire_opacity==23 and p.controls[('preview','wire_opacity')].get_value()==23
assert p.opacity_slider.get_value()==23
assert p.material_sidebar.show_hidden and p.material_sidebar.hidden.is_checked()
assert files()==saved
(root/'native-details.json').write_text(json.dumps({'passed':True,'material':mi.get_path_name(),
    'native_attached':True,'confirmed_material_only':True,'cancel_writes':0,'model_save_keeps_material_dirty':True,
    'create_instance_isolated':True,'reload_preserves_mesh_material_and_wireframe':True,'reload_preserves_opacity':True},ensure_ascii=False,indent=2),encoding='utf8')
(root/'ui-baseline.json').write_text(json.dumps(files()),encoding='utf8')
