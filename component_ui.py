"""Per-receiver component facts and explicit additive install/repair plans."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView, QPlainTextEdit)

import components
from core import UnknownHost, trust_host, display_text
from i18n import tr, translate_standard_buttons
from setup_ui import Task
from setup_plan import validate_maintenance
from ui_style import LocalizedMessageBox as QMessageBox, plain_tooltip


def labels():
    return {'spotify': 'Spotify Soloist', 'bluetooth': tr('Bluetooth audio'),
            'helpers': tr('Monitoring and pairing helpers'), 'music': 'MPD / USB / UPnP',
            'web': tr('HTTPS web player'), 'updates': tr('Automatic updates'),
            'cockpit': 'Cockpit', 'privacy': tr('Process privacy (hidepid=2)'),
            'shutdown': tr('Tray shutdown permission'), 'airplay': 'AirPlay'}


class ComponentsDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config.copy()
        self.worker = None
        self.snapshot = None
        self.recheck = False
        self.setWindowTitle(tr('Pi components — check and repair'))
        self.resize(850, 650)
        layout = QVBoxLayout(self)
        self.target = QLabel(display_text(config['username'] + '@' + config['host']))
        self.target.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.target)
        intro = QLabel(tr('Check this Pi, then select components to install or repair. Unselected features are not removed or reconfigured. Shared packages may be updated. Service status does not prove successful audio playback.'))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([tr('Select'), tr('Component'), tr('Packages / binary'), tr('Configuration files'), tr('Service')])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self.selection_changed)
        layout.addWidget(self.table, 1)
        form = QFormLayout()
        self.hostname = QLineEdit(config['host'])
        self.usb = QComboBox()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(tr('Pi DNS name (TLS certificate)'), self.hostname)
        form.addRow(tr('FAT32 USB drive (must contain Music/)'), self.usb)
        form.addRow(tr('Pi sudo password (not saved)'), self.password)
        layout.addLayout(form)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(100)
        self.log.setMaximumHeight(110)
        layout.addWidget(self.log)
        row = QHBoxLayout()
        self.check_button = QPushButton(tr('Check all components'))
        self.repair_button = QPushButton(tr('Install / repair selected…'))
        self.audio_button = QPushButton(tr('Audio integrations…'))
        self.policy_button = QPushButton(tr('Playback source policy…'))
        self.updates_button = QPushButton(tr('System updates…'))
        self.close_button = QPushButton(tr('Close'))
        for button in (self.check_button, self.repair_button, self.audio_button, self.close_button):
            row.addWidget(button)
        layout.addLayout(row)
        more = QHBoxLayout()
        more.addWidget(self.policy_button)
        more.addWidget(self.updates_button)
        layout.addLayout(more)
        self.check_button.clicked.connect(self.check)
        self.repair_button.clicked.connect(self.repair)
        self.audio_button.clicked.connect(self.audio)
        self.policy_button.clicked.connect(lambda: self.maintenance('policy'))
        self.updates_button.clicked.connect(lambda: self.maintenance('updates'))
        self.close_button.clicked.connect(self.reject)
        self.controls()

    def selected(self):
        if self.snapshot is None:
            return []
        return sorted({facts['action'] for index, facts in enumerate(self.snapshot['rows'])
                       if self.table.item(index, 0).checkState() == Qt.CheckState.Checked})

    def selection_changed(self, item):
        if self.snapshot is None or item.column() != 0:
            return
        facts = self.snapshot['rows'][item.row()]
        self.table.blockSignals(True)
        for index, other in enumerate(self.snapshot['rows']):
            if facts['action'] == 'receiver' and other['action'] == 'receiver':
                self.table.item(index, 0).setCheckState(item.checkState())
            if facts['id'] == 'web' and item.checkState() == Qt.CheckState.Checked and other['id'] == 'music':
                self.table.item(index, 0).setCheckState(Qt.CheckState.Checked)
            if facts['id'] == 'music' and item.checkState() != Qt.CheckState.Checked and other['id'] == 'web':
                self.table.item(index, 0).setCheckState(Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self.controls()

    def controls(self):
        if not hasattr(self, 'repair_button'):
            return
        idle = self.worker is None
        for widget in (self.check_button, self.close_button, self.audio_button, self.policy_button, self.updates_button, self.table, self.password, self.hostname, self.usb):
            widget.setEnabled(idle)
        allowed = self.snapshot and self.snapshot['managed'] and self.snapshot['supported']
        self.repair_button.setEnabled(bool(idle and allowed and self.selected()))

    def append(self, text):
        self.log.appendPlainText('\n'.join(display_text(line, 4096) for line in str(text)[:4096].split('\n')))

    def start(self, action, callback):
        if self.worker:
            return
        task = Task(action, self)
        self.worker = task
        self.controls()
        task.progress.connect(self.append)
        task.result.connect(callback)
        task.failed.connect(self.failure)
        task.finished.connect(self.finished)
        task.start()

    def finished(self):
        task, self.worker = self.worker, None
        task.deleteLater()
        self.controls()
        if self.recheck:
            self.recheck = False
            self.check()

    def failure(self, exc):
        self.recheck = False
        self.snapshot = None
        if isinstance(exc, UnknownHost):
            box = QMessageBox(QMessageBox.Icon.Question, tr('Verify SSH host key'), '', parent=self)
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText(tr('{host}\n{fingerprint}\n\nVerify this fingerprint on the Pi before trusting it.', host=display_text(exc.hostname), fingerprint=exc.fingerprint))
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            translate_standard_buttons(box)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if box.exec() == QMessageBox.StandardButton.Yes:
                trust_host(exc)
                self.append(tr('Host key saved. Click Check Pi again.'))
        else:
            self.append(str(exc))

    def check(self):
        self.start(lambda _: components.check(self.config), self.show_inventory)

    def show_inventory(self, value):
        self.snapshot = value
        self.table.blockSignals(True)
        self.table.setRowCount(len(value['rows']))
        names = labels()
        for index, facts in enumerate(value['rows']):
            choice = QTableWidgetItem()
            choice.setCheckState(Qt.CheckState.Unchecked)
            if not value['managed'] or not value['supported'] or not facts['managed'] or facts['action'] == 'audio':
                choice.setFlags(choice.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(index, 0, choice)
            values = [names[facts['id']], *[tr('yes') if facts[key] is True else tr('no') if facts[key] is False else tr('unknown') for key in ('installed', 'configured', 'running')]]
            if facts['id'] in ('helpers', 'shutdown'):
                values[-1] = '—'
            for column, text in enumerate(values, 1):
                item = QTableWidgetItem(text)
                item.setToolTip(plain_tooltip(display_text(facts['details'], 2048)))
                self.table.setItem(index, column, item)
        self.table.blockSignals(False)
        if value['hostname']:
            self.hostname.setText(value['hostname'])
        self.usb.clear()
        def visit(devices, usb=False):
            for device in devices:
                is_usb = usb or device.get('tran') == 'usb'
                uuid = device.get('uuid')
                if is_usb and device.get('fstype') == 'vfat' and uuid and not any(p == '/' or (p or '').startswith('/boot') for p in device.get('mountpoints', [])):
                    self.usb.addItem(display_text(str(device.get('name', 'USB')) + ' · ' + uuid), uuid)
                visit(device.get('children', []), is_usb)
        visit(value['disks']['blockdevices'])
        index = self.usb.findData(value['usb_uuid'])
        if index >= 0:
            self.usb.setCurrentIndex(index)
        self.append(' · '.join(display_text(value[key]) for key in ('model', 'os', 'architecture')))
        if not value['managed']:
            self.append(tr('This is not a managed receiver for this account. Use fresh-Pi setup or integrate existing third-party services manually.'))
        self.append(tr('Check complete. Hover over a row for details. AirPlay variants are managed in Audio integrations. Private credentials are not read by this check.'))

    def repair(self):
        selected = self.selected()
        options = {name: name in selected or (name == 'music' and 'web' in selected)
                   for name in ('music', 'web', 'updates', 'cockpit', 'shutdown', 'privacy')}
        options.update(hostname=self.hostname.text().strip(), usb_uuid=(self.usb.currentData() or '') if options['music'] else '')
        try:
            request = validate_maintenance({'operation': 'maintain', 'components': selected, 'options': options})
        except ValueError as exc:
            self.append(str(exc))
            self.password.clear()
            return
        box = QMessageBox(QMessageBox.Icon.Question, tr('Review installation plan'), '', parent=self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        selected_names = [tr('Core receiver (Spotify, Bluetooth and helpers)') if name == 'receiver' else labels()[name] for name in selected]
        box.setText(display_text(self.config['username'] + '@' + self.config['host']) + '\n' + ', '.join(selected_names) +
                    '\n' + tr('Pi DNS name (TLS certificate)') + ': ' + request['options']['hostname'] +
                    ('\nUSB UUID: ' + request['options']['usb_uuid'] if request['options']['music'] else '') +
                    '\n\n' + tr('Install or repair the selected components? App-managed configuration may be replaced and affected services restarted. Existing credentials and media are preserved. Shared package dependencies may be updated. Stop playback first. No uninstall or automatic rollback is performed.'))
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        translate_standard_buttons(box)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        password = self.password.text()
        self.password.clear()
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        def completed(_):
            self.recheck = True
        self.start(lambda progress: components.maintain(self.config, request, password, progress), completed)

    def audio(self):
        from audio_ui import AudioDialog
        dialog = AudioDialog(self.config, self)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, dialog.refresh)
        dialog.exec()
        dialog.deleteLater()
        self.check()

    def maintenance(self, kind):
        from maintenance_ui import PolicyDialog, UpdatesDialog
        from PySide6.QtCore import QTimer
        dialog = (PolicyDialog if kind == 'policy' else UpdatesDialog)(self.config, self)
        QTimer.singleShot(0, dialog.refresh)
        dialog.exec()
        dialog.deleteLater()
        self.check()

    def reject(self):
        if self.worker is None:
            self.password.clear()
            super().reject()

    def closeEvent(self, event):
        if self.worker:
            event.ignore()
        else:
            self.password.clear()
            super().closeEvent(event)
