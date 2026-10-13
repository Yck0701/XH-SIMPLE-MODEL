"""Executed only in the launcher's disposable verification project."""
import ctypes
import gc
import hashlib
import json
from pathlib import Path
import time
import traceback
import unreal

root=Path(unreal.Paths.project_dir()).resolve()
assert (root/'VFXAdapterVerify.uproject').is_file() and root.name=='verify'
config=json.loads((root/'verify-config.json').read_text(encoding='utf-8'))
from vfx_mesh import runtime,preview,launcher_bridge,native_profiles

def assets():
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for base in ('Content','Config') for p in (root/base).rglob('*') if p.is_file()}

baseline=assets();stage=0;started=time.monotonic();last=0;busy=False
dll=preview.native()
selected=native_profiles.inspect(Path(unreal.Paths.engine_dir()).resolve(),root/'Plugins/VFXMeshGenerator/Native/Win64')
assert selected['compatible'] and selected['profile']==config['profile']
dll.VFX_GlobalEnvironmentHash.argtypes=[];dll.VFX_GlobalEnvironmentHash.restype=ctypes.c_uint
environment=dll.VFX_GlobalEnvironmentHash()
unreal.EditorPythonScripting.set_keep_python_script_alive(True)

def finish(passed,error=''):
    unreal.unregister_slate_post_tick_callback(handle)
    (root/'result.json').write_text(json.dumps(dict(config,passed=passed,error=error,
        close_reopen_cycles=3,reload_cycles=3,asset_writes=0,seconds=time.monotonic()-started)),encoding='utf-8')
    unreal.EditorPythonScripting.set_keep_python_script_alive(False)
    unreal.SystemLibrary.quit_editor()

def tick(_):
    global stage,last,busy
    if busy or time.monotonic()-last<1:return
    busy=True
    try:
        if time.monotonic()-started>240:raise RuntimeError('Preview lifecycle timed out')
        if stage==0:runtime.open_panel();stage=1;return
        if stage==1:
            panel=runtime._panel
            if not panel or not panel.viewer.attached:return
            assert panel.viewer.camera() is not None
            panel.settings.update(shape='sphere',segments=24,rings=16,radius=125.)
            panel.geometry_dirty=True;panel.apply()
            material=unreal.load_asset('/Engine/EngineMaterials/DefaultMaterial')
            assert material is not None
            panel.pickers.set_editor_property('material',material);panel.picker_changed(None)
            panel.viewer.wireframe=True;panel.viewer.wire_opacity=20;panel.viewer.cast_shadow=False
            panel.viewer.tick(.016);panel.material_sidebar.tick()
            assert panel.material_sidebar.attached and panel.material_sidebar.material is not None
            panel.environment_sidebar.tick();assert panel.environment_sidebar.read()
            stage=2;return
        if stage in (2,4,6):
            subsystem=unreal.get_editor_subsystem(unreal.EditorUtilitySubsystem)
            assert subsystem.close_tab_by_id(subsystem.get_tab_id_from_blueprint(runtime._blueprint))
            stage+=1;return
        if stage in (3,5,7):
            unreal.SystemLibrary.collect_garbage();gc.collect();runtime.open_panel();stage+=1;return
        if stage in (8,9,10):
            if not runtime._panel.viewer.attached:return
            launcher_bridge._reload();stage+=1;return
        if stage==11:
            unreal.SystemLibrary.collect_garbage();gc.collect();stage+=1;return
        assert runtime._panel.viewer.attached and runtime._panel.viewer.camera() is not None
        assert assets()==baseline,'Unexpected Content/Config writes'
        assert dll.VFX_GlobalEnvironmentHash()==environment,'Shared environment changed'
        finish(True)
    except Exception:finish(False,traceback.format_exc())
    finally:last=time.monotonic();busy=False

handle=unreal.register_slate_post_tick_callback(tick)
