"""Run the isolated editor suite without reentrant Slate test execution."""
import ctypes,json,time,traceback
from pathlib import Path
import unreal
from vfx_mesh import runtime,preview
root=Path(unreal.Paths.project_dir()).resolve()
assert root.name in ('vfx150-stock','vfx150-native')
tests=Path('H:/GPT/XH/output/vfx-source-repository/tests')
fn=preview.native().VFX_GlobalEnvironmentHash;fn.argtypes=[];fn.restype=ctypes.c_uint
(root/'global-environment-before.json').write_text(json.dumps({'hash':fn()}))
scripts=['ue_workspace_verify','ue_native_details_verify','ue_material_general_verify',
         'ue_environment_verify','ue_material_workflow_verify','ue_layout_verify','ue_custom_model_verify']
async_reports={'ue_layout_verify':'layout','ue_custom_model_verify':'custom-model'}
stage=0;busy=False;pending=None;pending_start=0;start=time.monotonic()

def run(_):
    global stage,busy,pending,pending_start,start
    if busy or time.monotonic()-start<2:return
    busy=True
    try:
        if pending:
            failure=root/(pending+'-failure.txt')
            if failure.exists():raise RuntimeError(failure.read_text(encoding='utf8'))
            report=root/(pending+'.json')
            if not report.exists():
                assert time.monotonic()-pending_start<90,'async test timeout: '+pending
                return
            assert json.loads(report.read_text())['passed'];pending=None
        if stage==0:
            runtime.open_panel();stage=1;return
        if 1<=stage<=len(scripts) and runtime._panel.viewer.attached:
            script=scripts[stage-1];stage+=1
            pending=async_reports.get(script);pending_start=time.monotonic()
            exec((tests/(script+'.py')).read_text(encoding='utf8'),{'__name__':'__main__'})
            return
        if stage==len(scripts)+1:
            (root/'ready.json').write_text(json.dumps({'attached':runtime._panel.viewer.attached,'suite_passed':True}));stage+=1
    except Exception:
        stage=100;pending=None;(root/'failure.txt').write_text(traceback.format_exc(),encoding='utf8')
    finally:start=time.monotonic();busy=False

handle=unreal.register_slate_post_tick_callback(run)
last=(root/'driver.py').stat().st_mtime_ns
def driver(_):
    global last,busy
    if busy:return
    path=root/'driver.py'
    if path.stat().st_mtime_ns!=last:
        last=path.stat().st_mtime_ns;busy=True
        try:exec(path.read_text(encoding='utf8'),{'__name__':'__main__'})
        except Exception:(root/'driver_error.txt').write_text(traceback.format_exc(),encoding='utf8')
        finally:busy=False
driver_handle=unreal.register_slate_post_tick_callback(driver)
