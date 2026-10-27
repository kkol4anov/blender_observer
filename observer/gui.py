import os
import threading
import time
from pathlib import Path
from PySide6.QtCore import Qt, QObject, QThread, Signal, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QAction
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLineEdit, QFileDialog, QMessageBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView, QLabel, QProgressBar, QCheckBox, QSystemTrayIcon,
    QMenu, QStyle,
)
from .storage import Store
from .service import Service
from .monitor import Monitor


class Worker(QObject):
    progress = Signal(str)
    done = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, function, cancel):
        super().__init__()
        self.function, self.cancel = function, cancel

    def run(self):
        try:
            self.done.emit(str(self.function(self.progress.emit, self.cancel.is_set)))
        except Exception as exc:
            self.failed.emit(f'{type(exc).__name__}: {exc}')
        finally:
            self.finished.emit()


class Window(QMainWindow):
    def __init__(self, database):
        super().__init__()
        self.store = Store(database)
        self.service = Service(self.store)
        self.monitor = Monitor()
        self.thread = None
        self.pending_events = []
        self.last_event = self.first_event = 0
        self.last_reconcile = time.monotonic()
        self.setWindowTitle('Blender Observer 2.0 — Blendlib / SQLite')
        self.resize(1250, 780)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        row = QHBoxLayout()
        self.root = QLineEdit(self.store.setting('root'))
        self.root.setReadOnly(True)
        self.root.setPlaceholderText('Выберите папку проекта')
        self.choose = QPushButton('Папка…')
        self.choose.clicked.connect(self.choose_root)
        row.addWidget(self.root, 1)
        row.addWidget(self.choose)
        layout.addLayout(row)
        buttons = QHBoxLayout()
        self.scan_button = QPushButton('Индексировать')
        self.full_button = QPushButton('Перечитать всё')
        self.watch_button = QPushButton('Начать наблюдение')
        self.polling = QCheckBox('Сетевая папка (опрос)')
        self.polling.setChecked(self.store.setting('polling') == '1')
        self.cancel_button = QPushButton('Остановить операцию')
        self.cancel_button.setEnabled(False)
        for w in (self.scan_button, self.full_button, self.watch_button, self.polling, self.cancel_button):
            buttons.addWidget(w)
        layout.addLayout(buttons)
        self.scan_button.clicked.connect(lambda: self.scan(False))
        self.full_button.clicked.connect(lambda: self.scan(True))
        self.watch_button.clicked.connect(self.toggle_watch)
        self.cancel_button.clicked.connect(lambda: self.cancel.set())
        self.search = QLineEdit()
        self.search.setPlaceholderText('Фильтр по пути, имени или ошибке в текущей вкладке')
        self.search.textChanged.connect(self.filter_rows)
        search_row = QHBoxLayout()
        search_row.addWidget(self.search, 1)
        self.all_links = QPushButton('Все зависимости')
        self.all_links.clicked.connect(self.show_all_links)
        search_row.addWidget(self.all_links)
        layout.addLayout(search_row)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.files = self.table(['Файл', 'Библиотек', 'Ошибка'], 'Файлы')
        self.links = self.table(['Файл-владелец', 'Путь в .blend', 'Библиотека', 'Состояние'], 'Зависимости')
        self.repairs = self.table(['ID', 'Файл-владелец', 'Путь в .blend', 'Прежняя библиотека', 'Новая библиотека', 'Ошибка'], 'Исправления')
        self.journal = self.table(['Время', 'Уровень', 'Сообщение'], 'Журнал')
        self.tabs.currentChanged.connect(self.filter_rows)
        self.files.itemSelectionChanged.connect(self.show_file_links)
        self.files.cellDoubleClicked.connect(self.open_folder)
        actions = QHBoxLayout()
        self.target_button = QPushButton('Назначить библиотеку…')
        self.apply_button = QPushButton('Применить выбранные исправления')
        self.remove_button = QPushButton('Убрать выбранные задания')
        self.import_button = QPushButton('Импорт старого JSON…')
        for w in (self.target_button, self.apply_button, self.remove_button, self.import_button):
            actions.addWidget(w)
        layout.addLayout(actions)
        self.target_button.clicked.connect(self.set_target)
        self.apply_button.clicked.connect(self.apply_selected)
        self.remove_button.clicked.connect(self.remove_selected)
        self.import_button.clicked.connect(self.import_json)
        self.status = QLabel('Готово. Выберите папку и запустите индексацию.')
        layout.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        layout.addWidget(self.bar)
        self.details = QLabel(f'База: {database}\nДвойной щелчок по файлу открывает его папку. Запись .blend — только по команде «Применить».')
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.details)
        self.mutators = [self.choose, self.scan_button, self.full_button, self.watch_button,
                         self.polling, self.target_button, self.apply_button, self.remove_button, self.import_button]
        self.tray = QSystemTrayIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon), self)
        menu = QMenu(self)
        show = QAction('Показать Blender Observer', self)
        show.triggered.connect(self.showNormal)
        menu.addAction(show)
        quit_action = QAction('Выход', self)
        quit_action.triggered.connect(self.close)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda _: self.showNormal())
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
            hide = QPushButton('Свернуть в трей')
            hide.clicked.connect(self.hide)
            actions.addWidget(hide)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self.refresh()

    def table(self, headers, title):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setStretchLastSection(True)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)
        self.tabs.addTab(table, title)
        return table

    def fill(self, table, rows):
        table.blockSignals(True)
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                item = QTableWidgetItem(str(value if value is not None else ''))
                item.setToolTip(item.text())
                table.setItem(i, j, item)
        table.setSortingEnabled(True)
        table.blockSignals(False)
        for j in range(table.columnCount()):
            table.setColumnWidth(j, 280 if j else 300)

    def refresh(self):
        self.fill(self.files, [(r['path'], r['n'], r['error']) for r in self.store.rows(
            'SELECT f.*,count(l.raw) n FROM files f LEFT JOIN links l ON f.path=l.owner GROUP BY f.path ORDER BY f.path')])
        self.load_links()
        self.fill(self.repairs, [tuple(r.values()) for r in self.store.rows('SELECT id,owner,raw,old_target,new_target,error FROM repairs ORDER BY id')])
        self.repairs.setColumnWidth(0, 55)
        self.fill(self.journal, [tuple(r.values()) for r in self.store.rows('SELECT time,level,message FROM journal ORDER BY id DESC LIMIT 1000')])
        self.journal.setColumnWidth(0, 160)
        self.journal.setColumnWidth(1, 80)
        self.filter_rows()

    def load_links(self, owner=None):
        rows = self.store.rows('SELECT l.*,s.available FROM links l LEFT JOIN target_status s ON s.target=l.target' + (' WHERE owner=?' if owner else '') + ' ORDER BY owner,l.target', (owner,) if owner else ())
        states = {1: 'Найдена', 0: 'Не найдена / недоступна', None: 'Не проверена'}
        self.fill(self.links, [(r['owner'], r['raw'], r['target'], states[r['available']]) for r in rows])

    def show_all_links(self):
        self.load_links()
        self.tabs.setCurrentWidget(self.links)
        self.filter_rows()

    def show_file_links(self):
        rows = self.selected(self.files)
        if len(rows) == 1:
            self.load_links(self.files.item(rows[0], 0).text())

    def filter_rows(self, *_):
        table = self.tabs.currentWidget()
        query = self.search.text().casefold()
        for row in range(table.rowCount()):
            table.setRowHidden(row, not any(query in table.item(row, col).text().casefold() for col in range(table.columnCount()) if table.item(row, col)))

    @staticmethod
    def selected(table):
        return sorted({i.row() for i in table.selectionModel().selectedRows() if not table.isRowHidden(i.row())})

    def open_folder(self, row, _):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.files.item(row, 0).text()).parent)))

    def choose_root(self):
        path = QFileDialog.getExistingDirectory(self, 'Папка проекта', self.root.text())
        if path:
            self.monitor.stop()
            self.pending_events.clear()
            self.monitor.drain()
            self.watch_button.setText('Начать наблюдение')
            self.root.setText(path)
            self.store.set_setting('root', path)

    def launch(self, function):
        if self.thread:
            return
        self.cancel = threading.Event()
        self.thread = QThread(self)
        self.worker = Worker(function, self.cancel)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.status.setText)
        self.worker.done.connect(self.status.setText)
        self.worker.failed.connect(self.report_error)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.finished)
        for w in self.mutators:
            w.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.bar.setRange(0, 0)
        self.thread.start()

    def finished(self):
        self.thread.deleteLater()
        self.thread = None
        self.worker = None
        for w in self.mutators:
            w.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        self.refresh()

    def report_error(self, message):
        self.store.log('ERROR', message)
        self.status.setText(message)
        QMessageBox.warning(self, 'Ошибка', message)

    def scan(self, force=False):
        root = self.root.text()
        if not os.path.isdir(root):
            self.report_error('Сначала выберите доступную папку проекта')
            return
        self.launch(lambda progress, cancel: self.service.scan(root, progress, cancel, force))

    def toggle_watch(self):
        if self.monitor.observer:
            self.monitor.stop()
            self.watch_button.setText('Начать наблюдение')
            self.status.setText('Наблюдение остановлено')
            return
        try:
            if not os.path.isdir(self.root.text()):
                raise ValueError('Выберите доступную папку проекта')
            self.store.set_setting('polling', int(self.polling.isChecked()))
            self.monitor.start(self.root.text(), self.polling.isChecked())
            self.watch_button.setText('Остановить наблюдение')
            self.scan()
        except Exception as exc:
            self.report_error(str(exc))

    def tick(self):
        events = self.monitor.drain()
        now = time.monotonic()
        if events:
            if not self.pending_events:
                self.first_event = now
            self.pending_events.extend(events)
            self.last_event = now
        reconcile = bool(self.monitor.observer) and now - self.last_reconcile >= 60
        settled = self.pending_events and (now - self.last_event >= 2 or now - self.first_event >= 10)
        if self.thread or not (settled or reconcile):
            return
        events, self.pending_events = self.pending_events, []
        root = self.root.text()
        self.last_reconcile = now
        def work(progress, cancel):
            for kind, src, dest, directory in events:
                if kind == 'moved':
                    try:
                        self.service.moved(src, dest, directory)
                    except Exception as exc:
                        self.store.log('ERROR', f'Перемещение {src} → {dest}: {exc}')
            return self.service.scan(root, progress, cancel)
        self.launch(work)

    def set_target(self):
        table = self.tabs.currentWidget()
        if table not in (self.links, self.repairs):
            QMessageBox.information(self, 'Библиотека', 'Выберите строки на вкладке «Зависимости» или «Исправления».')
            return
        rows = self.selected(table)
        if not rows:
            return
        target, _ = QFileDialog.getOpenFileName(self, 'Новая библиотека для выбранных строк', self.root.text(), 'Blender (*.blend *.BLEND)')
        if not target:
            return
        try:
            for row in rows:
                if table == self.links:
                    self.service.queue(table.item(row, 0).text(), table.item(row, 1).text(), target)
                else:
                    with self.store.connect() as db:
                        from .paths import canonical
                        db.execute('UPDATE repairs SET new_target=?,error=\'\' WHERE id=?', (canonical(target), int(table.item(row, 0).text())))
            self.refresh()
            self.tabs.setCurrentWidget(self.repairs)
        except Exception as exc:
            self.report_error(str(exc))

    def apply_selected(self):
        ids = [int(self.repairs.item(r, 0).text()) for r in self.selected(self.repairs)]
        if not ids:
            QMessageBox.information(self, 'Исправления', 'Выберите задания на вкладке «Исправления».')
            return
        if QMessageBox.question(self, 'Записать изменения?', f'Исправить выбранные ссылки: {len(ids)}?\nЗакройте эти файлы в Blender и остановите запись другими программами.\nРядом с каждым изменяемым файлом будет сохранена копия .observer-….bak.') != QMessageBox.StandardButton.Yes:
            return
        self.launch(lambda progress, cancel: self.service.apply(ids, progress, cancel))

    def remove_selected(self):
        ids = [int(self.repairs.item(r, 0).text()) for r in self.selected(self.repairs)]
        with self.store.connect() as db:
            db.executemany('DELETE FROM repairs WHERE id=?', ((i,) for i in ids))
        self.refresh()

    def import_json(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Старый индекс', str(Path(__file__).parent.parent / 'legacy'), 'JSON (*.json)')
        if path:
            self.launch(lambda p, c: self.store.import_legacy(path) or 'JSON импортирован. Выполните полную индексацию.')

    def closeEvent(self, event):
        if self.thread:
            self.cancel.set()
            self.status.setText('Остановка… Дождитесь окончания текущего файла и закройте окно повторно.')
            event.ignore()
            return
        self.timer.stop()
        self.monitor.stop()
        # Persist any moves received just before shutdown; never modify blends here.
        for kind, src, dest, directory in self.pending_events + self.monitor.drain():
            if kind == 'moved':
                try:
                    self.service.moved(src, dest, directory)
                except Exception as exc:
                    self.store.log('ERROR', str(exc))
        self.tray.hide()
        event.accept()
