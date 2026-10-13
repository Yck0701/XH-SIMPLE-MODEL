"""Download validation, project installation, backup and fixed editor IPC."""
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import ssl
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from version import VERSION, REPOSITORY
import native_profiles

PLUGIN = 'VFXMeshGenerator'
API = 'https://api.github.com/repos/' + REPOSITORY
WEB = 'https://github.com/' + REPOSITORY
STATIC_FEED = 'https://raw.githubusercontent.com/' + REPOSITORY + '/main/updates/vfx-manifest.json'
MAX_DOWNLOAD = 250 * 1024 * 1024


def read_json(path):
    return native_profiles.read_json(path)


def write_json(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def version_tuple(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d+\.\d+\.\d+',value):
        raise ValueError('版本号格式无效。')
    return tuple(map(int,value.split('.')))


def asset_url(url):
    p=urllib.parse.urlparse(url)
    prefix='/'+REPOSITORY+'/releases/download/'
    if p.scheme!='https' or p.netloc!='github.com' or not p.path.startswith(prefix) or p.query or p.fragment:
        raise ValueError('更新文件必须来自指定 GitHub 仓库的正式发行版。')
    parts=p.path[len(prefix):].split('/')
    if len(parts)!=2 or any(not re.fullmatch(r'[A-Za-z0-9._-]+',part) or part in ('.','..') for part in parts):
        raise ValueError('更新下载地址无效。')
    return url


def network_error(error):
    """Actionable message without exposing proxy credentials or signed URLs."""
    if isinstance(error,urllib.error.HTTPError):
        if error.code in (403,429):return 'GitHub 请求受限（HTTP '+str(error.code)+'），请稍后重试。'
        if error.code==404:return '该更新地址暂时没有文件（HTTP 404）。'
        return '下载服务返回 HTTP '+str(error.code)+'。'
    reason=getattr(error,'reason',error)
    if isinstance(reason,ssl.SSLError):return 'HTTPS 证书验证失败，请检查系统时间或联系网络管理员。'
    if isinstance(reason,(TimeoutError,socket.timeout)):return '连接超时，请检查网络或代理。'
    if isinstance(reason,socket.gaierror):return '无法解析 GitHub 域名，请检查 DNS 或网络。'
    if isinstance(error,(OSError,urllib.error.URLError)):return '无法连接 GitHub，请检查网络或代理；也可使用内置离线包。'
    return '更新信息或下载校验无效，请重试或下载完整启动器包。'


def retryable(error):
    if isinstance(error,urllib.error.HTTPError):return error.code in (408,429,500,502,503,504)
    if isinstance(getattr(error,'reason',error),ssl.SSLError):return False
    return isinstance(error,(urllib.error.URLError,TimeoutError,ConnectionError,http.client.IncompleteRead))


def fetch_json(url):
    for attempt in range(2):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'VFXMeshLauncher/'+VERSION,'Accept':'application/vnd.github+json'})
            with urllib.request.urlopen(req,timeout=10) as response:
                data=response.read(1024*1024+1)
                if len(data)>1024*1024:raise ValueError('更新信息过大。')
            return json.loads(data)
        except Exception as error:
            if isinstance(error,urllib.error.HTTPError):error.close()
            if attempt or not retryable(error):raise
            time.sleep(.3)


def validate_feed(feed):
    if not isinstance(feed,dict):raise ValueError('更新信息格式无效。')
    if feed.get('schema')!=1 or feed.get('product')!=PLUGIN or feed.get('repository')!=REPOSITORY:
        raise ValueError('更新信息与此插件不匹配。')
    for key in ('plugin','launcher'):
        item=feed[key]
        if not isinstance(item,dict):raise ValueError('更新文件信息格式无效。')
        version_tuple(item['version']);asset_url(item['url'])
        if not re.fullmatch(r'[0-9a-f]{64}',item.get('sha256','')):raise ValueError('更新信息缺少文件校验。')
        if type(item.get('size')) is not int or not 0<item['size']<=MAX_DOWNLOAD:raise ValueError('更新文件大小无效。')
    if feed.get('engine')!='5.8':raise ValueError('此启动器仅接受 UE 5.8 插件更新。')
    version_tuple(feed.get('minimum_launcher','1.1.0'))
    return feed


