"""Resize/retain/reset the splitter and survive deferred Slate GC/reloads."""
import ctypes,hashlib,json,math,time,traceback
from pathlib import Path
import unreal
from vfx_mesh import runtime,preview,launcher_bridge
root=Path(unreal.Paths.project_dir()).resolve()
assert root.name in ('vfx150-stock','vfx150-native')
p=runtime._panel;initial=p.layout_snapshot();sizes=[.3,.38,.32]
fn=preview.native().VFX_LayoutSizes
assert fn(p.columns.get_path_name(),(ctypes.c_double*3)(*sizes),1)
assert all(math.isclose(a,b,abs_tol=.0001) for a,b in zip(p.layout_snapshot(),sizes))
for key,(_,label,_) in p.sections.items():assert not label.get_editor_property('auto_wrap_text')
before={f.relative_to(root).as_posix():hashlib.sha256(f.read_bytes()).hexdigest()
        for base in ('Content','Config') for f in (root/base).rglob('*') if f.is_file()}
stage=0;busy=False;start=time.monotonic()
def observe(_):
    global p,stage,start,busy
    if busy or time.monotonic()-start<.8:return
    busy=True
    try:
        p=runtime._panel;p.tick()
        assert p.viewer.camera() is not None and p.material_sidebar.attached
        assert all(math.isclose(a,b,abs_tol=.0001) for a,b in zip(p.layout_snapshot(),sizes))
        if stage<3:
            launcher_bridge._reload();unreal.SystemLibrary.collect_garbage();stage+=1;return
        p.reset_layout()
        assert all(math.isclose(a,b,abs_tol=.0001) for a,b in zip(p.layout_snapshot(),[.21,.54,.25]))
        assert p.left_visible and p.right_visible
        after={f.relative_to(root).as_posix():hashlib.sha256(f.read_bytes()).hexdigest()
               for base in ('Content','Config') for f in (root/base).rglob('*') if f.is_file()}
        assert after==before
        (root/'layout.json').write_text(json.dumps({'passed':True,'resize_retained':True,'reset_layout':True,
            'three_deferred_reloads_and_gc':True,'section_titles_single_line':True,'asset_writes':0,
            'native_sha256':hashlib.sha256(Path(preview.native()._name).read_bytes()).hexdigest()},indent=2))
        unreal.unregister_slate_post_tick_callback(handle)
    except Exception:
        (root/'layout-failure.txt').write_text(traceback.format_exc(),encoding='utf8')
        unreal.unregister_slate_post_tick_callback(handle)
    finally:start=time.monotonic();busy=False
handle=unreal.register_slate_post_tick_callback(observe)
