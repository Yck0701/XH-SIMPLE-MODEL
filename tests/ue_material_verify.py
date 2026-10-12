"""Run inside the disposable 1.3 UE projects after ue_workspace_verify.py."""
import copy
import json
import uuid
from pathlib import Path
import unreal
from vfx_mesh import runtime,launcher_bridge,preview

root=Path(unreal.Paths.project_dir()).resolve()
assert root.name in ('vfx130-stock','vfx130-native')
# Reuse the independent content/config snapshot, without rerunning its test.
import hashlib
def files():
    return {f.relative_to(root).as_posix():hashlib.sha256(f.read_bytes()).hexdigest()
            for base in ('Content','Config') for f in (root/base).rglob('*') if f.is_file()}
def changed(before):
    after=files()
    return {k for k in before.keys()|after.keys() if before.get(k)!=after.get(k)}
def asset_file(asset):return 'Content/'+asset.get_path_name().split('.')[0][6:]+'.uasset'

p=runtime._panel;lib=unreal.MaterialEditingLibrary
folder='/Game/MaterialIsolation_'+uuid.uuid4().hex[:8]
assets=unreal.AssetToolsHelpers.get_asset_tools()
parent=assets.create_asset('M_Parameters',folder,unreal.Material,unreal.MaterialFactoryNew())
def expression(cls,name):
    e=lib.create_material_expression(parent,cls,0,0);e.set_editor_property('parameter_name',name);return e
scalar=expression(unreal.MaterialExpressionScalarParameter,'Roughness');scalar.set_editor_property('default_value',.5)
color=expression(unreal.MaterialExpressionVectorParameter,'Tint');color.set_editor_property('default_value',unreal.LinearColor(.1,.6,.3,1))
texture=expression(unreal.MaterialExpressionTextureSampleParameter2D,'MainTexture')
texture.set_editor_property('texture',unreal.load_asset('/Engine/EngineResources/DefaultTexture'))
switch=expression(unreal.MaterialExpressionStaticSwitchParameter,'UseTexture')
switch.set_editor_property('default_value',False)
assert lib.connect_material_expressions(texture,'',switch,'True')
assert lib.connect_material_expressions(color,'',switch,'False')
assert lib.connect_material_property(switch,'',unreal.MaterialProperty.MP_BASE_COLOR)
assert lib.connect_material_property(scalar,'',unreal.MaterialProperty.MP_ROUGHNESS)
lib.recompile_material(parent)
assert unreal.EditorAssetLibrary.save_loaded_asset(parent)
mi=assets.create_asset('MI_Parameters',folder,unreal.MaterialInstanceConstant,unreal.MaterialInstanceConstantFactoryNew())
lib.set_material_instance_parent(mi,parent);assert unreal.EditorAssetLibrary.save_loaded_asset(mi)
parent.set_editor_property('two_sided',True) # unrelated parent edits must remain dirty
p.pickers.set_editor_property('material',mi);p.picker_changed(None);p.apply()
sidebar=p.material_sidebar
assert set(sidebar.bindings)=={('scalar','Roughness'),('vector','Tint'),('texture','MainTexture'),('static_switch','UseTexture')},sidebar.bindings
baseline=files()
values={('scalar','Roughness'):.23,('vector','Tint'):unreal.LinearColor(.02,.35,.9,1),
        ('texture','MainTexture'):unreal.load_asset('/Engine/EngineResources/WhiteSquareTexture'),('static_switch','UseTexture'):True}
for key,value in values.items():
    sidebar.override(key,True)
    sidebar.bindings[key][0].set_editor_property('value',value);sidebar.change(key)