def check_updates():
    errors=[]
    try:return _release_feed()
    except (OSError,ValueError,KeyError,TypeError) as error:
        if isinstance(error,urllib.error.HTTPError):error.close()
        errors.append(('GitHub API',network_error(error)))
    # These are first-party public URLs, not third-party proxy services. The
    # static feed is published only after the associated release is available.
    for label,url in [('仓库版本文件',STATIC_FEED),('正式发行版',WEB+'/releases/latest/download/vfx-manifest.json')]:
        try:return validate_feed(fetch_json(url))
        except (OSError,ValueError,KeyError,TypeError) as error:errors.append((label,network_error(error)))
    raise RuntimeError('自动检查更新失败。\n'+'\n'.join(label+'：'+message for label,message in errors))


def _release_feed():
    # Search only this product's releases; unrelated releases in the repo are ignored.
    releases=fetch_json(API+'/releases?per_page=30')
    if not isinstance(releases,list):raise ValueError('发行版列表格式无效。')
    candidates=[]
    for release in releases:
        if release.get('draft') or release.get('prerelease'):continue
        tag=release.get('tag_name','')
        if not re.fullmatch(r'vfx-v\d+\.\d+\.\d+',tag):continue
        assets=[a for a in release.get('assets',[]) if a.get('name')=='vfx-manifest.json']
        if assets:candidates.append((version_tuple(tag[5:]),assets[0]))
    if not candidates:raise ValueError('仓库还没有正式更新包，可先使用内置版本。')
    asset=max(candidates,key=lambda item:item[0])[1]
    feed=validate_feed(fetch_json(asset_url(asset['browser_download_url'])))
    return feed


def release_asset_api(url):
    """Resolve only the exact asset from the configured repository and tag."""
    parts=urllib.parse.urlparse(asset_url(url)).path.split('/')
    tag,name=parts[-2:]
    release=fetch_json(API+'/releases/tags/'+tag)
    if release.get('draft') or release.get('prerelease') or release.get('tag_name')!=tag:raise ValueError('发行版无效。')
    matches=[a for a in release.get('assets',[]) if a.get('name')==name and a.get('browser_download_url')==url]
    if len(matches)!=1:raise ValueError('发行版中找不到对应下载文件。')
    identity=matches[0].get('id')
    if type(identity) is not int or identity<=0:raise ValueError('下载文件标识无效。')
    return API+'/releases/assets/'+str(identity)


def download(item, directory, progress=lambda _:None):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    url=asset_url(item['url']);destination=directory/(item['sha256']+'.zip')
    if destination.is_file() and destination.stat().st_size==item['size'] and digest(destination)==item['sha256']:return destination
    temporary=directory/(uuid.uuid4().hex+'.part')
    errors=[]
    try:
        for route in ('direct','api'):
            try:
                source=url if route=='direct' else release_asset_api(url)
                for attempt in range(2):
                    try:
                        request=urllib.request.Request(source,headers={'User-Agent':'VFXMeshLauncher/'+VERSION,'Accept':'application/octet-stream'})
                        with urllib.request.urlopen(request,timeout=20) as response,temporary.open('wb') as stream:
                            count=0;checksum=hashlib.sha256();progress(0)
                            while True:
                                chunk=response.read(256*1024)
                                if not chunk:break
                                count+=len(chunk)
                                if count>item['size'] or count>MAX_DOWNLOAD:raise ValueError('更新文件大小与发布信息不一致。')
                                stream.write(chunk);checksum.update(chunk);progress(count/item['size'])
                        if count!=item['size'] or checksum.hexdigest()!=item['sha256']:
                            raise ValueError('下载校验失败，未安装。请检查网络后重试。')
                        os.replace(temporary,destination);return destination
                    except Exception as error:
                        if isinstance(error,urllib.error.HTTPError):error.close()
                        if attempt or not retryable(error):raise
                        time.sleep(.3)
            except ValueError:raise  # Never disguise corruption as a network issue.
            except (OSError,RuntimeError,http.client.IncompleteRead) as error:errors.append(network_error(error))
        raise RuntimeError('两个 GitHub 下载通道均未成功。\n'+'\n'.join(dict.fromkeys(errors))+'\n可使用“浏览器下载”或“安装内置版本”。')
    finally:temporary.unlink(missing_ok=True)


