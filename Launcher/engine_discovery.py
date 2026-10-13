"""Bounded, read-only UE discovery. Never register engines or edit projects."""
import json
import os
from pathlib import Path
import re
import subprocess
from itertools import islice

import native_profiles


def path_key(path):
    return os.path.normcase(str(Path(str(path).strip().strip('\"')).expanduser().resolve())).casefold() if path else ''


def association_key(value):
    return str(value).strip().strip('{}').casefold()


def registry_engines():
    records=[]
    if os.name!='nt':return records
    import winreg
    for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Epic Games\Unreal Engine\Builds',0,winreg.KEY_READ|view) as key:
                for i in range(winreg.QueryInfoKey(key)[1]):
                    name,location,_=winreg.EnumValue(key,i)
                    if isinstance(location,str):records.append((name,location,'注册的引擎关联'))
        except OSError:pass
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\EpicGames\Unreal Engine',0,winreg.KEY_READ|view) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    name=winreg.EnumKey(key,i)
                    try:
                        with winreg.OpenKey(key,name) as child:location=winreg.QueryValueEx(child,'InstalledDirectory')[0]
                        if isinstance(location,str):records.append((name,location,'已安装引擎注册表'))
                    except OSError:pass
        except OSError:pass
    return records


def launcher_engines():
    path=Path(os.environ.get('PROGRAMDATA','C:/ProgramData'))/'Epic/UnrealEngineLauncher/LauncherInstalled.dat'
    try:
        entries=json.loads(path.read_text(encoding='utf-8-sig')).get('InstallationList',[])
        return [(str(e.get('AppName',''))[3:],e['InstallLocation'],'Epic 安装记录') for e in entries
                if isinstance(e,dict) and str(e.get('AppName','')).startswith('UE_') and isinstance(e.get('InstallLocation'),str)]
    except (OSError,ValueError,TypeError,AttributeError):return []


def running_engine(project):
    """Only a command line containing this exact absolute project is evidence."""
    if os.name!='nt' or not project:return []
    command="Get-CimInstance Win32_Process -Filter \"Name = 'UnrealEditor.exe'\" | Select-Object ExecutablePath,CommandLine | ConvertTo-Json -Compress"
    try:
        result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',command],capture_output=True,
                              encoding='utf-8',errors='replace',timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode or not result.stdout.strip():return []
        data=json.loads(result.stdout);data=data if isinstance(data,list) else [data]
        target=str(Path(project).resolve()).replace('\\','/').casefold()
        found=[]
        for record in data:
            tokens=re.findall(r'"([^"\r\n]+)"|([^\s"]+)',record.get('CommandLine') or '')
            if any((a or b).replace('\\','/').casefold()==target for a,b in tokens) and record.get('ExecutablePath'):
                found.append(record['ExecutablePath'])
        return found
    except (OSError,ValueError,subprocess.TimeoutExpired):return []


def default_roots():
    roots=[Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Epic Games']
    # Fixed drive roots only; no recursive disk search or network drive scan.
    if os.name=='nt':
        import ctypes
        mask=ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            root=Path(chr(65+i)+':/')
            if mask & (1<<i) and ctypes.windll.kernel32.GetDriveTypeW(str(root))==3:
                roots.extend((root,root/'Epic Games',root/'Program Files/Epic Games',root/'UnrealEngine'))
    return roots


def executables(location):
    value=os.path.expandvars(str(location).strip().strip('\"'))
    base=Path(value).expanduser()
    options=[base] if base.name.lower()=='unrealeditor.exe' else [base/'Engine/Binaries/Win64/UnrealEditor.exe',base/'Binaries/Win64/UnrealEditor.exe',base/'UnrealEditor.exe']
    return [p.resolve() for p in options if p.is_file()]


def discover(info=None,remembered='',known=(),roots=None,records=None,running=None):
    association=association_key(info.get('association','')) if info else ''
    numeric=bool(re.fullmatch(r'\d+\.\d+(?:\.\d+)?',association))
    candidates={};warnings=[]
    def add(location,source,priority=10,exact=False):
        try:
            for exe in executables(location):
                key=path_key(exe)
                if key in candidates:
                    entry=candidates[key]
                    if source not in entry['sources']:entry['sources'].append(source)
                    matches=numeric and (entry['version']==association or entry['version'].startswith(association+'.'))
                    if matches and priority<50:priority=50
                    if numeric and not matches and priority<95:priority=10;exact=False
                    if priority>entry['priority']:entry.update(priority=priority,exact=exact)
                    continue
                version=native_profiles.read_json(exe.parents[2]/'Build/Build.version')
                if not isinstance(version,dict) or any(type(version.get(k)) is not int for k in ('MajorVersion','MinorVersion')):continue
                number='.'.join(str(version.get(k,0)) for k in ('MajorVersion','MinorVersion','PatchVersion'))
                matches=numeric and (number==association or number.startswith(association+'.'))
                if matches and priority<50:priority=50
                # A neighbouring 5.7 must not silently override a project's explicit 5.8 association.
                if numeric and not matches and priority<95:priority=10;exact=False
                candidates[key]={'path':str(exe),'version':number,'sources':[source],'priority':priority,'exact':exact,
                                 'supported':(version['MajorVersion'],version['MinorVersion'])==(5,8)}
        except (OSError,ValueError,IndexError) as error:
            warnings.append(str(error))
    if remembered:add(remembered,'此工程上次确认的引擎',100,True)
    if info:
        for path in running_engine(info['file']) if running is None else running:add(path,'此工程正在运行的引擎',110,True)
        raw=str(info.get('association','')).strip()
        if raw and ('/' in raw or '\\' in raw):
            path=Path(raw);add(path if path.is_absolute() else info['root']/path,'工程中的路径关联',95,True)
        # Project-local, sibling and source-tree engines. Never descend into Content/Saved/Plugins.
        for depth,base in enumerate([info['root'],*list(info['root'].parents)]):
            priority=max(55,85-depth*5)
            for folder in ('Engine','UE','UnrealEngine'):add(base/folder,'工程附近的引擎',priority,True)
            if depth>2:continue
            try:
                for child in islice(base.iterdir(),256):
                    if child.is_dir() and re.match(r'^(UE[_-]|UnrealEngine)',child.name,re.I):add(child,'工程同级引擎目录',priority,True)
            except OSError:pass
    for name,location,source in registry_engines()+launcher_engines() if records is None else records:
        match=bool(association) and association_key(name)==association
        add(location,source+('（匹配工程关联）' if match else ''),90 if match and not numeric else 50 if match else 10,match and not numeric)
    for path in known:add(path,'最近使用的引擎')
    for base in default_roots() if roots is None else roots:
        base=Path(base);add(base,'常用安装目录')
        try:
            for child in islice(base.iterdir(),256):
                if child.is_dir() and re.match(r'^(UE[_-]|UnrealEngine)',child.name,re.I):add(child,'常用安装目录')
        except OSError:pass
    ordered=sorted(candidates.values(),key=lambda c:(-c['priority'],c['path'].casefold()))
    selected=None
    if ordered:
        best=ordered[0]
        peers=[c for c in ordered if c['priority']==best['priority']]
        if len(peers)==1 and (best['exact'] or (numeric and best['priority']>=50)):
            selected=best['path']
    return {'selected':selected,'candidates':ordered,'warnings':warnings[:8]}
