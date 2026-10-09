"""Russian desktop application. File and OS operations run off the UI thread."""
from __future__ import annotations

import copy
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

from PySide6.QtCore import QLibraryInfo, QLocale, QLockFile, QThread, QTimer, Qt, QTranslator, Slot, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QStackedWidget, QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget,
)

from . import REPOSITORY_URL, __version__, engine
from .backend import Backend, Outcome, Settings, Snapshot, data_directory
from .startup import elevate_windows, set_login_launch


DARK = dict(bg="#101217", panel="#181b23", raised="#20242f", border="#2c3140", text="#f2f3fa",
            muted="#969eb2", accent="#a99aff", button="#9986ff", button_text="#151020",
            hero="#232039", green="#72d4ae", amber="#e9bd7c", red="#f293a1")
LIGHT = dict(bg="#f4f5fa", panel="#ffffff", raised="#eceef6", border="#dfe2ee", text="#222638",
             muted="#697187", accent="#6651c8", button="#7058df", button_text="#ffffff",
             hero="#ede9fc", green="#168264", amber="#946014", red="#bc3852")

STATES = {
    "stock": ("Нужен патч", "amber"), "mixed": ("Нужен патч", "amber"),
    "patched": ("Патч установлен", "green"), "no-signature": ("Версия не распознана", "muted"),
    "unreadable": ("Нет доступа к файлу", "red"),
}


