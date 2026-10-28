"""Explicit source-policy and on-demand update dialogs; no hidden refresh timers."""
from PySide6.QtCore import Qt, QDateTime, QTimer
from PySide6.QtWidgets import (QDialog, QLabel, QVBoxLayout, QHBoxLayout, QPushButton,
    QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QSlider)
import audio
import updates
import volume
from core import display_text
from i18n import tr, translate_standard_buttons
from setup_ui import Task
from ui_style import LocalizedMessageBox as QMessageBox


class MaintenanceDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config, self.worker, self.snapshot = config.copy(), None, None
        self.body = QVBoxLayout(self)
        target = QLabel(display_text(config['username'] + '@' + config['host']))
        target.setTextFormat(Qt.TextFormat.PlainText)
        self.body.addWidget(target)
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        self.message.setMinimumHeight(70)
        self.body.addWidget(self.message)

    def confirm(self, text):
        box = QMessageBox(QMessageBox.Icon.Question, self.windowTitle(), '', parent=self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(text)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        translate_standard_buttons(box)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def start(self, operation, callback):
        if self.worker: return
        worker = Task(operation, self)
        self.worker = worker
        self.controls()
        worker.result.connect(callback)
        worker.progress.connect(self.progress)
        worker.failed.connect(self.failure)
        worker.finished.connect(self.finished_task)
        worker.start()

    def progress(self, value):
        self.message.setText('\n'.join(display_text(line, 4096) for line in str(value)[:4096].split('\n')))

    def failure(self, exc):
        self.progress(str(exc))

    def finished_task(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self.controls()

    def reject(self):
        if not self.worker: super().reject()

    def closeEvent(self, event):
        if self.worker: event.ignore()
        else: super().closeEvent(event)


class PolicyDialog(MaintenanceDialog):
    def __init__(self, config, parent=None):
        super().__init__(config, parent)
        self.setWindowTitle(tr('Playback source policy'))
        self.resize(610, 330)
        self.allow = QCheckBox(tr('Allow simultaneous audio sources'))
        self.allow.setChecked(True)
        self.body.addWidget(self.allow)
        note = QLabel(tr('Exclusive mode selects the latest-started supported source. Other players are paused where possible, or muted if their sender cannot pause. Muted senders may keep advancing; pause and play them again to switch back.'))
        note.setWordWrap(True)
        self.body.addWidget(note)
        security = QLabel(tr('Changing this setting restarts Spotify. Exclusive mode enables its localhost-only control API; other local users on the Pi can access that API. Mixing remains the default.'))
        security.setWordWrap(True)
        self.body.addWidget(security)
        row = QHBoxLayout()
        self.refresh_button = QPushButton(tr('Refresh now'))
        self.apply_button = QPushButton(tr('Apply source policy'))
        self.close_button = QPushButton(tr('Close'))
        for widget in (self.refresh_button, self.apply_button, self.close_button): row.addWidget(widget)
        self.body.addLayout(row)
        self.refresh_button.clicked.connect(self.refresh)
        self.apply_button.clicked.connect(self.apply)
        self.close_button.clicked.connect(self.reject)
        self.controls()

    def controls(self):
        idle = self.worker is None
        for widget in (self.allow, self.refresh_button, self.close_button): widget.setEnabled(idle)
        self.apply_button.setEnabled(bool(idle and self.snapshot and self.snapshot['installed']))

    def refresh(self):
        self.start(lambda _: audio.policy_request(self.config), self.show_status)

    def show_status(self, value):
        self.snapshot = value
        self.allow.setChecked(not value['exclusive'])
        if not value['installed']:
            self.progress(tr('Install / repair the core receiver in Pi components before using source policy.'))
        else:
            self.progress(tr('Policy service: {state} · Current source: {current}', state=tr(value['state']), current=value['source'] or '—') +
                          ('\n' + tr('Some audio controls failed; exclusivity is not guaranteed.') if value['degraded'] or (value['exclusive'] and value['state'] != 'active') else ''))

    def apply(self):
        value = {'exclusive': not self.allow.isChecked()}
        if not self.confirm(tr('Apply the source policy on this Pi? Spotify may restart and current playback may be interrupted.')): return
        self.start(lambda _: audio.policy_request(self.config, value), self.show_status)


class UpdatesDialog(MaintenanceDialog):
    def __init__(self, config, parent=None):
        super().__init__(config, parent)
        self.setWindowTitle(tr('Pi system updates'))
        self.resize(720, 490)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([tr('Package'), tr('Installed version'), tr('Available version')])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.body.addWidget(self.table)
        note = QLabel(tr('The initial list uses cached package indexes. Refresh lists checks the repositories. Install applies this Pi’s unattended-upgrades policy; held or excluded packages may remain. No immediate reboot is requested; the existing conditional reboot schedule remains. Soloist and source-built AirPlay 2 have separate update paths.'))
        note.setWordWrap(True)
        self.body.addWidget(note)
        row = QHBoxLayout()
        self.check_button = QPushButton(tr('Check cached updates'))
        self.refresh_button = QPushButton(tr('Refresh update lists'))
        self.install_button = QPushButton(tr('Install allowed updates…'))
        self.close_button = QPushButton(tr('Close'))
        for widget in (self.check_button, self.refresh_button, self.install_button, self.close_button): row.addWidget(widget)
        self.body.addLayout(row)
        self.check_button.clicked.connect(self.refresh)
        self.refresh_button.clicked.connect(lambda: self.operate('check'))
        self.install_button.clicked.connect(lambda: self.operate('install'))
        self.close_button.clicked.connect(self.reject)
        self.recheck = False
        self.controls()

    def controls(self):
        idle = self.worker is None
        self.check_button.setEnabled(idle)
        self.close_button.setEnabled(idle)
        ready = self.snapshot and self.snapshot['control_installed']
        self.refresh_button.setEnabled(bool(idle and ready))
        self.install_button.setEnabled(bool(idle and ready and self.snapshot['policy_configured']))

    def refresh(self):
        self.start(lambda _: updates.check(self.config), self.show_status)

    def show_status(self, value):
        self.snapshot = value
        self.table.setRowCount(len(value['packages']))
        for row, package in enumerate(value['packages']):
            for column, key in enumerate(('name', 'current', 'candidate')):
                self.table.setItem(row, column, QTableWidgetItem(package[key]))
        checked = QDateTime.fromSecsSinceEpoch(value['checked_at']).toLocalTime().toString('yyyy-MM-dd HH:mm') if value['checked_at'] else tr('unknown')
        text = tr('Pending updates: {count} · Last repository refresh: {time}', count=value['total'], time=checked)
        if value['truncated']: text += '\n' + tr('Showing the first 256 packages; the total includes all pending packages.')
        if value['reboot_required']: text += '\n' + tr('A reboot is required. The existing reboot schedule is unchanged.')
        if not value['control_installed'] or not value['policy_configured']:
            text += '\n' + tr('Install / repair Automatic updates in Pi components to enable these actions.')
        self.progress(text)

    def operate(self, operation):
        if operation == 'install' and not self.confirm(tr('Install updates allowed by this Pi’s configured policy? Services may restart. Keep this window open. Held or excluded packages remain listed; no immediate reboot is requested.')): return
        def completed(_): self.recheck = True
        self.start(lambda progress: updates.operate(self.config, operation, progress), completed)

    def finished_task(self):
        super().finished_task()
        if self.recheck:
            self.recheck = False
            self.refresh()

    def failure(self, exc):
        self.recheck = False
        super().failure(exc)


class VolumeDialog(MaintenanceDialog):
    def __init__(self, config, parent=None):
        super().__init__(config, parent)
        self.setWindowTitle(tr('Pi master volume'))
        self.resize(500, 240)
        self.pending = {}
        self.close_after = False
        row = QHBoxLayout()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.percent = QLabel('—')
        self.percent.setMinimumWidth(50)
        row.addWidget(self.slider, 1)
        row.addWidget(self.percent)
        self.body.addLayout(row)
        self.muted = QCheckBox(tr('Mute output'))
        self.body.addWidget(self.muted)
        note = QLabel(tr('Controls the combined PipeWire output, independently of each player. Maximum 100%; no software amplification.'))
        note.setWordWrap(True)
        self.body.addWidget(note)
        row = QHBoxLayout()
        self.refresh_button = QPushButton(tr('Refresh now'))
        self.close_button = QPushButton(tr('Close'))
        row.addWidget(self.refresh_button)
        row.addWidget(self.close_button)
        self.body.addLayout(row)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(180)
        self.debounce.timeout.connect(self.flush)
        self.slider.valueChanged.connect(self.changed_volume)
        self.slider.sliderReleased.connect(self.flush)
        self.muted.toggled.connect(lambda value: self.queue('mute', value))
        self.refresh_button.clicked.connect(self.refresh)
        self.close_button.clicked.connect(self.reject)
        self.controls()

    def controls(self):
        ready = self.snapshot is not None
        self.slider.setEnabled(ready and not self.close_after)
        self.muted.setEnabled(ready and not self.close_after)
        self.refresh_button.setEnabled(self.worker is None and not self.pending)
        self.close_button.setEnabled(True)

    def refresh(self):
        if not self.pending: self.start(lambda _: volume.request(self.config), self.show_status)

    def show_status(self, value):
        self.snapshot = value
        if 'volume' not in self.pending and not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(value['volume'])
            self.slider.blockSignals(False)
            self.percent.setText(str(value['volume']) + '%')
        if 'mute' not in self.pending:
            self.muted.blockSignals(True)
            self.muted.setChecked(value['muted'])
            self.muted.blockSignals(False)
        self.progress(value['name'] + ('\n' + tr('The current output exceeds 100%; this control only allows values up to 100%.') if value['amplified'] else ''))

    def changed_volume(self, value):
        self.percent.setText(str(value) + '%')
        self.queue('volume', value)

    def queue(self, action, value):
        if self.snapshot is None: return
        self.pending[action] = {'action': action, 'value': value, 'id': self.snapshot['id'], 'serial': self.snapshot['serial'], 'cookie': self.snapshot['cookie']}
        self.debounce.start()
        self.controls()

    def flush(self):
        self.debounce.stop()
        if self.worker or not self.pending: return
        action = next(iter(self.pending))
        value = self.pending.pop(action)
        self.start(lambda _: volume.request(self.config, value), self.show_status)

    def finished_task(self):
        super().finished_task()
        if self.pending: self.flush()
        elif self.close_after: super().reject()

    def failure(self, exc):
        self.pending.clear()
        self.snapshot = None
        super().failure(exc)

    def reject(self):
        if self.worker or self.pending:
            self.close_after = True
            self.flush()
            self.controls()
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker or self.pending:
            event.ignore()
            self.reject()
        else:
            super().closeEvent(event)
