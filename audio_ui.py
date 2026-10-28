"""Explicit, on-demand AirPlay management; no hidden polling or playback takeover."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFormLayout, QHBoxLayout, QLabel,
                              QLineEdit, QPushButton, QVBoxLayout, QComboBox)

import audio
from core import display_text
from i18n import tr, translate_standard_buttons
from setup_ui import Task
from ui_style import LocalizedMessageBox as QMessageBox


class AudioDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config.copy()
        self.worker = None
        self.snapshot = None
        self.refresh_after = False
        self.setWindowTitle(tr('Audio integrations'))
        self.resize(610, 430)
        layout = QVBoxLayout(self)
        intro = QLabel(tr('AirPlay audio receiver. A separate password is required. Source policy and equalizer controls are in Maintenance.'))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.status_label = QLabel(tr('Not connected'))
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItem(tr('Classic AirPlay (Debian package)'), 'classic')
        self.mode.addItem(tr('AirPlay 2 (pinned source build + NQPTP)'), 'airplay2')
        self.mode.setItemData(1, tr('AirPlay 2 updates use versions reviewed in a newer tray app. Reinstall to apply them; APT does not update this source build.'), Qt.ItemDataRole.ToolTipRole)
        form.addRow(tr('Installation choice'), self.mode)
        self.mode.currentIndexChanged.connect(self.controls)
        self.name = QLineEdit('Pi Audio')
        self.name.setMaxLength(50)
        self.password = QLineEdit()
        self.password.setMaxLength(128)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(tr('AirPlay name'), self.name)
        form.addRow(tr('AirPlay password'), self.password)
        self.admin = QLineEdit()
        self.admin.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(tr('Pi sudo password (not saved)'), self.admin)
        layout.addLayout(form)
        self.install_button = QPushButton(tr('Install AirPlay…'))
        self.save_button = QPushButton(tr('Save AirPlay settings'))
        self.enable_button = QPushButton(tr('Enable AirPlay'))
        self.disable_button = QPushButton(tr('Disable AirPlay'))
        self.refresh_button = QPushButton(tr('Refresh now'))
        self.close_button = QPushButton(tr('Close'))
        for controls in ((self.install_button, self.save_button),
                         (self.enable_button, self.disable_button),
                         (self.refresh_button, self.close_button)):
            row = QHBoxLayout()
            for button in controls:
                row.addWidget(button)
            layout.addLayout(row)
        note = QLabel(tr('AirPlay 2 compilation can take several minutes. Equalizer controls are in Maintenance; synchronized multiroom is planned.'))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.install_button.clicked.connect(self.install)
        self.save_button.clicked.connect(self.save)
        self.enable_button.clicked.connect(lambda: self.start(lambda _: audio.request(self.config, 'enable'), self.show_status))
        self.disable_button.clicked.connect(lambda: self.start(lambda _: audio.request(self.config, 'disable'), self.show_status))
        self.refresh_button.clicked.connect(self.refresh)
        self.close_button.clicked.connect(self.reject)
        self.controls()

    def controls(self):
        idle = self.worker is None
        installed = bool(self.snapshot and self.snapshot['installed'])
        matches = installed and self.snapshot.get('mode', 'classic') == self.mode.currentData()
        for control in (self.mode, self.name, self.password, self.admin, self.install_button,
                        self.refresh_button, self.close_button):
            control.setEnabled(idle)
        self.save_button.setEnabled(idle and matches)
        self.enable_button.setEnabled(idle and matches and self.snapshot['configured'])
        self.disable_button.setEnabled(idle and installed)

    def start(self, action, callback):
        if self.worker:
            return
        worker = Task(action, self)
        self.worker = worker
        self.controls()
        worker.progress.connect(self.message)
        worker.result.connect(callback)
        worker.failed.connect(self.failure)
        worker.finished.connect(self.finished_task)
        worker.start()

    def message(self, text):
        self.status_label.setText('\n'.join(display_text(line, 4096) for line in str(text)[:4096].split('\n')))

    def failure(self, exc):
        self.refresh_after = False
        self.snapshot = None
        self.message(tr('Audio operation failed. If the helper is not installed, use Install AirPlay. Check the Pi service journal for details.') + '\n' + str(exc))

    def finished_task(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self.controls()
        if self.refresh_after:
            self.refresh_after = False
            self.refresh()

    def refresh(self):
        self.start(lambda _: audio.request(self.config, 'status'), self.show_status)

    def show_status(self, value):
        first = self.snapshot is None
        self.snapshot = value
        if first:
            self.mode.blockSignals(True)
            self.mode.setCurrentIndex(self.mode.findData(value.get('mode', 'classic')))
            self.mode.blockSignals(False)
        if value['name'] and not self.name.isModified():
            self.name.setText(value['name'])
        if not value['installed']:
            self.message(tr('AirPlay is not installed.'))
            return
        self.message(tr('AirPlay: {state} · Start at boot: {enabled}',
                        state=tr(value['state']), enabled=tr('yes') if value['enabled'] else tr('no')) +
                     '\n' + display_text(value['version']) +
                     ('\n' + tr('AirPlay 2 timing: {state}', state=tr(value.get('timing', 'unknown'))) if value.get('mode') == 'airplay2' else ''))

    def install(self):
        mode = self.mode.currentData()
        if not self.confirm(tr('Install AirPlay…'), self.mode.currentText() + '\n\n' + tr('Install or repair this AirPlay variant? Receiving is left disabled. Existing settings are preserved separately for each variant. Unrelated installations are refused.')):
            self.admin.clear()
            return
        password = self.admin.text()
        self.admin.clear()
        def completed(_):
            self.refresh_after = True
        self.start(lambda progress: audio.install(self.config, password, progress, mode), completed)

    def save(self):
        settings = {'name': self.name.text().strip(), 'password': self.password.text()}
        self.password.clear()
        try:
            audio.validate_settings(settings)
        except ValueError as exc:
            self.message(str(exc))
            return
        if self.snapshot and self.snapshot['state'] == 'active':
            if not self.confirm(tr('Save AirPlay settings'), tr('Saving restarts AirPlay and disconnects its current listener. Continue?')):
                return
        self.start(lambda _: audio.request(self.config, 'configure', settings), self.show_status)

    def confirm(self, title, text):
        box = QMessageBox(QMessageBox.Icon.Question, title, '', parent=self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        translate_standard_buttons(box)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def reject(self):
        if not self.worker:
            self.password.clear()
            self.admin.clear()
            super().reject()

    def closeEvent(self, event):
        if self.worker:
            event.ignore()
        else:
            self.password.clear()
            self.admin.clear()
            super().closeEvent(event)
