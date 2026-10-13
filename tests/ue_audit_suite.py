"""Sequential multi-round audit; Slate save dialogs must not reenter the runner."""
import ctypes,hashlib,json,time,traceback
from pathlib import Path
import unreal
from vfx_mesh import runtime,preview

root=Path(unreal.Paths.project_dir()).resolve()
assert root.name in ('vfx150-stock','vfx150-native')
repo=Path('H:/GPT/XH/output/vfx-source-repository')
config=json.loads((root/'audit-config.json').read_text());mode=config['mode'];rounds=config['rounds']
scripts=[('ue_stability_verify','stability')]
if mode!='baseline':
    scripts=[('ue_workspace_verify','save-isolation'),('ue_native_details_verify','native-details'),
             ('ue_material_general_verify','material-general'),('ue_environment_verify','environment'),
             ('ue_material_workflow_verify','material-workflow'),('ue_layout_verify','layout'),
             ('ue_custom_model_verify','custom-model')]+scripts+[('ue_lifecycle_verify','lifecycle')]
start=time.monotonic();stage=-1;cycle=1;pending=None;pending_start=0;busy=False
output=root/('audit-'+mode);output.mkdir(exist_ok=True)
def protected_files():
    paths=list(root.glob('*.uproject'))
    for folder in ('Config','Source','Plugins'):
        paths.extend(p for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
    bystander=Path('H:/GPT/XH/tests/vfx152-bystander')
    paths.extend(p for p in bystander.rglob('*') if p.is_file())
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
protected_before=protected_files()
def tick(_):
    global start,stage,cycle,pending,pending_start,busy
    if busy or time.monotonic()-start<1:return
    busy=True
    try:
        if stage==-1:runtime.open_panel();stage=0;return
        if not runtime._panel.viewer.attached:return
        if pending:
            failure=root/(pending+'-failure.txt');report=root/(pending+'.json')
            if failure.exists():raise RuntimeError(failure.read_text(encoding='utf8'))
            if not report.exists():
                assert time.monotonic()-pending_start<180,'Timed out: '+pending
                return
            data=json.loads(report.read_text(encoding='utf8'));assert data['passed'],data
            assert protected_files()==protected_before,'Unrelated project/config/plugin/source files changed'
            (output/('round-'+str(cycle)+'-'+pending+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
            pending=None
        if stage==len(scripts):
            if cycle==rounds:
                (output/'complete.json').write_text(json.dumps({'passed':True,'rounds':rounds,'checks_per_round':len(scripts),
                    'protected_files':len(protected_before),'protected_hashes_unchanged':True}))
                unreal.unregister_slate_post_tick_callback(handle);return
            cycle+=1;stage=0
        name,pending=scripts[stage];stage+=1;pending_start=time.monotonic()
        for suffix in ('.json','-failure.txt'):
            path=root/(pending+suffix)
            if path.exists():path.rename(output/(path.name+'.previous-'+str(time.time_ns())))
        exec((repo/'tests'/(name+'.py')).read_text(encoding='utf8'),{'__name__':'__main__'})
    except Exception:
        (output/'failure.txt').write_text(traceback.format_exc(),encoding='utf8')
        unreal.unregister_slate_post_tick_callback(handle)
    finally:start=time.monotonic();busy=False
handle=unreal.register_slate_post_tick_callback(tick)
last=(root/'driver.py').stat().st_mtime_ns
def driver(_):
    global last,busy
    if busy:return
    path=root/'driver.py'
    if path.stat().st_mtime_ns==last:return
    last=path.stat().st_mtime_ns;busy=True
    try:exec(path.read_text(encoding='utf8'),{'__name__':'__main__'})
    except Exception:(output/'driver-failure.txt').write_text(traceback.format_exc(),encoding='utf8')
    finally:busy=False
driver_handle=unreal.register_slate_post_tick_callback(driver)