def ensure_regular_tree(root):
    root=Path(root)
    paths=[root]+list(root.rglob('*')) if root.exists() else [root]
    for p in paths:
        if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):
            raise ValueError('目标目录包含链接，请选择实际工程目录。')


def safe_extract(archive, target, expected_root):
    target=Path(target);ensure_regular_tree(target)
    with zipfile.ZipFile(archive) as z:
        members=z.infolist();names=set();total=0
        if len(members)>6000:raise ValueError('压缩包文件数量异常。')
        for entry in members:
            name=entry.filename
            if '\\' in name or name.startswith('/') or '\x00' in name:raise ValueError('压缩包路径无效。')
            if any(p in ('.','..','') for p in name.rstrip('/').split('/')):raise ValueError('压缩包路径无效。')
            parts=PurePosixPath(name).parts
            if not parts or parts[0]!=expected_root or any(part in ('.','..') or ':' in part or part.endswith((' ','.')) for part in parts):
                raise ValueError('压缩包路径超出插件目录。')
            if any(re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',part,re.I) for part in parts):
                raise ValueError('压缩包包含非法 Windows 文件名。')
            key=name.rstrip('/').casefold()
            if key in names or stat.S_ISLNK(entry.external_attr>>16):raise ValueError('压缩包包含重复路径或链接。')
            names.add(key);total+=entry.file_size
            if total>500*1024*1024:raise ValueError('解压大小超过限制。')
        for entry in members:
            destination=target.joinpath(*PurePosixPath(entry.filename).parts)
            if not destination.resolve().is_relative_to(target.resolve()):raise ValueError('解压目标路径无效。')
            if entry.is_dir():destination.mkdir(parents=True,exist_ok=True)
            else:
                destination.parent.mkdir(parents=True,exist_ok=True)
                with z.open(entry) as source,destination.open('wb') as out:shutil.copyfileobj(source,out)
    return target/expected_root


def project_info(filename):
    file=Path(filename).expanduser().resolve()
    if file.suffix.lower()!='.uproject' or not file.is_file():raise ValueError('请选择 Unreal 工程的 .uproject 文件。')
    data=read_json(file);root=file.parent
    # Do not follow a Plugins/Saved junction into a different project.
    for base in ('Plugins','Saved'):
        path=root/base
        if path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()):raise ValueError(base+' 是链接目录，请使用实际工程路径。')
    plugin=root/'Plugins'/PLUGIN
    ensure_regular_tree(plugin)
    installed=read_json(plugin/(PLUGIN+'.uplugin')).get('VersionName','') if (plugin/(PLUGIN+'.uplugin')).is_file() else ''
    disabled=[p['Name'] for p in data.get('Plugins',[]) if p.get('Enabled') is False and p.get('Name') in (PLUGIN,'PythonScriptPlugin','GeometryScripting','EditorScriptingUtilities','Niagara')]
    return {'file':file,'root':root,'plugin':plugin,'saved':root/'Saved'/PLUGIN,'version':installed,
            'association':str(data.get('EngineAssociation','')),'disabled':disabled}


