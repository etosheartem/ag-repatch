"""Render actual widgets with demonstration data; does not inspect installations."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from ag_repatch.backend import Item, Settings, Snapshot
from ag_repatch.engine import Target
from ag_repatch.gui import MainWindow

app = QApplication([])
app.setStyle('Fusion')
window = MainWindow(settings=Settings(theme='dark'), start_scan=False)
window.render(Snapshot([
    Item(Target(Path('/Applications/Antigravity.app/Contents/Resources/resources/bin/language_server_macos_arm'), 'language-server', 'Antigravity IDE'), 'stock', False, True),
    Item(Target(Path('/Users/artem/.local/bin/agy'), 'cli', 'Antigravity CLI'), 'patched', True, True),
], [], 'http://127.0.0.1:53129', True, 'running', '14:32'))
window.show()
dest = ROOT / 'docs/screenshots'
dest.mkdir(parents=True, exist_ok=True)
for theme in ('dark', 'light'):
    window.settings.theme = theme
    window.apply_theme()
    QTest.qWait(50)
    window.grab().save(str(dest / ('overview-' + theme + '.png')))
window.settings.theme = 'dark'
window.apply_theme()
window.navigate(1)
QTest.qWait(50)
window.grab().save(str(dest / 'settings.png'))
window.navigate(0)
window.resize(900, 700)
QTest.qWait(50)
(ROOT / 'build').mkdir(exist_ok=True)
window.grab().save(str(ROOT / 'build/preview-small.png'))
window.resize(1100, 800)
window.render(Snapshot([
    Item(Target(Path('/Users/artem/.local/bin/agy'), 'cli', 'Antigravity CLI'), 'stock', False, True),
], [], 'http://127.0.0.1:53129', True, 'running', '14:32'))
QTest.qWait(50)
window.grab().save(str(dest / 'agy-only.png'))
from ag_repatch.diagnostics import Check
window.diagnosis_finished([
    Check('Порт прокси', True, 'Соединение установлено.'),
    Check('HTTPS через прокси', True, 'Получен ответ HTTP 200.'),
    Check('Сервис Google', True, 'Сервер ответил (HTTP 404). Доступ к моделям и авторизация не проверялись.'),
])
window.navigate(3)
QTest.qWait(50)
window.grab().save(str(dest / 'diagnostics.png'))
window.open_setup()
QTest.qWait(50)
window.setup_dialog.grab().save(str(dest / 'setup.png'))
window.setup_dialog.reject()
