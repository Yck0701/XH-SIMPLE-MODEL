import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'Launcher'))
import core


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.project=self.root/'Project.uproject'
        core.write_json(self.project,{'FileVersion':3})

    def package(self,version='1.1.0',script='first',asset='asset'):
        archive=self.root/(version+'-'+str(time.time_ns())+'.zip')
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('VFXMeshGenerator/VFXMeshGenerator.uplugin',json.dumps({'VersionName':version,'CanContainContent':True}))
            z.writestr('VFXMeshGenerator/Content/Python/runtime.py',script)
            z.writestr('VFXMeshGenerator/Content/test.uasset',asset)
        return archive,{'version':version,'sha256':core.digest(archive),'size':archive.stat().st_size}

    def install(self,version='1.1.0',**kwargs):
        archive,item=self.package(version,**kwargs)
        return core.install(self.project,archive,item,editor_probe=lambda _:[])

    def state(self):
        info=core.project_info(self.project)
        core.write_json(info['saved']/'editor.json',{'protocol':1,'pid':123,'session':'test','time':time.time(),'plugin_dir':str(info['plugin'])})
        return info

    def test_version_and_url(self):
        self.assertGreater(core.version_tuple('1.10.0'),core.version_tuple('1.9.0'))
        for bad in ['v1.0.0','1.0','1.2.3;cmd']:
            with self.assertRaises(ValueError):core.version_tuple(bad)
        for bad in ['http://github.com/Yck0701/XH-SIMPLE-MODEL/releases/download/vfx-v1.1.0/a.zip',
                    'https://github.com/other/repo/releases/download/v1/a.zip',
                    core.WEB+'/releases/download/../a.zip',core.WEB+'/releases/download/v1/a.zip?x=1']:
            with self.assertRaises(ValueError):core.asset_url(bad)

    def test_archive_rejects_unsafe_members(self):
        for member in ['VFXMeshGenerator/../outside','VFXMeshGenerator/./x','/absolute',
                       'VFXMeshGenerator/NUL.txt','VFXMeshGenerator/a:stream','Other/x']:
            archive=self.root/'unsafe.zip'
            with zipfile.ZipFile(archive,'w') as z:z.writestr(member,'bad')
            with self.assertRaises(ValueError):core.safe_extract(archive,self.root/'out',core.PLUGIN)
        for link in (False,True):
            with zipfile.ZipFile(archive,'w') as z:
                a=zipfile.ZipInfo('VFXMeshGenerator/a');a.external_attr=(stat.S_IFLNK|0o777)<<16 if link else 0
                z.writestr(a,'bad')
                if not link:z.writestr('VFXMeshGenerator/A','duplicate')
            with self.assertRaises(ValueError):core.safe_extract(archive,self.root/'out',core.PLUGIN)

    def test_cold_install_upgrade_restore(self):
        self.install();result=self.install('1.2.0',script='second')
        self.assertTrue(Path(result['backup']).is_dir())
        self.assertEqual(core.project_info(self.project)['version'],'1.2.0')
        self.assertEqual(core.restore_backup(self.project,editor_probe=lambda _:[]),'1.1.0')
        self.assertEqual((core.project_info(self.project)['plugin']/'Content/Python/runtime.py').read_text(),'first')

    def test_hash_and_downgrade(self):
        self.install('1.2.0');archive,item=self.package('1.1.0')
        with self.assertRaises(ValueError):core.install(self.project,archive,item,editor_probe=lambda _:[])
        item['sha256']='0'*64
        with self.assertRaises(ValueError):core.install(self.project,archive,item,editor_probe=lambda _:[])
        self.assertEqual(core.project_info(self.project)['version'],'1.2.0')

    def test_asset_update_blocked_while_running(self):
        self.install();archive,item=self.package('1.2.0',asset='changed')
        with self.assertRaises(ValueError):core.install(self.project,archive,item,editor_probe=lambda _:[123])
        self.assertEqual(core.project_info(self.project)['version'],'1.1.0')

    def test_hot_update_acknowledgement(self):
        self.install();self.state();calls=[];archive,item=self.package('1.2.0',script='new')
        result=core.install(self.project,archive,item,editor_probe=lambda _:[123],ipc=lambda *args,**kw:calls.append(args))
        self.assertEqual(result['mode'],'hot_reloaded');self.assertEqual(calls[0][2],'1.2.0')

    def test_failed_reload_restores_files_and_retries_old_version(self):
        self.install();info=self.state();calls=[];archive,item=self.package('1.2.0',script='broken')
        def ipc(*args,**kwargs):
            calls.append(args[2])
            if args[2]=='1.2.0':raise RuntimeError('reload failed')
        with self.assertRaises(RuntimeError):core.install(self.project,archive,item,editor_probe=lambda _:[123],ipc=ipc)
        self.assertEqual(calls,['1.2.0','1.1.0']);self.assertEqual(core.project_info(self.project)['version'],'1.1.0')
        self.assertEqual((info['plugin']/'Content/Python/runtime.py').read_text(),'first')

    def test_bootstrap_requires_restart(self):
        self.install('1.0.0');archive,item=self.package('1.1.0',script='bootstrap')
        result=core.install(self.project,archive,item,editor_probe=lambda _:[123])
        self.assertEqual(result['mode'],'restart_required')

    def test_reflected_settings_changes_require_restart(self):
        self.install();info=self.state()
        schema=info['plugin']/'Content/Python/vfx_mesh/settings_types.py'
        schema.parent.mkdir(parents=True);schema.write_text('old schema')
        archive,item=self.package('1.2.0')
        with zipfile.ZipFile(archive,'a') as z:
            z.writestr('VFXMeshGenerator/Content/Python/vfx_mesh/settings_types.py','new schema')
        item.update(sha256=core.digest(archive),size=archive.stat().st_size)
        with self.assertRaises(ValueError):
            core.install(self.project,archive,item,editor_probe=lambda _:[123])
        self.assertEqual(schema.read_text(),'old schema')
        self.assertEqual(core.project_info(self.project)['version'],'1.1.0')

    def test_disabled_dependencies(self):
        core.write_json(self.project,{'Plugins':[{'Name':'PythonScriptPlugin','Enabled':False}]})
        with self.assertRaises(ValueError):self.install()

    def test_multiple_projects_and_partial_copy_failure_remain_isolated(self):
        bystander=self.root/'OtherProject';bystander.mkdir()
        protected=[self.project,self.root/'Content/Keep.uasset',self.root/'Config/DefaultEngine.ini',
                   self.root/'Source/Keep.cpp',self.root/'Plugins/OtherPlugin/Keep.uplugin',
                   bystander/'Other.uproject',bystander/'Content/Keep.uasset']
        for path in protected[1:]:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'unchanged')
        baseline={path:path.read_bytes() for path in protected}
        self.install();old=core.inventory(core.project_info(self.project)['plugin'])
        for _ in range(3):
            info=self.state();archive,item=self.package('1.2.0',script='second')
            original=core._copy_atomic;calls=[]
            def fail_once(source,target):
                original(source,target);calls.append(target)
                if len(calls)==1:raise OSError('interrupted after first file')
            with patch.object(core,'_copy_atomic',side_effect=fail_once):
                with self.assertRaises(OSError):core.install(self.project,archive,item,editor_probe=lambda _:[123],ipc=lambda *a,**k:None)
            self.assertEqual(core.inventory(info['plugin']),old)
            self.assertEqual({p:p.read_bytes() for p in protected},baseline)
            self.install('1.2.0',script='second')
            core.restore_backup(self.project,editor_probe=lambda _:[])
            self.assertEqual(core.inventory(info['plugin']),old)
            self.assertEqual({p:p.read_bytes() for p in protected},baseline)

    def test_native_engine_mismatch_leaves_plugin_unchanged(self):
        self.install()
        project_before=self.project.read_bytes()
        archive,item=self.package('1.2.0',script='new')
        engine=self.root/'Engine';exe=engine/'Binaries/Win64/UnrealEditor.exe'
        exe.parent.mkdir(parents=True);exe.write_bytes(b'editor')
        core.write_json(engine/'Build/Build.version',{'MajorVersion':5,'MinorVersion':8})
        core.write_json(exe.parent/'UnrealEditor.modules',{'BuildId':'installed-build'})
        with zipfile.ZipFile(archive,'a') as z:
            z.writestr('VFXMeshGenerator/Native/Win64/other-build/manifest.json','{}')
        item.update(sha256=core.digest(archive),size=archive.stat().st_size)
        with self.assertRaisesRegex(ValueError,'匹配'):
            core.install(self.project,archive,item,editor_probe=lambda _:[],engine_path=exe)
        self.assertEqual(core.project_info(self.project)['version'],'1.1.0')
        self.assertEqual(self.project.read_bytes(),project_before)
        self.assertEqual((core.project_info(self.project)['plugin']/'Content/Python/runtime.py').read_text(),'first')

    def test_native_engine_fingerprint_is_checked(self):
        archive,item=self.package('1.2.0')
        engine=self.root/'Engine';exe=engine/'Binaries/Win64/UnrealEditor.exe'
        exe.parent.mkdir(parents=True);exe.write_bytes(b'editor')
        core.write_json(engine/'Build/Build.version',{'MajorVersion':5,'MinorVersion':8})
        core.write_json(exe.parent/'UnrealEditor.modules',{'BuildId':'installed-build'})
        (exe.parent/'UnrealEditor-Core.dll').write_bytes(b'changed-engine')
        with zipfile.ZipFile(archive,'a') as z:
            z.writestr('VFXMeshGenerator/Native/Win64/installed-build/manifest.json',json.dumps({'engine_sha256':{'Binaries/Win64/UnrealEditor-Core.dll':'0'*64}}))
        item.update(sha256=core.digest(archive),size=archive.stat().st_size)
        with self.assertRaisesRegex(ValueError,'不匹配'):
            core.install(self.project,archive,item,editor_probe=lambda _:[],engine_path=exe)
        self.assertFalse(core.project_info(self.project)['plugin'].exists())

    def test_feed_selects_newest_product_release(self):
        def release(v,**kwargs):
            return dict(tag_name='vfx-v'+v,assets=[{'name':'vfx-manifest.json','browser_download_url':core.WEB+'/releases/download/vfx-v'+v+'/vfx-manifest.json'}],**kwargs)
        item={'version':'1.10.0','url':core.WEB+'/releases/download/vfx-v1.10.0/package.zip','sha256':'a'*64,'size':12}
        feed={'schema':1,'product':core.PLUGIN,'repository':core.REPOSITORY,'engine':'5.8','plugin':item,'launcher':item}
        with patch.object(core,'fetch_json',side_effect=[[release('1.9.0'),release('9.0.0',draft=True),release('1.10.0')],feed]) as get:
            self.assertEqual(core.check_updates()['plugin']['version'],'1.10.0')
            self.assertIn('vfx-v1.10.0',get.call_args.args[0])
        feed['plugin']=dict(item,sha256='wrong')
        with self.assertRaises(ValueError):core.validate_feed(feed)

    def test_download_rejects_corruption(self):
        item={'url':core.WEB+'/releases/download/vfx-v1.1.0/package.zip','sha256':'a'*64,'size':3}
        with patch.object(core.urllib.request,'urlopen',return_value=io.BytesIO(b'bad')):
            with self.assertRaises(ValueError):core.download(item,self.root/'downloads')
        self.assertEqual(list((self.root/'downloads').iterdir()),[])

    def test_ui_startup_check_and_settings(self):
        import app
        with patch.object(app,'STATE',self.root/'state'),patch.object(app.core,'check_updates',side_effect=OSError('offline')) as check:
            window=app.App();window.withdraw()
            deadline=time.monotonic()+2
            while time.monotonic()<deadline and window.check_time.get()=='尚未检查':window.update();time.sleep(.03)
            self.assertEqual(check.call_count,1);self.assertFalse(window.checking)
            window.auto_check.set(False);window.save_settings();window.destroy()
            second=app.App();second.withdraw();self.assertFalse(second.auto_check.get());second.close()

    def test_launcher_self_update_prepares_verified_version(self):
        import app
        archive=self.root/'launcher.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('VFXMeshLauncher/version.json',json.dumps({'version':'9.0.0'}))
            z.writestr('VFXMeshLauncher/VFX特效网格启动器.exe',b'test-executable')
        item={'version':'9.0.0','sha256':core.digest(archive),'size':archive.stat().st_size}
        with patch.object(app,'STATE',self.root/'self-update'),patch.object(app.core,'download',return_value=archive),patch.object(app.subprocess,'Popen') as launch:
            window=app.App(auto_check=False);window.withdraw();window.feed={'launcher':item}
            window.update_launcher();deadline=time.monotonic()+3
            while not window.closing and time.monotonic()<deadline:window.update();time.sleep(.03)
            self.assertTrue(window.closing);launch.assert_called_once()
            pointer=core.read_json(app.STATE/'preferred-launcher.json')
            self.assertEqual(pointer['version'],'9.0.0');self.assertEqual(pointer['sha256'],core.digest(pointer['exe']))


if __name__=='__main__':unittest.main()
