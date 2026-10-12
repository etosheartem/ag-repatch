import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QCheckBox

from ag_repatch import engine
from ag_repatch.backend import Backend, Item, Settings, Snapshot
from ag_repatch.diagnostics import diagnose
from ag_repatch.gui import MainWindow
from ag_repatch.i18n import language, set_language, tr
from ag_repatch.setup_ui import SetupDialog


class LanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        previous = language()
        self.addCleanup(set_language, previous)

    def test_russian_locales_and_english_fallback(self):
        for name, expected in [("ru_RU", "ru"), ("ru-BY", "ru"), ("ru_RU.UTF-8", "ru"),
                               ("C", "en"), ("en_US", "en"), ("de_DE", "en"), ("", "en")]:
            with self.subTest(locale=name):
                set_language(name)
                self.assertEqual(language(), expected)
                self.assertEqual(tr("Отмена"), "Отмена" if expected == "ru" else "Cancel")

    def test_english_pages_setup_and_installation_states(self):
        set_language("C")
        with tempfile.TemporaryDirectory() as directory, \
                patch("ag_repatch.gui.QSystemTrayIcon.isSystemTrayAvailable", return_value=False):
            window = MainWindow(Backend(Path(directory)), Settings(), start_scan=False)
            setup = SetupDialog(window)
            try:
                self.assertEqual([button.text() for button in window.nav],
                                 ["Overview", "Settings", "Log", "Diagnostics", "Updates"])
                window.render(Snapshot([], [], None, False, "absent", "12:00"))
                self.assertEqual(window.hero_title.text(), "No installations found")
                for state in ("stock", "patched", "interrupted", "unreadable", "no-signature"):
                    item = Item(engine.Target(Path(directory) / "agy", "cli", "agy"), state)
                    self.assertEqual(item.version, "Unknown")
                    window.render(Snapshot([item], [], None, False, "absent", "12:00"))
                    for step in range(3):
                        setup.pages.setCurrentIndex(step)
                        setup.refresh()
                        self.assertEqual(setup.found.text(), "Installations found: 1")
                        for parent in (window, setup):
                            texts = [parent.windowTitle()]
                            for kind in (QLabel, QPushButton, QCheckBox):
                                texts.extend(widget.text() for widget in parent.findChildren(kind))
                            for combo in parent.findChildren(QComboBox):
                                texts.extend(combo.itemText(i) for i in range(combo.count()))
                            for text in texts:
                                self.assertNotRegex(text, "[А-Яа-яЁё]")
                set_language("ru_RU")
                window.render(Snapshot([], [], None, False, "absent", "12:00"))
                self.assertEqual(window.hero_title.text(), "Установки не найдены")
            finally:
                setup.timer.stop()
                setup.deleteLater()
                window.timer.stop()
                window.deleteLater()
                self.app.processEvents()

    def test_english_operation_results_keep_patch_noop_behavior(self):
        set_language("en_US")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agy"
            path.write_bytes(b"prefix ineligible middle https_proxy suffix")
            backend = Backend(Path(directory) / "data")
            target = engine.Target(path, "cli", "agy")
            with patch.object(engine, "running_clients", return_value=[]):
                result = backend.apply(Settings(manage_proxy=False), [target])
                self.assertTrue(result.ok)
                self.assertTrue(result.changed)
                self.assertEqual(result.title, "Done — restart agy")
                result = backend.apply(Settings(manage_proxy=False), [target])
                self.assertTrue(result.ok)
                self.assertFalse(result.changed)
                self.assertIn("Already patched", result.lines[0])

    def test_english_connection_failure(self):
        set_language("en_US")
        with patch("socket.create_connection", side_effect=OSError):
            checks = diagnose(engine.PROXY_URL)
        self.assertEqual(checks[0].name, "Proxy port")
        self.assertIn("No connection", checks[0].detail)


if __name__ == "__main__":
    unittest.main()
