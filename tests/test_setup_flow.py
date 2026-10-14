import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'Launcher'))
import app
import core
import engine_discovery
import native_builder
import setup_flow
import build_environment
import environment_installer


class SetupFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name).resolve()
        self.base=self.root/'bundle';self.state=self.root/'state'
        core.write_json(self.state/'settings.json',{'auto_check':False,'auto_compat':False})
        self.project=self.root/'project/Test.uproject';core.write_json(self.project,{'FileVersion':3})
        self.engine=self.root/'engine/Engine';self.exe=self.engine/'Binaries/Win64/UnrealEditor.exe'
        self.exe.parent.mkdir(parents=True);self.exe.write_bytes(b'editor')
        self.version=dict(MajorVersion=5,MinorVersion=8,PatchVersion=3,Changelist=0,CompatibleChangelist=0)
        core.write_json(self.engine/'Build/Build.version',self.version)
        core.write_json(self.exe.parent/'UnrealEditor.modules',{'BuildId':'setup-test'})
        dll=self.exe.parent/'UnrealEditor-Core.dll';dll.write_bytes(b'core')
        self.archive=self.base/'offline/plugin.zip';self.archive.parent.mkdir(parents=True)
        with zipfile.ZipFile(self.archive,'w') as z:
            z.writestr(core.PLUGIN+'/'+core.PLUGIN+'.uplugin',json.dumps({'VersionName':'1.5.7'}))
            z.writestr(core.PLUGIN+'/Native/Win64/setup-test/manifest.json',json.dumps({'build_id':'setup-test','engine_version':self.version,'engine_sha256':{'Binaries/Win64/UnrealEditor-Core.dll':core.digest(dll)}}))
        self.item={'version':'1.5.7','file':'plugin.zip','sha256':core.digest(self.archive),'size':self.archive.stat().st_size}
        core.write_json(self.base/'offline/manifest.json',{'plugin':self.item})
        for module,name,value in [(app,'BASE',self.base),(app,'STATE',self.state)]:
            p=patch.object(module,name,value);p.start();self.addCleanup(p.stop)
        self.window=app.App(auto_check=False,initial_project=str(self.project),initial_engine=str(self.exe));self.window.withdraw()
        self.addCleanup(self.cleanup_window)

    def cleanup_window(self):
        if not self.window.closing:
            self.window.busy=False;self.window.close()

    def wait(self,predicate,timeout=4):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            self.window.update()
            if predicate():return
            time.sleep(.01)
        self.fail('Timed out: '+self.window.setup_detail.get())

    def context(self,compatible=True):
        report=core.diagnostic_report(self.exe,self.archive);report['engine']['compatible']=compatible
        return {'engine':str(self.exe),'info':core.project_info(self.project),'archive':self.archive,'item':self.item,
                'report':report,'origin':'offline','discovery':None,'preflight_error':'','cache_warning':'',
                'selection':(str(self.project),str(self.exe))}

    def test_selection_auto_checks_without_installing_and_remembers_per_project(self):
        before=core.inventory(self.project.parent)
        with patch.object(app.messagebox,'askyesno') as ask,patch.object(core,'install') as install:
            self.window.engine.set(str(self.engine.parent));self.wait(lambda:self.window.setup_result is not None)
            self.assertTrue(self.window.setup_result['report']['engine']['compatible']);ask.assert_not_called();install.assert_not_called()
        self.assertEqual(self.window.engine.get(),str(self.exe))
        self.assertEqual(core.read_json(self.state/'settings.json')['project_engines'][engine_discovery.path_key(self.project)],str(self.exe))
        self.assertEqual(before,core.inventory(self.project.parent))

    def test_project_switch_clears_old_engine_and_discards_late_result(self):
        second=self.root/'another/New.uproject';core.write_json(second,{'FileVersion':3})
        gate=threading.Event();started=threading.Event();seen=[]
        def inspect(project,engine,*args,**kwargs):
            seen.append((project,engine))
            if len(seen)==1:started.set();gate.wait(2);return self.context()
            return {'engine':'','info':core.project_info(project),'discovery':{'candidates':[],'selected':None}}
        with patch.object(setup_flow,'inspect_selection',side_effect=inspect):
            self.window.auto_setup();self.assertTrue(started.wait(1))
            self.window.project.set(str(second));self.assertEqual(self.window.engine.get(),'');gate.set()
            self.wait(lambda:len(seen)==2 and not self.window._scan_running)
        self.assertIsNone(self.window.setup_result);self.assertEqual(self.window.engine.get(),'')
        self.assertEqual(self.window.setup_title.get(),'还没有找到引擎')

    def test_compile_then_install_uses_same_selected_package_and_two_clear_confirmations(self):
        context=self.context(False);self.window.setup_result=context
        sentinel=self.project.parent/'Content/keep.uasset';sentinel.parent.mkdir();sentinel.write_bytes(b'keep')
        before={self.project:self.project.read_bytes(),sentinel:sentinel.read_bytes()}
        with patch.object(app.messagebox,'askyesno',return_value=True) as ask,patch.object(native_builder,'build',return_value={'work':'test'}) as build,patch.object(native_builder,'cached',return_value=(self.archive,self.item)),patch.object(core,'running_editors',return_value=[]):
            # core.install's default argument is bound at definition; keep the actual installer but supply an isolated process probe.
            original=core.install
            with patch.object(core,'install',side_effect=lambda *a,**kw:original(*a,**kw,editor_probe=lambda _:[])):
                self.window.continue_setup(context);self.wait(lambda:self.window.setup_title.get()=='安装完成，可以打开工作台')
            self.assertEqual(build.call_args.args[:3],(str(self.exe),self.archive,self.item))
            self.assertEqual([c.args[0] for c in ask.call_args_list],['开始自动编译与验证','确认安装到此工程'])
        self.assertEqual(before,{p:p.read_bytes() for p in before});self.assertEqual(core.project_info(self.project)['version'],'1.5.7')

    def test_declining_install_keeps_verified_result_without_writing_project(self):
        before=core.inventory(self.project.parent);context=self.context(False)
        with patch.object(app.messagebox,'askyesno',side_effect=[True,False]),patch.object(native_builder,'build',return_value={}),patch.object(core,'install') as install:
            self.window.continue_setup(context);self.wait(lambda:self.window.setup_title.get()=='适配已就绪，尚未安装');install.assert_not_called()
        self.assertEqual(before,core.inventory(self.project.parent))

    def test_successful_rebuild_replaces_corrupt_cache_for_immediate_install(self):
        context=self.context(False);context['cache_warning']='旧缓存损坏'
        with patch.object(app.messagebox,'askyesno',return_value=True),patch.object(native_builder,'build',return_value={}),patch.object(native_builder,'cached',return_value=(self.archive,self.item)) as cached,patch.object(core,'install',return_value={'mode':'installed'}):
            self.window.continue_setup(context);self.wait(lambda:self.window.setup_title.get()=='安装完成，可以打开工作台')
            cached.assert_called_once();self.assertEqual(context['cache_warning'],'')

    def test_compile_failure_and_cancel_never_reach_install(self):
        for cancel in (False,True):
            context=self.context(False)
            def build(*args,**kwargs):
                if cancel:
                    kwargs['cancel_event'].set();raise RuntimeError('已取消本次编译')
                raise RuntimeError('compile failed')
            with self.subTest(cancel=cancel),patch.object(app.messagebox,'askyesno',return_value=True),patch.object(native_builder,'build',side_effect=build),patch.object(core,'install') as install:
                self.window.continue_setup(context);self.wait(lambda:not self.window.busy)
                self.assertIn('已取消' if cancel else 'compile failed',self.window.setup_detail.get());install.assert_not_called()
        self.assertFalse(core.project_info(self.project)['plugin'].exists())

    def test_missing_development_files_offers_solution_before_build(self):
        context=self.context(False);context['preflight_error']='缺少 MSVC'
        with patch.object(self.window,'show_setup_solution') as solution,patch.object(native_builder,'build') as build,patch.object(app.messagebox,'askyesno') as ask:
            self.window.continue_setup(context);solution.assert_called_once_with('缺少 MSVC');build.assert_not_called();ask.assert_not_called()

    def environment_context(self):
        context=self.context(False);context['preflight_error']='缺少 MSVC'
        context['environment']={'repair':{'mode':'install','target':None,'components':['Microsoft.VisualStudio.Component.VC.14.44.17.14.x86.x64']}}
        return context

    def test_missing_environment_installs_rechecks_probes_and_continues_to_confirmed_install(self):
        context=self.environment_context();sequence=[]
        def record(name,result):
            def called(*args,**kwargs):sequence.append(name);return result
            return called
        with patch.object(app.messagebox,'askyesno',return_value=True) as ask,patch.object(environment_installer,'install',side_effect=record('environment','recheck')),patch.object(native_builder,'preflight',side_effect=record('preflight',{'environment':{}})),patch.object(build_environment,'probe',side_effect=record('probe',True)),patch.object(native_builder,'build',side_effect=record('build',{})),patch.object(native_builder,'cached',return_value=(self.archive,self.item)),patch.object(core,'install',side_effect=record('install',{'mode':'installed'})):
            self.window.continue_setup(context);self.wait(lambda:self.window.setup_title.get()=='安装完成，可以打开工作台')
        self.assertEqual(sequence,['environment','preflight','probe','build','install'])
        self.assertEqual([c.args[0] for c in ask.call_args_list],['补齐环境并继续编译','确认安装到此工程'])

    def test_declining_environment_install_changes_nothing(self):
        before=core.inventory(self.project.parent)
        with patch.object(app.messagebox,'askyesno',return_value=False),patch.object(environment_installer,'install') as install,patch.object(native_builder,'build') as build:
            self.window.continue_setup(self.environment_context());install.assert_not_called();build.assert_not_called()
        self.assertEqual(before,core.inventory(self.project.parent))

    def test_reboot_required_never_compiles_or_installs(self):
        with patch.object(app.messagebox,'askyesno',return_value=True),patch.object(environment_installer,'install',return_value='restart'),patch.object(native_builder,'build') as build,patch.object(core,'install') as install:
            self.window.continue_setup(self.environment_context());self.wait(lambda:not self.window.busy)
            self.assertEqual(self.window.setup_title.get(),'环境已安装，需要重启电脑');build.assert_not_called();install.assert_not_called()

    def test_success_exit_but_still_missing_environment_does_not_loop_install_or_compile(self):
        with patch.object(app.messagebox,'askyesno',return_value=True),patch.object(environment_installer,'install',return_value='recheck') as repair,patch.object(native_builder,'preflight',side_effect=ValueError('缺少 SDK')),patch.object(native_builder,'build') as build,patch.object(core,'install') as install:
            self.window.continue_setup(self.environment_context());self.wait(lambda:not self.window.busy)
            repair.assert_called_once();build.assert_not_called();install.assert_not_called()
            self.assertIn('缺少 SDK',self.window.setup_detail.get())

    def test_environment_signature_or_download_failure_never_reaches_compile(self):
        with patch.object(app.messagebox,'askyesno',return_value=True),patch.object(environment_installer,'install',side_effect=ValueError('签名校验失败')),patch.object(native_builder,'build') as build,patch.object(core,'install') as install:
            self.window.continue_setup(self.environment_context());self.wait(lambda:not self.window.busy)
            self.assertIn('签名校验失败',self.window.setup_detail.get());build.assert_not_called();install.assert_not_called()

    def test_changed_target_cannot_use_previous_install_context(self):
        context=self.context();other=self.root/'another.uproject';core.write_json(other,{})
        self.window.project.set(str(other))
        with patch.object(app.messagebox,'askyesno') as ask,patch.object(core,'install') as install:
            self.window.confirm_setup_install(context);ask.assert_not_called();install.assert_not_called()

    def test_failed_install_can_retry_without_recompiling(self):
        context=self.context();self.window.accept_inspection(context)
        with patch.object(app.messagebox,'askyesno',return_value=True),patch.object(native_builder,'cached',return_value=(self.archive,self.item)),patch.object(core,'install',side_effect=[ValueError('请保存并关闭工程'),{'mode':'installed'}]) as install,patch.object(native_builder,'build') as build:
            self.window.confirm_setup_install(context);self.wait(lambda:not self.window.busy)
            self.assertIn('无需重新编译',self.window.setup_detail.get())
            self.window.confirm_setup_install(context);self.wait(lambda:self.window.setup_title.get()=='安装完成，可以打开工作台')
            self.assertEqual(install.call_count,2);build.assert_not_called()

    def test_missing_old_engine_on_startup_does_not_override_new_project(self):
        self.window.close();core.write_json(self.state/'settings.json',{'project':'old.uproject','engine':str(self.exe),'auto_compat':False})
        self.window=app.App(initial_project=str(self.project),auto_check=False);self.window.withdraw()
        self.assertEqual(self.window.engine.get(),'')


if __name__=='__main__':unittest.main()
