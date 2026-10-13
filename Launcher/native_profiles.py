"""Pure read-only native profile selection, shared with the editor runtime.

Keep the copy in vfx_mesh/native_profiles.py byte-identical; release validation
checks this. A BuildId may identify several different engine patch builds.
"""
import hashlib
import json
from pathlib import Path
import re

VERSION_KEYS=('MajorVersion','MinorVersion','PatchVersion','Changelist','CompatibleChangelist')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


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