def detect_engine(info):
    candidates=[info['root']/'UE/Engine/Binaries/Win64/UnrealEditor.exe',info['root']/'Engine/Binaries/Win64/UnrealEditor.exe']
    if os.name=='nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Epic Games\Unreal Engine\Builds') as key:
                location=winreg.QueryValueEx(key,info['association'])[0]
                candidates.append(Path(location)/'Engine/Binaries/Win64/UnrealEditor.exe')
        except OSError:pass
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,'SOFTWARE\\EpicGames\\Unreal Engine\\'+info['association']) as key:
                location=winreg.QueryValueEx(key,'InstalledDirectory')[0]
                candidates.append(Path(location)/'Engine/Binaries/Win64/UnrealEditor.exe')
        except OSError:pass
    return next((p.resolve() for p in candidates if p.is_file()),None)


def engine_executable(path):
    value=os.fspath(path).strip() if path is not None else ''
    if len(value)>=2 and value[0]==value[-1] and value[0] in ('"',"'"):value=value[1:-1].strip()
    if not value:raise ValueError('尚未选择引擎。请点击“选择引擎”，选择本机的 UnrealEditor.exe。')
    executable=Path(os.path.expandvars(value)).expanduser().resolve()
    if executable.is_dir():
        candidates=[executable/'Binaries/Win64/UnrealEditor.exe',executable/'Engine/Binaries/Win64/UnrealEditor.exe',executable/'UnrealEditor.exe']
        existing=[p for p in candidates if p.is_file()]
        if len(existing)==1:executable=existing[0]
        elif len(existing)>1:raise ValueError('该目录内找到多个引擎，请明确选择对应的 UnrealEditor.exe：\n'+str(executable))
    if executable.name.lower()!='unrealeditor.exe' or not executable.is_file():
        raise ValueError('所填引擎路径不存在或不是 UnrealEditor.exe：\n'+str(executable)+'\n请检查当前电脑的盘符和目录，或重新浏览选择。')
    return executable


def validate_engine(path):
    executable=engine_executable(path)
    build=read_json(executable.parents[2]/'Build/Build.version')
    if not isinstance(build,dict) or any(type(build.get(k)) is not int for k in ('MajorVersion','MinorVersion')):
        raise ValueError('引擎版本信息缺少有效的 MajorVersion / MinorVersion：\n'+str(executable.parents[2]/'Build/Build.version'))
    if (build['MajorVersion'],build['MinorVersion'])!=(5,8):raise ValueError('当前插件适用于 UE 5.8，请选择对应引擎。')
    return executable


def native_compatibility(engine_path, native_root):
    """Read-only comparison. Drive letters never participate in fingerprints."""
    root=validate_engine(engine_path).parents[2]
    return native_profiles.inspect(root,native_root,hash_file=digest)


def engine_version_label(version):
    if not version:return '未记录（仍按 DLL 指纹检测）'
    number='.'.join(str(version.get(k,'?')) for k in ('MajorVersion','MinorVersion','PatchVersion'))
    return 'UE '+number+' / 构建号 '+str(version.get('Changelist','?'))


def compatibility_message(report):
    if report['reason']=='engine_version_mismatch':
        return '引擎版本与适配组件不一致。\n当前：'+engine_version_label(report['engine_version'])+'\n组件目标：'+engine_version_label(report['expected_engine_version'])+'\n\n相同 BuildId 不代表 DLL 内容相同。请选择项目实际使用的引擎，或为当前构建增加适配。未替换插件或改动项目资产。'
    if report['reason']=='missing_adapter':
        return '此引擎尚无匹配的原生预览组件。\nBuildId：'+report['build_id']+'\n请导出诊断报告以添加适配。未替换插件或改动项目资产。'
    different=[f for f in report['files'] if f['status']!='match']
    lines=[Path(f['file']).name+('（缺失）' if f['status']=='missing' else '（内容不同）') for f in different]
    return '引擎文件与预览组件不匹配：\n'+'\n'.join(lines)+'\n\n盘符不同不会触发此检查。请确认公司引擎同步完整，或点击“导出诊断报告”定位差异。未替换插件或改动项目资产。'


