"""Three-step first-run guide using the same operations as the main window."""
import copy
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QStackedWidget, QWidget, QCheckBox
from .backend import validate_proxy


class SetupDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle('Первый запуск — ag-repatch')
        self.resize(650, 430)
        layout = QVBoxLayout(self)
        self.heading = QLabel()
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        page = QWidget()
        body = QVBoxLayout(page)
        info = QLabel('Установите самостоятельный agy для Gemini в терминале или Antigravity IDE.\nДля agy установка IDE не требуется.')
        info.setWordWrap(True)
        body.addWidget(info)
        self.found = QLabel()
        self.found.setWordWrap(True)
        body.addWidget(self.found)
        for text, callback in [('Выбрать файл agy', window.add_file), ('Выбрать папку IDE', window.add_directory),
                               ('Проверить установки', window.scan), ('Инструкция установки agy', window.open_agy_docs)]:
            b = QPushButton(text)
            b.clicked.connect(callback)
            body.addWidget(b)
        body.addStretch()
        self.pages.addWidget(page)
        page = QWidget()
        body = QVBoxLayout(page)
        self.manage = QCheckBox('Настроить подключение через мой прокси')
        self.manage.setChecked(window.settings.manage_proxy)
        body.addWidget(self.manage)
        self.proxy = QLineEdit(window.settings.proxy_url)
        self.proxy.setAccessibleName('Адрес прокси')
        body.addWidget(self.proxy)
        hint = QLabel('Укажите уже работающий прокси. Приложение не устанавливает его.\nЕсли подключение настроено другим способом, снимите флажок.')
        hint.setWordWrap(True)
        body.addWidget(hint)
        check = QPushButton('Проверить подключение')
        check.clicked.connect(self.diagnose)
        body.addWidget(check)
        self.connection = QLabel('Проверка выполняет запросы к example.com и API Google через выбранный прокси.')
        self.connection.setWordWrap(True)
        body.addWidget(self.connection)
        body.addStretch()
        self.pages.addWidget(page)
        page = QWidget()
        body = QVBoxLayout(page)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        body.addWidget(self.summary)
        self.apply = QPushButton('Применить патч')
        self.apply.clicked.connect(window.apply_patch)
        body.addWidget(self.apply)
        body.addStretch()
        self.pages.addWidget(page)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        row = QHBoxLayout()
        later = QPushButton('Продолжить позже')
        later.clicked.connect(self.reject)
        row.addWidget(later)
        row.addStretch()
        self.back = QPushButton('Назад')
        self.back.clicked.connect(lambda: self.pages.setCurrentIndex(self.pages.currentIndex()-1))
        row.addWidget(self.back)
        self.next = QPushButton('Далее')
        self.next.clicked.connect(self.advance)
        row.addWidget(self.next)
        layout.addLayout(row)
        for child in self.findChildren(QLabel):
            child.setTextFormat(Qt.TextFormat.PlainText)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(200)
        self.finished.connect(self.timer.stop)
        self.refresh()

    def save_proxy(self):
        updated = copy.deepcopy(self.window.settings)
        updated.proxy_url = validate_proxy(self.proxy.text())
        updated.manage_proxy = self.manage.isChecked()
        updated.save(self.window.backend.directory)
        self.window.settings = updated
        self.window.proxy_input.setText(updated.proxy_url)
        self.window.manage_proxy.setChecked(updated.manage_proxy)

    def diagnose(self):
        if self.window.busy:
            return
        try:
            self.save_proxy()
            self.window.diagnose_connection()
        except (OSError, ValueError) as exc:
            self.error.setText(str(exc))

    def advance(self):
        if self.window.busy:
            return
        step = self.pages.currentIndex()
        try:
            if step == 1:
                self.save_proxy()
            if step == 2:
                updated = copy.deepcopy(self.window.settings)
                updated.onboarding_done = True
                updated.save(self.window.backend.directory)
                self.window.settings = updated
                self.accept()
            else:
                self.pages.setCurrentIndex(step + 1)
                self.error.clear()
        except (OSError, ValueError) as exc:
            self.error.setText(str(exc))
        self.refresh()

    def refresh(self):
        w = self.window
        step = self.pages.currentIndex()
        self.heading.setText(['Шаг 1 из 3 · Найдите установку', 'Шаг 2 из 3 · Настройте подключение', 'Шаг 3 из 3 · Примените патч'][step])
        snapshot = w.snapshot
        self.found.setText(f'Найдено установок: {len(snapshot.items)}' if snapshot else 'Ищем установки…')
        self.back.setEnabled(step > 0 and not w.busy)
        self.next.setText('Завершить' if step == 2 else 'Далее')
        self.next.setEnabled(not w.busy and (step > 0 or bool(snapshot and snapshot.items)))
        self.apply.setEnabled(w.apply_btn.isEnabled())
        self.summary.setText((w.notice.text() + '\n' if w.notice.text() else '') + w.hero_title.text() + '\n' + w.hero_text.text())
        if w.diagnostic_checks:
            self.connection.setText('\n'.join(('✓ ' if c.ok else '• ') + c.name + ': ' + c.detail for c in w.diagnostic_checks))

    def reject(self):
        if not self.window.busy:
            super().reject()
