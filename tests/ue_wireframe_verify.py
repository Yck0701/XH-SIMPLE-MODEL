"""Observe actual rendered overlay segments over several frames in isolated editors."""
import ctypes,hashlib,json,time,traceback
from pathlib import Path
import unreal
from vfx_mesh import runtime,preview
root=Path(unreal.Paths.project_dir()).resolve()
assert root.name in ('vfx140-stock','vfx140-native')
p=runtime._panel
fn=preview.native().VFX_WireInfo
fn.argtypes=[ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_double)];fn.restype=ctypes.c_int
def stats():
    data=(ctypes.c_double*4)();assert fn(p.viewer.path,data)
    return list(data)
def files():
    return {f.relative_to(root).as_posix():hashlib.sha256(f.read_bytes()).hexdigest()
            for base in ('Content','Config') for f in (root/base).rglob('*') if f.is_file()}
before=files();stage=0;samples=[];start=time.monotonic();deadline=start+45
p.viewer.wireframe=True;p.viewer.turntable=False;p.viewer.tick(.016)
dynamic=p.viewer.dynamic
def observe(_):
    global stage,start
    try:
        data=stats()
        if time.monotonic()-start<.5:return
        start=time.monotonic()
        if stage<4:
            if data[3]==0 or (samples and data[2]<=samples[-1][2]):
                assert start<deadline,('viewport did not render',data)
                return
            assert data[0]>0 and data[1]==1 and data[3]>0,data
            if samples:assert data[2]>samples[-1][2],(samples[-1],data)
            samples.append(data);stage+=1
            if stage==4:p.viewer.wireframe=False;p.viewer.tick(.016)
            return
        if stage==4:
            if data[3]!=0:
                assert start<deadline,('viewport did not turn off',data)
                return
            assert data[1]==0 and data[3]==0,data
            p.viewer.wireframe=True;p.viewer.tick(.016);stage=5;return
        if stage==6:
            if data[3]!=0:
                assert start<deadline,('zero opacity still drawing',data)
                return
            assert data[1]==1,'zero opacity changed checkbox'
            p.change('preview','wire_opacity',35);p.viewer.tick(.016)
            p.controls[('preview','wire_opacity')].set_value(35);stage=7;return
        if data[3]==0:
            assert start<deadline,('viewport did not turn on',data)
            return
        assert data[1]==1 and data[3]>0,data
        if stage==5:
            p.change('preview','wire_opacity',0);p.viewer.tick(.016);stage=6;return
        if stage==7:
            assert p.viewer.dynamic==dynamic,'opacity rebuilt the mesh'
        assert files()==before
        (root/'wireframe.json').write_text(json.dumps({'passed':True,'frames':samples,
            'toggle_off_on':True,'opacity_zero_hidden':True,'opacity_restored':35,'no_mesh_rebuild':True,'asset_writes':0,
            'native_sha256':hashlib.sha256(Path(preview.native()._name).read_bytes()).hexdigest()},indent=2))
        unreal.unregister_slate_post_tick_callback(handle)
    except Exception:
        (root/'wireframe-failure.txt').write_text(traceback.format_exc())
        unreal.unregister_slate_post_tick_callback(handle)
handle=unreal.register_slate_post_tick_callback(observe)
