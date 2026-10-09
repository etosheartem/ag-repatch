import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from ag_repatch import engine, startup, updates
from ag_repatch.backend import BackupStore, Item, Snapshot, Settings, installation_version
from ag_repatch.diagnostics import diagnose, support_report
from ag_repatch.filelock import lock_file
from ag_repatch.privileged import edit_plan, execute_plan

STOCK = b'header-ineligible-middle-https_proxy-tail'
PATCHED = STOCK.replace(b'ineligible', b'inexigible').replace(b'https_proxy', b'AG_LS_PROXY')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'agy'
        self.path.write_bytes(STOCK)
        self.store = BackupStore(self.root / 'data')
        self.store.prepare(self.path, STOCK, PATCHED)

    def test_interrupted_write_restores_exact_original(self):
        partial = STOCK.replace(b'https_proxy', b'AGtps_proxy')
        self.path.write_bytes(partial)
        self.assertIsNone(engine._scan(partial))
        self.assertEqual(self.store.recovery_original(self.path, partial), STOCK)
        self.store.change(self.path, recover=True)
        self.assertEqual(self.path.read_bytes(), STOCK)

    def test_refuses_unrelated_change_even_same_size(self):
        changed = PATCHED.replace(b'header', b'update')
        self.path.write_bytes(changed)
        self.assertIsNone(self.store.recovery_original(self.path, changed))
        with self.assertRaises(ValueError):
            self.store.change(self.path, recover=True)
        self.assertEqual(self.path.read_bytes(), changed)

    def test_refuses_modified_manifest_and_original(self):
        partial = STOCK.replace(b'ineligible', b'inexigible')
        record_path = next(self.store.directory.rglob('*.json'))
        record = json.loads(record_path.read_text())
        record['patched'] = '0' * 64
        record_path.write_text(json.dumps(record))
        self.assertIsNone(self.store.recovery_original(self.path, partial))

    def test_completed_operations_are_not_interrupted(self):
        self.assertIsNone(self.store.recovery_original(self.path, STOCK))
        self.assertIsNone(self.store.recovery_original(self.path, PATCHED))

    def test_cli_cannot_write_while_gui_holds_lock(self):
        with self.path.open('r+b') as file:
            lock_file(file)
            result = subprocess.run([sys.executable, '-c',
                'from pathlib import Path; from ag_repatch.engine import patch_file; import sys; print(patch_file(Path(sys.argv[1]))[0])',
                str(self.path)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(result.stdout.strip(), ('patched', 'already'))
        self.assertEqual(self.path.read_bytes(), STOCK)

    def test_metadata_version_without_executing_binary(self):
        (self.root / 'package.json').write_text('{"version":"1.2.3"}')
        self.assertEqual(installation_version(self.path), '1.2.3')

    @unittest.skipUnless(sys.platform == 'linux', 'Linux permission helper')
    def test_permission_helper_patch_restore_and_stale_plan(self):
        plan = {'path': str(self.path), 'expected': hashlib.sha256(STOCK).hexdigest(), 'edits': edit_plan(STOCK, PATCHED)}
        with patch.object(engine, 'running_clients', return_value=[]):
            execute_plan(plan)
            self.assertEqual(self.path.read_bytes(), PATCHED)
            with self.assertRaises(ValueError):
                execute_plan(plan)
            execute_plan({'path': str(self.path), 'expected': hashlib.sha256(PATCHED).hexdigest(), 'edits': edit_plan(PATCHED, STOCK)})
        self.assertEqual(self.path.read_bytes(), STOCK)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux permission helper')
    def test_permission_helper_rejects_arbitrary_write_and_symlink(self):
        plan = {'path': str(self.path), 'expected': hashlib.sha256(STOCK).hexdigest(), 'edits': [[0, 'arbitrary']]}
        with patch.object(engine, 'running_clients', return_value=[]):
            with self.assertRaises(ValueError):
                execute_plan(plan)
            real = self.root / 'real'
            self.path.rename(real)
            self.path.symlink_to(real)
            plan['edits'] = edit_plan(STOCK, PATCHED)
            with self.assertRaises(OSError):
                execute_plan(plan)
        self.assertEqual(real.read_bytes(), STOCK)


class NetworkTests(unittest.TestCase):
    def test_closed_proxy_stops_without_direct_fallback(self):
        with patch('socket.create_connection', side_effect=OSError), patch('requests.Session') as session:
            checks = diagnose('http://127.0.0.1:1234')
        self.assertFalse(checks[0].ok)
        session.assert_not_called()

    def test_proxy_used_for_each_probe_and_google_404_is_not_auth_success(self):
        session = MagicMock()
        first, second = MagicMock(), MagicMock()
        first.__enter__.return_value.status_code = 200
        second.__enter__.return_value.status_code = 404
        session.get.side_effect = [first, second]
        with patch('socket.create_connection'), patch('requests.Session') as factory:
            factory.return_value.__enter__.return_value = session
            checks = diagnose('http://127.0.0.1:1234')
        self.assertFalse(session.trust_env)
        self.assertEqual(session.proxies['https'], 'http://127.0.0.1:1234')
        self.assertTrue(all(c.ok for c in checks))
        self.assertIn('авторизация не проверялись', checks[-1].detail)
        for call in session.get.call_args_list:
            self.assertFalse(call.kwargs['allow_redirects'])

    def test_tls_failure_has_actionable_russian_message(self):
        import requests
        with patch('socket.create_connection'), patch('requests.Session') as factory:
            factory.return_value.__enter__.return_value.get.side_effect = requests.exceptions.SSLError('secret local path')
            checks = diagnose('https://127.0.0.1:1234')
        self.assertIn('дату системы', checks[-1].detail)
        self.assertNotIn('secret', checks[-1].detail)

    def test_support_report_contains_no_paths_process_arguments_or_proxy(self):
        item = Item(engine.Target(Path('/home/private-person/key-token/agy'), 'cli', 'agy'), 'stock')
        snapshot = Snapshot([item], ['agy --token secret'], 'http://private-server:1234', True, 'running', '12:00')
        report = support_report(snapshot, [])
        for value in ('private-person', 'key-token', 'secret', 'private-server'):
            self.assertNotIn(value, report)
        self.assertTrue(json.loads(report)['порт_прокси_доступен'])


class UpdateTests(unittest.TestCase):
    def release(self, payload=b'file'):
        name = 'ag-repatch-windows-x64.exe'
        return updates.Release('v99.0.0', 'Изменения', name,
            updates.REPOSITORY_URL + '/releases/download/v99.0.0/' + name, hashlib.sha256(payload).hexdigest(), len(payload))

    def test_versions_and_platform_selection(self):
        self.assertGreater(updates.version_tuple('v2.10.0'), updates.version_tuple('2.9.0'))
        for value in ('v2.1.0-beta', '../../bad', 'latest'):
            with self.assertRaises(ValueError):
                updates.version_tuple(value)
        with patch.object(updates.sys, 'platform', 'darwin'), patch('platform.machine', return_value='arm64'):
            self.assertEqual(updates.asset_name(), 'ag-repatch-macos-arm64.dmg')
        with patch.object(updates.sys, 'platform', 'linux'), patch('platform.machine', return_value='aarch64'):
            with self.assertRaises(ValueError):
                updates.asset_name()

    def test_verified_download_and_corruption_cleanup(self):
        release = self.release()
        with tempfile.TemporaryDirectory() as tmp, patch.object(updates, 'asset_name', return_value=release.name), patch('requests.Session') as factory:
            response = factory.return_value.__enter__.return_value.get.return_value.__enter__.return_value
            response.iter_content.return_value = [b'fi', b'le']
            path = updates.download_release(release, tmp)
            self.assertEqual(path.read_bytes(), b'file')
            response.iter_content.return_value = [b'evil']
            with self.assertRaises(ValueError):
                updates.download_release(release, tmp)
            self.assertEqual(path.read_bytes(), b'file')
            self.assertEqual(list(path.parent.glob('.download-*')), [])

    def test_release_rejects_wrong_source_and_missing_checksum(self):
        release = self.release()
        data = {'tag_name': release.version, 'assets': [{'name': release.name, 'browser_download_url': release.url, 'size': 4}]}
        with patch.object(updates, 'asset_name', return_value=release.name), patch('requests.Session') as factory:
            factory.return_value.__enter__.return_value.get.return_value.json.return_value = data
            with self.assertRaises(ValueError):
                updates.check_release()
            data['assets'][0]['digest'] = 'sha256:' + release.sha256
            self.assertEqual(updates.check_release().sha256, release.sha256)
            data['assets'][0]['browser_download_url'] = 'https://other.invalid/file.exe'
            with self.assertRaises(ValueError):
                updates.check_release()


class LinuxIntegrationTests(unittest.TestCase):
    def test_autostart_and_menu_use_persistent_appimage_path(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(startup.sys, 'platform', 'linux'), patch.dict(os.environ,
            {'APPIMAGE': str(Path(tmp) / 'Мои приложения/ag-repatch.AppImage'), 'XDG_CONFIG_HOME': tmp, 'XDG_DATA_HOME': tmp}):
            startup.set_login_launch(True)
            content = (Path(tmp) / 'autostart/ag-repatch.desktop').read_text()
            self.assertIn('Мои приложения', content)
            self.assertIn('ag-repatch.AppImage', content)
            self.assertEqual(startup.launch_command(), [os.path.abspath(os.environ['APPIMAGE'])])
            self.assertIn('--background', content)
            self.assertNotIn('/tmp/.mount', content)
            entry = startup.install_linux_menu(b'icon')
            self.assertIn('Type=Application', entry.read_text())
            startup.set_login_launch(False)
            self.assertFalse((Path(tmp) / 'autostart/ag-repatch.desktop').exists())

    def test_exec_escaping(self):
        content = startup.desktop_entry(['/tmp/A B/$test%file', '--background'])
        self.assertIn('%%file', content)
        self.assertIn('\\\\$', content)
        with self.assertRaises(ValueError):
            startup.desktop_entry(['/bad\nExec=other'])


class InstallUpdateTests(unittest.TestCase):
    def test_archive_installs_and_rejects_traversal_and_external_links(self):
        import io
        import tarfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'app.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                entry = tarfile.TarInfo('ag-repatch/ag-repatch')
                entry.mode = 0o755
                entry.size = 5
                tar.addfile(entry, io.BytesIO(b'hello'))
            executable = updates.extract_linux_archive(archive, root / 'safe')
            self.assertEqual(executable.read_bytes(), b'hello')
            for name, link in [('../escape', ''), ('ag-repatch/escape', '/tmp/outside')]:
                with tarfile.open(archive, 'w:gz') as tar:
                    entry = tarfile.TarInfo(name)
                    if link:
                        entry.type = tarfile.SYMTYPE
                        entry.linkname = link
                    tar.addfile(entry)
                with self.assertRaises(ValueError):
                    updates.extract_linux_archive(archive, root / 'unsafe')

    def test_changed_download_is_never_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'app.exe'
            path.write_bytes(b'changed')
            release = updates.Release('v99.0.0', '', 'app.exe', '', hashlib.sha256(b'original').hexdigest(), 8)
            with self.assertRaises(ValueError):
                updates.install_update(path, release)
