"""Environment editing, layout retention and shared-profile isolation in test editors."""
import copy,ctypes,hashlib,json,math
from pathlib import Path
import unreal
from vfx_mesh import runtime,preview,launcher_bridge
root=Path(unreal.Paths.project_dir()).resolve()
assert root.name in ('vfx150-stock','vfx150-native')
p=runtime._panel;p.viewer.tick(.016);p.environment_sidebar.tick();e=p.environment_sidebar
fn=preview.native().VFX_GlobalEnvironmentHash;fn.argtypes=[];fn.restype=ctypes.c_uint
global_hash=fn()
assert global_hash==json.loads((root/'global-environment-before.json').read_text())['hash']

def files():
    return {f.relative_to(root).as_posix():hashlib.sha256(f.read_bytes()).hexdigest()
            for base in ('Content','Config') for f in (root/base).rglob('*') if f.is_file()}

before=files();mesh=p.mesh;dynamic=p.viewer.dynamic;defaults=copy.deepcopy(e.defaults)
state=copy.deepcopy(e.snapshot());v=state[0]
v[0]=2.8;v[1]=3.2;v[2]=87.;v[3]=-23.;v[4]=53.;v[5:8]=[.9,.5,.2]
v[8]=1.25;v[9]=.7;v[10]=0.;v[11:14]=[.04,.07,.1];v[14]=.8
v[15]=0.;v[16]=0.;v[17]=1.;v[18]=-12.;v[19]=1.
cube=unreal.load_asset('/Engine/EditorMaterials/AssetViewer/T_GreyAmbient')
assert isinstance(cube,unreal.TextureCube)
state=(v,cube.get_path_name());e.apply(state);p.viewer.tick(.016)
actual=e.read()
assert all(math.isclose(a,b,abs_tol=.001) for a,b in zip(actual[0],v)),actual
assert actual[1]==state[1] and p.viewer.dynamic==dynamic and p.mesh==mesh
assert not p.viewer.floor and not p.viewer.environment
assert fn()==global_hash and files()==before
invalid=v.copy();invalid[0]=float('nan')
assert not preview.native().VFX_EnvironmentSet(p.viewer.path,(ctypes.c_double*20)(*invalid),state[1])
assert e.read()==actual
p.switch_sidebar('environment');p.expand_section('transform',False);p.change('layout','left_visible',False)
launcher_bridge._reload();p=runtime._panel;p.viewer.tick(.016);p.environment_sidebar.tick();e=p.environment_sidebar
assert p.sidebar_tab=='environment' and not p.left_visible and p.right_visible
assert p.sections['transform'][0].get_visibility()==unreal.SlateVisibility.COLLAPSED
restored=e.read()
assert all(math.isclose(a,b,abs_tol=.001) for a,b in zip(restored[0],actual[0])) and restored[1]==actual[1]
assert p.mesh==mesh and files()==before and fn()==global_hash
e.reset();reset=e.read()
assert all(math.isclose(a,b,abs_tol=.001) for a,b in zip(reset[0],defaults[0])) and reset[1]==defaults[1]
# Leave a familiar material and layout ready for real mouse/render checks.
p.change('layout','left_visible',True);p.expand_section('transform',True);p.sync_controls();p.viewer.tick(.016);e.tick()
assert files()==before and fn()==global_hash
(root/'environment.json').write_text(json.dumps({'passed':True,'all_environment_values_applied':True,
    'global_profile_unchanged':True,'asset_writes':0,'no_mesh_rebuild':True,'reset_restores_defaults':True,
    'reload_preserves_environment_and_layout':True,'invalid_input_rejected':True,
    'native_sha256':hashlib.sha256(Path(preview.native()._name).read_bytes()).hexdigest()},indent=2))
(root/'ui-environment-baseline.json').write_text(json.dumps(files()))
