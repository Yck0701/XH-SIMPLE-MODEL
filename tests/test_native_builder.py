import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'Launcher'))
import core
import native_builder as builder
import native_profiles


class NativeBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.engine=self.root/'引擎 with spaces/Engine';self.exe=self.engine/'Binaries/Win64/UnrealEditor.exe'
        self.exe.parent.mkdir(parents=True);self.exe.write_bytes(b'editor')
        self.version=dict(MajorVersion=5,MinorVersion=8,PatchVersion=2,Changelist=56702186,CompatibleChangelist=55116800)
        core.write_json(self.engine/'Build/Build.version',self.version)
        core.write_json(self.exe.parent/'UnrealEditor.modules',{'BuildId':'55116800'})
        for name in builder.MODULES:(self.exe.parent/('UnrealEditor-'+name+'.dll')).write_bytes(name.encode())
        self.native=self.root/'Native';self.snapshot=builder.fingerprint(self.engine)
        self.archive=self.root/'source.zip'
        with zipfile.ZipFile(self.archive,'w') as z:
            z.writestr(core.PLUGIN+'/'+core.PLUGIN+'.uplugin',json.dumps({'VersionName':'1.5.4'}))
            z.writestr(core.PLUGIN+'/Native/Win64/55116800/manifest.json',json.dumps(dict(self.snapshot,engine_version=dict(self.version,PatchVersion=3))))
        self.item={'version':'1.5.4','sha256':core.digest(self.archive),'size':self.archive.stat().st_size}
        self.payload=self.root/'compiler';self.payload.mkdir()
        shutil.copytree(Path(__file__).resolve().parents[1]/'scripts/CompilerHost',self.payload/'CompilerHost')
        shutil.copytree(Path(__file__).resolve().parents[1]/'scripts/RulesHelper',self.payload/'RulesHelper')
        (self.payload/'verify_native.py').write_text('test')
        core.write_json(self.payload/'manifest.json',{'files':{p.relative_to(self.payload).as_posix():core.digest(p) for p in self.payload.rglob('*') if p.is_file()}})
        self.cache=self.root/'cache'

    def profile(self,name,**overrides):
        core.write_json(self.native/name/'manifest.json',dict(self.snapshot,**overrides))

    def test_shared_build_id_selects_exact_version_and_hash(self):
        self.profile('55116800',engine_version=dict(self.version,PatchVersion=3))
        self.assertEqual(native_profiles.inspect(self.engine,self.native)['reason'],'engine_version_mismatch')
        self.profile('55116800/variants/patch2')
        r=native_profiles.inspect(self.engine,self.native)
        self.assertTrue(r['compatible']);self.assertEqual(r['profile'],'55116800/variants/patch2')
        (self.exe.parent/'UnrealEditor-Core.dll').write_bytes(b'changed')
        self.assertFalse(native_profiles.inspect(self.engine,self.native)['compatible'])

    def test_shared_selector_is_identical(self):
        root=Path(__file__).resolve().parents[1]
        self.assertEqual((root/'Launcher/native_profiles.py').read_bytes(),(root/'Plugin/VFXMeshGenerator/Content/Python/vfx_mesh/native_profiles.py').read_bytes())

    def test_startup_detects_selected_engine_and_requires_compile_confirmation(self):
        import app,time
        base=self.root/'bundle';(base/'offline').mkdir(parents=True)
        shutil.copy2(self.archive,base/'offline/plugin.zip');core.write_json(base/'offline/manifest.json',{'plugin':dict(self.item,file='plugin.zip')})
        report={'engine':{'compatible':False,'build_id':'55116800','engine_version':self.version}}
        for answer in (False,True):
            with self.subTest(answer=answer),patch.object(app,'BASE',base),patch.object(app,'STATE',self.root/'state'),patch.object(builder,'diagnostic',return_value=report) as detect,patch.object(app.messagebox,'askyesno',return_value=answer) as ask,patch.object(app.App,'compile_adapter') as compile_adapter:
                window=app.App(auto_check=False,initial_engine=str(self.exe));window.withdraw()
                deadline=time.monotonic()+3
                while not ask.called and time.monotonic()<deadline:window.update();time.sleep(.02)
                self.assertEqual(detect.call_args.args[0],str(self.exe.resolve()))
                self.assertIn('5.8.2',ask.call_args.args[1]);self.assertIn('55116800',ask.call_args.args[1])
                if answer:compile_adapter.assert_called_once_with(confirmed=True)
                else:compile_adapter.assert_not_called()
                window.close()

    def test_startup_controls_remain_visible_at_minimum_size(self):
        import app
        with patch.object(app,'STATE',self.root/'state'):
            window=app.App(auto_check=False);window.geometry('980x760');window.update()
            def children(parent):
                for child in parent.winfo_children():yield child;yield from children(child)
            buttons=[w for w in children(window.pages['home']) if isinstance(w,app.ttk.Button)]
            window.content_canvas.yview_moveto(1);window.update()
            button=next(b for b in buttons if str(b.cget('text'))=='为当前引擎编译适配…')
            self.assertTrue(window.status_label.winfo_viewable())
            self.assertGreaterEqual(button.winfo_rooty(),window.content_canvas.winfo_rooty())
            self.assertLessEqual(button.winfo_rooty()+button.winfo_height(),window.status_label.winfo_rooty())
            window.close()

    def test_cache_ignores_drive_and_rejects_new_source_engine_or_corruption(self):
        folder=self.cache/builder.key(self.snapshot,self.item['sha256']);folder.mkdir(parents=True)
        shutil.copy2(self.archive,folder/'plugin.zip')
        receipt={'schema':1,'source_sha256':self.item['sha256'],'engine':self.snapshot,'plugin':self.item,'verification':{'passed':True}}
        core.write_json(folder/'receipt.json',receipt)
        relocated=self.root/'other/Engine';shutil.copytree(self.engine,relocated)
        self.assertIsNotNone(builder.cached(relocated/'Binaries/Win64/UnrealEditor.exe',self.item,self.cache))
        self.assertIsNone(builder.cached(self.exe,dict(self.item,sha256='0'*64),self.cache))
        receipt['verification']['passed']=False;core.write_json(folder/'receipt.json',receipt)
        with self.assertRaisesRegex(ValueError,'未验证'):builder.cached(self.exe,self.item,self.cache)
        receipt['verification']['passed']=True;core.write_json(folder/'receipt.json',receipt)
        (folder/'plugin.zip').write_bytes(b'corrupted')
        with self.assertRaisesRegex(ValueError,'缓存校验失败'):builder.cached(self.exe,self.item,self.cache)

    def test_preflight_missing_sdk_and_payload_tampering(self):
        self.assertTrue(builder.validate_payload(self.payload))
        with self.assertRaisesRegex(ValueError,'缺少开发文件'):builder.preflight(self.exe,self.payload)
        (self.payload/'verify_native.py').write_text('tampered')
        with self.assertRaisesRegex(ValueError,'校验失败'):builder.validate_payload(self.payload)

    def test_rules_repair_is_confined_to_cache_and_unrelated_errors_do_not_retry(self):
        rules=self.engine/'Intermediate/Build/BuildRules';core.write_json(rules/'UE5RulesManifest.json',{'keep':True})
        before=core.inventory(rules);work=self.root/'repair';work.mkdir()
        core.write_json(self.engine/'Binaries/DotNET/UnrealBuildTool/UnrealBuildTool.runtimeconfig.json',{'runtimeOptions':{'framework':{'version':'10.0.0'}}})
        calls=[]
        def process(args,cwd,log,timeout,env=None):
            calls.append(args)
            if Path(log).name=='configure.log':
                Path(log).write_text("Expecting to find a type in a module rules named 'DerivedDataCache'");raise RuntimeError('rules error')
        config={'engine':self.engine,'dotnet':self.root/'dotnet.exe'}
        (self.root/'sdk/10.0.203').mkdir(parents=True)
        with patch.object(builder,'run_process',side_effect=process):
            self.assertTrue(builder.configure(config,['-SkipBuild','-NoEngineChanges'],work,self.payload,{},lambda _:None))
        self.assertEqual(len(calls),3);self.assertIn(str(work/'rules-cache'),[str(a) for a in calls[-1]])
        self.assertIn('<clear />',(work/'rules-helper/NuGet.Config').read_text())
        self.assertEqual(core.inventory(rules),before)
        def other(*args,**kwargs):
            (work/'configure.log').write_text('Windows SDK not found');raise RuntimeError('SDK error')
        with patch.object(builder,'run_process',side_effect=other) as run:
            with self.assertRaisesRegex(RuntimeError,'SDK error'):builder.configure(config,[],work,self.payload,{},lambda _:None)
            self.assertEqual(run.call_count,1)

    def simulate(self,failure=None):
        config={'engine':self.engine,'dotnet':self.root/'dotnet','libraries':[],'version':'test engine'}
        def process(args,cwd,log,timeout,env=None):
            work=Path(log).parent
            if Path(log).name=='configure.log':
                if failure=='compile':Path(log).write_text('compiler unavailable');raise RuntimeError('compiler unavailable')
                target=work/'host/Plugins/VFXPreviewBuild/Intermediate'
                target.mkdir();(target/'VFXPreviewBuild.Shared.rsp').write_text('')
                (target/'Definitions.h').write_text('')
            elif Path(log).name=='compile.log':
                (work/'VFXNativePreview.dll').write_bytes(b'candidate')
                if failure=='changed':(self.exe.parent/'UnrealEditor-Core.dll').write_bytes(b'new-engine')
            else:
                if failure=='crash':raise RuntimeError('editor crashed')
                data=core.read_json(work/'verify/verify-config.json')
                core.write_json(work/'verify/result.json',dict(data,passed=failure!='verify'))
        with patch.object(builder,'preflight',return_value=config),patch.object(builder,'run_process',side_effect=process),patch.object(builder,'compiler_arguments',return_value=(self.root/'cl.exe',[])):
            return builder.build(self.exe,self.archive,self.item,self.payload,self.cache)

    def test_failures_never_publish_installable_adapter(self):
        for failure in ('compile','crash','verify','changed'):
            with self.subTest(failure=failure):
                with self.assertRaises(RuntimeError):self.simulate(failure)
                self.assertFalse(list(self.cache.glob('*/receipt.json')))
                self.assertIsNone(builder.cached(self.exe,self.item,self.cache))

    def test_verified_overlay_preserves_shipped_profiles_and_other_project_files(self):
        protected=self.root/'OtherProject/Content/keep.uasset';protected.parent.mkdir(parents=True);protected.write_bytes(b'unchanged')
        self.simulate();local=builder.cached(self.exe,self.item,self.cache);self.assertIsNotNone(local)
        dest=core.safe_extract(local[0],self.root/'extracted',core.PLUGIN)
        self.assertEqual(len(native_profiles.manifests(dest/'Native/Win64')),2)
        self.assertTrue(native_profiles.inspect(self.engine,dest/'Native/Win64')['compatible'])
        project=self.root/'Target/Test.uproject';core.write_json(project,{'FileVersion':3})
        keep=project.parent/'Config/keep.ini';keep.parent.mkdir();keep.write_bytes(b'unchanged')
        before={p:p.read_bytes() for p in (protected,project,keep)}
        core.install(project,*local,engine_path=self.exe,editor_probe=lambda _:[])
        self.assertEqual(before,{p:p.read_bytes() for p in before})
        self.assertEqual(core.digest(self.archive),self.item['sha256'])


if __name__=='__main__':unittest.main()