sidebar.flush()
assert abs(lib.get_material_instance_scalar_parameter_value(mi,'Roughness')-.23)<1e-5
assert lib.get_material_instance_vector_parameter_value(mi,'Tint')==values[('vector','Tint')]
assert lib.get_material_instance_texture_parameter_value(mi,'MainTexture')==values[('texture','MainTexture')]
assert lib.get_material_instance_static_switch_parameter_value(mi,'UseTexture') is True
assert files()==baseline,'Material live edit wrote files'
sidebar.override(('scalar','Roughness'),False)
assert abs(lib.get_material_instance_scalar_parameter_value(mi,'Roughness')-.5)<1e-5
assert not lib.is_material_instance_parameter_overridden(mi,'Roughness')
sidebar.override(('scalar','Roughness'),True)
sidebar.bindings[('scalar','Roughness')][0].set_editor_property('value',.31);sidebar.change(('scalar','Roughness'))
confirm=p.confirm;p.confirm=lambda *args:False
assert sidebar.save() is False and files()==baseline
p.confirm=lambda *args:True
assert sidebar.save() is True
assert changed(baseline)=={asset_file(mi)},changed(baseline)
saved=files()
# Model save must not accidentally save the next material edit.
sidebar.bindings[('scalar','Roughness')][0].set_editor_property('value',.42);sidebar.change(('scalar','Roughness'));sidebar.flush()
p.settings['radius']=181.;p.geometry_dirty=True;p.unsaved=True;p.apply()
assert p.save() is True
assert changed(saved)=={asset_file(p.mesh)},changed(saved)
dirty=unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()
assert any('MI_Parameters' in str(v) for v in dirty) and any('M_Parameters' in str(v) for v in dirty)
# Create-instance cancel and confirmation have the same save boundary.
p.pickers.set_editor_property('material',parent);p.picker_changed(None);p.folder.set_text(folder)
saved=files();p.confirm=lambda *args:False
assert p.material_sidebar.create_instance() is False and files()==saved
p.confirm=lambda *args:True
assert p.material_sidebar.create_instance() is True
child=p.current_material();assert child.get_editor_property('parent')==parent
assert changed(saved)=={asset_file(child)},changed(saved)
p.confirm=confirm
p.pickers.set_editor_property('material',mi);p.picker_changed(None);p.apply();sidebar=p.material_sidebar
sidebar.override(('static_switch','UseTexture'),False)
sidebar.bindings[('scalar','Roughness')][0].set_editor_property('value',.64);sidebar.change(('scalar','Roughness'))
p.viewer.wireframe=True;p.viewer.tick(.016)
assert preview.native().VFX_GlobalParameter(mi.get_path_name(),0,'Roughness')==1
assert preview.native().VFX_GlobalParameter(mi.get_path_name(),1,'Roughness')==0
assert preview.native().VFX_Wireframe(p.viewer.path,1)==1
before=copy.deepcopy(p.settings);mesh=p.mesh;saved=files()
launcher_bridge._reload();p=runtime._panel
assert p.viewer.wireframe and p.settings==before and p.mesh==mesh
assert abs(lib.get_material_instance_scalar_parameter_value(mi,'Roughness')-.64)<1e-5
assert files()==saved,'Reload wrote assets'
p.viewer.tick(.016)
assert p.viewer.attached and p.viewer.last_flags[-1]==1
sub=unreal.get_editor_subsystem(unreal.EditorUtilitySubsystem)
sub.close_tab_by_id(runtime.TAB);runtime.open_panel();p=runtime._panel
assert p.viewer.wireframe and p.mesh==mesh and p.current_material()==mi
p.viewer.tick(.016)
p.material_sidebar.refresh(True);p.scroll.scroll_widget_into_view(p.material_sidebar.info,False)
report={'passed':True,'parameter_types':['scalar','vector','texture','static_switch'],
        'live_edit_writes':0,'cancel_save_writes':0,'material_save_only_target':True,
        'model_save_keeps_material_dirty':True,'create_instance_only_target':True,
        'override_inheritance':True,'reload_keeps_pending_material_and_wireframe':True,
        'reopen_preserves_state':True,'native_wireframe':True,'material':mi.get_path_name()}
(root/'material-isolation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
