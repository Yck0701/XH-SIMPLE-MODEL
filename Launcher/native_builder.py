"""Compile only our adapter, using the selected engine's SDK in a private host.

No user project is accepted by this module. A successful compiler exit alone is
not installable: a disposable editor must pass the lifecycle verification first.
"""
import json
from contextvars import ContextVar
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid
import zipfile
import core
import native_profiles

_cancel=ContextVar('build_cancel',default=None)


def check_cancelled():
    event=_cancel.get()
    if event is not None and event.is_set():raise RuntimeError('已取消本次编译。未安装到用户工程，可稍后重试。')


MODULES = ('Core','CoreUObject','Engine','Slate','SlateCore','UMG','UnrealEd','AdvancedPreviewScene',
           'GeometryFramework','GeometryCore','DynamicMesh','RenderCore','RHI','InputCore','ApplicationCore',
           'Projects','ToolMenus','TraceLog','EditorFramework','MaterialEditor','PropertyEditor')
DOCS = 'https://dev.epicgames.com/documentation/en-us/unreal-engine/setting-up-visual-studio-development-environment-for-cplusplus-projects-in-unreal-engine'


def libraries(engine):
    result=[]
    for module in MODULES:
        name='UnrealEditor-'+module+'.lib'
        path=engine/f'Intermediate/Build/Win64/x64/UnrealEditor/Development/{module}'/name
        if not path.is_file():
            candidates=list((engine/'Plugins/Runtime/GeometryProcessing/Intermediate').rglob(name))
            if len(candidates)!=1:
                raise ValueError('所选引擎缺少 C++ 开发库：'+name+'。请补齐此构建的开发文件，不能使用其他版本的库。')
            path=candidates[0]
        result.append(path)
    return result


def fingerprint(engine):
    version=core.read_json(engine/'Build/Build.version')
    identity=core.read_json(engine/'Binaries/Win64/UnrealEditor.modules')['BuildId']
    if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',identity):
        raise ValueError('引擎 BuildId 无效。')
    # Hash every linked engine module, including geometry plugin DLLs.
    hashes={}
    for module in MODULES:
        name='UnrealEditor-'+module+'.dll'
        path=engine/'Binaries/Win64'/name
        if not path.is_file():
            candidates=list((engine/'Plugins/Runtime/GeometryProcessing/Binaries/Win64').glob(name))
            if len(candidates)!=1:raise ValueError('引擎缺少运行组件：'+name)
            path=candidates[0]
        hashes[path.relative_to(engine).as_posix()]=core.digest(path)
    return {'build_id':identity,'engine_version':{k:version.get(k) for k in native_profiles.VERSION_KEYS},'engine_sha256':hashes}


def key(snapshot,source_hash):
    import hashlib
    return hashlib.sha256(json.dumps([snapshot,source_hash],sort_keys=True).encode()).hexdigest()


def validate_payload(payload):
    payload=Path(payload);core.ensure_regular_tree(payload)
    manifest=core.read_json(payload/'manifest.json')
    required={'CompilerHost/VFXPreviewBuild.uplugin','CompilerHost/Source/VFXPreviewBuild/VFXPreviewBuild.Build.cs',
              'CompilerHost/Source/VFXPreviewBuild/Host.cpp','RulesHelper/Program.cs','verify_native.py'}
    if set(manifest['files'])!=required:raise ValueError('编译附件不完整，请重新解压完整启动器。')
    for name,checksum in manifest['files'].items():
        if core.digest(payload/name)!=checksum:raise ValueError('编译附件校验失败：'+name)
    return True


def windows_sdks(roots=None):
    if roots is None:
        roots=[Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'Windows Kits/10']
        if os.environ.get('WindowsSdkDir'):roots.append(Path(os.environ['WindowsSdkDir']))
        if os.name=='nt':
            import winreg
            for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\Microsoft\Windows Kits\Installed Roots',0,winreg.KEY_READ|view) as key:
                        roots.append(Path(winreg.QueryValueEx(key,'KitsRoot10')[0]))
                except OSError:pass
    found=[]
    for root in roots:
        root=Path(root)
        for include in (root/'Include').glob('10.*'):
            version=include.name
            files=(include/'um/Windows.h',include/'ucrt/stdio.h',root/'Lib'/version/'um/x64/kernel32.lib',
                   root/'Lib'/version/'ucrt/x64/ucrt.lib',root/'bin'/version/'x64/rc.exe')
            if all(p.is_file() for p in files):found.append(str(root)+' / '+version)
    return sorted(set(found))


