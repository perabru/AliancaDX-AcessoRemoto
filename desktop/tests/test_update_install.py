"""Bounded downloads, verified handoff, cancellation and installer lifecycle."""
from dataclasses import replace
from email.message import Message
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, HTTPSHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import update_install as updater
from updates import UpdateResult

PAYLOAD = b'MZ' + bytes(range(256)) * 2000
URL = 'https://github.com/perabru/AliancaDX-AcessoRemoto/releases/download/v7.6.0/AliancaDX_Controlador_Setup.exe'


def result(**changes):
    return replace(UpdateResult('available', '7.5.0', '7.6.0', 'Disponível', URL, len(PAYLOAD), hashlib.sha256(PAYLOAD).hexdigest()), **changes)


class Response(io.BytesIO):
    def __init__(self, data=PAYLOAD, url=URL, status=200, headers=None):
        super().__init__(data)
        self.url, self.status = url, status
        self.headers = Message()
        for k, v in (headers or {}).items():
            for value in v if isinstance(v, tuple) else (v,):
                self.headers[k] = value
        self.read_calls = []
    def geturl(self):
        return self.url
    def read1(self, size=-1):
        self.read_calls.append(size)
        return super().read1(size)


class UpdateInstallTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.local = Path(self.folder.name)
        self.env = patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)})
        self.env.start()
        self.cancelled, self.progress = threading.Event(), Mock()
    def tearDown(self):
        self.env.stop()
        self.folder.cleanup()
    def download(self, response=None, metadata=None):
        response = response or Response()
        opener = Mock(return_value=response)
        path = updater.download_update(metadata or result(), 'controller', self.cancelled, self.progress, opener=opener)
        return path, response, opener
    def assert_empty_cache(self):
        base = updater._cache_base()
        self.assertEqual(list(base.iterdir()), [])
    def installation(self, role='controller', version='7.5.0'):
        target = self.local / 'Programs' / 'MiniAnyDesk' / updater.ROLES[role][0]
        (target / '.venv/Scripts').mkdir(parents=True, exist_ok=True)
        (target / '.venv/Scripts/pythonw.exe').write_bytes(b'MZ')
        (target / '.minianydesk-app.json').write_text(json.dumps({'application': 'MiniAnyDeskNative', 'role': role, 'version': version}))
        (target / 'update_install.py').write_text('# helper')
        return target
    def test_streams_into_private_cache_with_progress_and_exact_hash(self):
        path, response, opener = self.download()
        self.assertEqual(path.read_bytes(), PAYLOAD)
        self.assertEqual(path.name, updater.ASSET_NAMES['controller'])
        self.assertTrue(response.closed)
        self.assertGreater(len(self.progress.call_args_list), 1)
        self.progress.assert_called_with(len(PAYLOAD), len(PAYLOAD))
        self.assertLessEqual(max(response.read_calls), 256 * 1024)
        req = opener.call_args.args[0]
        self.assertEqual(req.get_method(), 'GET')
        self.assertNotIn('Authorization', req.headers)
        self.assertEqual(req.full_url, URL)
        self.assertEqual(opener.call_args.kwargs['timeout'], updater.DOWNLOAD_TIMEOUT_SECONDS)
        updater.cleanup_download(path)
        self.assert_empty_cache()
    def test_mismatched_hash_truncation_and_excess_never_leave_installer(self):
        for payload in (PAYLOAD[:-1], PAYLOAD + b'x', b'MZ' + b'X' * (len(PAYLOAD)-2)):
            with self.subTest(size=len(payload)):
                with self.assertRaises(updater.UpdateInstallError):
                    self.download(Response(payload))
                self.assert_empty_cache()
    def test_non_pe_asset_is_rejected_even_with_matching_digest(self):
        data = b'not an EXE'
        with self.assertRaisesRegex(updater.UpdateInstallError, 'instalador Windows'):
            self.download(Response(data), result(download_size=len(data), sha256=hashlib.sha256(data).hexdigest()))
        self.assert_empty_cache()
    def test_size_and_sha_required_and_role_must_match(self):
        for invalid in (result(sha256=None), result(sha256='g'*64), result(sha256='A'*64),
                        result(download_size=True), result(download_size=1), result(download_size=updater.MAX_INSTALLER_BYTES+1),
                        result(download_url=URL.replace('Controlador', 'Host')), result(status='current')):
            opener = Mock()
            with self.assertRaises(updater.UpdateInstallError):
                updater.download_update(invalid, 'controller', self.cancelled, self.progress, opener=opener)
            opener.assert_not_called()
    def test_bad_http_metadata_is_rejected_without_reading(self):
        for kwargs in ({'status': 206}, {'status': True}, {'url': 'https://evil.example/app.exe'},
                       {'headers': {'Content-Length': str(len(PAYLOAD)-1)}},
                       {'headers': {'Content-Length': (str(len(PAYLOAD)), str(len(PAYLOAD)))}},
                       {'headers': {'Content-Encoding': 'gzip'}}):
            response = Response(**kwargs)
            with self.assertRaises(ValueError):
                self.download(response)
            self.assertEqual(response.read_calls, [])
            self.assert_empty_cache()
    def test_known_github_cdn_and_no_other_redirects(self):
        path, _, _ = self.download(Response(url='https://release-assets.githubusercontent.com/github-production-release-asset/1?signature=x'))
        self.assertTrue(path.exists())
        handler = updater._AssetRedirects()
        for url in ('http://release-assets.githubusercontent.com/file', 'https://github.com/another/repo/file',
                    'https://release-assets.githubusercontent.com.evil.example/file',
                    'https://user:pass@release-assets.githubusercontent.com/file',
                    'https://release-assets.githubusercontent.com:443/file',
                    'https://release-assets.githubusercontent.com/file#fragment', 'file:///tmp/setup.exe'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                handler.redirect_request(Request(URL), None, 302, '', {}, url)
    def test_https_opener_keeps_tls_validation(self):
        director = Mock()
        with patch.object(updater, 'build_opener', return_value=director) as build:
            updater._open_asset(Request(URL), timeout=3)
        https = next(x for x in build.call_args.args if isinstance(x, HTTPSHandler))
        self.assertTrue(https._context.check_hostname)
        self.assertTrue(https._context.verify_mode)
        self.assertTrue(any(isinstance(x, updater._AssetRedirects) for x in build.call_args.args))
    def test_cancel_before_download_never_opens_network(self):
        self.cancelled.set()
        opener = Mock()
        with self.assertRaises(updater.UpdateCancelled):
            updater.download_update(result(), 'controller', self.cancelled, self.progress, opener=opener)
        opener.assert_not_called()
    def test_cancel_mid_download_removes_partial(self):
        def cancel(*args):
            self.cancelled.set()
        self.progress.side_effect = cancel
        with self.assertRaises(updater.UpdateCancelled):
            self.download()
        self.assert_empty_cache()
    def test_deadline_and_network_failure_remove_partial(self):
        times = iter((0, updater.DOWNLOAD_DEADLINE_SECONDS+1))
        with patch.object(updater.time, 'monotonic', side_effect=lambda: next(times)):
            with self.assertRaisesRegex(updater.UpdateInstallError, 'demorou'):
                self.download()
        self.assert_empty_cache()
        with self.assertRaisesRegex(updater.UpdateInstallError, 'concluir o download'):
            updater.download_update(result(), 'controller', self.cancelled, self.progress, opener=Mock(side_effect=OSError('offline')))
        self.assert_empty_cache()
    def test_cache_symlink_is_refused(self):
        destination = self.local / 'elsewhere'; destination.mkdir()
        (self.local/'MiniAnyDesk').symlink_to(destination, target_is_directory=True)
        with self.assertRaises(updater.UpdateInstallError):
            self.download()
        self.assertEqual(list(destination.iterdir()), [])
    def test_cleanup_never_removes_arbitrary_user_folder(self):
        folder = self.local / 'pending-user'; folder.mkdir()
        exe = folder / updater.ASSET_NAMES['controller']; exe.write_bytes(PAYLOAD)
        updater.cleanup_download(exe)
        self.assertTrue(exe.exists())
    def test_installed_windows_role_and_default_folder_are_required(self):
        target = self.installation()
        self.assertEqual(updater.install_target('controller', platform='win32', source=target), target)
        for role, platform, source in (('host','win32',target), ('controller','linux',target), ('controller','win32',self.local)):
            with self.assertRaises(updater.UpdateInstallError):
                updater.install_target(role, platform=platform, source=source)
        (target/'.minianydesk-app.json').write_text('{"application":"Other","role":"controller"}')
        with self.assertRaises(updater.UpdateInstallError):
            updater.install_target('controller', platform='win32', source=target)
    def test_launch_uses_base_python_and_argv_then_ready_handshake(self):
        path, _, _ = self.download()
        target = self.installation()
        base = self.local / 'Python312'; base.mkdir(); pythonw = base / 'pythonw.exe'; pythonw.write_bytes(b'MZ')
        process = Mock(); process.poll.return_value = None
        def start(argv, **kwargs):
            plan = json.loads(Path(argv[-1]).read_text())
            self.assertEqual(plan['role'], 'controller')
            self.assertEqual(plan['sha256'], result().sha256)
            self.assertEqual(plan['parent_pid'], os.getpid())
            Path(argv[-1]).with_name('helper.ready').write_text('ready')
            self.assertEqual(argv[:3], [str(pythonw), str(target/'update_install.py'), '--apply'])
            self.assertEqual(kwargs['cwd'], target)
            return process
        with patch.object(updater, 'install_target', return_value=target), patch.object(sys, '_base_executable', str(base/'python.exe')):
            self.assertIs(updater.launch_update(path, result(), 'controller', popen=start), process)
    def test_tampered_file_cannot_start_helper(self):
        path, _, _ = self.download()
        path.write_bytes(b'MZ' + b'x'*(len(PAYLOAD)-2))
        launch = Mock()
        with patch.object(updater,'install_target', return_value=self.local), self.assertRaises(updater.UpdateInstallError):
            updater.launch_update(path, result(), 'controller', popen=launch)
        launch.assert_not_called()
    def prepare_plan(self):
        path, _, _ = self.download()
        target = self.installation()
        plan = path.parent/'apply.json'
        plan.write_text(json.dumps({'role':'controller','version':'7.6.0','sha256':result().sha256,'size':len(PAYLOAD),
                                    'installer':str(path),'parent_pid':os.getpid()+1}))
        return path,target,plan
    def test_helper_waits_parent_installs_silent_shell_free_and_reopens_correct_entry(self):
        path,target,plan=self.prepare_plan(); order=[]
        def wait(pid):
            self.assertTrue(plan.with_name('helper.ready').is_file()); order.append('closed')
        def run(argv,**kwargs):
            self.assertEqual(order,['closed']); order.append('installed')
            self.assertEqual(argv,[str(path),'/S','/UPDATE'])
            self.assertNotIn('shell',kwargs)
            marker=target/'.minianydesk-app.json'; row=json.loads(marker.read_text()); row['version']='7.6.0'; marker.write_text(json.dumps(row))
            return SimpleNamespace(returncode=0)
        launcher=Mock()
        with patch.object(updater,'install_target',return_value=target):
            updater.apply_plan(plan,runner=run,launcher=launcher,wait_parent=wait)
        launcher.assert_called_once_with([str(target/'.venv/Scripts/pythonw.exe'),str(target/'controller.py')],cwd=target,close_fds=True)
        self.assertFalse(path.parent.exists())
    def test_helper_rechecks_hash_after_parent_exits(self):
        path,target,plan=self.prepare_plan(); runner=Mock()
        def tamper(_):
            path.write_bytes(b'MZ'+b'X'*(len(PAYLOAD)-2))
        with patch.object(updater,'install_target',return_value=target), self.assertRaises(updater.UpdateInstallError):
            updater.apply_plan(plan,runner=runner,wait_parent=tamper)
        runner.assert_not_called()
    def test_failed_installer_or_unconfirmed_version_never_relaunches(self):
        for code in (1,0):
            path,target,plan=self.prepare_plan(); launcher=Mock()
            with patch.object(updater,'install_target',return_value=target), self.assertRaises(updater.UpdateInstallError):
                updater.apply_plan(plan,runner=Mock(return_value=SimpleNamespace(returncode=code)),launcher=launcher,wait_parent=Mock())
            launcher.assert_not_called()
            updater.cleanup_download(path)
    def test_helper_refuses_downgrade_wrong_plan_and_changed_installer(self):
        path,target,plan=self.prepare_plan()
        for change in ({'version':'7.4.0'}, {'parent_pid':True}, {'installer':str(self.local/'else.exe')}, {'sha256':'f'*64}):
            original=json.loads(plan.read_text()); row=dict(original); row.update(change); plan.write_text(json.dumps(row))
            runner=Mock()
            with patch.object(updater,'install_target',return_value=target), self.assertRaises(updater.UpdateInstallError):
                updater.apply_plan(plan,runner=runner,wait_parent=Mock())
            runner.assert_not_called(); plan.write_text(json.dumps(original))
    def test_cancel_handoff_signals_helper_without_force_killing(self):
        path,_,_=self.download(); process=Mock()
        self.assertTrue(updater.cancel_handoff(path,process))
        self.assertTrue(path.with_name('helper.abort').is_file())
        process.wait.assert_called_once_with(timeout=3)
        process.terminate.assert_not_called(); process.kill.assert_not_called()
    def test_payload_installs_restores_and_uninstalls_updater(self):
        root=Path(__file__).resolve().parents[1]
        nsi=(root/'installer/MiniAnyDesk.nsi').read_text()
        for text in ('!insertmacro RestoreFile "update_install.py"','!insertmacro BackupFile "update_install.py"',
                     'File "${PROJECT_ROOT}/update_install.py"','Delete "$INSTDIR\\update_install.py"',
                     '${GetOptions} $0 "/UPDATE" $1','${ROLE} --auto-update'):
            self.assertIn(text,nsi)
        self.assertIn('"update_install.py"',(root/'installer/build_installers.py').read_text())
        self.assertIn('"update_install.py"',(root/'installer_python.py').read_text())
        self.assertIn('--from-nsis %MINI_UPDATE%',(root/'installer/bootstrap.cmd').read_text())


if __name__=='__main__':
    unittest.main()
