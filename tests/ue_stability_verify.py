"""Measure rebuilds, callback reentry and reload retention in disposable editors."""
import gc,hashlib,json,time,traceback,weakref
from pathlib import Path
import unreal
from vfx_mesh import runtime,preview,engine,launcher_bridge

root=Path(unreal.Paths.project_dir()).resolve()
assert root.name in ('vfx150-stock','vfx150-native')
bystander=Path('H:/GPT/XH/tests/vfx152-bystander')
assert bystander.is_dir()
def files(base):
    return {str(p.relative_to(base)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in base.rglob('*') if p.is_file()}
other_before=files(bystander)
p=runtime._panel;confirm=p.confirm;p.confirm=lambda *args:True;p.new();p.confirm=confirm
optimized=hasattr(p.viewer,'prepare')
p.settings.update(shape='sphere',segments=64,rings=64)
p.apply();p.viewer.tick(.016)
original=engine.dynamic_mesh;calls=[]
def counted(settings):
    start=time.perf_counter();result=original(settings);calls.append(time.perf_counter()-start);return result
engine.dynamic_mesh=counted
try:
    start=time.perf_counter()
    for _ in range(8):p.viewer.update(p.settings,None);p.viewer.tick(.016)
    material={'builds':len(calls),'seconds':time.perf_counter()-start}
    p.folder.set_text('/Game/Stability');p.name.set_text('SM_Stress')
    p.confirm=lambda *args:True;p.save();p.confirm=confirm;calls.clear()
    start=time.perf_counter()
    for radius in (101.,103.,105.,107.):
        p.settings['radius']=radius;p.geometry_dirty=True;p.apply();p.viewer.tick(.016)
    geometry={'builds':len(calls),'seconds':time.perf_counter()-start}
finally:engine.dynamic_mesh=original;p.confirm=confirm

tick=p.tick;depth=[0,0];ticks=[]
def nested_tick():
    depth[0]+=1;depth[1]=max(depth);ticks.append(1)
    if depth[0]==1:runtime._tick(0)
    depth[0]-=1
p.tick=nested_tick
try:runtime._tick(0)
finally:p.tick=tick
guard=[];p.tick=lambda:guard.append(1)
try:p.safe(lambda:runtime._tick(0))
finally:p.tick=tick
if optimized:
    assert material['builds']==0,material
    assert geometry['builds']==4,geometry
    assert depth[1]==1 and not guard,(depth,guard)

asset_before={str(f.relative_to(root)):hashlib.sha256(f.read_bytes()).hexdigest()
              for folder in ('Content','Config') for f in (root/folder).rglob('*') if f.is_file()}
p=None;tick=None;confirm=None
old_refs=[];reload_times=[];hash_reads=[];stage=0;start=time.monotonic();busy=False
path_open=Path.open
def tracked_open(path,*args,**kwargs):
    if path.suffix=='.dll':hash_reads.append(str(path))
    return path_open(path,*args,**kwargs)
Path.open=tracked_open
def run(_):
    global stage,start,busy
    if busy or time.monotonic()-start<1:return
    busy=True
    try:
        if stage<4:
            old_refs.append(weakref.ref(runtime._panel))
            t=time.perf_counter();launcher_bridge._reload();reload_times.append(time.perf_counter()-t)
            stage+=1;return
        Path.open=path_open;unreal.SystemLibrary.collect_garbage();gc.collect()
        if stage<6:stage+=1;return
        assert runtime._panel.mesh is not None and runtime._panel.settings['radius']==107.
        assert runtime._panel.viewer.camera() is not None
        after={str(f.relative_to(root)):hashlib.sha256(f.read_bytes()).hexdigest()
               for folder in ('Content','Config') for f in (root/folder).rglob('*') if f.is_file()}
        assert after==asset_before and files(bystander)==other_before
        if optimized:
            assert not hash_reads,hash_reads
            assert not any(r() is not None for r in old_refs),'Old panels retained after reload'
        report={'passed':True,'optimized':optimized,'material':material,'geometry':geometry,
                'max_tick_depth':depth[1],'ticks_during_action':len(guard),'reload_seconds':reload_times,
                'reload_dll_reads':len(hash_reads),'retained_old_panels':sum(r() is not None for r in old_refs),
                'reload_asset_writes':0,'bystander_unchanged':True,
                'runtime_sha256':hashlib.sha256(Path(runtime.__file__).read_bytes()).hexdigest(),
                'native_sha256':hashlib.sha256(Path(preview.native()._name).read_bytes()).hexdigest()}
        (root/'stability.json').write_text(json.dumps(report,indent=2))
        unreal.unregister_slate_post_tick_callback(handle)
    except Exception:
        Path.open=path_open;(root/'stability-failure.txt').write_text(traceback.format_exc(),encoding='utf8')
        unreal.unregister_slate_post_tick_callback(handle)
    finally:busy=False;start=time.monotonic()
handle=unreal.register_slate_post_tick_callback(run)
