"""Read UE's SDK policy; validate usable tools rather than registry presence.

Only Microsoft C++/Windows SDK components are repairable. Engine files and
global BuildConfiguration.xml are never changed by this module.
"""
import ctypes
import os
from pathlib import Path
import re
import subprocess
import tempfile

import core


class EnvironmentRequired(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__('\n'.join(report['problems']))


def version(value):
    if not re.fullmatch(r'\d+(?:\.\d+){1,3}', str(value)):
        raise ValueError('无法识别开发组件版本：' + str(value))
    return tuple(map(int, str(value).split('.'))) + (0,) * (4 - len(str(value).split('.')))


def supported_msvc(value, policy):
    current = version(value)
    if current < version(policy['MinimumVisualCppVersion']):
        return False
    for interval in policy.get('BannedVisualCppVersions', []):
        low, high = interval.split('-', 1)
        if version(low) <= current <= version(high):
            return False
    return True


def read_policy(engine):
    path = Path(engine) / 'Config/Windows/Windows_SDK.json'
    if not path.is_file():
        raise ValueError('引擎缺少 Windows_SDK.json 版本要求。请补齐同构建开发包，不能猜测编译器版本。')
    policy = core.read_json(path)
    for key in ('MinimumVisualCppVersion', 'MinVersion', 'MaxVersion'):
        version(policy[key])
    return policy


def compiler_version(path, family):
    """Servicing updates replace cl.exe without renaming the family directory."""
    if os.name != 'nt':
        return family
    api = ctypes.windll.version
    handle = ctypes.c_ulong()
    size = api.GetFileVersionInfoSizeW(str(path), ctypes.byref(handle))
    if not size:
        raise ValueError('无法读取编译器版本：' + str(path))
    data = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(str(path), 0, size, data):
        raise ValueError('无法验证编译器：' + str(path))
    pointer = ctypes.c_void_p(); length = ctypes.c_uint()
    if not api.VerQueryValueW(data, '\\', ctypes.byref(pointer), ctypes.byref(length)) or length.value < 52:
        raise ValueError('编译器版本信息不完整：' + str(path))
    fixed = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
    return f'{(fixed[2] >> 16) - 5}.{fixed[2] & 65535}.{fixed[3] >> 16}'


def vs_instances():
    where = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft Visual Studio/Installer/vswhere.exe'
    if not where.is_file():
        return []
    result = subprocess.run([str(where), '-all', '-products', '*', '-format', 'json', '-utf8'],
                            capture_output=True, timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('Visual Studio 安装记录读取失败，请打开微软安装器修复后重试。')
    import json
    return json.loads(result.stdout.decode('utf-8-sig'))


def toolchains(policy, instances):
    found = []
    for instance in instances:
        if not instance.get('isComplete', False) or not instance.get('isLaunchable', False):
            continue
        major = str(instance.get('installationVersion', '')).split('.')[0]
        if major not in ('17', '18'):
            continue
        root = Path(instance['installationPath'])
        for tools in (root / 'VC/Tools/MSVC').glob('*'):
            files = ('bin/Hostx64/x64/cl.exe', 'bin/Hostx64/x64/link.exe', 'include/vector',
                     'lib/x64/msvcrt.lib', 'lib/x64/msvcprt.lib')
            if not all((tools / p).is_file() for p in files):
                continue
            try:
                actual = compiler_version(tools / files[0], tools.name)
                if not supported_msvc(actual, policy):
                    continue
                preferred = any(version(r.split('-')[0]) <= version(tools.name) <= version(r.split('-')[1])
                                for r in policy.get('PreferredVisualCppVersions', []))
                found.append({'root': str(tools), 'version': actual, 'family': tools.name,
                              'compiler': 'VisualStudio2022' if major == '17' else 'VisualStudio2026',
                              'preferred': preferred, 'instance': instance})
            except ValueError:
                continue
    return sorted(found, key=lambda t: (t['preferred'], version(t['version'])), reverse=True)


def sdk_roots():
    roots = [Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Windows Kits/10']
    if os.environ.get('WindowsSdkDir'):
        roots.append(Path(os.environ['WindowsSdkDir']))
    if os.name == 'nt':
        import winreg
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Windows Kits\Installed Roots', 0, winreg.KEY_READ | view) as key:
                    roots.append(Path(winreg.QueryValueEx(key, 'KitsRoot10')[0]))
            except OSError:
                pass
    return list(dict.fromkeys(roots))


def sdks(policy, roots=None):
    found = []
    for root in sdk_roots() if roots is None else roots:
        root = Path(root)
        for include in (root / 'Include').glob('10.*'):
            v = include.name
            try:
                if not version(policy['MinVersion']) <= version(v) <= version(policy['MaxVersion']):
                    continue
            except ValueError:
                continue
            required = (include / 'um/Windows.h', include / 'ucrt/stdio.h', include / 'shared/sdkddkver.h',
                        root / f'Lib/{v}/um/x64/kernel32.lib', root / f'Lib/{v}/ucrt/x64/ucrt.lib',
                        root / f'bin/{v}/x64/rc.exe')
            if all(p.is_file() for p in required):
                found.append({'root': str(root), 'version': v})
    return sorted(found, key=lambda s: (s['version'] == policy.get('MainVersion'), version(s['version'])), reverse=True)


def repair_plan(policy, missing, instances):
    # Fixed, documented component IDs. Never execute arbitrary engine-supplied IDs.
    components = []
    if 'msvc' in missing:
        family = '14.44.35211'
        if not supported_msvc(family, policy):
            return None
        components.append('Microsoft.VisualStudio.Component.VC.14.44.17.14.x86.x64')
    if 'sdk' in missing:
        candidates = [('10.0.22621.0', 'Microsoft.VisualStudio.Component.Windows11SDK.22621'),
                      ('10.0.26100.0', 'Microsoft.VisualStudio.Component.Windows11SDK.26100')]
        candidates.sort(key=lambda c: c[0] != policy.get('MainVersion'))
        compatible = [c for c in candidates if version(policy['MinVersion']) <= version(c[0]) <= version(policy['MaxVersion'])]
        if not compatible:
            return None
        components.append(compatible[0][1])
    if not components:
        return None
    # Prefer a complete 2022 instance; do not modify arbitrary VS major versions.
    existing = [i for i in instances if str(i.get('installationVersion', '')).startswith('17.') and i.get('isComplete') and i.get('isLaunchable')]
    existing.sort(key=lambda i: i.get('productId') != 'Microsoft.VisualStudio.Product.BuildTools')
    target = existing[0] if existing else None
    if target and target.get('channelId', 'VisualStudio.17.Release') != 'VisualStudio.17.Release':
        # Do not silently switch a company-managed LTSC / layout channel.
        return None
    return {'components': components, 'target': target, 'mode': 'modify' if target else 'install',
            'update_first': bool(target and version(target['installationVersion']) < version('17.14'))}


def inspect(engine):
    policy = read_policy(engine); instances = vs_instances()
    vc = toolchains(policy, instances); sdk = sdks(policy)
    missing = []; problems = []
    if not vc:
        missing.append('msvc'); problems.append('缺少当前 UE 接受的完整 MSVC x64 编译器（已排除 UE 禁用版本和损坏组件）。')
    if not sdk:
        missing.append('sdk'); problems.append('缺少当前 UE 接受的完整 Windows SDK（头文件、x64 库和资源编译器）。')
    if any(i.get('isRebootRequired') for i in instances):
        missing.append('restart'); problems.append('微软安装器报告需要重启 Windows。请保存工作并重启后，再检测与编译。')
    return {'ready': not missing, 'missing': missing, 'problems': problems, 'policy': policy,
            'toolchain': vc[0] if vc else None, 'sdk': sdk[0] if sdk else None,
            'repair': None if 'restart' in missing else repair_plan(policy, missing, instances)}


def ubt_arguments(report):
    return ['-Compiler=' + report['toolchain']['compiler'], '-CompilerVersion=' + report['toolchain']['version'],
            '-WindowsSDKVersion=' + report['sdk']['version']]


def probe(report, work):
    """Compile and link a Windows C++ program in our cache; never run its EXE."""
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    tools = Path(report['toolchain']['root']); sdk = Path(report['sdk']['root']); v = report['sdk']['version']
    with tempfile.TemporaryDirectory(prefix='toolchain-', dir=work) as temp:
        folder = Path(temp); src = folder / 'probe.cpp'
        src.write_text('#include <windows.h>\n#include <vector>\nint main(){std::vector<int> v{1};return int(GetCurrentProcessId())+v[0];}\n', encoding='utf-8')
        command = [str(tools / 'bin/Hostx64/x64/cl.exe'), '/nologo', '/EHsc', '/MD', '/std:c++20',
                   '/I' + str(tools / 'include')]
        command += ['/I' + str(sdk / f'Include/{v}/{name}') for name in ('ucrt', 'shared', 'um')]
        command += [str(src), '/Fo' + str(folder / 'probe.obj'), '/Fe' + str(folder / 'probe.exe'), '/link',
                    '/LIBPATH:' + str(tools / 'lib/x64')]
        command += ['/LIBPATH:' + str(sdk / f'Lib/{v}/{name}/x64') for name in ('um', 'ucrt')]
        env = dict(os.environ)
        for name in ('CL', '_CL_', 'LINK', '_LINK_', 'INCLUDE', 'LIB', 'LIBPATH'):
            env.pop(name, None)
        env['PATH'] = str(tools / 'bin/Hostx64/x64') + os.pathsep + env.get('PATH', '')
        try:
            result = subprocess.run(command, cwd=folder, env=env, capture_output=True, timeout=60,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as error:
            raise ValueError('编译环境试编译超过 60 秒。请检查防病毒拦截或磁盘状态后重试。') from error
        (work / 'environment-probe.log').write_bytes(result.stdout + result.stderr)
        if result.returncode or not (folder / 'probe.exe').is_file():
            raise ValueError('C++ 环境试编译未通过，请用 Visual Studio Installer 修复 C++ 工具和 SDK。日志：' + str(work / 'environment-probe.log'))
    return True
