"""Build versioned Windows artifacts. Run from a Python with PyInstaller installed."""
import hashlib
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Launcher'))
from version import VERSION,REPOSITORY
import core


def zip_tree(root,destination,prefix):
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for path in sorted(root.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc':
                z.write(path,prefix+'/'+path.relative_to(root).as_posix())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--reuse-launcher',action='store_true',help='Only repackage plugin/docs when launcher code has not changed')
    args=parser.parse_args()
    descriptor=core.read_json(ROOT/'Plugin/VFXMeshGenerator/VFXMeshGenerator.uplugin')
    versions=core.read_json(ROOT/'version.json')
    assert descriptor['VersionName']==versions['version']==versions['plugin_version']==VERSION
    assert (ROOT/'Launcher/native_profiles.py').read_bytes()==(ROOT/'Plugin/VFXMeshGenerator/Content/Python/vfx_mesh/native_profiles.py').read_bytes(), 'Shared profile selectors differ'
    release=ROOT/'release'/VERSION;release.mkdir(parents=True,exist_ok=True)
    plugin=release/f'VFXMeshGenerator-{VERSION}-UE5.8.zip'
    zip_tree(ROOT/'Plugin/VFXMeshGenerator',plugin,'VFXMeshGenerator')
    if not args.reuse_launcher:
        subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir',
            '--name','VFX特效网格启动器','--icon',str(ROOT/'Launcher/assets/app.ico'),'--distpath',str(ROOT/'dist'/VERSION),'--workpath',str(ROOT/'build'),
            '--specpath',str(ROOT/'build'),str(ROOT/'Launcher/app.py')],check=True,cwd=ROOT)
    bundle=ROOT/'dist'/VERSION/'VFX特效网格启动器'
    assert (bundle/'VFX特效网格启动器.exe').is_file()
    shutil.copytree(ROOT/'Launcher/assets',bundle/'assets',dirs_exist_ok=True)
    offline=bundle/'offline';offline.mkdir(exist_ok=True)
    for stale in offline.glob('*.zip'):stale.unlink()
    shutil.copy2(plugin,offline/plugin.name)
    plugin_item={'version':VERSION,'sha256':core.digest(plugin),'size':plugin.stat().st_size,'file':plugin.name}
    core.write_json(offline/'manifest.json',{'schema':1,'plugin':plugin_item})
    compiler=bundle/'compiler';compiler.mkdir(exist_ok=True)
    shutil.copytree(ROOT/'scripts/CompilerHost',compiler/'CompilerHost',dirs_exist_ok=True)
    shutil.copytree(ROOT/'scripts/RulesHelper',compiler/'RulesHelper',dirs_exist_ok=True)
    shutil.copy2(ROOT/'scripts/verify_native.py',compiler/'verify_native.py')
    core.write_json(compiler/'manifest.json',{'files':{
        p.relative_to(compiler).as_posix():core.digest(p) for p in compiler.rglob('*') if p.is_file() and p.name!='manifest.json'}})
    shutil.copy2(ROOT/'docs/使用教学.html',bundle/'使用教学.html')
    shutil.copy2(ROOT/'version.json',bundle/'version.json')
    (bundle/'先读我.txt').write_text('完整解压后，双击 VFX特效网格启动器.exe。\n选择 .uproject → 自动查找引擎并检测 → 必要时一键编译验证 → 确认安装 → 启动工作台。\n请保留 assets、compiler、_internal 与 offline 目录。详细步骤见 使用教学.html。\n',encoding='utf-8-sig')
    launcher=release/f'VFXMeshLauncher-{VERSION}-Windows.zip'
    zip_tree(bundle,launcher,'VFXMeshLauncher')
    base=f'https://github.com/{REPOSITORY}/releases/download/vfx-v{VERSION}/'
    feed={'schema':1,'product':'VFXMeshGenerator','repository':REPOSITORY,'engine':'5.8','minimum_launcher':'1.5.8',
        'notes':'请先更新启动器到 1.5.8。编译前按当前 UE 要求验证 MSVC、Windows SDK、引擎开发文件与 .NET。缺少支持的组件时，下载并验证微软安装器、预选组件，用户完成系统授权与安装确认后自动复查和试编译，再继续插件编译、验证与安装确认。支持重试、取消和重启提示；不强制终止系统安装器，不自动重启，不修改工程关联或无关项目文件。'}
    for key,file in [('plugin',plugin),('launcher',launcher)]:
        feed[key]={'version':VERSION,'url':base+file.name,'sha256':core.digest(file),'size':file.stat().st_size}
    core.validate_feed(feed);core.write_json(release/'vfx-manifest.json',feed)
    checksums='\n'.join(core.digest(p)+'  '+p.name for p in [plugin,launcher,release/'vfx-manifest.json'])+'\n'
    (release/'SHA256SUMS.txt').write_text(checksums,encoding='utf-8')
    print(json.dumps({'bundle':str(bundle),'release':str(release),'files':[{ 'name':p.name,'bytes':p.stat().st_size} for p in release.iterdir()]},ensure_ascii=False))


if __name__=='__main__':main()
