"""Engine selection survives metadata errors; UE descriptors stay read-only."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'Launcher'))
import core
import native_profiles


class EngineInputTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.engine=self.root/'中文 UE/Engine'
        self.exe=self.engine/'Binaries/Win64/UnrealEditor.exe'
        self.exe.parent.mkdir(parents=True);self.exe.write_bytes(b'test executable')
        self.version={'MajorVersion':5,'MinorVersion':8,'PatchVersion':3,'Changelist':0,'CompatibleChangelist':55116800}
        core.write_json(self.engine/'Build/Build.version',self.version)
        core.write_json(self.exe.parent/'UnrealEditor.modules',{'BuildId':'test-engine'})
        self.dll=self.exe.parent/'UnrealEditor-Core.dll';self.dll.write_bytes(b'engine module')
        self.base=self.root/'bundle';(self.base/'offline').mkdir(parents=True)
        self.archive=self.base/'offline/plugin.zip'
        self.profile={'build_id':'test-engine','engine_version':self.version,'engine_sha256':{'Binaries/Win64/UnrealEditor-Core.dll':core.digest(self.dll)}}
        with zipfile.ZipFile(self.archive,'w') as z:
            z.writestr('VFXMeshGenerator/VFXMeshGenerator.uplugin',json.dumps({'VersionName':'1.5.6'}))
            z.writestr('VFXMeshGenerator/Native/Win64/test-engine/manifest.json',json.dumps(self.profile))
        core.write_json(self.base/'offline/manifest.json',{'plugin':{'version':'1.5.6','file':'plugin.zip','sha256':core.digest(self.archive),'size':self.archive.stat().st_size}})
        self.state=self.root/'state';core.write_json(self.state/'settings.json',{'auto_compat':False})

    def window(self,engine=None):
        import app
        for field,value in (('BASE',self.base),('STATE',self.state)):
            context=patch.object(app,field,value);context.start();self.addCleanup(context.stop)
        window=app.App(auto_check=False,initial_engine=str(engine if engine is not None else self.exe));window.withdraw()
        self.addCleanup(lambda:window.close() if not window.closing else None)
        return window

    def test_copy_as_path_spaces_environment_and_engine_directories(self):
        before=core.inventory(self.engine)
        with patch.dict(os.environ,{'VFX_TEST_ENGINE':str(self.engine)}):
            for value in ('  "'+str(self.exe)+'"  '," '"+str(self.exe)+"' ",self.engine,self.engine.parent,self.exe.parent,'%VFX_TEST_ENGINE%' if os.name=='nt' else '$VFX_TEST_ENGINE'):
                with self.subTest(value=value):self.assertEqual(core.validate_engine(value),self.exe.resolve())
        self.assertEqual(before,core.inventory(self.engine))

    def test_absent_path_differs_from_selected_unavailable_path(self):
        with self.assertRaisesRegex(ValueError,'尚未选择引擎'):core.validate_engine('   ')
        missing=self.root/'other-drive/UnrealEditor.exe'
        with self.assertRaises(ValueError) as result:core.validate_engine(missing)
        self.assertIn(str(missing),str(result.exception));self.assertNotIn('尚未选择',str(result.exception))

    def test_ue_comments_trailing_commas_and_escaped_strings_read_without_writes(self):
        for name in ('Project.uproject','Plugin.uplugin','UnrealEditor.modules','Build.version'):
            file=self.root/name
            value={'url':'https://example.test/a//b/*c*/','quote':'a\\"b','array':[1,{'value':2}]}
            source='\ufeff{\n// valid UE comment\n'+json.dumps(value,ensure_ascii=False)[1:-1]+', /* trailing comment */\n}'
            file.write_text(source,encoding='utf-8');before=file.read_bytes()
            self.assertEqual(core.read_json(file),value);self.assertEqual(file.read_bytes(),before)
        file=self.root/'Nested.uproject';file.write_text('{"items":[1,{"value":2,},],}',encoding='utf-8')
        self.assertEqual(core.read_json(file),{'items':[1,{'value':2}]})

    def test_invalid_descriptors_fail_with_file_and_original_line(self):
        file=self.engine/'Build/Build.version'
        for text in ('{\nINVALID\n}',"{\n'key':1\n}",'{\n/* unclosed', '{"items":[,]}','{\n<<<<<<< HEAD\n}'):
            with self.subTest(text=text):
                file.write_text(text,encoding='utf-8');before=file.read_bytes()
                with self.assertRaises(ValueError) as result:core.validate_engine(self.exe)
                self.assertIn(str(file),str(result.exception));self.assertIn('行',str(result.exception))
                self.assertNotIn('请选择引擎',str(result.exception));self.assertEqual(file.read_bytes(),before)
        file.write_text('[]',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'版本信息'):core.validate_engine(self.exe)

    def test_manifests_and_receipts_remain_strict(self):
        for name in ('manifest.json','receipt.json'):
            file=self.root/name
            for data in ('{\n// comment\n"schema":1}', '{"schema":1,}'):
                file.write_text(data,encoding='utf-8')
                with self.assertRaisesRegex(ValueError,name):core.read_json(file)

    def test_detect_selected_engine_after_browse_and_directory_entry(self):
        import app
        window=self.window('')
        with patch.object(app.filedialog,'askopenfilename',return_value=str(self.exe)),patch.object(app.messagebox,'showerror') as error:
            window.browse_engine();self.assertEqual(window.engine.get(),str(self.exe.resolve()))
            window.engine.set(' "'+str(self.engine.parent)+'" ')
            before=core.inventory(self.engine);window.check_compatibility()
            deadline=time.monotonic()+4
            while window.busy and time.monotonic()<deadline:window.update();time.sleep(.01)
            self.assertFalse(window.busy);error.assert_not_called()
            self.assertEqual(window.engine.get(),str(self.exe.resolve()))
            self.assertEqual(window.status.get(),'兼容性检测通过。')
            self.assertEqual(core.read_json(self.state/'settings.json')['engine'],str(self.exe.resolve()))
            self.assertEqual(before,core.inventory(self.engine))

    def test_browse_keeps_engine_and_logs_before_metadata_error_dialog(self):
        import app
        window=self.window('');file=self.engine/'Build/Build.version';file.write_text('{\nINVALID\n}',encoding='utf-8')
        def shown(title,message,**kwargs):
            self.assertEqual(title,'引擎信息读取失败');self.assertIn(str(file),message);self.assertIn('第 2 行',message)
            self.assertIn(str(file),(self.state/'launcher.log').read_text(encoding='utf-8'))
        with patch.object(app.filedialog,'askopenfilename',return_value=str(self.exe)),patch.object(app.messagebox,'showerror',side_effect=shown) as error:
            window.browse_engine();error.assert_called_once()
        self.assertEqual(window.engine.get(),str(self.exe));self.assertEqual(core.read_json(self.state/'settings.json')['engine'],str(self.exe))

    def test_invalid_package_is_not_reported_as_missing_engine(self):
        import app
        window=self.window();file=self.base/'offline/manifest.json';file.write_text('{\nINVALID\n}',encoding='utf-8')
        with patch.object(app.messagebox,'showerror') as error,patch.object(window,'worker') as worker:
            window.check_compatibility();self.assertEqual(error.call_args.args[0],'兼容性检测未完成')
            self.assertIn(str(file),error.call_args.args[1]);worker.assert_not_called()
            error.reset_mock();window.compile_adapter();self.assertEqual(error.call_args.args[0],'编译准备未完成')
            self.assertIn(str(file),error.call_args.args[1]);worker.assert_not_called()
        self.assertEqual(window.engine.get(),str(self.exe.resolve()))

    def test_modules_error_identifies_file_and_mismatch_stays_blocked(self):
        modules=self.exe.parent/'UnrealEditor.modules';modules.write_text('{\ninvalid\n}',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'UnrealEditor.modules'):core.diagnostic_report(self.exe,self.archive)
        modules.write_text('{\n// UE descriptor\n"BuildId":"different-build",\n}',encoding='utf-8')
        report=core.diagnostic_report(self.exe,self.archive);self.assertFalse(report['engine']['compatible'])
        self.assertEqual(report['engine']['reason'],'missing_adapter')
        core.write_json(modules,{'BuildId':'test-engine'});self.dll.write_bytes(b'changed')
        self.assertFalse(core.diagnostic_report(self.exe,self.archive)['engine']['compatible'])

    def test_async_failure_is_logged_while_dialog_is_still_open(self):
        import app
        window=self.window()
        window.after_cancel(window._pump_after);window._pump_after=None
        def shown(*args,**kwargs):self.assertIn('trace already saved',(self.state/'launcher.log').read_text(encoding='utf-8'))
        with patch.object(app.messagebox,'showerror',side_effect=shown) as error:
            window.events.put(('error','JSON failed','trace already saved',True));window.pump();error.assert_called_once()


if __name__=='__main__':unittest.main()