def compatibility_solution(report):
    if report['compatible']:
        return '检测通过：引擎 BuildId 与所有必需 DLL 指纹匹配。可安装对应插件；首次安装或原生组件更新前，保存工作并关闭该工程。'
    if report['reason']=='engine_version_mismatch':
        return compatibility_message(report)+'\n\n解决方法：\n1. 在启动器选择项目实际使用的 UnrealEditor.exe，再重新检测。\n2. 若项目必须使用当前版本，点击“为当前引擎编译适配”。需要此引擎的完整 C++ 开发文件、MSVC 和 Windows SDK。编译后独立验证通过才能安装。\n无需因为此提示重装或认定引擎文件损坏；不要只替换单个 DLL 或修改 BuildId。'
    if report['reason']=='missing_adapter':
        return '解决方法：选择工程实际使用的 UnrealEditor.exe；若选择正确，请点击“为当前引擎编译适配”，编译并验证新组件。缺少开发文件时请按提示补齐；编译或验证失败时提供编译日志。不要修改 BuildId 或复制其他构建的预览 DLL。'
    missing=any(f['status']=='missing' for f in report['files'])
    return ('解决方法：先用公司引擎同步工具补齐报告中缺失的文件。\n' if missing else '解决方法：先确认公司引擎的同步批次，以及是否安装过修改引擎 DLL 的补丁。\n')+\
        '请将引擎可执行文件、DLL 和 .modules 文件同步到同一完整构建，再重新检测。不要仅替换单个 DLL。\n'+\
        '若必须保留当前构建，请导出诊断报告，按此构建生成并验证适配组件。重新下载插件不能解决实际引擎文件差异；盘符本身无需修改。'


def diagnostic_report(engine_path, archive):
    # No project/engine writes and no source code, assets, credentials or machine
    # paths in this report. The user chooses whether to share the resulting JSON.
    with tempfile.TemporaryDirectory(prefix='vfx-diagnostic-') as temporary:
        plugin=safe_extract(archive,temporary,PLUGIN)
        report=native_compatibility(engine_path,plugin/'Native/Win64')
        return {'schema':1,'launcher_version':VERSION,'plugin_version':read_json(plugin/(PLUGIN+'.uplugin'))['VersionName'],
                'engine':report,'solution':compatibility_solution(report)}


def running_editors(info):
    if os.name!='nt':return []
    powershell=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    code="[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); $ErrorActionPreference='Stop'; @((Get-CimInstance Win32_Process -Filter \"Name='UnrealEditor.exe' OR Name='UnrealEditor-Cmd.exe'\") | Select-Object ProcessId,CommandLine) | ConvertTo-Json -Compress"
    result=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-Command',code],capture_output=True,
        encoding='utf-8',errors='replace',timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:raise RuntimeError('无法确认 UE 是否正在运行，请关闭该工程后重试。')
    records=json.loads(result.stdout or '[]');records=records if isinstance(records,list) else [records]
    target=str(info['file']).replace('\\','/').casefold()
    matches=[]
    for record in records:
        command=record.get('CommandLine')
        if not command:raise RuntimeError('无法读取某个 UE 进程的工程路径，请关闭该实例后重试。')
        command=command.replace('\\','/').casefold()
        # Relative project launches cannot be resolved from CIM; conservatively
        # treat the same project filename as a match.
        if target in command or info['file'].name.casefold() in command:
            matches.append(int(record['ProcessId']))
        elif '.uproject' not in command:
            raise RuntimeError('有 UE 实例未提供完整工程路径，请关闭该实例后重试。')
    return matches


