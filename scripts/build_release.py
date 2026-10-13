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
    release=ROOT/'release'/VERSION;release.mkdir(parents=True,exist_ok=True)
    plugin=release/f'VFXMeshGenerator-{VERSION}-UE5.8.zip'
    zip_tree(ROOT/'Plugin/VFXMeshGenerator',plugin,'VFXMeshGenerator')
    if not args.reuse_launcher:
        subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--onedir',
            '--name','VFX特效网格启动器','--distpath',str(ROOT/'dist'/VERSION),'--workpath',str(ROOT/'build'),
            '--specpath',str(ROOT/'build'),str(ROOT/'Launcher/app.py')],check=True,cwd=ROOT)
    bundle=ROOT/'dist'/VERSION/'VFX特效网格启动器'
    assert (bundle/'VFX特效网格启动器.exe').is_file()
    offline=bundle/'offline';offline.mkdir(exist_ok=True)
    for stale in offline.glob('*.zip'):stale.unlink()
    shutil.copy2(plugin,offline/plugin.name)
    plugin_item={'version':VERSION,'sha256':core.digest(plugin),'size':plugin.stat().st_size,'file':plugin.name}
    core.write_json(offline/'manifest.json',{'schema':1,'plugin':plugin_item})
    shutil.copy2(ROOT/'docs/使用教学.html',bundle/'使用教学.html')
    shutil.copy2(ROOT/'version.json',bundle/'version.json')
    (bundle/'先读我.txt').write_text('完整解压后，双击 VFX特效网格启动器.exe。\n选择 .uproject → 安装插件 → 重启 UE → 特效网格 → 打开独立工作台。\n请保留 _internal 与 offline 目录。详细步骤见 使用教学.html。\n',encoding='utf-8-sig')
    launcher=release/f'VFXMeshLauncher-{VERSION}-Windows.zip'
    zip_tree(bundle,launcher,'VFXMeshLauncher')
    base=f'https://github.com/{REPOSITORY}/releases/download/vfx-v{VERSION}/'
    feed={'schema':1,'product':'VFXMeshGenerator','repository':REPOSITORY,'engine':'5.8','minimum_launcher':'1.2.0',
        'notes':'三栏可手动拖动，修复标题和参数名断行，精简重复入口。材质默认副本预览，支持实时迭代原实例；保存弹窗明确选择替换或新增，并显示结果和路径。补齐材质通用属性与重载、JX3 Blend Mode、环境参数。支持自定义网格和骨骼动画预览，顶底盖默认关闭，新增预览投影开关。保存仅写入确认的目标资产。含原生组件更新，请保存自己的工作并关闭工程后安装。'}
    for key,file in [('plugin',plugin),('launcher',launcher)]:
        feed[key]={'version':VERSION,'url':base+file.name,'sha256':core.digest(file),'size':file.stat().st_size}
    core.validate_feed(feed);core.write_json(release/'vfx-manifest.json',feed)
    checksums='\n'.join(core.digest(p)+'  '+p.name for p in [plugin,launcher,release/'vfx-manifest.json'])+'\n'
    (release/'SHA256SUMS.txt').write_text(checksums,encoding='utf-8')
    print(json.dumps({'bundle':str(bundle),'release':str(release),'files':[{ 'name':p.name,'bytes':p.stat().st_size} for p in release.iterdir()]},ensure_ascii=False))


if __name__=='__main__':main()
