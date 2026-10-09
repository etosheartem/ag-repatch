import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ag_repatch import engine
from ag_repatch.backend import Backend, Item, Settings, Snapshot
from ag_repatch.gui import MainWindow


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tray_patch = patch('ag_repatch.gui.QSystemTrayIcon.isSystemTrayAvailable', return_value=False)
        self.tray_patch.start()
        self.window = MainWindow(Backend(Path(self.tmp.name)), Settings(theme='dark'), start_scan=False)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.timer.stop()
        self.assertFalse(self.window.busy)
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        self.tray_patch.stop()
        self.tmp.cleanup()

    def snapshot(self, state='stock', running=None):
        item = Item(engine.Target(Path('/tmp/agy'), 'cli', 'Antigravity CLI'), state, state == 'patched', True)
        return Snapshot([item], running or [], engine.PROXY_URL, True, 'running', '12:00')

    def wait_job(self):
        end = time.monotonic() + 5
        while self.window.busy and time.monotonic() < end:
            QTest.qWait(10)
        self.assertFalse(self.window.busy)

    def test_states_and_buttons(self):
        self.window.render(self.snapshot())
        self.assertTrue(self.window.apply_btn.isEnabled())
        self.window.render(self.snapshot(running=['agy']))
        self.assertFalse(self.window.apply_btn.isEnabled())
        self.assertIn('закройте', self.window.hero_title.text())
        self.window.render(self.snapshot('patched'))
        self.assertFalse(self.window.apply_btn.isEnabled())
        self.assertTrue(self.window.restore_btn.isEnabled())

    def test_empty_state(self):
        self.window.render(Snapshot([], [], None, False, 'absent', '12:00'))
        self.assertIn('не найден', self.window.hero_title.text())
        self.assertIn('устанавливать Antigravity IDE не нужно', self.window.hero_text.text())
        self.assertFalse(self.window.apply_btn.isEnabled())

    def test_standalone_agy_scan_patch_and_restore_without_ide(self):
        path = Path(self.tmp.name) / 'agy'
        original = b'prefix ineligible middle https_proxy suffix'
        path.write_bytes(original)
        self.window.settings.manage_proxy = False
        roots = ([path], [Path(self.tmp.name) / 'missing-ide'])
        # Use real discovery, patching, backup and restoration. Only OS service
        # calls and search roots are isolated from the machine running the test.
        with patch.object(engine, '_roots_linux', return_value=roots), \
             patch.object(engine, '_roots_macos', return_value=roots), \
             patch.object(engine, '_roots_windows', return_value=roots), \
             patch.object(engine, 'running_clients', return_value=[]), \
             patch.object(engine, 'proxy_env_read', return_value=None), \
             patch.object(engine, 'service_status', return_value='absent'), \
             patch('socket.create_connection', side_effect=OSError):
            self.window.scan()
            self.wait_job()
            self.assertEqual(len(self.window.snapshot.items), 1)
            self.assertEqual(self.window.snapshot.items[0].target.kind, 'cli')
            self.assertTrue(self.window.apply_btn.isEnabled())
            self.assertIn('Установка IDE не требуется', self.window.hero_text.text())
            self.window.apply_btn.click()
            self.wait_job()
            self.assertEqual(path.read_bytes(), original.replace(b'ineligible', b'inexigible').replace(b'https_proxy', b'AG_LS_PROXY'))
            self.assertEqual(self.window.hero_title.text(), 'Патч agy установлен')
            self.assertEqual(self.window.notice.text(), 'Готово — перезапустите agy')
            self.assertTrue(self.window.restore_btn.isEnabled())
            with patch.object(self.window, 'confirm', return_value=True):
                self.window.restore_btn.click()
                self.wait_job()
            self.assertEqual(path.read_bytes(), original)
            self.assertTrue(self.window.apply_btn.isEnabled())

    def test_worker_returns_to_ui_thread(self):
        with patch.object(self.window.backend, 'scan', return_value=self.snapshot()):
            self.window.scan()
            self.wait_job()
        self.assertIn('восстановить', self.window.hero_title.text())
        self.assertTrue(self.window.apply_btn.isEnabled())

    def test_worker_failure_is_visible(self):
        with patch.object(self.window.backend, 'scan', side_effect=OSError('Тестовая ошибка')), \
             self.assertLogs('ag-repatch', level='ERROR'):
            self.window.scan()
            self.wait_job()
        self.assertIn('Тестовая ошибка', self.window.notice.text())
        self.assertEqual(self.window.hero_title.text(), 'Проверка не завершена')
        self.assertTrue(self.window.check_btn.isEnabled())

    def test_logs_are_plain_text(self):
        self.window.append_log('<b>Файл</b>')
        self.assertIn('<b>Файл</b>', self.window.log_view.toPlainText())

    def test_auto_patch_is_opt_in(self):
        with patch.object(self.window, 'apply_patch') as apply:
            self.window.maybe_auto_patch(self.snapshot())
        apply.assert_not_called()

    def test_auto_patch_defers_for_running_client(self):
        self.window.settings.auto_patch = True
        with patch.object(self.window, 'apply_patch') as apply:
            self.window.maybe_auto_patch(self.snapshot(running=['agy']))
        apply.assert_not_called()

    def test_auto_patch_does_not_loop_after_failure(self):
        self.window.settings.auto_patch = True
        path = Path(self.tmp.name) / 'agy'
        path.write_bytes(b'test')
        snapshot = self.snapshot()
        snapshot.items[0].target = engine.Target(path, 'cli', 'Antigravity CLI')
        with patch.object(self.window, 'apply_patch') as apply:
            self.window.maybe_auto_patch(snapshot)
            self.window.maybe_auto_patch(snapshot)
        apply.assert_called_once()

    def test_proxy_clear_disables_management(self):
        from ag_repatch.backend import Outcome
        with patch.object(self.window, 'confirm', return_value=True), \
             patch.object(self.window.backend, 'clear_proxy', return_value=Outcome('Удалено', [])), \
             patch.object(self.window.backend, 'scan', return_value=self.snapshot()):
            self.window.clear_proxy()
            self.wait_job()
        self.assertFalse(self.window.settings.manage_proxy)
        self.assertFalse(Settings.load(Path(self.tmp.name)).manage_proxy)

    def test_settings_validate_auto_patch(self):
        self.window.auto_check.setChecked(False)
        self.window.auto_patch.setChecked(True)
        self.window.save_settings()
        self.assertIn('включите', self.window.settings_notice.text())
        self.assertFalse(self.window.settings.auto_patch)

    def test_light_theme(self):
        self.window.settings.theme = 'light'
        self.window.apply_theme()
        self.assertEqual(self.window.colors['bg'], '#f4f5fa')
