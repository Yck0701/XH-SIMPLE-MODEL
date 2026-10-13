"""Repeated close/reopen and deferred launcher request without asset writes."""
import gc,hashlib,json,time,traceback,uuid,weakref
from pathlib import Path
import unreal
from vfx_mesh import runtime,launcher_bridge
root=Path(unreal.Paths.project_dir()).resolve()
assert root.name in ('vfx150-stock','vfx150-native')
def files():
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for base in ('Content','Config') for p in (root/base).rglob('*') if p.is_file()}
before=files();model=runtime._panel.mesh;settings=dict(runtime._panel.settings)
subsystem=unreal.get_editor_subsystem(unreal.EditorUtilitySubsystem)
old_refs=[];stage=0;start=time.monotonic();busy=False
def tick(_):
    global stage,start,busy
    if busy or time.monotonic()-start<.8:return
    busy=True
    try:
        if stage<12:
            if stage%2==0:
                old_refs.append(weakref.ref(runtime._panel))
                tab_id=subsystem.get_tab_id_from_blueprint(runtime._blueprint)
                assert subsystem.close_tab_by_id(tab_id),str(tab_id)
            else:
                assert not runtime._tab_open
                runtime.open_panel()
            stage+=1;return
        unreal.SystemLibrary.collect_garbage();gc.collect()
        if stage<14:stage+=1;return
        assert runtime._panel.mesh==model and runtime._panel.settings==settings
        assert not any(r() is not None for r in old_refs)
        identity=uuid.uuid4().hex
        launcher_bridge._write(root/'Saved/VFXMeshGenerator/request.json',{
            'protocol':1,'id':identity,'session':launcher_bridge._session,
            'time':time.time(),'action':'reload','version':launcher_bridge._version()})
        runtime._panel.busy=True
        try:
            launcher_bridge._last_tick=0;launcher_bridge._tick(0)
            assert identity not in launcher_bridge._seen
        finally:runtime._panel.busy=False
        launcher_bridge._last_tick=0;launcher_bridge._tick(0)
        response=json.loads((root/'Saved/VFXMeshGenerator/response.json').read_text())
        assert response['id']==identity and response['ok']
        assert runtime._panel.mesh==model and runtime._panel.settings==settings
        assert files()==before
        (root/'lifecycle.json').write_text(json.dumps({'passed':True,'close_reopen_cycles':6,
            'old_panels_collected':True,'reload_deferred_while_busy':True,'parameters_retained':True,'asset_writes':0}))
        unreal.unregister_slate_post_tick_callback(handle)
    except Exception:
        (root/'lifecycle-failure.txt').write_text(traceback.format_exc(),encoding='utf8')
        unreal.unregister_slate_post_tick_callback(handle)
    finally:busy=False;start=time.monotonic()
handle=unreal.register_slate_post_tick_callback(tick)
