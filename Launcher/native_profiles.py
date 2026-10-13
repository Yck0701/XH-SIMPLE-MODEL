"""Pure read-only native profile selection, shared with the editor runtime.

Keep the copy in vfx_mesh/native_profiles.py byte-identical; release validation
checks this. A BuildId may identify several different engine patch builds.
"""
import hashlib
import json
from pathlib import Path
import re

VERSION_KEYS=('MajorVersion','MinorVersion','PatchVersion','Changelist','CompatibleChangelist')


def _unreal_json(text):
    """Match UE descriptor comments/trailing commas without changing strings or offsets."""
    chars=list(text);i=0;quoted=False
    while i<len(text):
        if quoted:
            if text[i]=='\\':i+=2;continue
            if text[i]=='"':quoted=False
        elif text[i]=='"':quoted=True
        elif text.startswith('//',i):
            end=text.find('\n',i)
            if end<0:end=len(text)
            for n in range(i,end):
                if chars[n]!='\r':chars[n]=' '
            i=end;continue
        elif text.startswith('/*',i):
            end=text.find('*/',i+2)
            if end<0:raise json.JSONDecodeError('Unterminated comment',text,i)
            for n in range(i,end+2):
                if chars[n] not in '\r\n':chars[n]=' '
            i=end+2;continue
        i+=1
    clean=''.join(chars);i=0;quoted=False
    while i<len(clean):
        if quoted:
            if clean[i]=='\\':i+=2;continue
            if clean[i]=='"':quoted=False
        elif clean[i]=='"':quoted=True
        elif clean[i]==',':
            end=i+1
            while end<len(clean) and clean[end].isspace():end+=1
            previous=i-1
            while previous>=0 and clean[previous].isspace():previous-=1
            if end<len(clean) and clean[end] in ']}' and previous>=0 and clean[previous] not in '[{:,':chars[i]=' '
        i+=1
    return ''.join(chars)


def read_json(path):
    path=Path(path)
    try:text=path.read_text(encoding='utf-8-sig')
    except UnicodeError as error:
        raise ValueError('文件编码无法读取：\n'+str(path)+'\n请提供完整的 UTF-8 文件。未修改原文件。') from error
    except OSError as error:
        raise OSError('无法读取文件：\n'+str(path)+'\n请确认文件存在、可访问且已同步完整。') from error
    try:
        # UE's file reader accepts these extensions for engine/project descriptors.
        # Package manifests, update feeds, and verification receipts stay strict JSON.
        if path.name.lower()=='build.version' or path.suffix.lower() in ('.uproject','.uplugin','.modules'):
            text=_unreal_json(text)
        return json.loads(text)
    except json.JSONDecodeError as error:
        hint='请重新完整解压启动器，或重新下载校验通过的安装包。' if path.name=='manifest.json' else \
             '本机编译记录不完整，请重新编译适配；不会安装未验证缓存。' if path.name=='receipt.json' else \
             '请检查该文件是否同步完整、含合并冲突或无效 JSON；不要修改 BuildId 来绕过兼容性检查。'
        raise ValueError('JSON 文件格式错误：\n'+str(path)+'\n第 '+str(error.lineno)+' 行，第 '+str(error.colno)+' 列。\n'+hint+'\n未修改原文件。') from error


def digest(path):
    checksum=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):checksum.update(block)
    return checksum.hexdigest()


def manifests(native_root):
    root=Path(native_root)
    return sorted(set(root.glob('*/manifest.json'))|set(root.glob('*/variants/*/manifest.json')))


def inspect(engine_root,native_root,hash_file=digest):
    root=Path(engine_root);native_root=Path(native_root)
    version=read_json(root/'Build/Build.version')
    build=read_json(root/'Binaries/Win64/UnrealEditor.modules')['BuildId']
    if not isinstance(build,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',build):raise ValueError('引擎 BuildId 无效。')
    inventory=[]
    for path in manifests(native_root):
        relative=path.relative_to(native_root)
        metadata=read_json(path)
        if metadata.get('build_id',relative.parts[0])!=relative.parts[0]:raise ValueError('适配组件的 BuildId 与目录不一致。')
        inventory.append((path,metadata))
    profiles=[{'profile':p.parent.relative_to(native_root).as_posix(),'build_id':p.relative_to(native_root).parts[0],
               'engine_version':m.get('engine_version')} for p,m in inventory]
    report={'build_id':build,'engine_version':{k:version.get(k) for k in VERSION_KEYS},
            'supported_builds':sorted({p['build_id'] for p in profiles}),'supported_profiles':profiles,
            'files':[],'compatible':False,'reason':'missing_adapter'}
    actual={};candidates=[]
    for path,adapter in inventory:
        if path.relative_to(native_root).parts[0]!=build:continue
        hashes=adapter['engine_sha256']
        if not hashes:raise ValueError('适配组件缺少引擎校验清单。')
        files=[]
        for name,expected in hashes.items():
            if not re.fullmatch(r'(?:Plugins/Runtime/GeometryProcessing/)?Binaries/Win64/UnrealEditor-[A-Za-z0-9_-]+\.dll',name):raise ValueError('引擎校验清单路径无效。')
            if not re.fullmatch(r'[0-9a-f]{64}',expected):raise ValueError('引擎校验指纹无效。')
            if name not in actual:
                file=root/name;actual[name]=hash_file(file) if file.is_file() else None
            current=actual[name]
            files.append({'file':name,'expected_sha256':expected,'actual_sha256':current,
                          'status':'match' if current==expected else 'missing' if current is None else 'different'})
        expected=adapter.get('engine_version')
        version_match=not expected or all(version.get(k)==expected.get(k) for k in VERSION_KEYS[:4])
        compatible=version_match and all(f['status']=='match' for f in files)
        candidate=dict(report,profile=path.parent.relative_to(native_root).as_posix(),files=files,
                       expected_engine_version=expected,compatible=compatible,
                       reason='verified' if compatible else 'different_files' if version_match else 'engine_version_mismatch')
        # Prefer exact version diagnostics over another patch with a shared ID.
        score=(compatible,version_match,sum(f['status']=='match' for f in files))
        candidates.append((score,candidate))
    if candidates:return max(candidates,key=lambda item:item[0])[1]
    return report
