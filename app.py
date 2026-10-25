#!/usr/bin/env python3
"""Pi Audio Tray: a small, configurable SSH-based audio monitor."""
import fcntl
import json
import os
from pathlib import Path
import sys
import threading
import time

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
    QPushButton, QSpinBox, QSystemTrayIcon, QVBoxLayout, QWidget)

from core import (PairSession, UnknownHost, config_dir, disable_pairing,
                  install_helper, load_config, poll, save_config, trust_host)


def icon(color='#8c97a8'):
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    svg = (root / 'assets/pi-audio-tray.svg').read_text().replace('#22c58b', color)
    renderer = QSvgRenderer(svg.encode())
    result = QIcon()
    for size in (16, 22, 32, 48, 64, 128, 256):
        image = QPixmap(size, size)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        result.addPixmap(image)
    return result


class Events(QObject):
    done = Signal(str, object)
    failed = Signal(str, object)
    pairing = Signal(object)


class Settings(QDialog):
    def __init__(self, config, parent):
        super().__init__(parent)
        self.setWindowTitle('Connection settings')
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        note = QLabel('Settings stay on this computer. SSH keys are used in place;\n'
                      'their contents are never copied to the Pi or the app.')
        layout.addWidget(note)
        form = QFormLayout()
        self.fields = {}
        for key, label, hint in [('host', 'Pi hostname / IP', 'audio-pi.local'),
                                  ('username', 'SSH username', 'your Pi user'),
                                  ('host_key_alias', 'Known-host alias (optional)', 'previous SSH hostname'),
                                  ('key_file', 'SSH private key (optional)', '~/.ssh/id_ed25519')]:
            entry = QLineEdit(config[key])
            entry.setPlaceholderText(hint)
            self.fields[key] = entry
            if key == 'key_file':
                row = QHBoxLayout()
                row.addWidget(entry)
                button = QPushButton('Browse…')
                button.clicked.connect(self.browse)
                row.addWidget(button)
                form.addRow(label, row)
            else:
                form.addRow(label, entry)
        for key, label, minimum, maximum in [('port', 'SSH port', 1, 65535),
                                             ('poll_seconds', 'Refresh interval (seconds)', 5, 300)]:
            entry = QSpinBox()
            entry.setRange(minimum, maximum)
            entry.setValue(config[key])
            self.fields[key] = entry
            form.addRow(label, entry)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def browse(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Select private SSH key', str(Path.home() / '.ssh'))
        if filename:
            self.fields['key_file'].setText(filename)

    def values(self):
        return {key: value.value() if isinstance(value, QSpinBox) else value.text().strip()
                for key, value in self.fields.items()}


class TrayApp(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.config = load_config()
        self.events = Events()
        self.events.done.connect(self.done)
        self.events.failed.connect(self.failed)
        self.events.pairing.connect(self.pair_event)
        self.busy = set()
        self.session = None
        self.expires = 0
        self.dialogs = {}
        self.snapshot = None
        self.closing = False
        self.generation = 0
        self.setWindowTitle('Pi Audio Tray')
        self.setWindowIcon(icon())
        self.setMinimumWidth(450)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel('Pi Audio Tray')
        title.setStyleSheet('font-size: 24px; font-weight: 600;')
        layout.addWidget(title)
        subtitle = QLabel('Your audio receiver, at a glance')
        subtitle.setStyleSheet('color: #8996a8; margin-bottom: 16px;')
        layout.addWidget(subtitle)
        self.rows = {}
        form = QFormLayout()
        form.setVerticalSpacing(14)
        for key, label in [('pi', 'Raspberry Pi'), ('spotify', 'Spotify Connect'),
                           ('bluetooth', 'Bluetooth'), ('audio', 'Bluetooth audio'),
                           ('devices', 'Connected devices'), ('pairing', 'New pairing')]:
            value = QLabel('—')
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setWordWrap(True)
            self.rows[key] = value
            form.addRow(label, value)
        layout.addLayout(form)
        self.notice = QLabel('Configure your Pi to get started.')
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet('color: #8996a8; margin: 16px 0;')
        self.notice.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.notice)
        controls = QHBoxLayout()
        self.pair_button = QPushButton('Enable pairing · 3 min')
        self.pair_button.clicked.connect(self.start_pairing)
        self.off_button = QPushButton('Disable pairing')
        self.off_button.clicked.connect(self.stop_pairing)
        controls.addWidget(self.pair_button)
        controls.addWidget(self.off_button)
        layout.addLayout(controls)
        extras = QHBoxLayout()
        for label, callback in [('Refresh', self.refresh), ('Settings…', self.settings),
                                 ('Set up Pi…', self.setup)]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            extras.addWidget(button)
        layout.addLayout(extras)

        self.menu = QMenu()
        self.menu.setTitle('Pi Audio Tray')
        self.menu_rows = {}
        for key in self.rows:
            item = self.menu.addAction('—')
            item.setEnabled(False)
            self.menu_rows[key] = item
        self.menu.addSeparator()
        self.on_action = self.menu.addAction('Enable pairing · 3 min', self.start_pairing)
        self.off_action = self.menu.addAction('Disable pairing', self.stop_pairing)
        self.menu.addSeparator()
        self.menu.addAction('Open status', self.open_window)
        self.menu.addAction('Refresh now', self.refresh)
        self.menu.addAction('Settings…', self.settings)
        self.menu.addAction('Set up / update Pi helper…', self.setup)
        self.menu.addSeparator()
        self.menu.addAction('Quit', self.quit)
        self.tray = QSystemTrayIcon(icon(), self)
        self.tray.setContextMenu(self.menu)
        self.tray.setToolTip('Pi Audio Tray')
        self.tray.activated.connect(lambda reason: self.open_window()
                                   if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(self.config['poll_seconds'] * 1000)
        self.clock = QTimer(self)
        self.clock.timeout.connect(self.update_countdown)
        self.clock.start(1000)
        self.render_offline('Not connected')
        QTimer.singleShot(100, self.refresh if self.config['host'] else self.open_window)

    def job(self, kind, func):
        if kind in self.busy:
            return
        self.busy.add(kind)
        generation = self.generation
        def worker():
            try:
                value = func()
                if generation == self.generation:
                    self.events.done.emit(kind, value)
            except Exception as exc:
                if generation == self.generation:
                    self.events.failed.emit(kind, exc)
        threading.Thread(target=worker, daemon=True).start()

    def refresh(self):
        if self.config['host']:
            config = self.config.copy()
            self.job('poll', lambda: poll(config))

    def row(self, key, value):
        labels = {'pi': 'Pi', 'spotify': 'Spotify', 'bluetooth': 'Bluetooth',
                  'audio': 'BT audio', 'devices': 'Devices', 'pairing': 'Pairing'}
        self.rows[key].setText(value)
        self.menu_rows[key].setText(labels[key] + ': ' + value)

    def render_offline(self, message):
        self.snapshot = None
        self.row('pi', 'Offline / unavailable')
        for key in ('spotify', 'bluetooth', 'audio', 'devices', 'pairing'):
            self.row(key, 'Unknown')
        self.tray.setIcon(icon('#e07878'))
        self.tray.setToolTip('Pi Audio Tray · unavailable')
        self.notice.setText(message)
        self.enable_controls()

    def enable_controls(self):
        can_start = bool(self.snapshot) and self.session is None
        self.on_action.setEnabled(can_start)
        self.pair_button.setEnabled(can_start)
        can_stop = bool(self.snapshot) or self.session is not None
        self.off_action.setEnabled(can_stop)
        self.off_button.setEnabled(can_stop)

    def done(self, kind, data):
        self.busy.discard(kind)
        if kind == 'poll':
            self.snapshot = data
            services, adapter = data['services'], data['adapter']
            healthy = adapter.get('Powered') and all(services.get(s) == 'active' for s in ('raspotify', 'bluetooth', 'bluealsa', 'bluealsa-aplay'))
            self.tray.setIcon(icon('#55d9ad' if healthy else '#e8b75f'))
            self.tray.setToolTip('Pi Audio Tray · ' + ('services running' if healthy else 'check services'))
            self.row('pi', 'Online · ' + self.config['host'])
            self.row('spotify', services.get('raspotify', 'unknown') + ' (service)')
            self.row('bluetooth', 'Powered on' if adapter.get('Powered') else 'Powered off / unavailable')
            self.row('audio', 'Ready' if all(services.get(s) == 'active' for s in ('bluealsa', 'bluealsa-aplay')) else 'Service stopped')
            self.row('devices', ', '.join(d['name'] for d in data['devices']) or 'None')
            self.row('pairing', 'Enabled' if adapter.get('Pairable') else 'Disabled')
            self.notice.setText('Updated ' + time.strftime('%H:%M:%S') + ' · Spotify status shows the service, not track playback.')
            self.update_countdown()
            self.enable_controls()
        else:
            if kind == 'setup':
                self.notice.setText('Pi helper installed. Checking connection…')
            self.refresh()

    def failed(self, kind, error):
        self.busy.discard(kind)
        if isinstance(error, UnknownHost):
            # Never silently trust an unknown server or a changed key.
            self.timer.stop()
            reply = QMessageBox.question(self, 'Verify the Pi SSH host key',
                f'Host: {error.hostname}\n\n{error.fingerprint}\n\n'
                'Compare this fingerprint with the Pi before trusting it.\nTrust this host key?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                trust_host(error)
                self.refresh()
            self.timer.start(self.config['poll_seconds'] * 1000)
        elif kind == 'poll':
            self.render_offline(str(error))
        else:
            QMessageBox.warning(self, 'Pi Audio Tray', str(error))

    def open_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def settings(self):
        if self.session:
            QMessageBox.information(self, 'Pairing active', 'Disable pairing before changing the connection.')
            return
        dialog = Settings(self.config, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            config = dialog.values()
            if not config['host'] or not config['username']:
                QMessageBox.warning(self, 'Settings', 'Enter a hostname and SSH username.')
                return
            save_config(config)
            self.config = config
            self.generation += 1
            self.busy.clear()
            self.render_offline('Connecting…')
            self.timer.start(config['poll_seconds'] * 1000)
            self.refresh()

    def setup(self):
        if not self.config['host']:
            self.settings()
            return
        if self.session:
            QMessageBox.information(self, 'Pairing active', 'Disable pairing before updating the helper.')
            return
        if QMessageBox.question(self, 'Set up Pi helper',
            'Install the Bluetooth helper and Python D-Bus libraries on your Pi?\n\n'
            'This uses SSH and requires passwordless sudo on the Pi.\n'
            'Your audio services and their settings remain in place.') != QMessageBox.StandardButton.Yes:
            return
        self.notice.setText('Installing helper on the Pi…')
        config = self.config.copy()
        self.job('setup', lambda: install_helper(config))

    def start_pairing(self):
        if self.session:
            return
        self.session = PairSession(self.config, self.events.pairing.emit)
        self.row('pairing', 'Starting…')
        self.enable_controls()
        threading.Thread(target=self.session.run, daemon=True).start()

    def stop_pairing(self):
        try:
            if self.session:
                self.session.stop()
        except Exception:
            pass
        config = self.config.copy()
        self.job('off', lambda: disable_pairing(config))

    def pair_event(self, event):
        kind = event['event']
        if kind == 'ready':
            self.expires = event['expires']
            self.tray.showMessage('Bluetooth pairing enabled', 'Choose your Pi on the device you want to pair. The window closes in 3 minutes.')
            self.refresh()
        elif kind == 'confirm':
            token = event['token']
            text = f"Pair with {event['name']}?\n{event['address']}"
            if event.get('code'):
                text += f"\n\nConfirm the same code on your device: {event['code']}"
            text += '\n\nApproved devices will be trusted for future connections.'
            dialog = QMessageBox(QMessageBox.Icon.Question, 'Bluetooth pairing request', text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
            dialog.setDefaultButton(QMessageBox.StandardButton.No)
            dialog.setTextFormat(Qt.TextFormat.PlainText)
            self.dialogs[token] = dialog
            def reply(result):
                self.dialogs.pop(token, None)
                if self.session:
                    try:
                        self.session.send(token=token, action='approve' if result == QMessageBox.StandardButton.Yes else 'reject')
                    except Exception as exc:
                        self.notice.setText(str(exc))
            dialog.finished.connect(reply)
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
        elif kind == 'canceled':
            dialog = self.dialogs.pop(event['token'], None)
            if dialog:
                dialog.reject()
        elif kind == 'display':
            self.tray.showMessage('Bluetooth passkey', event['code'])
        elif kind == 'error':
            self.notice.setText(event['message'])
            self.tray.showMessage('Pairing failed', event['message'], QSystemTrayIcon.MessageIcon.Warning)
        elif kind == 'ended':
            self.session = None
            self.expires = 0
            for dialog in list(self.dialogs.values()):
                dialog.reject()
            self.dialogs.clear()
            self.enable_controls()
            self.refresh()
            if self.closing:
                self.app.quit()

    def update_countdown(self):
        if self.expires and self.session:
            self.row('pairing', f'Enabled · {max(0, self.expires - int(time.time()))}s remaining')

    def quit(self):
        self.timer.stop()
        if self.session:
            self.closing = True
            self.stop_pairing()
            QTimer.singleShot(5000, self.app.quit)
        else:
            self.app.quit()

    def closeEvent(self, event):
        if QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.hide()
        else:
            self.quit()


def main():
    if '--check' in sys.argv:
        print(json.dumps(poll(load_config())))
        return 0
    app = QApplication(sys.argv)
    if '--self-test' in sys.argv:
        from PySide6.QtCore import qVersion
        assert not icon().isNull()
        print(json.dumps({'gui': 'ok', 'qt': qVersion(),
                          'tray_available': QSystemTrayIcon.isSystemTrayAvailable()}))
        return 0
    app.setApplicationName('Pi Audio Tray')
    app.setQuitOnLastWindowClosed(False)
    lock = open(config_dir() / 'instance.lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        QMessageBox.information(None, 'Pi Audio Tray', 'Pi Audio Tray is already running. Open it from the tray icon.')
        return 0
    app.setStyle('Fusion')
    app.setStyleSheet('''
        QWidget { font-size: 13px; }
        QPushButton { padding: 7px 12px; }
        QLineEdit, QSpinBox { padding: 5px; }
    ''')
    window = TrayApp(app)
    if '--show' in sys.argv or not QSystemTrayIcon.isSystemTrayAvailable():
        window.open_window()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