def heartbeat(info):
    try:
        data=read_json(info['saved']/'editor.json')
        if data.get('protocol')==1 and 0<=time.time()-data.get('time',0)<12:
            if Path(data['plugin_dir']).resolve()==info['plugin'].resolve():return data
    except (OSError,ValueError,KeyError,TypeError):pass
    return None


def bridge_request(info, action, expected_version='', timeout=15):
    state=heartbeat(info)
    if not state:raise RuntimeError('UE 热更新连接未就绪。首次安装或长时间编译后，请重启工程再试。')
    identity=uuid.uuid4().hex
    write_json(info['saved']/'request.json',{'protocol':1,'session':state['session'],'id':identity,
        'action':action,'version':expected_version,'time':time.time()})
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            result=read_json(info['saved']/'response.json')
            if result.get('id')==identity and result.get('session')==state['session']:
                if not result.get('ok'):raise RuntimeError(result.get('error','UE 未能载入更新。'))
                if action=='reload' and result.get('version')!=expected_version:raise RuntimeError('UE 载入版本与更新版本不一致。')
                return result
        except (OSError,ValueError):pass
        time.sleep(.2)
    raise TimeoutError('UE 尚未响应，可能正在编译或弹出对话框。请稍后重试，或保存工程后重启 UE。')


def inventory(root):
    return {p.relative_to(root).as_posix():digest(p) for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}


def can_hot_update(old,new):
    if not old.is_dir():return False
    before,after=inventory(old),inventory(new)
    a=read_json(old/(PLUGIN+'.uplugin'));b=read_json(new/(PLUGIN+'.uplugin'))
    for key in ('Version','VersionName','Description','IsBetaVersion'):
        a.pop(key,None);b.pop(key,None)
    if a!=b:return False
    for name in before.keys()|after.keys():
        if before.get(name)==after.get(name):continue
        if name==PLUGIN+'.uplugin' or name.endswith('.md'):continue
        if name.startswith('Content/Python/') and name.endswith('.py'):
            # The bridge keeps its own tick callback while other modules reload.
            # Bridge changes itself require a restart if it already existed.
            if name.endswith('/settings_types.py'):return False
            if name.endswith('/launcher_bridge.py') and name in before:return False
            continue
        return False
    return True


def _copy_atomic(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
    try:shutil.copyfile(source,temporary);os.replace(temporary,target)
    finally:temporary.unlink(missing_ok=True)


def _replace_scripts(source,target):
    before,after=inventory(target),inventory(source)
    for name in before.keys()-after.keys():(target/name).unlink()
    for name in after:
        if before.get(name)!=after[name]:_copy_atomic(source/name,target/name)
    for p in target.rglob('*.pyc'):p.unlink()


@contextmanager
def project_lock(info):
    ensure_regular_tree(info['saved']);info['saved'].mkdir(parents=True,exist_ok=True)
    with (info['saved']/'install.lock').open('a+b') as stream:
        if os.name=='nt':
            import msvcrt
            stream.write(b'0');stream.flush();stream.seek(0)
            try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise RuntimeError('另一个启动器正在更新此工程，请稍后再试。')
        try:yield
        finally:
            if os.name=='nt':stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)


