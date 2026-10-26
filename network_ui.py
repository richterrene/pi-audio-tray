"""On-demand UPnP source management over the existing verified SSH connection."""
import threading
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout
from core import display_text, music_request


class NetworkEvents(QObject):
    finished = Signal(object, object)


class NetworkWindow(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('Pi Audio · Network music')
        self.resize(760, 560)
        self.server = None
        self.history = [('0', 'Library')]
        self.offset = 0
        self.more = False
        self.busy = False
        self.pending = []
        self.connection = None
        self.events = NetworkEvents(self)
        self.events.finished.connect(self.operation_done)
        layout = QVBoxLayout(self)
        note = QLabel('Discover UPnP/DLNA servers, then enable a trusted server to browse it.\n'
                      'Tracks and albums join the same queue as USB music and the web player.')
        note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(note)
        self.sources = QListWidget()
        self.sources.setMaximumHeight(130)
        self.sources.itemDoubleClicked.connect(self.open_source)
        layout.addWidget(self.sources)
        bar = QHBoxLayout()
        for title, callback in [('Discover servers', lambda: self.request({'action': 'network_discover'})),
                                ('Enabled servers', lambda: self.request({'action': 'network_sources'})),
                                ('Enable / reconnect', self.enable), ('Remove source', self.remove)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            bar.addWidget(button)
        layout.addLayout(bar)
        self.location = QLabel('Double-click an enabled server to browse.')
        self.location.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.location)
        self.items = QListWidget()
        self.items.itemDoubleClicked.connect(self.activate)
        layout.addWidget(self.items)
        bar = QHBoxLayout()
        for title, callback in [('Up', self.up), ('Previous page', lambda: self.page(-100)),
                                ('Next page', lambda: self.page(100)), ('Refresh', self.browse),
                                ('Add to queue', lambda: self.add(False)), ('Play selected', lambda: self.add(True))]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            bar.addWidget(button)
        layout.addLayout(bar)
        self.message = QLabel('Discovery runs only when requested. Servers must be awake and reachable from the Pi.')
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

    def request(self, request):
        if not self.isVisible():
            return
        if len(self.pending) >= 16:
            self.message.setText('Please wait for pending requests to finish.')
            return
        config = self.parent().config.copy()
        generation = getattr(self.parent(), 'generation', 0)
        if self.connection != (config, generation):
            self.connection = (config.copy(), generation)
            self.pending.clear()
            self.server = None
            self.sources.clear()
            self.items.clear()
            self.location.setText('Select a source for this Pi connection.')
            if request['action'] not in ('network_sources', 'network_discover'):
                self.message.setText('Connection changed. Reload enabled servers first.')
                return
        self.pending.append((request.copy(), config, generation))
        self.start_pending()

    def start_pending(self):
        if self.busy:
            return
        if not self.isVisible():
            self.pending.clear()
            return
        while self.pending:
            request, config, generation = self.pending.pop(0)
            if config != self.parent().config or generation != getattr(self.parent(), 'generation', 0):
                continue
            self.busy = True
            self.message.setText('Discovering media servers…' if request['action'] == 'network_discover' else 'Contacting Pi…')
            def work():
                try:
                    result = music_request(config, request)
                    self.events.finished.emit((request, config, generation, result), None)
                except Exception as exc:
                    self.events.finished.emit((request, config, generation, None), exc)
            threading.Thread(target=work, daemon=True).start()
            return

    def operation_done(self, payload, error):
        request, config, generation, result = payload
        try:
            if config != self.parent().config or generation != getattr(self.parent(), 'generation', 0):
                return
            if error:
                self.message.setText('Network music: ' + display_text(error))
                return
            action = request['action']
            if action in ('network_sources', 'network_discover'):
                rows = result.get('sources')
                if not isinstance(rows, list) or len(rows) > 32:
                    raise ValueError('Invalid source list')
                self.sources.clear()
                for row in rows:
                    if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ('id', 'name', 'address')):
                        raise ValueError('Invalid source')
                    item = QListWidgetItem(('✓ ' if row.get('enabled') else '+ ') + display_text(row['name']) + ' · ' + display_text(row['address']))
                    item.setData(Qt.ItemDataRole.UserRole, row)
                    self.sources.addItem(item)
                self.message.setText(f'{len(rows)} server(s). Select one, enable it, then double-click to browse.')
            elif action == 'network_browse':
                if not self.server or request['server'] != self.server['id'] or request['object'] != self.history[-1][0] or request['offset'] != self.offset:
                    return
                rows = result.get('items')
                if not isinstance(rows, list) or len(rows) > 100:
                    raise ValueError('Invalid network music page')
                self.items.clear()
                for row in rows:
                    if not isinstance(row, dict) or not isinstance(row.get('object'), str) or not isinstance(row.get('Title'), str):
                        raise ValueError('Invalid network music entry')
                    title = ('[Folder] ' if row.get('folder') else '') + display_text(row['Title'])
                    if row.get('Artist'):
                        title += ' — ' + display_text(row['Artist'])
                    item = QListWidgetItem(title)
                    item.setData(Qt.ItemDataRole.UserRole, row)
                    self.items.addItem(item)
                self.more = result.get('more') is True
                self.location.setText(display_text(self.server['name']) + ' / ' + ' / '.join(display_text(x[1]) for x in self.history) + f' · {len(rows)} entries')
                self.message.setText('Double-click a folder to browse or a track to play. Add an album or track to the shared queue.')
            elif action in ('network_enable', 'network_remove'):
                if action == 'network_remove' and self.server and self.server['id'] == request['server']:
                    self.server = None
                    self.items.clear()
                    self.location.setText('Source removed. Already queued tracks remain in the queue.')
                self.request({'action': 'network_sources'})
            else:
                self.message.setText('Added to the shared queue.' + (' Playback started.' if action == 'network_play' else ''))
        except (TypeError, ValueError, KeyError) as exc:
            self.message.setText(display_text(exc))
        finally:
            self.busy = False
            self.start_pending()

    def selected_source(self):
        item = self.sources.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def enable(self):
        row = self.selected_source()
        if row:
            self.request({'action': 'network_enable', 'server': row['id']})

    def remove(self):
        row = self.selected_source()
        if row:
            self.request({'action': 'network_remove', 'server': row['id']})

    def open_source(self, item):
        row = item.data(Qt.ItemDataRole.UserRole)
        if not row.get('enabled'):
            self.message.setText('Enable this server first. Only enable media servers you trust.')
            return
        self.server = row.copy()
        self.history = [('0', 'Library')]
        self.offset = 0
        self.browse()

    def browse(self):
        if self.server:
            self.request({'action': 'network_browse', 'server': self.server['id'], 'object': self.history[-1][0], 'offset': self.offset})

    def activate(self, item):
        row = item.data(Qt.ItemDataRole.UserRole)
        if row.get('folder'):
            self.history.append((row['object'], row['Title']))
            self.offset = 0
            self.browse()
        else:
            self.add(True)

    def add(self, play):
        item = self.items.currentItem()
        if self.server and item:
            row = item.data(Qt.ItemDataRole.UserRole)
            self.request({'action': 'network_play' if play else 'network_add', 'server': self.server['id'], 'object': row['object']})

    def up(self):
        if len(self.history) > 1:
            self.history.pop()
            self.offset = 0
            self.browse()

    def page(self, delta):
        if delta > 0 and not self.more:
            return
        self.offset = max(0, self.offset + delta)
        self.browse()

    def showEvent(self, event):
        super().showEvent(event)
        self.request({'action': 'network_sources'})

    def hideEvent(self, event):
        self.pending.clear()
        super().hideEvent(event)