def preflight(engine_path,payload):
    engine=core.validate_engine(engine_path).parents[2];validate_payload(payload)
    for name in ('Source/Runtime/Core/Public/CoreMinimal.h','Binaries/DotNET/UnrealBuildTool/UnrealBuildTool.dll'):
        if not (engine/name).is_file():raise ValueError('所选引擎缺少开发文件：'+name+'。请安装此构建的 C++ 开发组件。')
    if not (engine/'Build/InstalledBuild.txt').is_file():raise ValueError('自动编译适用于已安装的 UE 开发包。源码引擎请先生成独立开发包，以免构建工具写入引擎源码目录。')
    runtime=core.read_json(engine/'Binaries/DotNET/UnrealBuildTool/UnrealBuildTool.runtimeconfig.json')
    major=runtime['runtimeOptions']['framework']['version'].split('.')[0]
    dotnets=[p for p in (engine/'Binaries/ThirdParty/DotNet').rglob('dotnet.exe')
             if any((p.parent/'shared/Microsoft.NETCore.App').glob(major+'.*')) and 'arm64' not in p.as_posix().lower()]
    if len(dotnets)!=1:raise ValueError('找不到引擎配套的 .NET '+major+' 运行环境。请补齐此引擎的开发组件。')
    vswhere=Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'Microsoft Visual Studio/Installer/vswhere.exe'
    if not vswhere.is_file():raise ValueError('未检测到 Visual Studio C++ 工具链。请安装 Visual Studio / Build Tools 的 C++ 游戏开发或桌面开发、MSVC x64/x86 和 Windows SDK。')
    result=subprocess.run([str(vswhere),'-products','*','-requires','Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
                           '-property','installationPath'],capture_output=True,encoding='utf-8',errors='replace',timeout=30,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode or not result.stdout.strip():raise ValueError('缺少 MSVC x64/x86 编译器。请在 Visual Studio Installer 中添加 C++ 开发工作负载与 Windows SDK；版本以所选 UE 的要求为准。')
    if not windows_sdks():raise ValueError('未找到完整的 Windows SDK（头文件、x64 库与资源编译器）。请在 Visual Studio Installer 的“单个组件”中安装所选 UE 要求的 Windows SDK，然后点击重试。')
    libs=libraries(engine)
    if shutil.disk_usage(Path(payload).anchor).free<2*1024**3:raise ValueError('启动器所在磁盘可用空间不足 2 GB。')
    return {'engine':engine,'dotnet':dotnets[0],'libraries':libs,'version':core.engine_version_label(core.read_json(engine/'Build/Build.version'))}


def run_process(args,cwd,log,timeout,env=None):
    """No shell, no generated batch file. Kill only the child tree we started."""
    with Path(log).open('wb') as output:
        process=subprocess.Popen([str(a) for a in args],cwd=cwd,stdout=output,stderr=subprocess.STDOUT,env=env,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+timeout
        try:
            while True:
                check_cancelled()
                if time.monotonic()>deadline:raise subprocess.TimeoutExpired(args,timeout)
                try:code=process.wait(timeout=.25);break
                except subprocess.TimeoutExpired:pass
        except (subprocess.TimeoutExpired,RuntimeError) as error:
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=30,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            process.wait(timeout=30)
            if isinstance(error,RuntimeError):raise
            raise RuntimeError('操作超时，已停止本次测试进程。日志：'+str(log))
    if code:
        details=Path(log).read_text(encoding='utf-8',errors='replace')[-12000:]
        missing=re.search(r"module rules named '([^']+)'",details)
        hint=('所选引擎的预编译规则库缺少 '+missing[1]+' 模块。请向引擎维护者获取同一构建的完整 C++ 开发包（包含 UE5Rules、头文件与导入库）。' if missing else
              '若提示 MSVC / Windows SDK 缺失，请按日志版本安装；若为 C++ API 错误，请提供日志以增加源码适配。')
        raise RuntimeError('编译或独立验证失败（退出码 '+str(code)+'）。未安装。\n'+hint+'\n查看日志：'+str(log))


def compiler_arguments(shared,engine,configured,source,out,libs):
    lines=[];includes=[]
    for line in shared.read_text(encoding='utf-8-sig').splitlines():
        if line=='/c' or line.startswith(('/we','/W4')):continue
        if line.startswith(('/I ','/external:I ')):
            value=line.split('"')[1];path=Path(value)
            if not path.is_absolute():path=(engine/'Source'/path).resolve()
            includes.append(path);line=line.split('"')[0]+'"'+str(path)+'"'
        lines.append(line)
    msvc=[p.parent for p in includes if p.name.lower()=='include' and p.parent.parent.name.lower()=='msvc']
    sdk=[p.parent for p in includes if p.name.lower()=='ucrt' and p.parent.parent.name.lower()=='include']
    if len(msvc)!=1 or len(sdk)!=1:raise ValueError('无法识别 UE 选择的 MSVC / Windows SDK；请提供配置日志。')
    compiler=msvc[0]/'bin/Hostx64/x64/cl.exe'
    libdirs=[msvc[0]/'lib/x64',sdk[0].parent.parent/'Lib'/sdk[0].name/'ucrt/x64',sdk[0].parent.parent/'Lib'/sdk[0].name/'um/x64']
    if not compiler.is_file() or not all(p.is_dir() for p in libdirs):raise ValueError('UE 选择的 MSVC 或 Windows SDK 不完整。请修复 C++ 开发组件。')
    lines+=['/std:c++20','/GR-','/LD','/W0','/FI"'+str(configured/'Definitions.h')+'"','"'+str(source)+'"',
            '/Fo"'+str(out/'Preview.obj')+'"','/Fe"'+str(out/'VFXNativePreview.dll')+'"']
    lines+=['/link /INCREMENTAL:NO '+' '.join(['/LIBPATH:"'+str(p)+'"' for p in libdirs]+['"'+str(p)+'"' for p in libs])]
    return compiler,lines


def configure(config,arguments,work,payload,env,progress):
    engine=config['engine'];rules=engine/'Intermediate/Build/BuildRules'
    before=core.inventory(rules)
    try:
        try:
            run_process([config['dotnet'],engine/'Binaries/DotNET/UnrealBuildTool/UnrealBuildTool.dll',*arguments],work,work/'configure.log',600,env)
            return False
        except RuntimeError:
            if not re.search(r"module rules named '[^']+'",(work/'configure.log').read_text(encoding='utf-8',errors='replace')):raise
        progress('引擎规则缓存不完整，正在独立目录重建规则副本…')
        helper=work/'rules-helper';helper.mkdir()
        shutil.copy2(Path(payload)/'RulesHelper/Program.cs',helper/'Program.cs')
        runtime=core.read_json(engine/'Binaries/DotNET/UnrealBuildTool/UnrealBuildTool.runtimeconfig.json')
        major=runtime['runtimeOptions']['framework']['version'].split('.')[0]
        if not re.fullmatch(r'\d{1,2}',major):raise ValueError('无法识别引擎 .NET 版本。')
        if not any((config['dotnet'].parent/'sdk').glob(major+'.*')):
            raise ValueError('规则缓存需要重建，但所选引擎未附带 .NET '+major+' SDK。请补齐同构建开发包的 .NET SDK 或正确的预编译规则库。')
        (helper/'RulesHelper.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net'+major+'.0</TargetFramework><ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable></PropertyGroup></Project>',encoding='utf-8')
        # All dependencies come from the engine's bundled SDK. Never fetch NuGet packages.
        (helper/'NuGet.Config').write_text('<configuration><packageSources><clear /></packageSources></configuration>',encoding='utf-8')
        env=dict(env,DOTNET_GENERATE_ASPNET_CERTIFICATE='false',DOTNET_CLI_HOME=str(helper/'dotnet-home'))
        run_process([config['dotnet'],'build',helper/'RulesHelper.csproj','--configfile',helper/'NuGet.Config','-v','minimal'],work,work/'rules-helper.log',120,env)
        run_process([config['dotnet'],helper/f'bin/Debug/net{major}.0/RulesHelper.dll',engine/'Binaries/DotNET/UnrealBuildTool',
                     work/'rules-cache','-rootdirectory='+str(engine.parent),*arguments],work,work/'configure-repaired.log',600,env)
        return True
    finally:
        if core.inventory(rules)!=before:raise RuntimeError('引擎规则文件在配置期间发生变化，已停止本次构建。请检查引擎同步状态。')


def cached(engine_path,item,cache):
    if not Path(cache).is_dir() or not any(Path(cache).glob('*/receipt.json')):return None
    snapshot=fingerprint(core.validate_engine(engine_path).parents[2]);folder=Path(cache)/key(snapshot,item['sha256'])
    receipt=folder/'receipt.json'
    if not receipt.is_file():return None
    data=core.read_json(receipt);archive=folder/'plugin.zip'
    if (data.get('schema')!=1 or data.get('source_sha256')!=item['sha256'] or data.get('engine')!=snapshot
            or data.get('verification',{}).get('passed') is not True or data.get('plugin',{}).get('version')!=item['version']
            or not archive.is_file() or core.digest(archive)!=data['plugin']['sha256'] or archive.stat().st_size!=data['plugin']['size']):
        raise ValueError('本机适配缓存校验失败，请重新编译；不会安装未验证文件。')
    return archive,data['plugin']


def diagnostic(engine_path,archive,item,cache):
    local=cached(engine_path,item,cache)
    report=core.diagnostic_report(engine_path,local[0] if local else archive)
    report['local_adapter']=bool(local)
    return report


def build(engine_path,archive,item,payload,cache,progress=lambda message:None,cancel_event=None):
    token=_cancel.set(cancel_event)
    try:
        check_cancelled()
        return _build(engine_path,archive,item,payload,cache,progress)
    finally:_cancel.reset(token)


def _build(engine_path,archive,item,payload,cache,progress):
    check_cancelled()
    progress('1/4  检查引擎开发文件与 C++ 工具链…')
    config=preflight(engine_path,payload);engine=config['engine']
    if core.digest(archive)!=item['sha256'] or Path(archive).stat().st_size!=item['size']:raise ValueError('原始插件安装包校验失败。')
    cache=Path(cache).resolve();core.ensure_regular_tree(cache);cache.mkdir(parents=True,exist_ok=True)
    if cache.is_relative_to(engine):raise ValueError('编译缓存不能位于引擎目录。')
    if shutil.disk_usage(cache).free<2*1024**3:raise ValueError('编译缓存所在磁盘可用空间不足 2 GB。')
    before=fingerprint(engine)
    work=cache/('work-'+uuid.uuid4().hex);work.mkdir()
    core.write_json(cache/'last-build.json',{'work':str(work),'status':'building'})
    try:
        host=work/'host';host.mkdir();target=host/'Plugins/VFXPreviewBuild'
        shutil.copytree(Path(payload)/'CompilerHost',target)
        project=host/'NativeHost.uproject'
        core.write_json(project,{'FileVersion':3,'Plugins':[{'Name':'VFXPreviewBuild','Enabled':True}]})
        check_cancelled()
        progress('2/4  由所选 UE 生成编译配置…（首次可能需要数分钟）')
        env=os.environ.copy();env.update(DOTNET_CLI_TELEMETRY_OPTOUT='1',DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1',DOTNET_NOLOGO='1')
        repaired=configure(config,['UnrealEditor','Win64','Development','-Project='+str(project),'-plugin='+str(target/'VFXPreviewBuild.uplugin'),
                     '-NoUBTMakefiles','-NoHotReload','-SkipBuild','-NoEngineChanges','-NoUBA','-NoXGE','-NoFASTBuild','-NoSNDBS',
                     '-Log='+str(work/'ubt.log'),'-UTF8Output'],work,payload,env,progress)
        configs=list(target.rglob('VFXPreviewBuild.Shared.rsp'))
        if len(configs)!=1:raise ValueError('UE 未生成唯一的编译配置，请查看 '+str(work/'configure.log'))
        plugin=core.safe_extract(archive,work/'stage',core.PLUGIN)
        # MSVC's forced-include reader can reject a >260 character UBT path even
        # when Python/.NET generated that file successfully (MSIX user homes).
        shutil.copy2(configs[0].parent/'Definitions.h',work/'Definitions.h')
        compiler,lines=compiler_arguments(configs[0],engine,work,plugin/'Source/NativePreview/Preview.cpp',work,config['libraries'])
        rsp=work/'compile.rsp';rsp.write_text('\n'.join(lines),encoding='utf-8-sig')
        env['PATH']=str(compiler.parent)+os.pathsep+env.get('PATH','')
        check_cancelled()
        progress('2/4  正在编译当前引擎专用组件…')
        run_process([compiler,'@'+str(rsp)],work,work/'compile.log',600,env)
        if fingerprint(engine)!=before:raise ValueError('编译过程中引擎发生变化，请等待引擎同步完成后重试。')
        identity=key(before,item['sha256']);version=before['engine_version']
        relative=Path(before['build_id'])/'variants'/('local-'+identity[:20])
        dest=plugin/'Native/Win64'/relative;dest.mkdir(parents=True)
        shutil.copy2(work/'VFXNativePreview.dll',dest/'VFXNativePreview.dll')
        core.write_json(dest/'manifest.json',dict(before,dll_sha256=core.digest(dest/'VFXNativePreview.dll'),local_build=True))
        # Testing contains only the candidate. The resulting package retains all shipped profiles.
        smoke=work/'verify';smoke.mkdir();verify_plugin=smoke/'Plugins'/core.PLUGIN
        shutil.copytree(plugin,verify_plugin,ignore=shutil.ignore_patterns('Native','__pycache__','*.pyc'))
        shutil.copytree(dest,verify_plugin/'Native/Win64'/relative)
        smoke_project=smoke/'VFXAdapterVerify.uproject'
        core.write_json(smoke_project,{'FileVersion':3,'Plugins':[{'Name':core.PLUGIN,'Enabled':True}]})
        shutil.copy2(Path(payload)/'verify_native.py',smoke/'verify_native.py')
        core.write_json(smoke/'verify-config.json',{'nonce':uuid.uuid4().hex,'profile':relative.as_posix()})
        check_cancelled()
        progress('3/4  在独立空工程验证预览、材质与重复开关…（会短暂打开测试编辑器）')
        run_process([engine_path,smoke_project,'-nosplash','-nosound','-unattended','-NoRestoreOpenAssetTabs',
                     '-NoLiveCoding','-ddc=NoShared','-ExecutePythonScript='+(smoke/'verify_native.py').as_posix(),
                     '-abslog='+str(work/'verify-editor.log')],smoke,work/'verify-process.log',900,env)
        if not (smoke/'result.json').is_file():raise RuntimeError('编辑器未完成验证。请查看 '+str(work/'verify-editor.log')+' 中的 Python / 组件加载错误。')
        report=core.read_json(smoke/'result.json');verify_config=core.read_json(smoke/'verify-config.json')
        if report.get('passed') is not True or report.get('nonce')!=verify_config['nonce'] or report.get('profile')!=relative.as_posix():
            raise RuntimeError('独立编辑器验证未通过，请查看 '+str(work/'verify-editor.log'))
        if fingerprint(engine)!=before:raise ValueError('验证过程中引擎发生变化，结果已丢弃，请重试。')
        check_cancelled()
        progress('4/4  验证通过，正在保存本机适配包…')
        ready=cache/identity;ready.mkdir(exist_ok=True);temporary=work/'plugin.zip'
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as zipped:
            for path in sorted(plugin.rglob('*')):
                if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc':zipped.write(path,core.PLUGIN+'/'+path.relative_to(plugin).as_posix())
        derived={'version':item['version'],'sha256':core.digest(temporary),'size':temporary.stat().st_size}
        check_cancelled()
        os.replace(temporary,ready/'plugin.zip')
        core.write_json(ready/'receipt.json',{'schema':1,'source_sha256':item['sha256'],'engine':before,'plugin':derived,
                                             'verification':report,'rules_rebuilt_in_cache':repaired,'work':str(work),'compiler':str(compiler),'time':time.time()})
        core.write_json(cache/'last-build.json',{'work':str(work),'status':'passed','receipt':str(ready/'receipt.json')})
        return {'version':config['version'],'work':str(work),'receipt':str(ready/'receipt.json')}
    except Exception as error:
        core.write_json(cache/'last-build.json',{'work':str(work),'status':'failed','error':str(error)})
        raise RuntimeError(str(error)+'\n本次工作目录：'+str(work)+'\n未安装到用户工程。') from error
