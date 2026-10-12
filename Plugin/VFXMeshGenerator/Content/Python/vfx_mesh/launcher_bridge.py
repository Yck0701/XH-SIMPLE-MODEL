"""Fixed local open/reload protocol. No arbitrary commands or network listener."""
import importlib
import json
import os
from pathlib import Path
import time
import uuid
import unreal

PROTOCOL = 1
_handle = None
_last_tick = 0.
_session = uuid.uuid4().hex
_seen = set()
_open_on_start = False
_recovery_panel = None
_root = Path(__file__).resolve().parents[3]
_directory = Path(unreal.Paths.project_saved_dir()).resolve() / 'VFXMeshGenerator'


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp-' + str(os.getpid()))
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, path)


def _version():
    return json.loads((_root/'VFXMeshGenerator.uplugin').read_text(encoding='utf-8-sig'))['VersionName']


_loaded_version = _version()


def _reload():
    global _loaded_version, _recovery_panel
    from . import runtime, geometry, engine, preview, material_sidebar
    old = _recovery_panel or runtime._panel
    if old and hasattr(old,'material_sidebar'):old.material_sidebar.flush()
    _recovery_panel = old
    # Rebind to the existing widget; preserve the in-memory mesh and unsaved edits.
    if runtime._watch is not None:
        unreal.unregister_slate_post_tick_callback(runtime._watch)
        runtime._watch = None
    importlib.invalidate_caches()
    for module in (geometry, engine, preview, material_sidebar, runtime):
        importlib.reload(module)
    runtime.startup()
    if old:
        panel = runtime.open_panel()
        runtime._restore(panel, old)
        panel.status.set_text('插件已热更新，当前模型与未保存参数已保留。')
    _loaded_version = _version()
    _recovery_panel = None


def _tick(_):
    global _last_tick, _open_on_start
    if time.monotonic() - _last_tick < .75: return
    _last_tick = time.monotonic()
    try:
        if _open_on_start:
            _open_on_start = False
            from .runtime import open_panel
            open_panel()
        _write(_directory/'editor.json', {'protocol':PROTOCOL, 'pid':os.getpid(),
            'session':_session, 'time':time.time(), 'version':_loaded_version,
            'plugin_dir':str(_root)})
        request_file = _directory/'request.json'
        if not request_file.is_file() or request_file.stat().st_size > 4096: return
        packet = json.loads(request_file.read_text(encoding='utf-8'))
        identity = packet.get('id','')
        if packet.get('session') != _session or identity in _seen or not identity: return
        if packet.get('protocol') != PROTOCOL or abs(time.time()-packet.get('time',0)) > 60: return
        _seen.add(identity)
        if len(_seen) > 100: _seen.clear(); _seen.add(identity)
        result = {'id':identity, 'session':_session, 'time':time.time(), 'ok':False}
        try:
            if packet.get('action') == 'open':
                from .runtime import open_panel
                open_panel()
            elif packet.get('action') == 'reload':
                if packet.get('version') != _version(): raise ValueError('版本不一致，请重新检查更新。')
                _reload()
            else: raise ValueError('不支持的启动器操作。')
            result.update(ok=True, version=_loaded_version)
        except Exception as error:
            result['error'] = str(error)
            unreal.log_warning('[VFX Launcher] ' + str(error))
        _write(_directory/'response.json', result)
    except (OSError, ValueError, TypeError):
        pass  # A concurrently replaced file is retried next tick.


def startup():
    global _handle, _open_on_start
    if _handle is None:
        _open_on_start = '-VFXMeshOpen' in unreal.SystemLibrary.get_command_line()
        _handle = unreal.register_slate_post_tick_callback(_tick)