def install(project, archive, expected, editor_probe=running_editors, ipc=bridge_request, engine_path=None):
    info=project_info(project)
    if info['disabled']:raise ValueError('工程禁用了依赖插件，请在 UE 插件设置中启用：'+ '、'.join(info['disabled']))
    if digest(archive)!=expected['sha256'] or Path(archive).stat().st_size!=expected['size']:raise ValueError('安装包校验失败。')
    with project_lock(info),tempfile.TemporaryDirectory(prefix='stage-',dir=info['saved']) as temporary:
        staged=safe_extract(archive,Path(temporary),PLUGIN)
        descriptor=read_json(staged/(PLUGIN+'.uplugin'))
        if (staged/'Native/Win64').is_dir():
            selected=engine_path or detect_engine(info)
            if not selected:raise ValueError('请选择此工程对应的 UE 引擎，以匹配原生预览组件。')
            report=native_compatibility(selected,staged/'Native/Win64')
            if not report['compatible']:raise ValueError(compatibility_message(report))
        if descriptor.get('VersionName')!=expected['version']:raise ValueError('安装包版本与更新信息不一致。')
        if info['version'] and version_tuple(expected['version'])<version_tuple(info['version']):raise ValueError('不会将较新插件覆盖为旧版本。恢复旧版请使用备份功能。')
        editors=editor_probe(info)
        if len(editors)>1:raise ValueError('此工程打开了多个 UE 实例，请先关闭多余实例。')
        hot=can_hot_update(info['plugin'],staged)
        if editors and not hot:raise ValueError('此次包含面板、原生组件或资源更新，请保存并关闭此工程，再点击安装 / 更新。')
        state=heartbeat(info)
        if state and state.get('pid') not in editors:state=None
        backup=None
        if info['plugin'].exists():
            backup=info['saved']/'backups'/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])/PLUGIN
            backup.parent.mkdir(parents=True,exist_ok=True)
            shutil.copytree(info['plugin'],backup,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        try:
            if editors:
                _replace_scripts(staged,info['plugin'])
            else:
                # Stage on the same volume, keep a full backup, and swap directories.
                info['plugin'].parent.mkdir(parents=True,exist_ok=True)
                parked=Path(temporary)/'previous'
                if info['plugin'].exists():info['plugin'].rename(parked)
                try:staged.rename(info['plugin'])
                except Exception:
                    if parked.exists():parked.rename(info['plugin'])
                    raise
            mode='installed'
            if editors and state:
                ipc(info,'reload',expected['version']);mode='hot_reloaded'
            elif editors:mode='restart_required'
        except Exception:
            if backup:
                if editors:_replace_scripts(backup,info['plugin'])
                else:
                    if info['plugin'].exists():
                        failed=Path(temporary)/'failed';info['plugin'].rename(failed)
                    shutil.copytree(backup,info['plugin'])
            if editors and state:
                try:ipc(info,'reload',info['version'],timeout=8)
                except Exception:pass
            raise
        write_json(info['saved']/'last-install.json',{'version':expected['version'],'backup':str(backup) if backup else '',
            'time':time.time(),'mode':mode})
        return {'mode':mode,'version':expected['version'],'backup':str(backup) if backup else ''}


def restore_backup(project, editor_probe=running_editors):
    info=project_info(project)
    with project_lock(info):
        if editor_probe(info):raise ValueError('恢复旧版前，请保存并关闭此工程。')
        record=read_json(info['saved']/'last-install.json');backup=Path(record.get('backup','')).resolve()
        if not backup.is_relative_to((info['saved']/'backups').resolve()) or not (backup/(PLUGIN+'.uplugin')).is_file():
            raise ValueError('没有可恢复的旧版本备份。')
        ensure_regular_tree(backup)
        displaced=info['saved']/'backups'/('before-restore-'+uuid.uuid4().hex)/PLUGIN
        displaced.parent.mkdir(parents=True)
        if info['plugin'].exists():info['plugin'].rename(displaced)
        try:shutil.copytree(backup,info['plugin'])
        except Exception:
            if info['plugin'].exists():shutil.rmtree(info['plugin'])
            if displaced.exists():displaced.rename(info['plugin'])
            raise
        version=read_json(info['plugin']/(PLUGIN+'.uplugin'))['VersionName']
        write_json(info['saved']/'last-install.json',{'version':version,'backup':str(displaced),'time':time.time(),'mode':'restored'})
        return version


def launch(project,engine):
    info=project_info(project)
    if not info['version']:raise ValueError('请先安装插件。')
    if running_editors(info):
        bridge_request(info,'open');return 'opened'
    executable=validate_engine(engine)
    subprocess.Popen([str(executable),str(info['file']),'-VFXMeshOpen'],cwd=info['root'])
    return 'launched'
