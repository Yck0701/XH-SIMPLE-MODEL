from pathlib import Path
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Launcher'))
import core
import build_environment as env
import environment_installer as installer

POLICY = {'MinimumVisualCppVersion': '14.38.33130', 'MinVersion': '10.0.19041.0', 'MaxVersion': '10.9.99999.0',
          'MainVersion': '10.0.22621.0', 'PreferredVisualCppVersions': ['14.44.35207-14.44.99999'],
          'BannedVisualCppVersions': ['14.44.0-14.44.35210', '14.39.0-14.43.99999']}


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.root = Path(self.temp.name)
        self.engine = self.root / 'Engine'
        core.write_json(self.engine / 'Config/Windows/Windows_SDK.json', POLICY)
        self.instance = {'installationPath': str(self.root / 'VS'), 'installationVersion': '17.14.1',
                         'isComplete': True, 'isLaunchable': True, 'productId': 'Microsoft.VisualStudio.Product.BuildTools'}
        self.tools = self.root / 'VS/VC/Tools/MSVC/14.44.35207'
        for name in ('bin/Hostx64/x64/cl.exe', 'bin/Hostx64/x64/link.exe', 'include/vector', 'lib/x64/msvcrt.lib', 'lib/x64/msvcprt.lib'):
            self.touch(self.tools / name)
        self.sdk = self.root / 'SDK'; self.sdk_version = '10.0.22621.0'
        for name in ('Include/{v}/um/Windows.h', 'Include/{v}/ucrt/stdio.h', 'Include/{v}/shared/sdkddkver.h',
                     'Lib/{v}/um/x64/kernel32.lib', 'Lib/{v}/ucrt/x64/ucrt.lib', 'bin/{v}/x64/rc.exe'):
            self.touch(self.sdk / name.format(v=self.sdk_version))

    def touch(self, path):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'fixture')

    def test_actual_servicing_version_not_family_determines_ban(self):
        with patch.object(env, 'compiler_version', return_value='14.44.35211'):
            report = env.toolchains(POLICY, [self.instance]); self.assertEqual(len(report), 1)
            self.assertEqual(report[0]['family'], '14.44.35207')
        with patch.object(env, 'compiler_version', return_value='14.44.35210'):
            self.assertEqual(env.toolchains(POLICY, [self.instance]), [])
        self.assertFalse(env.supported_msvc('14.42.34433', POLICY))

    def test_incomplete_install_and_missing_linker_are_rejected(self):
        with patch.object(env, 'compiler_version', return_value='14.44.35211'):
            self.assertEqual(env.toolchains(POLICY, [dict(self.instance, isComplete=False)]), [])
            (self.tools / 'bin/Hostx64/x64/link.exe').unlink()
            self.assertEqual(env.toolchains(POLICY, [self.instance]), [])

    def test_sdk_requires_supported_version_and_all_components(self):
        self.assertEqual(len(env.sdks(POLICY, [self.sdk])), 1)
        self.assertEqual(env.sdks(dict(POLICY, MinVersion='10.0.26100.0'), [self.sdk]), [])
        (self.sdk / f'Include/{self.sdk_version}/shared/sdkddkver.h').unlink()
        self.assertEqual(env.sdks(POLICY, [self.sdk]), [])

    def test_ready_does_not_plan_reinstallation_and_does_not_write_engine(self):
        before = core.inventory(self.engine)
        with patch.object(env, 'vs_instances', return_value=[self.instance]), patch.object(env, 'compiler_version', return_value='14.44.35211'), patch.object(env, 'sdk_roots', return_value=[self.sdk]):
            r = env.inspect(self.engine)
        self.assertTrue(r['ready']); self.assertIsNone(r['repair'])
        self.assertEqual(env.ubt_arguments(r), ['-Compiler=VisualStudio2022', '-CompilerVersion=14.44.35211', '-WindowsSDKVersion=10.0.22621.0'])
        self.assertEqual(before, core.inventory(self.engine))

    def test_plan_adds_only_missing_components_and_no_guessed_unsupported_versions(self):
        p = env.repair_plan(POLICY, ['sdk'], [self.instance])
        self.assertEqual(p['mode'], 'modify'); self.assertEqual(len(p['components']), 1)
        self.assertIn('Windows11SDK.22621', p['components'][0])
        self.assertEqual(env.repair_plan(POLICY, ['msvc', 'sdk'], [])['mode'], 'install')
        self.assertIsNone(env.repair_plan(dict(POLICY, MinimumVisualCppVersion='14.60.0'), ['msvc'], []))
        self.assertIsNone(env.repair_plan(dict(POLICY, MinVersion='11.0.0', MaxVersion='11.1.0'), ['sdk'], []))

    def test_old_visual_studio_updates_before_adding_newer_compiler(self):
        p = env.repair_plan(POLICY, ['msvc'], [dict(self.instance, installationVersion='17.8.1')])
        self.assertTrue(p['update_first'])
        args = installer.arguments(p, update=True)
        self.assertEqual(args[0], 'update'); self.assertNotIn('--add', args)
        self.assertEqual(installer.arguments(p)[0], 'modify')
        self.assertIsNone(env.repair_plan(POLICY, ['msvc'], [dict(self.instance, channelId='Company.Managed.Layout')]))

    def test_signature_check_uses_windows_modules_and_handles_null_signer(self):
        path=self.root/'installer.exe';path.write_bytes(b'fake')
        result=subprocess.CompletedProcess([],0,b'{"status":"Valid","subject":"O=Microsoft Corporation"}',b'')
        with patch.object(installer.subprocess,'run',return_value=result) as run,patch.dict(installer.os.environ,{'PSModulePath':'PowerShell7/Modules'}):
            self.assertEqual(installer.verify_signature(path),core.digest(path))
            self.assertIn('WindowsPowerShell',run.call_args.kwargs['env']['PSModulePath'])
            self.assertNotIn('PowerShell7',run.call_args.kwargs['env']['PSModulePath'])
        with patch.object(installer.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'{"status":"Valid","subject":null}',b'')):
            with self.assertRaisesRegex(ValueError,'数字签名'):installer.verify_signature(path)

    def test_real_probe_failure_cannot_be_mistaken_for_ready(self):
        report = {'toolchain': {'root': str(self.tools)}, 'sdk': {'root': str(self.sdk), 'version': self.sdk_version}}
        with patch.object(env.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'failed', b'')):
            with self.assertRaisesRegex(ValueError, '试编译未通过'):
                env.probe(report, self.root / 'probe')
        self.assertTrue((self.root / 'probe/environment-probe.log').is_file())
        self.assertFalse(list((self.root / 'probe').glob('toolchain-*')))

    def test_installer_arguments_preserve_user_license_and_restart_decisions(self):
        plan = env.repair_plan(POLICY, ['sdk'], [self.instance]); args = installer.arguments(plan)
        self.assertIn('--wait', args); self.assertIn('modify', args)
        for forbidden in ('--quiet', '--passive', '--force', '--remove', '--all', '--includeRecommended'):
            self.assertNotIn(forbidden, args)
        self.assertEqual(args[args.index('--installPath') + 1], self.instance['installationPath'])
        with self.assertRaises(ValueError):installer.arguments(dict(plan, components=['untrusted.component']))

    def test_signature_blocks_non_microsoft_and_invalid_signatures(self):
        path = self.root / 'installer.exe'; path.write_bytes(b'fake')
        for status, subject in [('UnknownError', 'O=Microsoft Corporation'), ('Valid', 'O=Other Company'), ('Valid', 'O=Microsoft Corporation Fake')]:
            result = subprocess.CompletedProcess([], 0, json.dumps({'status': status, 'subject': subject}).encode(), b'')
            with patch.object(installer.subprocess, 'run', return_value=result), self.assertRaises(ValueError):
                installer.verify_signature(path)

    def test_official_https_redirect_allowlist(self):
        self.assertTrue(installer.allowed_url(installer.URL))
        self.assertTrue(installer.allowed_url('https://download.visualstudio.microsoft.com/path.exe'))
        for url in ('http://aka.ms/file', 'https://microsoft.com.evil.test/file', 'https://evil.test/file', 'https://user:pass@aka.ms/file'):
            self.assertFalse(installer.allowed_url(url))

    def test_exit_codes_distinguish_reboot_cancel_busy_and_failure(self):
        self.assertEqual(installer.exit_status(0), 'recheck')
        for code in (3010, 1641):self.assertEqual(installer.exit_status(code), 'restart')
        for code in (1223, 1602, 1618, 1001, 5004, 740, 8005, 12345):
            with self.assertRaises(ValueError):installer.exit_status(code)

    def test_reboot_marker_requires_new_boot_before_continuing(self):
        core.write_json(self.root / 'Environment/restart-required.json', {'boot_time': 1000, 'code': 3010})
        with patch.object(installer, 'boot_time', return_value=1001), self.assertRaisesRegex(ValueError, '需要重启'):
            installer.check_restart(self.root)
        with patch.object(installer, 'boot_time', return_value=2000):installer.check_restart(self.root)

    def test_install_completion_always_rechecks_and_tracks_only_own_process(self):
        plan = env.repair_plan(POLICY, ['sdk'], [self.instance]); path = self.root / 'installer.exe'; path.write_bytes(b'fixture')
        process = MagicMock(pid=1234, created=5678); process.poll.return_value = 0
        with patch.object(installer, 'installer_active', return_value=False), patch.object(installer, 'download', return_value=path), patch.object(installer, 'verify_signature', return_value=core.digest(path)), patch.object(installer, 'start_installer', return_value=process), patch.object(installer.shutil, 'disk_usage', return_value=MagicMock(free=100 * 1024**3)):
            self.assertEqual(installer.install(plan, self.root, lambda _:None, threading.Event()), 'recheck')
        process.close.assert_called_once(); process.terminate.assert_not_called()
        self.assertFalse((self.root / 'Environment/active-installer.json').exists())
        self.assertEqual(core.read_json(self.root / 'Environment/last-install.json')['code'], 0)

    def test_old_visual_studio_upgrade_then_add_components_without_early_compile(self):
        plan = env.repair_plan(POLICY, ['msvc'], [dict(self.instance, installationVersion='17.8.1')])
        path = self.root / 'installer.exe'; path.write_bytes(b'fixture')
        with patch.object(installer, 'installer_active', return_value=False), patch.object(installer, 'download', return_value=path), patch.object(installer, 'verify_signature', return_value=core.digest(path)), patch.object(installer, 'monitor_installer', side_effect=['recheck','recheck']) as monitor, patch.object(installer.shutil, 'disk_usage', return_value=MagicMock(free=100 * 1024**3)):
            self.assertEqual(installer.install(plan, self.root, lambda _:None, threading.Event()), 'recheck')
            self.assertEqual([call.args[1][0] for call in monitor.call_args_list], ['update','modify'])
        with patch.object(installer, 'installer_active', return_value=False), patch.object(installer, 'download', return_value=path), patch.object(installer, 'verify_signature', return_value=core.digest(path)), patch.object(installer, 'monitor_installer', return_value='restart') as monitor, patch.object(installer.shutil, 'disk_usage', return_value=MagicMock(free=100 * 1024**3)):
            self.assertEqual(installer.install(plan, self.root, lambda _:None, threading.Event()), 'restart')
            monitor.assert_called_once()

    def test_failed_download_is_bounded_to_three_attempts(self):
        opener=MagicMock();opener.open.side_effect=OSError('offline')
        with patch.object(installer.urllib.request,'build_opener',return_value=opener):
            with self.assertRaisesRegex(ValueError,'下载失败'):installer.download(self.root,lambda _:None,threading.Event())
        self.assertEqual(opener.open.call_count,3)
        self.assertFalse(list(self.root.glob('*.part')))

    def test_active_installer_blocks_duplicate_launch_and_cancel_does_not_start(self):
        plan = env.repair_plan(POLICY, ['sdk'], [self.instance])
        with patch.object(installer, 'installer_active', return_value=True), patch.object(installer, 'download') as download:
            with self.assertRaisesRegex(ValueError, '还在运行'):installer.install(plan, self.root, lambda _:None, threading.Event())
            download.assert_not_called()
        cancel = threading.Event(); cancel.set()
        with patch.object(installer, 'installer_active', return_value=False), patch.object(installer.shutil, 'disk_usage', return_value=MagicMock(free=100 * 1024**3)), patch.object(installer, 'download') as download:
            with self.assertRaisesRegex(ValueError, '已取消'):installer.install(plan, self.root, lambda _:None, cancel)
            download.assert_not_called()

    def test_timeout_never_kills_microsoft_installer_and_keeps_tracking_record(self):
        plan = env.repair_plan(POLICY, ['sdk'], [self.instance]); path = self.root / 'installer.exe'; path.write_bytes(b'fixture')
        process = MagicMock(pid=1234, created=5678); process.poll.return_value = None
        with patch.object(installer, 'installer_active', return_value=False), patch.object(installer, 'download', return_value=path), patch.object(installer, 'verify_signature', return_value=core.digest(path)), patch.object(installer, 'start_installer', return_value=process), patch.object(installer.shutil, 'disk_usage', return_value=MagicMock(free=100 * 1024**3)):
            with self.assertRaisesRegex(ValueError, '未强制终止'):installer.install(plan, self.root, lambda _:None, threading.Event(), timeout=-1)
        process.close.assert_called_once(); process.terminate.assert_not_called()
        self.assertTrue((self.root / 'Environment/active-installer.json').is_file())


if __name__ == '__main__':unittest.main()
