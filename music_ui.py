"""Optional local-library window. Playback details are polled only while visible."""
from pathlib import PurePosixPath
import threading
from i18n import tr
from PySide6.QtCore import QObject, Qt, QTimer, Signal, QUrl, QSignalBlocker
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QSpinBox, QTabWidget, QVBoxLayout)
from core import connection_key, display_text, music_request, music_web_url

READ_ACTIONS = frozenset(('browse', 'queue', 'status'))


class MusicEvents(QObject):
    finished = Signal(object, object)


class MusicWindow(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(tr('Pi Audio · Music'))
        self.resize(780, 580)
        self.path, self.offset, self.more, self.busy = '', 0, False, False
        self.queue_offset = 0
        self.pending_requests = []
        self.active_background = False
        self.page_contexts = {}
        self.page_more = {}
        self.events = MusicEvents(self)
        self.events.finished.connect(self.operation_done)
        layout = QVBoxLayout(self)
        note = QLabel(tr('The phone interface and this window share one player and queue.\n'
                      'Pause Spotify or Bluetooth playback before starting local music.'))
        note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(note)
        self.now = QLabel(tr('Player not checked yet'))
        self.now.setTextFormat(Qt.TextFormat.PlainText)
        self.now.setWordWrap(True)
        layout.addWidget(self.now)
        controls = QHBoxLayout()
        self.buttons = []
        for text, action in [(tr('Previous'), 'previous'), (tr('Play'), 'play'), (tr('Pause'), 'pause'), (tr('Stop'), 'stop'), (tr('Next'), 'next')]:
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, a=action: self.request({'action': a}))
            controls.addWidget(button)
            self.buttons.append(button)
        controls.addWidget(QLabel(tr('Volume')))
        self.volume = QSpinBox()
        self.volume.setRange(0, 100)
        self.volume.setSuffix(' %')
        self.volume.editingFinished.connect(lambda: self.request({'action': 'volume', 'value': self.volume.value()}))
        controls.addWidget(self.volume)
        layout.addLayout(controls)
        self.location = QLabel(tr('Library'))
        self.location.setTextFormat(Qt.TextFormat.PlainText)
        self.location.setWordWrap(True)
        layout.addWidget(self.location)
        self.tabs = QTabWidget()
        self.library, self.queue = QListWidget(), QListWidget()
        self.tabs.addTab(self.library, tr('Library'))
        self.tabs.addTab(self.queue, tr('Queue'))
        self.tabs.currentChanged.connect(self.reload_page)
        self.library.itemDoubleClicked.connect(self.activate_library)
        self.queue.itemDoubleClicked.connect(self.activate_queue)
        layout.addWidget(self.tabs)
        navigation = QHBoxLayout()
        for text, callback in [(tr('Up'), self.up), (tr('Previous page'), lambda: self.page(-100)),
                                (tr('Next page'), lambda: self.page(100)), (tr('Refresh'), self.reload_page)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            navigation.addWidget(button)
            self.buttons.append(button)
        layout.addLayout(navigation)
        navigation = QHBoxLayout()
        for text, callback in [(tr('Add selected to queue'), self.add_selected), (tr('Rescan USB'), lambda: self.request({'action': 'update'})),
                                (tr('Open web player'), self.open_web)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            navigation.addWidget(button)
            self.buttons.append(button)
        layout.addLayout(navigation)
        self.message = QLabel(tr('Double-click a folder to browse, or a track to play it.'))
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        network = QPushButton(tr('Network music…'))
        network.clicked.connect(self.open_network)
        layout.addWidget(network)
        self.network_window = None
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh_status)

    def reset_connection(self):
        """Drop old library/queue data without destroying an in-flight worker."""
        self.pending_requests.clear()
        self.path, self.offset, self.queue_offset, self.more = '', 0, 0, False
        self.page_contexts.clear()
        self.page_more.clear()
        self.library.clear()
        self.queue.clear()
        self.tabs.setCurrentIndex(0)
        self.location.setText(tr('Library'))
        self.now.setText(tr('Player not checked yet'))
        self.message.setText(tr('Double-click a folder to browse, or a track to play it.'))
        self.volume.setValue(0)
        if self.network_window is not None:
            self.network_window.reset_connection()

    def request(self, request, background=False):
        if not self.isVisible() or self.isMinimized():
            return
        if background and (self.busy or self.pending_requests):
            return
        request = request.copy()
        action = request.get('action')
        # Coalesce obsolete page reads/volume edits while retaining user commands.
        if action in READ_ACTIONS:
            self.pending_requests = [job for job in self.pending_requests
                                     if job[0].get('action') not in READ_ACTIONS]
        elif action == 'volume':
            self.pending_requests = [job for job in self.pending_requests if job[0].get('action') != 'volume']
        if len(self.pending_requests) >= 32:
            self.message.setText(tr('Too many pending commands. Wait for the Pi to respond.'))
            return
        config = self.parent().config.copy()
        generation = getattr(self.parent(), 'generation', 0)
        self.pending_requests.append((request, config, generation, background))
        self.start_pending()

    def start_pending(self):
        if self.busy:
            return
        if not self.isVisible() or self.isMinimized():
            self.pending_requests = [job for job in self.pending_requests if job[0].get('action') not in READ_ACTIONS]
        while self.pending_requests:
            # Playback commands take priority over queued navigation reads.
            index = next((i for i, job in enumerate(self.pending_requests)
                          if job[0].get('action') not in READ_ACTIONS), 0)
            request, config, generation, background = self.pending_requests.pop(index)
            if connection_key(config) != connection_key(self.parent().config) or generation != getattr(self.parent(), 'generation', 0):
                continue
            self.busy, self.active_background = True, background
            def work():
                try:
                    result = music_request(config, request)
                    status = result if request['action'] == 'status' else music_request(config, {'action': 'status'})
                    self.events.finished.emit((request, result, status, config, generation), None)
                except Exception as exc:
                    self.events.finished.emit((request, None, None, config, generation), exc)
            threading.Thread(target=work, daemon=True).start()
            return

    def operation_done(self, payload, error):
        background = self.active_background
        self.active_background = False
        # Keep completion callbacks from starting another read ahead of queued controls.
        self.busy = True
        try:
            self.apply_result(payload, error, background)
        finally:
            self.busy = False
            self.start_pending()

    def apply_result(self, payload, error, background=False):
        request, result, status, config, *generation = payload
        if connection_key(config) != connection_key(self.parent().config) or (generation and generation[0] != getattr(self.parent(), 'generation', 0)):
            return
        if error:
            self.message.setText(tr('Music unavailable: ') + display_text(error) +
                                 tr(' · The Pi needs the optional local-music setup. See docs/LOCAL_MUSIC.md.'))
            return
        if not isinstance(status, dict) or not isinstance(status.get('status'), dict) or not isinstance(status.get('current'), dict):
            self.message.setText(tr('Invalid music status received'))
            return
        current, state = status['current'], status['status']
        title = current.get('Title') or current.get('file') or tr('No track selected')
        self.now.setText(tr(display_text(state.get('state', 'unknown'))) + ' · ' + display_text(title) +
                         (' — ' + display_text(current['Artist']) if current.get('Artist') else ''))
        try:
            volume = int(state.get('volume', -1))
            volume_pending = any(job[0].get('action') == 'volume' for job in self.pending_requests)
            if 0 <= volume <= 100 and not volume_pending and not self.volume.hasFocus() and not self.volume.lineEdit().hasFocus():
                self.volume.setValue(volume)
        except (ValueError, TypeError):
            pass
        action = request['action']
        if action in ('browse', 'queue'):
            # A navigation request may finish after the user has changed folder/tab/page.
            if action == 'browse':
                context = (request.get('path', ''), request.get('offset', 0))
                if self.tabs.currentIndex() != 0 or context != (self.path, self.offset):
                    return
                widget = self.library
            else:
                context = (request.get('offset', 0),)
                if self.tabs.currentIndex() != 1 or context != (self.queue_offset,):
                    return
                widget = self.queue
            items = result.get('items')
            if not isinstance(items, list) or len(items) > 100 or any(
                    not isinstance(row, dict) or not isinstance(row.get('directory', row.get('file')), str)
                    for row in items):
                self.message.setText(tr('Invalid music page received'))
                return
            preserve = self.page_contexts.get(action) == context
            self.update_items(widget, items, preserve)
            self.page_contexts[action] = context
            self.more = result.get('more') is True
            self.page_more[action] = (context, self.more)
            count = tr('entries {first}–{last}', first=request.get('offset', 0) + 1, last=request.get('offset', 0) + len(items)) if items else tr('No entries')
            self.location.setText((tr('Library / ') + self.path if action == 'browse' else tr('Queue')) + ' · ' + count)
            if not background:
                self.message.setText(tr('Double-click a folder to browse, or a track to play it.') +
                                     (tr(' More entries on the next page.') if self.more else ''))
        elif action != 'status':
            self.message.setText(tr('USB rescan requested. Refresh the library after the scan finishes.') if action == 'update' else tr('Player updated.'))
            self.reload_page()

    @staticmethod
    def item_key(row):
        if 'Id' in row:
            return ('id', str(row['Id']))
        return ('directory', row['directory']) if 'directory' in row else ('file', row.get('file', ''))

    def update_items(self, widget, rows, preserve):
        existing = [widget.item(i).data(Qt.ItemDataRole.UserRole) for i in range(widget.count())]
        if preserve and existing == rows:
            return
        selected = widget.currentItem()
        selected_key = self.item_key(selected.data(Qt.ItemDataRole.UserRole)) if preserve and selected else None
        scroll = widget.verticalScrollBar().value() if preserve else 0
        blocker = QSignalBlocker(widget)
        for index, row in enumerate(rows):
            key = self.item_key(row)
            item = widget.item(index)
            if item is None or self.item_key(item.data(Qt.ItemDataRole.UserRole)) != key:
                found = next((i for i in range(index + 1, widget.count())
                              if self.item_key(widget.item(i).data(Qt.ItemDataRole.UserRole)) == key), None)
                item = widget.takeItem(found) if found is not None else QListWidgetItem()
                widget.insertItem(index, item)
            path = row.get('directory') or row.get('file', '')
            title = row.get('Title') or PurePosixPath(str(path)).name
            text = (tr('[Folder] ') if 'directory' in row else '') + display_text(title)
            if row.get('Artist'):
                text += ' — ' + display_text(row['Artist'])
            if item.text() != text:
                item.setText(text)
            if item.data(Qt.ItemDataRole.UserRole) != row:
                item.setData(Qt.ItemDataRole.UserRole, row)
        while widget.count() > len(rows):
            widget.takeItem(widget.count() - 1)
        selected_row = next((i for i, row in enumerate(rows) if self.item_key(row) == selected_key), -1)
        widget.setCurrentRow(selected_row)
        widget.verticalScrollBar().setValue(scroll)
        del blocker

    def reload_page(self, *args, background=False):
        if self.tabs.currentIndex() == 0:
            context = (self.path, self.offset)
            self.more = self.page_more.get('browse', (None, False)) == (context, True)
            self.request({'action': 'browse', 'path': self.path, 'offset': self.offset}, background=background)
        else:
            self.more = self.page_more.get('queue', (None, False)) == ((self.queue_offset,), True)
            self.request({'action': 'queue', 'offset': self.queue_offset}, background=background)

    def refresh_status(self):
        if self.isVisible() and not self.isMinimized():
            if self.tabs.currentIndex() == 1:
                self.reload_page(background=True)
            else:
                self.request({'action': 'status'}, background=True)

    def activate_library(self, item):
        row = item.data(Qt.ItemDataRole.UserRole)
        if 'directory' in row:
            self.path, self.offset = row['directory'], 0
            self.reload_page()
        elif 'file' in row:
            self.request({'action': 'play_file', 'path': row['file']})

    def activate_queue(self, item):
        try:
            song_id = int(item.data(Qt.ItemDataRole.UserRole)['Id'])
        except (KeyError, ValueError, TypeError):
            return
        self.request({'action': 'play_id', 'value': song_id})

    def add_selected(self):
        item = self.library.currentItem()
        if self.tabs.currentIndex() == 0 and item:
            row = item.data(Qt.ItemDataRole.UserRole)
            self.request({'action': 'add', 'path': row.get('directory') or row.get('file')})

    def up(self):
        self.path, self.offset = str(PurePosixPath(self.path).parent), 0
        if self.path == '.':
            self.path = ''
        self.reload_page()

    def page(self, delta):
        if delta > 0 and not self.more:
            return
        if self.tabs.currentIndex() == 0:
            self.offset = max(0, self.offset + delta)
        else:
            self.queue_offset = max(0, self.queue_offset + delta)
        self.reload_page()

    def open_web(self):
        try:
            QDesktopServices.openUrl(QUrl(music_web_url(self.parent().config)))
        except ValueError as exc:
            self.message.setText(display_text(exc))

    def open_network(self):
        from network_ui import NetworkWindow
        if self.network_window is None:
            self.network_window = NetworkWindow(self.parent())
        self.network_window.show()
        self.network_window.raise_()
        self.network_window.activateWindow()

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()
        self.reload_page()

    def hideEvent(self, event):
        self.pending_requests = [job for job in self.pending_requests if job[0].get('action') not in READ_ACTIONS]
        self.timer.stop()
        if self.network_window is not None:
            self.network_window.hide()
        super().hideEvent(event)
