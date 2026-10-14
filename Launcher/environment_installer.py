"""Download a signed Microsoft installer and monitor it without killing it.

The official interactive UI owns license/UAC prompts. No acceptance switches,
forced process closure, reboot, engine writes or project writes are used.
"""
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import uuid

import core

URL = 'https://aka.ms/vs/17/release/vs_BuildTools.exe'
DOCS = 'https://learn.microsoft.com/en-us/visualstudio/install/use-command-line-parameters-to-install-visual-studio?view=vs-2022'
MAX_BYTES = 16 * 1024 * 1024
COMPONENTS = {'Microsoft.VisualStudio.Component.VC.14.44.17.14.x86.x64',
              'Microsoft.VisualStudio.Component.Windows11SDK.22621',
              'Microsoft.VisualStudio.Component.Windows11SDK.26100'}


def allowed_url(url):
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or '').lower()
    return parsed.scheme == 'https' and not parsed.username and not parsed.password and (host == 'aka.ms' or host == 'microsoft.com' or host.endswith('.microsoft.com'))


class MicrosoftRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise ValueError('微软安装器下载跳转到了非官方地址，已停止。请使用官方安装器。')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(folder, progress, cancel):
    destination = Path(folder) / ('vs_BuildTools-' + uuid.uuid4().hex + '.exe')
    partial = destination.with_suffix('.part')
    opener = urllib.request.build_opener(MicrosoftRedirect())
    for attempt in range(3):
        try:
            progress(f'下载微软官方环境安装器（第 {attempt + 1}/3 次）…')
            request = urllib.request.Request(URL, headers={'User-Agent': 'VFXMeshLauncher'})
            with opener.open(request, timeout=20) as response, partial.open('wb') as output:
                if not allowed_url(response.geturl()):
                    raise ValueError('安装器来源不是微软官方地址。')
                total = 0; started = time.monotonic()
                while True:
                    if cancel.is_set():
                        raise ValueError('已取消环境下载，可稍后重试。')
                    if time.monotonic() - started > 180:
                        raise TimeoutError('下载超过 3 分钟')
                    chunk = response.read(128 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError('环境安装器大小异常，已停止。')
                    output.write(chunk)
                expected = response.headers.get('Content-Length')
                if not total or (expected is not None and total != int(expected)):
                    raise OSError('安装器下载不完整')
            partial.replace(destination)
            return destination
        except ValueError:
            partial.unlink(missing_ok=True)
            raise
        except OSError as error:
            partial.unlink(missing_ok=True)
            if attempt == 2:
                raise ValueError('微软安装器下载失败。请检查网络 / 公司代理，或通过官方安装器补齐后重试。未开始编译。\n' + str(error)) from error
    raise AssertionError('unreachable')


def verify_signature(path):
    env = dict(os.environ, VFX_ENV_INSTALLER=str(Path(path).resolve()))
    # A launcher started from PowerShell 7 can inherit incompatible module paths
    # into Windows PowerShell 5.1. Use only Windows' own signed security module.
    windows = Path(os.environ.get('SystemRoot', 'C:/Windows'))
    powershell = windows / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    env['PSModulePath'] = str(powershell.parent / 'Modules')
    script = ('$ErrorActionPreference="Stop"; '
              '$s=Microsoft.PowerShell.Security\\Get-AuthenticodeSignature -LiteralPath $env:VFX_ENV_INSTALLER; '
              '[pscustomobject]@{status=[string]$s.Status;subject=$s.SignerCertificate.Subject}|ConvertTo-Json -Compress')
    result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', script],
                            env=env, capture_output=True, timeout=45,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        signature = json.loads(result.stdout.decode('utf-8-sig'))
    except (ValueError, UnicodeError) as error:
        raise ValueError('无法验证微软安装器数字签名，未运行安装器。') from error
    if result.returncode or signature.get('status') != 'Valid' or not re.search(r'(?:^|,\s*)O=Microsoft Corporation(?=,|$)', signature.get('subject') or ''):
        raise ValueError('微软安装器数字签名无效，未运行安装器。请从官方渠道下载。')
    return core.digest(path)


def arguments(plan, update=False):
    components = plan['components']
    if not components or not set(components) <= COMPONENTS:
        raise ValueError('环境组件不在支持的自动安装清单中。')
    args = []
    if plan['mode'] == 'modify':
        target = plan['target']
        if not str(target['installationVersion']).startswith('17.'):
            raise ValueError('仅支持自动补齐 Visual Studio 2022。')
        path = Path(target['installationPath'])
        if not path.is_absolute() or not path.is_dir():
            raise ValueError('Visual Studio 安装路径已失效，请重新检测。')
        args += ['update' if update else 'modify', '--installPath', str(path), '--channelId', 'VisualStudio.17.Release']
    elif plan['mode'] != 'install':
        raise ValueError('未知环境安装操作。')
    # Interactive mode leaves all license and restart decisions to the user.
    args += ['--wait']
    if not update:
        for component in components:
            args += ['--add', component]
    return args


def kernel_api():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessId.argtypes = [wintypes.HANDLE]
    kernel.GetProcessId.restype = wintypes.DWORD
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    return kernel


def created_at(kernel, handle):
    times = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
        raise ctypes.WinError(ctypes.get_last_error())
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


def installer_active(record):
    if not record.is_file():
        return False
    data = core.read_json(record); kernel = kernel_api()
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, int(data['pid']))
    if not handle:
        # Access denied is not evidence that the installer stopped.
        if ctypes.get_last_error() == 5:
            raise ValueError('无法读取上次微软安装器状态。请关闭已完成的安装器后重试，或重启电脑。')
        return False
    try:
        return created_at(kernel, handle) == data['created'] and kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        kernel.CloseHandle(handle)


