"""Optional local-library window. Playback details are polled only while visible."""
from pathlib import PurePosixPath
import threading
from PySide6.QtCore import QObject, Qt, QTimer, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QSpinBox, QTabWidget, QVBoxLayout)
from core import display_text, music_request, music_web_url


class MusicEvents(QObject):
    finished = Signal(object, object)


class MusicWindow(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('Pi Audio · USB music')
        self.resize(780, 580)
        self.path, self.offset, self.more, self.busy = '', 0, False, False
        self.queue_offset = 0
        self.events = MusicEvents(self)
        self.events.finished.connect(self.operation_done)
        layout = QVBoxLayout(self)
        note = QLabel('The phone interface and this window share one player and queue.\n'
                      'Pause Spotify or Bluetooth playback before starting local music.')
        note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(note)
        self.now = QLabel('Player not checked yet')
        self.now.setTextFormat(Qt.TextFormat.PlainText)
        self.now.setWordWrap(True)
        layout.addWidget(self.now)
        controls = QHBoxLayout()
        self.buttons = []
        for text, action in [('Previous', 'previous'), ('Play', 'play'), ('Pause', 'pause'), ('Stop', 'stop'), ('Next', 'next')]:
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, a=action: self.request({'action': a}))
            controls.addWidget(button)
            self.buttons.append(button)
        controls.addWidget(QLabel('Volume'))
        self.volume = QSpinBox()
        self.volume.setRange(0, 100)
        self.volume.setSuffix(' %')
        self.volume.editingFinished.connect(lambda: self.request({'action': 'volume', 'value': self.volume.value()}))
        controls.addWidget(self.volume)
        layout.addLayout(controls)
        self.location = QLabel('Library')
        self.location.setTextFormat(Qt.TextFormat.PlainText)
        self.location.setWordWrap(True)
        layout.addWidget(self.location)
        self.tabs = QTabWidget()
        self.library, self.queue = QListWidget(), QListWidget()
        self.tabs.addTab(self.library, 'Library')
        self.tabs.addTab(self.queue, 'Queue')
        self.tabs.currentChanged.connect(self.reload_page)
        self.library.itemDoubleClicked.connect(self.activate_library)
        self.queue.itemDoubleClicked.connect(self.activate_queue)
        layout.addWidget(self.tabs)
        navigation = QHBoxLayout()
        for text, callback in [('Up', self.up), ('Previous page', lambda: self.page(-100)),
                                ('Next page', lambda: self.page(100)), ('Refresh', self.reload_page)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            navigation.addWidget(button)
            self.buttons.append(button)
        layout.addLayout(navigation)
        navigation = QHBoxLayout()
        for text, callback in [('Add selected to queue', self.add_selected), ('Rescan USB', lambda: self.request({'action': 'update'})),
                                ('Open web player', self.open_web)]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            navigation.addWidget(button)
            self.buttons.append(button)
        layout.addLayout(navigation)
        self.message = QLabel('Double-click a folder to browse, or a track to play it.')
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh_status)

    def request(self, request):
        if self.busy or not self.isVisible() or self.isMinimized():
            return
        self.busy = True
        self.tabs.setEnabled(False)
        config = self.parent().config.copy()
        for button in self.buttons:
            button.setEnabled(False)
        self.volume.setEnabled(False)
        def work():
            try:
                result = music_request(config, request)
                status = result if request['action'] == 'status' else music_request(config, {'action': 'status'})
                self.events.finished.emit((request, result, status, config), None)
            except Exception as exc:
                self.events.finished.emit((request, None, None, config), exc)
        threading.Thread(target=work, daemon=True).start()

    def operation_done(self, payload, error):
        self.busy = False
        self.tabs.setEnabled(True)
        for button in self.buttons:
            button.setEnabled(True)
        self.volume.setEnabled(True)
        request, result, status, config = payload
        if config != self.parent().config:
            return
        if error:
            self.message.setText('Music unavailable: ' + display_text(error) +
                                 ' · The Pi needs the optional local-music setup. See docs/LOCAL_MUSIC.md.')
            return
        if not isinstance(status, dict) or not isinstance(status.get('status'), dict) or not isinstance(status.get('current'), dict):
            self.message.setText('Invalid music status received')
            return
        current, state = status['current'], status['status']
        title = current.get('Title') or current.get('file') or 'No track selected'
        self.now.setText(display_text(state.get('state', 'unknown')) + ' · ' + display_text(title) +
                         (' — ' + display_text(current['Artist']) if current.get('Artist') else ''))
        try:
            volume = int(state.get('volume', -1))
            if 0 <= volume <= 100 and not self.volume.hasFocus():
                self.volume.setValue(volume)
        except (ValueError, TypeError):
            pass
        action = request['action']
        if action in ('browse', 'queue'):
            widget = self.library if action == 'browse' else self.queue
            items = result.get('items')
            if not isinstance(items, list) or len(items) > 100 or any(not isinstance(x, dict) for x in items):
                self.message.setText('Invalid music page received')
                return
            widget.clear()
            for row in items:
                path = row.get('directory') or row.get('file', '')
                title = row.get('Title') or PurePosixPath(str(path)).name
                text = ('[Folder] ' if 'directory' in row else '') + display_text(title)
                if row.get('Artist'):
                    text += ' — ' + display_text(row['Artist'])
                item = QListWidgetItem(text, widget)
                item.setData(Qt.ItemDataRole.UserRole, row)
            self.more = result.get('more') is True
            self.location.setText(('Library / ' + self.path if action == 'browse' else 'Queue') +
                                  f' · entries {request.get("offset", 0) + 1}–{request.get("offset", 0) + len(items)}')
            self.message.setText('Double-click a folder to browse, or a track to play it.' +
                                 (' More entries on the next page.' if self.more else ''))
        elif action != 'status':
            self.message.setText('Player updated.')
            self.reload_page()

    def reload_page(self, *args):
        if self.tabs.currentIndex() == 0:
            self.request({'action': 'browse', 'path': self.path, 'offset': self.offset})
        else:
            self.request({'action': 'queue', 'offset': self.queue_offset})

    def refresh_status(self):
        if self.isVisible() and not self.isMinimized():
            # Queue changes from the phone become visible when its tab is open.
            if self.tabs.currentIndex() == 1:
                self.reload_page()
            else:
                self.request({'action': 'status'})

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

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()
        self.reload_page()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)