def app_icon(size=128) -> QIcon:
    image = QPixmap(size, size)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#9986ff"))
    p.drawRoundedRect(0, 0, size, size, size * .27, size * .27)
    p.setPen(QPen(QColor("#191425"), size * .075, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    from PySide6.QtCore import QPointF
    p.drawLine(QPointF(size*.27, size*.53), QPointF(size*.44, size*.69))
    p.drawLine(QPointF(size*.44, size*.69), QPointF(size*.75, size*.34))
    p.end()
    return QIcon(image)


def label(text="", role="", wrap=False):
    w = QLabel(text)
    w.setTextFormat(Qt.TextFormat.PlainText)
    w.setWordWrap(wrap)
    if role:
        w.setObjectName(role)
    return w


def button(text, callback, role=""):
    w = QPushButton(text)
    w.setCursor(Qt.CursorShape.PointingHandCursor)
    if role:
        w.setObjectName(role)
    w.clicked.connect(callback)
    return w


def card():
    w = QFrame()
    w.setObjectName("card")
    return w


class Worker(QThread):
    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.value = (None, "Операция завершилась без результата.")

    @Slot()
    def run(self):
        try:
            self.value = (self.fn(), "")
        except Exception as exc:
            logging.getLogger("ag-repatch").exception("Ошибка операции")
            self.value = (None, str(exc))


class MainWindow(QMainWindow):
    def __init__(self, backend=None, settings=None, start_scan=True):
        super().__init__()
        self.backend = backend or Backend()
        self.load_error = ""
        try:
            self.settings = settings or Settings.load(self.backend.directory)
        except (OSError, ValueError, TypeError) as exc:
            self.settings = Settings()
            self.load_error = "Не удалось прочитать настройки. Используются значения по умолчанию: " + str(exc)
        self.snapshot: Snapshot | None = None
        self.thread = None
        self.busy = False
        self.quitting = False
        self.automatic = False
        self._auto_attempted = set()
        self.log = logging.getLogger("ag-repatch")
        self.setWindowTitle("ag-repatch — Antigravity IDE и Gemini через agy")
        self.setWindowIcon(app_icon())
        self.resize(1100, 800)
        self.setMinimumSize(900, 700)
        self.build()
        self.apply_theme()
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(app_icon(), self)
            self.tray.setToolTip("ag-repatch — проверка IDE и agy")
            menu = QMenu(self)
            menu.addAction("Открыть приложение", self.show_window)
            menu.addAction("Проверить состояние", self.scan)
            menu.addAction("Репозиторий GitHub", self.open_repository)
            menu.addSeparator()
            menu.addAction("Завершить работу", self.quit_app)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(self.tray_activated)
            self.tray.show()
        self.timer = QTimer(self)
        self.timer.setInterval(60_000)
        self.timer.timeout.connect(self.periodic_check)
        self.timer.start()
        self.last_check.setText("Проверка ещё не выполнялась")
        self.refresh_actions()
        QApplication.instance().styleHints().colorSchemeChanged.connect(lambda _: self.apply_theme())
        if self.load_error:
            self.report(self.load_error, False)
        if start_scan:
            QTimer.singleShot(0, self.scan)

    def build(self):
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(210)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(22, 30, 22, 24)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(44, 44))
        side.addWidget(logo)
        side.addSpacing(12)
        side.addWidget(label("ag-repatch", "brand"))
        side.addWidget(label("Antigravity IDE · agy", "muted"))
        side.addSpacing(38)
        self.nav = []
        for index, text in enumerate(("Обзор", "Настройки", "Журнал")):
            b = button(text, lambda checked=False, i=index: self.navigate(i), "nav")
            b.setCheckable(True)
            b.setMinimumHeight(46)
            self.nav.append(b)
            side.addWidget(b)
        side.addStretch()
        side.addWidget(label("Исходный код и обновления", "small"))
        self.repository_btn = button("Репозиторий GitHub", self.open_repository, "link")
        self.repository_btn.setToolTip(REPOSITORY_URL)
        side.addWidget(self.repository_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        side.addSpacing(16)
        side.addWidget(label("Версия " + __version__, "small"))
        layout.addWidget(sidebar)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self.build_overview()
        self.build_settings()
        self.build_log()
        self.navigate(0)

    def page(self, title, description):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(16)
        layout.addWidget(label(title, "pageTitle"))
        layout.addWidget(label(description, "muted", True))
        self.pages.addWidget(w)
        return layout

    def build_overview(self):
        layout = self.page("Antigravity IDE и agy", "Gemini через agy работает без установки IDE. Проверяем то, что установлено.")
        self.hero = QFrame()
        self.hero.setObjectName("hero")
        h = QVBoxLayout(self.hero)
        h.setContentsMargins(25, 22, 25, 22)
        h.setSpacing(10)
        self.hero_kicker = label("ПРОВЕРКА СОСТОЯНИЯ", "eyebrow")
        self.hero_title = label("Ищем установленные приложения", "heroTitle", True)
        self.hero_text = label("Это займёт несколько секунд.", "muted", True)
        h.addWidget(self.hero_kicker)
        h.addWidget(self.hero_title)
        h.addWidget(self.hero_text)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        h.addWidget(self.progress)
        self.progress.hide()
        layout.addWidget(self.hero)
        row = QHBoxLayout()
        self.install_count = label("Установки", "sectionTitle")
        row.addWidget(self.install_count)
        row.addStretch()
        self.add_btn = button("+ Добавить установку", self.add_menu, "link")
        row.addWidget(self.add_btn)
        layout.addLayout(row)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumHeight(155)
        self.targets_widget = QWidget()
        self.targets_layout = QVBoxLayout(self.targets_widget)
        self.targets_layout.setContentsMargins(0, 0, 0, 0)
        self.targets_layout.setSpacing(10)
        self.targets_layout.addStretch()
        scroll.setWidget(self.targets_widget)
        layout.addWidget(scroll, 1)
        proxy = card()
        pl = QVBoxLayout(proxy)
        pl.setContentsMargins(18, 14, 18, 14)
        pr = QHBoxLayout()
        pr.addWidget(label("Подключение через прокси", "sectionTitle"))
        pr.addStretch()
        self.proxy_badge = label("Проверяем…", "badge")
        pr.addWidget(self.proxy_badge)
        pl.addLayout(pr)
        self.proxy_detail = label("", "muted", True)
        pl.addWidget(self.proxy_detail)
        layout.addWidget(proxy)
        self.notice = label("", "notice", True)
        self.notice.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.notice.hide()
        layout.addWidget(self.notice)
        self.admin_btn = button("Перезапустить с правами администратора", self.elevate, "link")
        self.admin_btn.hide()
        layout.addWidget(self.admin_btn)
        actions = QHBoxLayout()
        self.apply_btn = button("Применить патч", self.apply_patch, "primary")
        self.apply_btn.setMinimumHeight(46)
        self.check_btn = button("Проверить снова", self.scan)
        self.restore_btn = button("Восстановить оригиналы", self.restore)
        actions.addWidget(self.apply_btn)
        actions.addWidget(self.check_btn)
        actions.addStretch()
        actions.addWidget(self.restore_btn)
        layout.addLayout(actions)
        self.last_check = label("", "small")
        layout.addWidget(self.last_check)

    def build_settings(self):
        layout = self.page("Настройки", "Приложение сохраняет изменения только после нажатия кнопки ниже.")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(16)
        proxy = card()
        p = QVBoxLayout(proxy)
        p.setContentsMargins(20, 18, 20, 18)
        p.addWidget(label("Прокси", "sectionTitle"))
        self.manage_proxy = QCheckBox("Настраивать прокси при применении патча")
        self.manage_proxy.setChecked(self.settings.manage_proxy)
        p.addWidget(self.manage_proxy)
        self.proxy_input = QLineEdit(self.settings.proxy_url)
        self.proxy_input.setAccessibleName("Адрес прокси")
        p.addWidget(self.proxy_input)
        p.addWidget(label("Укажите адрес уже установленного прокси. Сам прокси в приложение не входит.", "muted", True))
        self.clear_btn = button("Удалить настройку прокси из системы", self.clear_proxy, "link")
        p.addWidget(self.clear_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        content.addWidget(proxy)
        auto = card()
        a = QVBoxLayout(auto)
        a.setContentsMargins(20, 18, 20, 18)
        a.addWidget(label("Автоматическая проверка", "sectionTitle"))
        self.auto_check = QCheckBox("Проверять состояние каждую минуту")
        self.auto_check.setChecked(self.settings.auto_check)
        self.auto_patch = QCheckBox("Применять патч после обновлений автоматически")
        self.auto_patch.setChecked(self.settings.auto_patch)
        self.login = QCheckBox("Запускать при входе в систему")
        self.login.setChecked(self.settings.start_at_login)
        self.login.setEnabled(sys.platform in ("win32", "darwin"))
        a.addWidget(self.auto_check)
        a.addWidget(self.auto_patch)
        a.addWidget(self.login)
        a.addWidget(label("Автопатч работает при включённой проверке, пока запущен ag-repatch. Если IDE или agy запущены, операция откладывается. Резервная копия создаётся всегда.", "muted", True))
        content.addWidget(auto)
        appearance = QHBoxLayout()
        appearance.addWidget(label("Оформление", "sectionTitle"))
        appearance.addStretch()
        self.theme = QComboBox()
        for title, key in (("Как в системе", "system"), ("Светлое", "light"), ("Тёмное", "dark")):
            self.theme.addItem(title, key)
        self.theme.setCurrentIndex(self.theme.findData(self.settings.theme))
        appearance.addWidget(self.theme)
        content.addLayout(appearance)
        content.addWidget(label("Добавленные вручную пути", "sectionTitle"))
        self.path_list = QListWidget()
        self.path_list.setMaximumHeight(110)
        self.path_list.addItems(self.settings.extra_paths)
        content.addWidget(self.path_list)
        content.addWidget(button("Убрать выбранный путь из поиска", self.remove_path, "link"), alignment=Qt.AlignmentFlag.AlignLeft)
        content.addWidget(button("Открыть папку резервных копий", self.open_backups, "link"), alignment=Qt.AlignmentFlag.AlignLeft)
        content.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        self.settings_notice = label("", "muted", True)
        layout.addWidget(self.settings_notice)
        self.save_btn = button("Сохранить настройки", self.save_settings, "primary")
        layout.addWidget(self.save_btn, alignment=Qt.AlignmentFlag.AlignLeft)

    def build_log(self):
        layout = self.page("Журнал операций", "Результаты проверок и подробности, которые помогут разобраться с ошибкой.")
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(1500)
        self.log_view.setPlaceholderText("Здесь появятся результаты проверок и операций.")
        layout.addWidget(self.log_view, 1)
        row = QHBoxLayout()
        row.addWidget(button("Скопировать журнал", self.copy_log))
        row.addWidget(button("Открыть папку журнала", self.open_logs))
        row.addStretch()
        layout.addLayout(row)

    def navigate(self, index):
        self.pages.setCurrentIndex(index)
        for i, b in enumerate(self.nav):
            b.setChecked(i == index)

    def apply_theme(self):
        dark = self.settings.theme == "dark" or (self.settings.theme == "system" and
                  QApplication.instance().styleHints().colorScheme() == Qt.ColorScheme.Dark)
        self.colors = c = DARK if dark else LIGHT
        self.setStyleSheet(f"""
            QWidget {{ color: {c['text']}; font-size: 13px; }}
            QMainWindow, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {c['bg']}; }}
            #sidebar {{ background: {c['panel']}; border-right: 1px solid {c['border']}; }}
            QLabel {{ background: transparent; }}
            #brand {{ font-size: 23px; font-weight: 700; }}
            #pageTitle {{ font-size: 27px; font-weight: 700; }}
            #heroTitle {{ font-size: 25px; font-weight: 650; }}
            #sectionTitle {{ font-size: 14px; font-weight: 600; }}
            #muted, #small {{ color: {c['muted']}; }}
            #small {{ font-size: 11px; }}
            #eyebrow {{ color: {c['accent']}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
            #card {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 12px; }}
            #hero {{ background: {c['hero']}; border: 1px solid {c['border']}; border-radius: 16px; }}
            #notice {{ background: {c['raised']}; border-radius: 8px; padding: 10px; }}
            QPushButton {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 10px 13px; font-weight: 500; }}
            QPushButton:hover {{ background: {c['raised']}; border-color: {c['accent']}; }}
            QPushButton:focus {{ border: 1px solid {c['accent']}; }}
            QPushButton:disabled {{ color: {c['muted']}; background: {c['raised']}; border-color: {c['border']}; }}
            QPushButton#primary {{ background: {c['button']}; color: {c['button_text']}; border: none; font-weight: 650; }}
            QPushButton#primary:hover {{ background: {c['accent']}; }}
            QPushButton#primary:disabled {{ background: {c['raised']}; color: {c['muted']}; }}
            QPushButton#nav {{ background: transparent; border: none; text-align: left; padding-left: 15px; color: {c['muted']}; }}
            QPushButton#nav:checked {{ color: {c['accent']}; background: {c['hero']}; }}
            QPushButton#link {{ color: {c['accent']}; background: transparent; border: none; padding: 6px 0; }}
            QLineEdit, QComboBox, QListWidget, QTextEdit {{ background: {c['panel']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 9px; selection-background-color: {c['button']}; }}
            QComboBox QAbstractItemView {{ background: {c['panel']}; color: {c['text']}; selection-background-color: {c['hero']}; }}
            QCheckBox {{ spacing: 10px; padding: 5px 0; }}
            QCheckBox::indicator {{ width: 17px; height: 17px; }}
            QProgressBar {{ background: {c['border']}; border: none; border-radius: 1px; }}
            QProgressBar::chunk {{ background: {c['accent']}; }}
            QScrollBar:vertical {{ width: 7px; background: transparent; }}
            QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 3px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QToolTip {{ background: {c['panel']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 6px; }}
        """)
        if self.snapshot:
            self.render(self.snapshot)

    def refresh_actions(self):
        s = self.snapshot
        can_apply = bool(s and (s.pending or (s.items and self.settings.manage_proxy and s.proxy_env != self.settings.proxy_url)))
        self.apply_btn.setEnabled(not self.busy and can_apply and not s.running)
        self.apply_btn.setText("Настроить прокси" if s and not s.pending and can_apply else "Применить патч")
        self.restore_btn.setEnabled(bool(not self.busy and s and not s.running and any(i.restorable for i in s.items)))
        for w in (self.check_btn, self.add_btn, self.save_btn, self.clear_btn):
            w.setEnabled(not self.busy)
        self.progress.setVisible(self.busy)

    def render(self, s):
        self.snapshot = s
        c = self.colors
        while self.targets_layout.count() > 1:
            item = self.targets_layout.takeAt(0)
            item.widget().hide()
            item.widget().deleteLater()
        for item in s.items:
            w = card()
            l = QVBoxLayout(w)
            l.setContentsMargins(17, 13, 17, 13)
            r = QHBoxLayout()
            title = "agy — Gemini в терминале" if item.target.kind == "cli" else "Antigravity IDE"
            r.addWidget(label(title, "sectionTitle"))
            r.addStretch()
            text, color = STATES[item.state]
            badge = label(text)
            badge.setStyleSheet("color: " + c[color] + "; font-weight: 600;")
            r.addWidget(badge)
            l.addLayout(r)
            path = label(str(item.target.path), "small", True)
            path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            l.addWidget(path)
            if item.restorable:
                l.addWidget(label("Оригинал сохранён · доступен откат", "small"))
            self.targets_layout.insertWidget(self.targets_layout.count() - 1, w)
        self.install_count.setText("Установки · " + str(len(s.items)))
        ready = bool(s.items) and all(i.state == "patched" for i in s.items)
        cli_only = bool(s.items) and all(i.target.kind == "cli" for i in s.items)
        if not s.items:
            title, detail = "Установки не найдены", "Добавьте файл agy или папку IDE. Для Gemini через agy устанавливать Antigravity IDE не нужно."
            self.hero_kicker.setText("ДОБАВЬТЕ УСТАНОВКУ")
        elif s.running and s.pending:
            title = "Сначала закройте сеансы agy" if cli_only else "Сначала закройте запущенные клиенты"
            detail = "Запущены: " + ", ".join(s.running) + ". После закрытия нажмите «Проверить снова»."
            self.hero_kicker.setText("НУЖНО ВАШЕ ДЕЙСТВИЕ")
        elif s.pending:
            title = "Можно восстановить патч agy" if cli_only else "Можно восстановить патч"
            detail = ("Найден самостоятельный CLI agy. Сохраним оригинал и применим патч. Установка IDE не требуется."
                      if cli_only else "Сохраним оригиналы и изменим только нужные участки файлов. После этого перезапустите используемый клиент.")
            self.hero_kicker.setText("ОБНАРУЖЕНЫ ИСХОДНЫЕ ФАЙЛЫ")
        elif ready:
            title = "Патч agy установлен" if cli_only else "Патч установлен"
            detail = ("CLI готов со стороны патча. Antigravity IDE не требуется. Проверьте подключение ниже и запустите agy."
                      if cli_only else "Все найденные файлы пропатчены. Состояние подключения показано отдельно ниже.")
            self.hero_kicker.setText("ФАЙЛЫ В ПОРЯДКЕ")
        else:
            title, detail = "Нужна дополнительная проверка", "Не все файлы удалось распознать или прочитать. Неизвестные версии остаются без изменений."
            self.hero_kicker.setText("НУЖНО ВАШЕ ВНИМАНИЕ")
        self.hero_title.setText(title)
        self.hero_text.setText(detail)
        self.proxy_badge.setText("Порт доступен" if s.proxy_reachable else "Нет соединения")
        self.proxy_badge.setStyleSheet("color: " + c["green" if s.proxy_reachable else "amber"] + "; font-weight: 600;")
        env = "Адрес сохранён в системе." if s.proxy_env == self.settings.proxy_url else "Адрес в системе отличается от настроек." if s.proxy_env else "Адрес ещё не сохранён в системе."
        service = {"running": "Служба запущена.", "stopped": "Служба остановлена.", "absent": "Встроенная служба не найдена.", "unknown": "Состояние службы неизвестно."}.get(s.service, "")
        self.proxy_detail.setText(self.settings.proxy_url + " · " + env + "\n" + service + " Проверка порта не гарантирует доступ к сервису Google.")
        self.last_check.setText("Проверено в " + s.checked_at + ("  ·  Автопроверка раз в минуту" if self.settings.auto_check else ""))
        self.admin_btn.setVisible(os.name == "nt" and not engine.is_admin() and
                                  any(not i.writable for i in s.items if i.state != "patched"))
        self.refresh_actions()

    def add_menu(self):
        menu = QMenu(self)
        menu.addAction("Выбрать папку или приложение .app…", self.add_directory)
        menu.addAction("Выбрать исполняемый файл…", self.add_file)
        menu.exec(self.add_btn.mapToGlobal(self.add_btn.rect().bottomLeft()))

    def add_directory(self):
        path = QFileDialog.getExistingDirectory(self, "Папка установки IDE или agy", "",
                                                QFileDialog.Option.DontUseNativeDialog)
        if path:
            self.add_path(path)

    def add_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Файл agy или language_server", "", "Все файлы (*)",
                                             options=QFileDialog.Option.DontUseNativeDialog)
        if path:
            self.add_path(path)

    def add_path(self, path):
        if self.busy:
            return
        p = Path(path)
        if p.suffix.lower() == ".app":
            p = p / "Contents/Resources"
        value = str(p.resolve())
        if value not in self.settings.extra_paths:
            updated = copy.deepcopy(self.settings)
            updated.extra_paths.append(value)
            try:
                updated.save(self.backend.directory)
            except OSError as exc:
                self.report("Не удалось сохранить путь: " + str(exc), False)
                return
            self.settings = updated
            self.path_list.addItem(value)
        self.scan()

    def remove_path(self):
        if self.busy:
            return
        row = self.path_list.currentRow()
        if row >= 0:
            self.path_list.takeItem(row)
            self.settings_notice.setText("Нажмите «Сохранить настройки», чтобы применить изменение.")

    def save_settings(self):
        updated = Settings(proxy_url=self.proxy_input.text(), manage_proxy=self.manage_proxy.isChecked(),
                           auto_check=self.auto_check.isChecked(), auto_patch=self.auto_patch.isChecked(),
                           start_at_login=self.login.isChecked(), theme=self.theme.currentData(),
                           extra_paths=[self.path_list.item(i).text() for i in range(self.path_list.count())])
        try:
            from .backend import validate_proxy
            validate_proxy(updated.proxy_url)
            if updated.auto_patch and not updated.auto_check:
                raise ValueError("Для автоматического патча включите автоматическую проверку.")
            changed_login = updated.start_at_login != self.settings.start_at_login
            if changed_login:
                set_login_launch(updated.start_at_login)
            try:
                updated.save(self.backend.directory)
            except OSError:
                if changed_login:
                    set_login_launch(self.settings.start_at_login)
                raise
        except (OSError, ValueError) as exc:
            self.settings_notice.setText(str(exc))
            self.append_log(str(exc))
            return
        self.settings = updated
        self._auto_attempted.clear()
        self.settings_notice.setText("Настройки сохранены. Адрес прокси будет применён кнопкой на экране обзора.")
        self.apply_theme()
        self.append_log("Настройки сохранены.")
        self.scan()

    def run_job(self, fn, callback, message):
        if self.busy:
            return
        self.busy = True
        self.hero_kicker.setText(message)
        self.refresh_actions()
        self.thread = Worker(fn, self)
        self._callback = callback
        self.thread.finished.connect(self.job_finished)
        self.thread.start()

    @Slot()
    def job_finished(self):
        thread = self.thread
        thread.wait()
        result, error = thread.value
        thread.deleteLater()
        self.thread = None
        self.busy = False
        self.refresh_actions()
        if error:
            self.automatic = False
            self.report("Не удалось завершить операцию: " + error, False)
            if self.snapshot:
                self.render(self.snapshot)
            else:
                self.hero_kicker.setText("НУЖНО ВАШЕ ВНИМАНИЕ")
                self.hero_title.setText("Проверка не завершена")
                self.hero_text.setText("Повторите проверку. Подробности доступны в журнале.")
        else:
            if not self.quitting:
                self._callback(result)
        if self.quitting:
            QApplication.instance().quit()

    def scan(self):
        if self.busy:
            return
        settings = copy.deepcopy(self.settings)
        self.run_job(lambda: self.backend.scan(settings), self.scan_finished, "ПРОВЕРЯЕМ СОСТОЯНИЕ")

    def scan_finished(self, snapshot):
        self.render(snapshot)
        summary = "Проверка: установок — %d, нужен патч — %d, порт прокси — %s." % (
            len(snapshot.items), len(snapshot.pending), "доступен" if snapshot.proxy_reachable else "недоступен")
        if summary != getattr(self, "_last_summary", None):
            self.append_log(summary)
            self._last_summary = summary
        if self.automatic:
            self.automatic = False
            self.maybe_auto_patch(snapshot)

    def periodic_check(self):
        if self.settings.auto_check and not self.busy:
            self.automatic = True
            self.scan()

    def maybe_auto_patch(self, snapshot):
        if not self.settings.auto_patch or snapshot.running or not snapshot.pending:
            return
        keys = []
        try:
            for item in snapshot.pending:
                if not item.writable:
                    return
                stat = item.target.path.stat()
                keys.append((str(item.target.path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            return
        fingerprint = tuple(keys)
        if fingerprint in self._auto_attempted:
            return
        self._auto_attempted.add(fingerprint)
        self.apply_patch()

    def apply_patch(self):
        if self.busy or not self.snapshot:
            return
        targets = [i.target for i in self.snapshot.pending]
        settings = copy.deepcopy(self.settings)
        self.run_job(lambda: self.backend.apply(settings, targets), self.operation_finished, "ПРИМЕНЯЕМ ПАТЧ")

    def confirm(self, title, text, action):
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(QMessageBox.Icon.Question)
        yes = box.addButton(action, QMessageBox.ButtonRole.AcceptRole)
        no = box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        return box.clickedButton() == yes

    def restore(self):
        if self.busy or not self.snapshot:
            return
        targets = [i.target for i in self.snapshot.items if i.restorable]
        if not targets or not self.confirm("Восстановление оригиналов", "Будут восстановлены сохранённые файлы этой версии. Региональное ограничение может вернуться. Автоматический патч будет выключен. Настройка прокси останется прежней.", "Восстановить"):
            return
        updated = copy.deepcopy(self.settings)
        updated.auto_patch = False
        try:
            updated.save(self.backend.directory)
        except OSError as exc:
            self.report("Не удалось выключить автопатч: " + str(exc), False)
            return
        self.settings = updated
        self.auto_patch.setChecked(False)
        self.run_job(lambda: self.backend.apply(updated, targets, restore=True), self.operation_finished, "ВОССТАНАВЛИВАЕМ ОРИГИНАЛЫ")

    def clear_proxy(self):
        if self.busy or not self.confirm("Удаление настройки прокси", "Удалить AG_LS_PROXY из пользовательского окружения? Патч и служба прокси останутся без изменений. Автоматическая настройка прокси будет выключена.", "Удалить настройку"):
            return
        updated = copy.deepcopy(self.settings)
        updated.manage_proxy = False
        try:
            updated.save(self.backend.directory)
        except OSError as exc:
            self.report(str(exc), False)
            return
        self.settings = updated
        self.manage_proxy.setChecked(False)
        self.run_job(self.backend.clear_proxy, self.operation_finished, "УДАЛЯЕМ НАСТРОЙКУ ПРОКСИ")

    def operation_finished(self, result: Outcome):
        self.report(result.title, result.ok)
        for line in result.lines:
            self.append_log(line)
        if not result.ok:
            self.notice.setText(result.title + ". Подробности — в журнале.")
        self.settings_notice.setText(result.title)
        if not self.isVisible() and self.tray:
            self.tray.showMessage("ag-repatch", result.title)
        self.scan()

    def report(self, text, ok=True):
        self.notice.setText(text)
        self.notice.show()
        self.append_log(text)
        if not ok:
            self.notice.setStyleSheet("color: " + self.colors["amber"] + ";")
        else:
            self.notice.setStyleSheet("")

    def append_log(self, text):
        from datetime import datetime
        # insertPlainText prevents file paths / OS errors from being interpreted as HTML.
        self.log_view.moveCursor(self.log_view.textCursor().MoveOperation.End)
        self.log_view.insertPlainText(datetime.now().strftime("%H:%M:%S") + "  " + text + "\n\n")
        self.log.info(text)

    def copy_log(self):
        QApplication.clipboard().setText(self.log_view.toPlainText())

    def open_backups(self):
        self.open_directory(self.backend.backups.directory)

    def open_logs(self):
        self.open_directory(self.backend.directory)

    def open_repository(self):
        if not QDesktopServices.openUrl(QUrl(REPOSITORY_URL)):
            self.report("Не удалось открыть браузер. Репозиторий: " + REPOSITORY_URL, False)

    def open_directory(self, path):
        try:
            path.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                raise OSError("Не удалось открыть папку: " + str(path))
        except OSError as exc:
            self.report(str(exc), False)

    def elevate(self):
        if not self.busy and elevate_windows():
            self.quit_app()

    @Slot(QSystemTrayIcon.ActivationReason)
    def tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        if self.busy:
            self.quitting = True
            self.timer.stop()
            self.report("Завершим текущую операцию и закроем приложение.")
        else:
            QApplication.instance().quit()

    def shutdown(self):
        # Native application-menu exits must not destroy a running QThread.
        self.timer.stop()
        if self.thread is not None:
            self.thread.wait()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
            self.report("Дождитесь завершения операции перед закрытием.")
        elif self.tray and self.settings.auto_check:
            event.ignore()
            self.hide()
            if not getattr(self, "_tray_hint_shown", False):
                self.tray.showMessage("ag-repatch", "Проверка продолжается в фоне. Для выхода выберите «Завершить работу» в меню значка.")
                self._tray_hint_shown = True
        else:
            event.accept()
            QApplication.instance().quit()


def main():
    QLocale.setDefault(QLocale(QLocale.Language.Russian, QLocale.Country.Russia))
    app = QApplication(sys.argv)
    app.setApplicationName("ag-repatch")
    app.setOrganizationName("ag-repatch")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI" if os.name == "nt" else "Helvetica Neue" if sys.platform == "darwin" else "DejaVu Sans", 10))
    translator = QTranslator(app)
    translations = str(Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "translations")
    if (translator.load("qtbase_ru", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
            or translator.load("qtbase_ru", translations)):
        app.installTranslator(translator)
    if "--smoke-test" in sys.argv:
        # Packaging check: load Qt plugins, resources and widgets without scanning
        # installations, changing environment, or writing user settings.
        from PySide6.QtWidgets import QDialogButtonBox
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        if buttons.button(QDialogButtonBox.StandardButton.Cancel).text().replace("&", "") != "Отмена":
            raise RuntimeError("Не удалось загрузить русский перевод диалогов Qt.")
        window = MainWindow(settings=Settings(), start_scan=False)
        window.render(Snapshot([], [], None, False, "absent", "00:00"))
        window.show()
        QTimer.singleShot(100, app.quit)
        return app.exec()
    directory = data_directory()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        QMessageBox.critical(None, "Не удалось запустить ag-repatch", "Нет доступа к папке настроек: " + str(exc))
        return 1
    lock = QLockFile(str(directory / "application.lock"))
    if not lock.tryLock(10000 if "--wait-for-exit" in sys.argv else 0):
        box = QMessageBox()
        box.setWindowTitle("ag-repatch уже запущен")
        box.setText("Откройте приложение через его значок в области уведомлений или существующее окно.")
        box.addButton("Понятно", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        return 0
    handler = RotatingFileHandler(directory / "ag-repatch.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("ag-repatch")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    window = MainWindow()
    app.aboutToQuit.connect(window.shutdown)
    if "--background" not in sys.argv or not window.tray:
        window.show()
    result = app.exec()
    lock.unlock()
    return result