class InstallerProcess:
    def __init__(self, handle):
        self.kernel = kernel_api(); self.handle = handle
        self.pid = self.kernel.GetProcessId(handle); self.created = created_at(self.kernel, handle)

    def poll(self):
        status = self.kernel.WaitForSingleObject(self.handle, 0)
        if status == 258:
            return None
        if status != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = wintypes.DWORD()
        if not self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value

    def close(self):
        self.kernel.CloseHandle(self.handle)


def start_installer(path, args, cwd):
    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('fMask', wintypes.ULONG), ('hwnd', wintypes.HWND),
                    ('lpVerb', wintypes.LPCWSTR), ('lpFile', wintypes.LPCWSTR), ('lpParameters', wintypes.LPCWSTR),
                    ('lpDirectory', wintypes.LPCWSTR), ('nShow', ctypes.c_int), ('hInstApp', wintypes.HINSTANCE),
                    ('lpIDList', ctypes.c_void_p), ('lpClass', wintypes.LPCWSTR), ('hkeyClass', wintypes.HKEY),
                    ('dwHotKey', wintypes.DWORD), ('hIcon', wintypes.HANDLE), ('hProcess', wintypes.HANDLE)]
    info = ShellExecuteInfo(); info.cbSize = ctypes.sizeof(info); info.fMask = 0x40 | 0x100
    info.lpVerb = 'runas'; info.lpFile = str(path); info.lpParameters = subprocess.list2cmdline(args)
    info.lpDirectory = str(cwd); info.nShow = 1  # official interactive installer must be visible
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == 1223:
            raise ValueError('已取消管理员授权。未开始编译，点击重试可继续。')
        raise ctypes.WinError(error)
    if not info.hProcess:
        raise ValueError('无法跟踪微软安装器。请在官方安装器完成后重新检测。')
    return InstallerProcess(info.hProcess)


def exit_status(code):
    if code == 0:
        return 'recheck'
    if code in (3010, 1641):
        return 'restart'
    hints = {1223: '已取消管理员授权', 1602: '已取消微软环境安装',
             1618: '另一项安装尚未完成，请等它结束', 1001: 'Visual Studio Installer 正在运行，请先完成或关闭它',
             5004: '环境下载被取消或网络不可用，请检查网络', 8005: '请先重启 Windows 完成已有安装',
             740: '需要管理员权限，请允许微软安装器的系统授权'}
    raise ValueError(hints.get(code, '微软环境安装未完成') + f'（退出码 {code}）。尚未编译。处理后可重试。')


