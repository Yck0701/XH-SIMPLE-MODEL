import hashlib
import http.client
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'Launcher'))
import core


class UpdateNetworkTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.data=b'verified archive'
        self.item={'version':'1.5.3','url':core.WEB+'/releases/download/vfx-v1.5.3/package.zip',
                   'sha256':hashlib.sha256(self.data).hexdigest(),'size':len(self.data)}
        self.feed={'schema':1,'product':core.PLUGIN,'repository':core.REPOSITORY,'engine':'5.8',
                   'plugin':self.item,'launcher':self.item}

    def test_api_rate_limit_uses_repository_feed(self):
        error=urllib.error.HTTPError(core.API,403,'rate limit',{},None)
        with patch.object(core,'fetch_json',side_effect=[error,self.feed]) as get:
            self.assertEqual(core.check_updates(),self.feed)
            self.assertEqual(get.call_args.args[0],core.STATIC_FEED)

    def test_latest_release_fallback_and_invalid_metadata(self):
        with patch.object(core,'fetch_json',side_effect=[OSError('offline'),{'schema':9},self.feed]):
            self.assertEqual(core.check_updates(),self.feed)
        with patch.object(core,'fetch_json',side_effect=OSError('proxy user:password@host')):
            with self.assertRaises(RuntimeError) as error:core.check_updates()
            self.assertIn('自动检查更新失败',str(error.exception))
            self.assertNotIn('password',str(error.exception))

    def test_transient_json_request_is_retried(self):
        with patch.object(core.urllib.request,'urlopen',side_effect=[urllib.error.URLError(socket.timeout()),io.BytesIO(json.dumps(self.feed).encode())]),patch.object(core.time,'sleep'):
            self.assertEqual(core.fetch_json(core.STATIC_FEED),self.feed)

    def test_blocked_release_download_uses_exact_asset_api(self):
        release={'tag_name':'vfx-v1.5.3','assets':[{'id':123,'name':'package.zip','browser_download_url':self.item['url']}]}
        with patch.object(core,'fetch_json',return_value=release),patch.object(core.urllib.request,'urlopen',side_effect=[urllib.error.HTTPError(self.item['url'],403,'blocked',{},None),io.BytesIO(self.data)]) as get:
            path=core.download(self.item,self.root)
            self.assertEqual(path.read_bytes(),self.data)
            self.assertEqual(get.call_args.args[0].full_url,core.API+'/releases/assets/123')
        self.assertFalse(list(self.root.glob('*.part')))

    def test_partial_download_retry_truncates_previous_bytes(self):
        class Broken(io.BytesIO):
            def read(self,size=-1):
                if self.tell():raise http.client.IncompleteRead(b'partial')
                return super().read(3)
        with patch.object(core.urllib.request,'urlopen',side_effect=[Broken(self.data),io.BytesIO(self.data)]),patch.object(core.time,'sleep'):
            self.assertEqual(core.download(self.item,self.root).read_bytes(),self.data)

    def test_all_routes_fail_cleanly_and_credentials_are_redacted(self):
        with patch.object(core.urllib.request,'urlopen',side_effect=urllib.error.URLError('secret-proxy')),patch.object(core,'fetch_json',side_effect=OSError('secret-proxy')),patch.object(core.time,'sleep'):
            with self.assertRaises(RuntimeError) as error:core.download(self.item,self.root)
            self.assertNotIn('secret-proxy',str(error.exception))
            self.assertIn('安装内置版本',str(error.exception))
        self.assertEqual(list(self.root.iterdir()),[])

    def test_different_asset_or_repository_is_rejected(self):
        release={'tag_name':'vfx-v1.5.3','assets':[{'id':123,'name':'package.zip','browser_download_url':'https://example.com/package.zip'}]}
        with patch.object(core,'fetch_json',return_value=release):
            with self.assertRaises(ValueError):core.release_asset_api(self.item['url'])

    def test_corrupt_cache_is_redownloaded_and_bad_data_never_installed(self):
        path=self.root/(self.item['sha256']+'.zip');path.write_bytes(b'bad')
        with patch.object(core.urllib.request,'urlopen',return_value=io.BytesIO(self.data)):
            self.assertEqual(core.download(self.item,self.root).read_bytes(),self.data)
        other=dict(self.item,sha256='0'*64)
        with patch.object(core.urllib.request,'urlopen',return_value=io.BytesIO(self.data)),patch.object(core,'release_asset_api') as api:
            with self.assertRaises(ValueError):core.download(other,self.root)
            api.assert_not_called()
        self.assertFalse(list(self.root.glob('*.part')))


class CompatibilityReportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.engine=self.root/'公司引擎/Engine';self.exe=self.engine/'Binaries/Win64/UnrealEditor.exe'
        self.exe.parent.mkdir(parents=True);self.exe.write_bytes(b'editor')
        core.write_json(self.engine/'Build/Build.version',{'MajorVersion':5,'MinorVersion':8,'PatchVersion':3})
        core.write_json(self.exe.parent/'UnrealEditor.modules',{'BuildId':'test-build'})
        self.dll=self.exe.parent/'UnrealEditor-Core.dll';self.dll.write_bytes(b'known')
        self.native=self.root/'Native'
        core.write_json(self.native/'test-build/manifest.json',{'engine_sha256':{'Binaries/Win64/UnrealEditor-Core.dll':core.digest(self.dll)}})

    def test_relocated_engine_is_compatible_without_writes(self):
        import shutil
        target=self.root/'OtherDrive/UE Engine';shutil.copytree(self.engine,target)
        before=core.inventory(self.root)
        a=core.native_compatibility(self.exe,self.native)
        b=core.native_compatibility(target/'Binaries/Win64/UnrealEditor.exe',self.native)
        self.assertTrue(a['compatible']);self.assertEqual(a,b)
        self.assertEqual(core.inventory(self.root),before)

    def test_changed_missing_and_unsupported_builds_have_solutions(self):
        self.dll.write_bytes(b'other build')
        report=core.native_compatibility(self.exe,self.native)
        self.assertFalse(report['compatible']);self.assertEqual(report['files'][0]['status'],'different')
        self.assertIn('UnrealEditor-Core.dll',core.compatibility_message(report))
        self.assertIn('同一完整构建',core.compatibility_solution(report))
        self.dll.unlink();report=core.native_compatibility(self.exe,self.native)
        self.assertEqual(report['files'][0]['status'],'missing');self.assertIn('补齐',core.compatibility_solution(report))
        core.write_json(self.exe.parent/'UnrealEditor.modules',{'BuildId':'unknown'})
        report=core.native_compatibility(self.exe,self.native)
        self.assertEqual(report['reason'],'missing_adapter');self.assertIn('编译并验证',core.compatibility_solution(report))

    def test_report_contains_no_private_paths_and_only_reads_engine(self):
        archive=self.root/'plugin.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr(core.PLUGIN+'/'+core.PLUGIN+'.uplugin',json.dumps({'VersionName':'1.5.3'}))
            z.writestr(core.PLUGIN+'/Native/Win64/test-build/manifest.json',(self.native/'test-build/manifest.json').read_bytes())
        before=core.inventory(self.root);report=core.diagnostic_report(self.exe,archive)
        self.assertTrue(report['engine']['compatible'])
        self.assertNotIn(str(self.root),json.dumps(report));self.assertEqual(core.inventory(self.root),before)

    def test_manifest_cannot_read_outside_engine(self):
        core.write_json(self.native/'test-build/manifest.json',{'engine_sha256':{'../../secret':'a'*64}})
        with self.assertRaisesRegex(ValueError,'路径无效'):core.native_compatibility(self.exe,self.native)


if __name__=='__main__':unittest.main()
