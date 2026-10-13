"""Download validation, project installation, backup and fixed editor IPC."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
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

PLUGIN = 'VFXMeshGenerator'
API = 'https://api.github.com/repos/' + REPOSITORY
WEB = 'https://github.com/' + REPOSITORY
MAX_DOWNLOAD = 250 * 1024 * 1024


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


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


def fetch_json(url):
    req=urllib.request.Request(url,headers={'User-Agent':'VFXMeshLauncher/'+VERSION,'Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(req,timeout=18) as response:
        data=response.read(1024*1024+1)
        if len(data)>1024*1024:raise ValueError('更新信息过大。')
    return json.loads(data)


def validate_feed(feed):
    if feed.get('schema')!=1 or feed.get('product')!=PLUGIN or feed.get('repository')!=REPOSITORY:
        raise ValueError('更新信息与此插件不匹配。')
    for key in ('plugin','launcher'):
        item=feed[key];version_tuple(item['version']);asset_url(item['url'])
        if not re.fullmatch(r'[0-9a-f]{64}',item.get('sha256','')):raise ValueError('更新信息缺少文件校验。')
        if type(item.get('size')) is not int or not 0<item['size']<=MAX_DOWNLOAD:raise ValueError('更新文件大小无效。')
    if feed.get('engine')!='5.8':raise ValueError('此启动器仅接受 UE 5.8 插件更新。')
    version_tuple(feed.get('minimum_launcher','1.1.0'))
    return feed


def check_updates():
    # Search only this product's releases; unrelated releases in the repo are ignored.
    releases=fetch_json(API+'/releases?per_page=30')
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


def download(item, directory, progress=lambda _:None):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    url=asset_url(item['url']);destination=directory/(item['sha256']+'.zip')
    if destination.is_file() and digest(destination)==item['sha256']:return destination
    temporary=directory/(uuid.uuid4().hex+'.part')
    try:
        request=urllib.request.Request(url,headers={'User-Agent':'VFXMeshLauncher/'+VERSION})
        with urllib.request.urlopen(request,timeout=25) as response,temporary.open('wb') as stream:
            count=0;checksum=hashlib.sha256()
            while True:
                chunk=response.read(256*1024)
                if not chunk:break
                count+=len(chunk)
                if count>item['size'] or count>MAX_DOWNLOAD:raise ValueError('更新文件大小与发布信息不一致。')
                stream.write(chunk);checksum.update(chunk);progress(count/item['size'])
        if count!=item['size'] or checksum.hexdigest()!=item['sha256']:
            raise ValueError('下载校验失败，未安装。请检查网络后重试。')
        os.replace(temporary,destination);return destination
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


def validate_engine(path):
    executable=Path(path).resolve()
    if executable.name.lower()!='unrealeditor.exe' or not executable.is_file():raise ValueError('请选择引擎的 UnrealEditor.exe。')
    build=read_json(executable.parents[2]/'Build/Build.version')
    if (build['MajorVersion'],build['MinorVersion'])!=(5,8):raise ValueError('当前插件适用于 UE 5.8，请选择对应引擎。')
    return executable


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
            root=validate_engine(selected).parents[2]
            build=read_json(root/'Binaries/Win64/UnrealEditor.modules')['BuildId']
            manifest=staged/'Native/Win64'/build/'manifest.json'
            if not manifest.is_file():raise ValueError('此引擎尚无匹配的原生预览组件，需要为当前引擎构建适配包。未更改工程。')
            for name,checksum in read_json(manifest)['engine_sha256'].items():
                if digest(root/name)!=checksum:raise ValueError('引擎文件与预览组件不匹配，未更改工程。')
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
