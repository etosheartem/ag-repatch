import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from ag_repatch import engine
from ag_repatch.backend import Backend, BackupStore, Settings, validate_proxy

STOCK = b'\0' * 71 + b'ineligible' + b'\1' * 9 + b'https_proxy' + b'\2' * 100


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / 'language_server_test'
        self.path.write_bytes(STOCK)
        self.store = BackupStore(self.root / 'data')

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_and_idempotence(self):
        self.path.chmod(0o755)
        mode = self.path.stat().st_mode
        self.store.change(self.path)
        expected = STOCK.replace(b'ineligible', b'inexigible').replace(b'https_proxy', b'AG_LS_PROXY')
        self.assertEqual(self.path.read_bytes(), expected)
        self.assertEqual(self.path.stat().st_mode, mode)
        self.assertEqual(self.store.change(self.path), 'Уже пропатчен')
        self.store.change(self.path, restore=True)
        self.assertEqual(self.path.read_bytes(), STOCK)
        self.assertEqual(self.path.stat().st_mode, mode)

    def test_stat_and_fstat_may_report_different_ctime(self):
        import os
        from types import SimpleNamespace
        real_fstat = os.fstat

        def different_ctime(fd):
            stat = real_fstat(fd)
            return SimpleNamespace(st_dev=stat.st_dev, st_ino=stat.st_ino,
                                   st_size=stat.st_size, st_mtime_ns=stat.st_mtime_ns,
                                   st_ctime_ns=stat.st_ctime_ns + 1000000)

        with patch('ag_repatch.backend.os.fstat', side_effect=different_ctime):
            self.store.change(self.path)
            self.store.change(self.path, restore=True)
        self.assertEqual(self.path.read_bytes(), STOCK)

    def test_unknown_unchanged_no_backup(self):
        self.path.write_bytes(b'ineligible alone')
        with self.assertRaises(ValueError):
            self.store.change(self.path)
        self.assertEqual(self.path.read_bytes(), b'ineligible alone')
        self.assertFalse(self.store.directory.exists())

    def test_restore_refuses_new_version(self):
        self.store.change(self.path)
        self.path.write_bytes(self.path.read_bytes() + b'new version')
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.change(self.path, restore=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_multiple_versions_have_own_originals(self):
        self.store.change(self.path)
        new = b'next version' + STOCK
        self.path.write_bytes(new)
        self.store.change(self.path)
        self.store.change(self.path, restore=True)
        self.assertEqual(self.path.read_bytes(), new)
        self.assertEqual(len(list(self.store.directory.rglob('*.bin'))), 2)

    def test_corrupt_backup_refused(self):
        self.store.change(self.path)
        next(self.store.directory.rglob('*.bin')).write_bytes(b'corrupt')
        current = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.change(self.path, restore=True)
        self.assertEqual(self.path.read_bytes(), current)

    def test_failed_backup_prevents_write(self):
        with patch('ag_repatch.backend.atomic_write', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.change(self.path)
        self.assertEqual(self.path.read_bytes(), STOCK)

    def test_write_failure_rolls_back(self):
        real = self.store._write_differences
        attempts = []
        def fail_once(f, before, after):
            attempts.append(1)
            if len(attempts) == 1:
                f.seek(71)
                f.write(b'inexigible')
                f.flush()
                raise OSError('interrupted')
            real(f, before, after)
        with patch.object(self.store, '_write_differences', side_effect=fail_once):
            with self.assertRaises(OSError):
                self.store.change(self.path)
        self.assertEqual(self.path.read_bytes(), STOCK)
        self.assertTrue(list(self.store.directory.rglob('*.bin')))

    def test_changed_during_backup_refused(self):
        prepare = self.store.prepare
        def update(path, original, changed):
            prepare(path, original, changed)
            path.write_bytes(b'updated' + original)
        with patch.object(self.store, 'prepare', side_effect=update):
            # Windows byte locks reject the competing writer itself; Unix
            # advisory locks allow an uncooperative writer, detected by stat.
            with self.assertRaises(PermissionError if os.name == 'nt' else ValueError):
                self.store.change(self.path)
        self.assertEqual(self.path.read_bytes(), STOCK if os.name == 'nt' else b'updated' + STOCK)

    def test_restores_mixed_file_to_exact_previous_state(self):
        mixed = STOCK.replace(b'ineligible', b'inexigible')
        self.path.write_bytes(mixed)
        self.store.change(self.path)
        self.store.change(self.path, restore=True)
        self.assertEqual(self.path.read_bytes(), mixed)


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.backend = Backend(self.root / 'data')
        self.path = self.root / 'agy'
        self.path.write_bytes(STOCK)
        self.target = engine.Target(self.path, 'cli', 'Antigravity CLI')
        self.settings = Settings(manage_proxy=False, extra_paths=[str(self.path)])

    def tearDown(self):
        self.tmp.cleanup()

    def test_running_process_blocks_write(self):
        with patch.object(engine, 'running_clients', return_value=['agy']):
            result = self.backend.apply(self.settings, [self.target])
        self.assertFalse(result.ok)
        self.assertEqual(self.path.read_bytes(), STOCK)

    def test_scan_is_read_only_and_detects_updated_files(self):
        with patch.object(engine, 'find_targets', return_value=[self.target]), \
             patch.object(engine, 'running_clients', return_value=[]), \
             patch.object(engine, 'proxy_env_read', return_value=None), \
             patch.object(engine, 'service_status', return_value='absent'), \
             patch('socket.create_connection', side_effect=OSError):
            self.assertEqual(self.backend.scan(self.settings).items[0].state, 'stock')
            self.assertFalse(self.backend.directory.exists())
            self.backend.apply(self.settings, [self.target])
            state = self.backend.scan(self.settings).items[0]
            self.assertEqual(state.state, 'patched')
            self.assertTrue(state.restorable)
            self.path.write_bytes(b'new' + STOCK)
            self.assertEqual(self.backend.scan(self.settings).items[0].state, 'stock')

    def test_clear_proxy_does_not_discover_or_patch(self):
        with patch.object(engine, 'proxy_env_clear', return_value='applied'), \
             patch.object(engine, 'find_targets') as find:
            result = self.backend.clear_proxy()
        self.assertTrue(result.ok)
        find.assert_not_called()
        self.assertEqual(self.path.read_bytes(), STOCK)

    def test_settings_round_trip(self):
        self.settings.save(self.root)
        self.assertEqual(Settings.load(self.root), self.settings)

    def test_bad_settings_not_silently_replaced(self):
        (self.root / 'settings.json').write_text('{bad')
        with self.assertRaises(ValueError):
            Settings.load(self.root)
        self.assertEqual((self.root / 'settings.json').read_text(), '{bad')

    def test_proxy_validation(self):
        for value in (None, 1, 'file:///etc/passwd', 'http://x:0', 'http://x:99999', 'http://x/a', 'http://user:pass@x', 'bad'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_proxy(value)
        self.assertEqual(validate_proxy(' http://127.0.0.1:53129/ '), engine.PROXY_URL)
        self.assertEqual(validate_proxy('http://[::1]:8080'), 'http://[::1]:8080')

    def test_failed_live_environment_reported(self):
        with patch.object(engine, '_IS_WIN', False), patch.object(engine, '_IS_MAC', False), \
             patch.object(engine, '_IS_LINUX', True), patch.object(engine, '_conf_path', return_value=self.root / 'env'), \
             patch.object(engine, '_run', return_value=(1, 'failed')):
            self.assertTrue(engine.proxy_env_apply(engine.PROXY_URL).startswith('error:'))
            self.assertTrue(engine.proxy_env_clear().startswith('error:'))

    def test_service_failure_reported(self):
        with patch.object(engine, 'proxy_env_apply', return_value='applied'), \
             patch.object(engine, 'service_start', return_value='error:failed'):
            self.assertFalse(self.backend.configure_proxy(Settings()).ok)

    def test_windows_service_does_not_depend_on_ui_language(self):
        with patch.object(engine, '_IS_MAC', False), patch.object(engine, '_IS_WIN', True), \
             patch.object(engine, '_run', return_value=(0, '4\r\n')):
            self.assertEqual(engine.service_status(), 'running')


class CliTests(unittest.TestCase):
    def test_unset_does_not_require_installation(self):
        spec = importlib.util.spec_from_file_location('cli', Path(__file__).resolve().parents[1] / 'ag-repatch.py')
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with patch.object(cli, 'proxy_env_clear', return_value='applied'), \
             patch.object(cli, 'find_targets') as find, patch.object(cli, 'patch') as mutate:
            self.assertEqual(cli.main(['--unset', '--plain']), 0)
        find.assert_not_called()
        mutate.assert_not_called()
