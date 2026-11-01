"""Explicit Bluetooth discovery and device actions, scoped to one selected Pi."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHeaderView, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem)
import bluetooth_devices as bluetooth
from core import display_text
from i18n import tr
from maintenance_ui import MaintenanceDialog


class BluetoothDialog(MaintenanceDialog):
    def __init__(self, config, parent=None):
        super().__init__(config, parent)
        self.setWindowTitle(tr('Bluetooth devices'))
        self.resize(760, 540)
        self.reading = False
        self.pending_command = None
        self.close_after = False
        self.pending_pair = None
        self.mode = QComboBox()
        self.mode.addItem(tr('All audio devices'), 'all')
        self.mode.addItem(tr('Audio sources (phones / computers)'), 'source')
        self.mode.addItem(tr('Audio outputs (headphones / speakers)'), 'output')
        self.body.addWidget(self.mode)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr('Device'), tr('Type'), tr('Connection'), tr('Automatic connection')])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.body.addWidget(self.table, 1)
        output_row = QHBoxLayout()
        self.output = QComboBox()
        self.output_button = QPushButton(tr('Use audio output'))
        output_row.addWidget(self.output, 1)
        output_row.addWidget(self.output_button)
        self.body.addLayout(output_row)
        self.output_button.clicked.connect(self.set_output)
        self.automatic = QCheckBox(tr('Automatically connect the selected device'))
        self.body.addWidget(self.automatic)
        note = QLabel(tr('Auto-connect off still allows Connect. Disconnect pauses reconnection until Connect or a manager restart. Pairing never selects an audio output.'))
        note.setWordWrap(True)
        self.body.addWidget(note)
        self.scan_button = QPushButton(tr('Find devices · 20 s'))
        self.pair_source_button = QPushButton(tr('Pair audio source…'))
        self.pair_output_button = QPushButton(tr('Pair headphones / speakers…'))
        self.connect_button = QPushButton(tr('Connect'))
        self.disconnect_button = QPushButton(tr('Disconnect'))
        self.refresh_button = QPushButton(tr('Refresh now'))
        self.install_button = QPushButton(tr('Install / repair Bluetooth management…'))
        self.close_button = QPushButton(tr('Close'))
        for buttons in ((self.scan_button, self.pair_source_button, self.pair_output_button),
                        (self.connect_button, self.disconnect_button, self.refresh_button),
                        (self.install_button, self.close_button)):
            row = QHBoxLayout()
            for button in buttons:
                button.setAutoDefault(False)
                row.addWidget(button)
            self.body.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setInterval(4000)
        self.timer.timeout.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        self.mode.currentIndexChanged.connect(self.populate_devices)
        self.automatic.clicked.connect(lambda enabled: self.operate('automatic', enabled=enabled))
        self.scan_button.clicked.connect(lambda: self.operate('scan'))
        self.refresh_button.clicked.connect(self.refresh)
        self.connect_button.clicked.connect(lambda: self.operate('connect'))
        self.disconnect_button.clicked.connect(lambda: self.operate('disconnect'))
        self.pair_source_button.clicked.connect(self.pair_source)
        self.pair_output_button.clicked.connect(self.pair_output)
        self.install_button.clicked.connect(self.install)
        self.close_button.clicked.connect(self.reject)
        self.controls()

    def selected(self):
        row = self.table.currentRow()
        if row < 0 or self.snapshot is None: return None
        item = self.table.item(row, 0)
        if item is None: return None
        mac = item.data(Qt.ItemDataRole.UserRole)
        return next((d for d in self.snapshot['devices'] if d['address'] == mac), None)

    def controls(self):
        idle = (self.worker is None or self.reading) and self.pending_pair is None and self.pending_command is None and not self.close_after
        device = self.selected()
        ready = bool(idle and self.snapshot and self.snapshot['powered'])
        self.output.setEnabled(idle)
        self.output_button.setEnabled(bool(idle and self.output.currentData()))
        self.scan_button.setEnabled(ready)
        self.refresh_button.setEnabled(idle)
        self.install_button.setEnabled(idle and self.worker is None)
        self.mode.setEnabled((idle or self.reading) and not self.close_after)
        self.table.setEnabled((idle or self.reading) and not self.close_after)
        self.pair_source_button.setEnabled(ready and self.worker is None and hasattr(self.parentWidget(), 'start_pairing'))
        self.pair_output_button.setEnabled(bool(ready and self.worker is None and device and not device['paired'] and device['role'] in ('output', 'both', 'unknown') and hasattr(self.parentWidget(), 'start_pairing')))
        paired = bool(ready and device and device['paired'])
        self.connect_button.setEnabled(bool(paired and not device['connected']))
        self.disconnect_button.setEnabled(bool(paired and device['connected']))
        self.automatic.setEnabled(paired)
        self.close_button.setEnabled(True)

    def selection_changed(self):
        device = self.selected()
        self.automatic.setChecked(bool(device and device['automatic']))
        self.controls()

    def populate_devices(self):
        old = self.selected()
        output = self.output.currentData()
        self.output.clear()
        for item in self.snapshot['outputs'] if self.snapshot else []:
            self.output.addItem(item['description'] + (' ✓' if item['default'] else ''), item['name'])
        index = self.output.findData(output)
        if index >= 0: self.output.setCurrentIndex(index)
        selected = old['address'] if old else None
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        kind = self.mode.currentData()
        for d in self.snapshot['devices'] if self.snapshot else []:
            if kind != 'all' and d['role'] not in (kind, 'both', 'unknown'): continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            state = tr('Connected') if d['connected'] else tr('Paired') if d['paired'] else tr('Discovered')
            if d['paused'] and not d['connected']: state = tr('Disconnected · auto-connect paused')
            values = (d['name'], {'source': tr('Audio source'), 'output': tr('Audio output'),
                'both': tr('Source and output'), 'unknown': tr('Unknown type')}[d['role']], state,
                tr('Enabled') if d['automatic'] else tr('Disabled'))
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(d['address'])
                if col == 0: item.setData(Qt.ItemDataRole.UserRole, d['address'])
                self.table.setItem(row, col, item)
            if selected == d['address']: self.table.selectRow(row)
        self.table.blockSignals(False)
        self.selection_changed()

    def show_status(self, value):
        self.snapshot = value
        self.populate_devices()
        self.progress(tr('Bluetooth preference enforcement failed; automatic connection is not guaranteed.') if value['degraded'] else tr('Scanning for nearby devices…') if value['discovering'] else tr('Bluetooth devices updated.'))

    def refresh(self):
        if self.isVisible() and not self.isMinimized() and self.pending_pair is None and not self.worker and not self.close_after:
            self.reading = True
            self.start(lambda _: bluetooth.request(self.config), self.show_status)

    def operate(self, action, **extra):
        if (self.worker and not self.reading) or not self.snapshot or self.close_after: return
        value = {'action': action, 'adapter': self.snapshot['adapter'], **extra}
        if action not in ('scan', 'stop-scan'):
            device = self.selected()
            if device is None: return
            value['address'] = device['address']
        if self.worker:
            self.pending_command = value
            self.controls()
        else:
            self.progress(tr('Updating Bluetooth device…'))
            self.start(lambda _: bluetooth.request(self.config, value), self.show_status)

    def set_output(self):
        name = self.output.currentData()
        if name:
            value = {'action': 'output', 'name': name}
            if self.worker and self.reading:
                self.pending_command = value
                self.controls()
            elif not self.worker:
                self.start(lambda _: bluetooth.request(self.config, value), self.show_status)

    def pair_source(self):
        self.mode.setCurrentIndex(self.mode.findData('source'))
        self.parentWidget().start_pairing(from_bluetooth=True)
        self.progress(tr('On the phone or computer, select this Pi in Bluetooth settings and approve the pairing request.'))

    def pair_output(self):
        device = self.selected()
        if not device or not self.snapshot: return
        self.pending_pair = {'action': 'pair', 'adapter': self.snapshot['adapter'], 'address': device['address']}
        self.parentWidget().start_pairing(from_bluetooth=True)
        self.pair_deadline = 15
        self.progress(tr('Starting the approved pairing window…'))
        self.controls()
        self.wait_for_agent()

    def wait_for_agent(self):
        if self.pending_pair is None: return
        parent = self.parentWidget()
        if parent.session and parent.expires:
            value, self.pending_pair = self.pending_pair, None
            self.start(lambda _: bluetooth.request(self.config, value), self.show_status)
        elif self.pair_deadline > 0:
            self.pair_deadline -= 1
            QTimer.singleShot(1000, self.wait_for_agent)
        else:
            self.pending_pair = None
            self.progress(tr('Pairing could not start. Check the Pi helper and try again.'))
            self.controls()

    def install(self):
        if not self.confirm(tr('Install Bluetooth device management? WirePlumber restarts and current audio connections may be interrupted. Existing pairings are retained.')): return
        from ui_style import get_text
        password, accepted = get_text(self, tr('Bluetooth management setup'), tr('Pi sudo password (not saved)'), QLineEdit.EchoMode.Password)
        if accepted:
            self.start(lambda progress: bluetooth.install(self.config, password, progress), lambda _: None)

    def finished_task(self):
        self.reading = False
        super().finished_task()
        if self.close_after:
            self.pending_command = None
            self.reject()
        elif self.pending_command is not None:
            value, self.pending_command = self.pending_command, None
            self.start(lambda _: bluetooth.request(self.config, value), self.show_status)

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def reject(self):
        self.pending_pair = None
        self.timer.stop()
        if self.worker:
            self.close_after = True
            self.progress(tr('Finishing Bluetooth operation before closing…'))
            return
        parent = self.parentWidget()
        if parent is not None:
            # Pairing confirmations are parented here while this dialog is modal.
            # Reject/remove them before Qt deletes their parent; late agent events
            # must not call methods on already-deleted QMessageBox objects.
            for box in list(getattr(parent, 'dialogs', {}).values()):
                box.reject()
            if hasattr(parent, 'dialogs'): parent.dialogs.clear()
            if getattr(parent, 'session', None): parent.stop_pairing()
        super().reject()

    def closeEvent(self, event):
        event.ignore()
        self.reject()
