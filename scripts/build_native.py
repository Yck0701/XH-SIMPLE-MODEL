"""Build an engine-specific native viewport; verify in the target editor before release."""
import argparse,hashlib,json,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--engine',required=True);p.add_argument('--sdk',required=True)
p.add_argument('--vcvars',required=True);p.add_argument('--configuration-dir');args=p.parse_args()
engine=Path(args.engine).resolve();sdk=Path(args.sdk).resolve();plugin=ROOT/'Plugin/VFXMeshGenerator'
out=ROOT/'build/native'/hashlib.sha256(str(engine).encode()).hexdigest()[:10];out.mkdir(parents=True,exist_ok=True)
if args.configuration_dir:configured=Path(args.configuration_dir)
else:
    host=ROOT/'build/native-host';target=host/'Plugins/VFXPreviewBuild'
    shutil.copytree(ROOT/'scripts/CompilerHost',target,dirs_exist_ok=True)
    project=host/'NativeHost.uproject';project.write_text(json.dumps({'FileVersion':3,'Plugins':[{'Name':'VFXPreviewBuild','Enabled':True}]}))
    subprocess.run(['cmd.exe','/d','/c',str(sdk/'Build/BatchFiles/Build.bat'),'UnrealEditor','Win64','Development',
        '-Project='+str(project),'-plugin='+str(target/'VFXPreviewBuild.uplugin'),'-noubtmakefiles','-nohotreload','-SkipBuild',
        '-NoUBA','-NoXGE','-NoFASTBuild','-NoSNDBS','-UTF8Output'],check=True)
    configured=target/'Intermediate/Build/Win64/x64/UnrealEditor/Development/VFXPreviewBuild'
lines=[]
for line in (configured/'VFXPreviewBuild.Shared.rsp').read_text(encoding='utf-8-sig').splitlines():
    if line in ('/c','/X') or line.startswith(('/we','/W4')):continue
    if line.startswith(('/I ','/external:I ')):
        original=line.split('"')[1];path=Path(original)
        if path.is_absolute() and path.is_relative_to(sdk):
            candidate=engine/path.relative_to(sdk)
            if candidate.exists():path=candidate
        if not path.is_absolute():
            path=(engine/'Source'/path).resolve()
            if not path.exists():path=(sdk/'Source'/original).resolve()
        if path.is_relative_to(engine/'Source/Runtime/Engine'):path=sdk/path.relative_to(engine)
        line=line.split('"')[0]+'"'+str(path)+'"'
    lines.append(line)
for line in list(lines):
    if line.startswith(('/I ','/external:I ')):
        path=Path(line.split('"')[1])
        if path.is_relative_to(engine):
            fallback=sdk/path.relative_to(engine)
            if fallback.exists():lines.append('/I "'+str(fallback)+'"')
lines+=['/std:c++20','/GR-','/LD','/W0','/FI"'+str(configured/'Definitions.h')+'"',
    '"'+str(plugin/'Source/NativePreview/Preview.cpp')+'"','/Fo"'+str(out/'Preview.obj')+'"',
    '/Fe"'+str(out/'VFXNativePreview.dll')+'"','/link','/INCREMENTAL:NO']
modules=('Core','CoreUObject','Engine','Slate','SlateCore','UMG','UnrealEd','AdvancedPreviewScene','GeometryFramework','GeometryCore',
         'DynamicMesh','RenderCore','RHI','InputCore','ApplicationCore','Projects','ToolMenus','TraceLog','EditorFramework','MaterialEditor','PropertyEditor')
for mod in modules:
    lib=sdk/f'Intermediate/Build/Win64/x64/UnrealEditor/Development/{mod}/UnrealEditor-{mod}.lib'
    if not lib.is_file():
        candidates=list((sdk/'Plugins/Runtime/GeometryProcessing/Intermediate').rglob('UnrealEditor-'+mod+'.lib'))
        if len(candidates)!=1:raise FileNotFoundError(lib)
        lib=candidates[0]
    lines.append('"'+str(lib)+'"')
rsp=out/'compile.rsp';rsp.write_text('\n'.join(lines),encoding='utf-8-sig')
cmd=out/'build.cmd';cmd.write_text('@echo off\nchcp 65001 >nul\ncall "'+args.vcvars+'" -vcvars_ver=14.50\nif errorlevel 1 exit /b %errorlevel%\ncl @"'+str(rsp)+'"\n',encoding='utf-8')
subprocess.run(['cmd.exe','/d','/c',str(cmd)],cwd=out,check=True)
build_id=json.loads((engine/'Binaries/Win64/UnrealEditor.modules').read_text())['BuildId']
dest=plugin/'Native/Win64'/build_id;dest.mkdir(parents=True,exist_ok=True)
shutil.copy2(out/'VFXNativePreview.dll',dest/'VFXNativePreview.dll')
hashes={f'Binaries/Win64/UnrealEditor-{mod}.dll':hashlib.sha256((engine/f'Binaries/Win64/UnrealEditor-{mod}.dll').read_bytes()).hexdigest()
        for mod in ('Core','CoreUObject','Engine','Slate','SlateCore','UMG','UnrealEd','AdvancedPreviewScene','GeometryFramework','MaterialEditor','PropertyEditor')}
(dest/'manifest.json').write_text(json.dumps({'build_id':build_id,'engine_sha256':hashes,'dll_sha256':hashlib.sha256((dest/'VFXNativePreview.dll').read_bytes()).hexdigest()},indent=2))
print('Built '+str(dest))