def boot_time():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetTickCount64.restype = ctypes.c_ulonglong
    return time.time() - kernel.GetTickCount64() / 1000


def check_restart(state):
    record = Path(state) / 'Environment/restart-required.json'
    if record.is_file():
        data = core.read_json(record)
        if abs(boot_time() - data['boot_time']) < 120:
            raise ValueError('环境安装已完成，但需要重启 Windows 才能继续。请保存工作并重启，再打开此启动器。')


@contextmanager
def install_lock(folder):
    import msvcrt
    with (folder / 'installer.lock').open('a+b') as stream:
        stream.write(b'0'); stream.flush(); stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise ValueError('另一个启动器正在准备环境，请等待它完成。') from error
        try:
            yield
        finally:
            stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def install(plan, state, progress, cancel, timeout=5400):
    folder = Path(state) / 'Environment'; core.ensure_regular_tree(folder); folder.mkdir(parents=True, exist_ok=True)
    with install_lock(folder):
        return _install(plan, folder, progress, cancel, timeout)


def _install(plan, folder, progress, cancel, timeout):
    record = folder / 'active-installer.json'
    if installer_active(record):
        raise ValueError('上次微软安装器还在运行，请先在安装器中完成或取消，不会重复启动安装。')
    args = arguments(plan)
    for root in {Path(os.environ.get('SystemDrive', 'C:') + '/'), folder,
                 Path(plan['target']['installationPath']) if plan.get('target') else Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)'))}:
        if shutil.disk_usage(root).free < 8 * 1024 ** 3:
            raise ValueError('环境安装前请在系统盘和安装位置至少留出 8 GB 空间；实际需求以微软安装器显示为准。')
    if cancel.is_set():
        raise ValueError('已取消环境准备。')
    path = download(folder, progress, cancel)
    progress('正在验证微软安装器数字签名…')
    digest = verify_signature(path)
    if cancel.is_set():
        raise ValueError('已取消，未启动环境安装器。')
    core.write_json(folder / 'install-plan.json', {'source': URL, 'sha256': digest, 'plan': plan})
    if plan.get('update_first'):
        progress('现有 Visual Studio 2022 较旧，先在微软窗口升级；完成后自动补齐组件…')
        status = monitor_installer(path, arguments(plan, update=True), folder, digest, progress, cancel, timeout)
        if status == 'restart':
            return status
    progress('请完成微软安装器的授权 / 安装确认；组件已选好，完成后自动检查并继续…')
    return monitor_installer(path, args, folder, digest, progress, cancel, timeout)


def monitor_installer(path, args, folder, digest, progress, cancel, timeout):
    record = folder / 'active-installer.json'
    if cancel.is_set():
        raise ValueError('已取消，未启动下一步环境安装。')
    if core.digest(path) != digest:
        raise ValueError('安装器在签名验证后发生变化，未运行。请重试下载。')
    process = start_installer(path, args, folder)
    core.write_json(record, {'pid': process.pid, 'created': process.created})
    started = time.monotonic(); last_message = started
    try:
        while True:
            code = process.poll()
            if code is not None:
                record.unlink(missing_ok=True)
                core.write_json(folder / 'last-install.json', {'code': code, 'seconds': round(time.monotonic() - started), 'sha256': digest})
                if code in (3010, 1641):
                    core.write_json(folder / 'restart-required.json', {'boot_time': boot_time(), 'code': code})
                if cancel.is_set():
                    raise ValueError('已停止自动继续。微软安装器已退出，可重新检测后再编译。')
                return exit_status(code)
            elapsed = time.monotonic() - started
            if elapsed > timeout:
                raise ValueError('微软安装器仍未退出。请查看其窗口的下载进度或确认提示；未强制终止安装。处理完后点击重试。')
            if time.monotonic() - last_message >= 10:
                progress('已请求停止自动继续；请在微软安装器中取消或完成安装。' if cancel.is_set() else
                         f'等待微软环境安装完成（{int(elapsed // 60)} 分钟）。请查看安装器进度 / 授权提示；完成后关闭安装器。')
                last_message = time.monotonic()
            time.sleep(.5)
    finally:
        process.close()  # never terminate a system installer mid-transaction
