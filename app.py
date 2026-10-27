#!/usr/bin/env python3
"""Pi Audio Tray: a small, configurable SSH-based audio monitor."""
import json
import os
from pathlib import Path
import sys
import threading
import tempfile
import time

from PySide6.QtCore import QObject, QTimer, Qt, Signal, QEvent
from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
    QPushButton, QSpinBox, QSystemTrayIcon, QVBoxLayout, QWidget)

from core import (PairSession, UnknownHost, config_dir, disable_pairing,
                  install_helper, load_config, poll, save_config, store_soloist_key,
                  remove_soloist_key, update_soloist, trust_host, display_text, instance_lock_file, shutdown_pi)
from music_ui import MusicWindow
from version import VERSION
from ui_style import STYLE, StableLabel, symbol
from platform_support import lock_instance


def plain_message(parent, icon_type, title, text, buttons, default):
    box = QMessageBox(icon_type, title, '', buttons, parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(''.join(c for c in str(text) if c == '\n' or c.isprintable())[:4096])
    box.setDefaultButton(default)
    return box.exec()


def plain_question(parent, title, text, buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                   default=QMessageBox.StandardButton.No):
    return plain_message(parent, QMessageBox.Icon.Question, title, text, buttons, default)


def plain_warning(parent, title, text):
    return plain_message(parent, QMessageBox.Icon.Warning, title, text,
                         QMessageBox.StandardButton.Ok, QMessageBox.StandardButton.Ok)


def plain_information(parent, title, text):
    return plain_message(parent, QMessageBox.Icon.Information, title, text,
                         QMessageBox.StandardButton.Ok, QMessageBox.StandardButton.Ok)


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
                                  ('key_file', 'SSH private key (optional)', '~/.ssh/id_ed25519'),
                                  ('music_web_url', 'Music HTTPS URL (optional)', 'https://audio-pi.local:8443/')]:
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
        soloist_label = QLabel('Spotify Soloist')
        soloist_label.setStyleSheet('font-weight: 600; margin-top: 12px;')
        layout.addWidget(soloist_label)
        layout.addWidget(QLabel('The API key is stored only on the configured Pi.'))
        soloist_controls = QHBoxLayout()
        key_button = QPushButton('Set / replace API key…')
        key_button.clicked.connect(lambda: parent.soloist_key(self))
        remove_button = QPushButton('Remove API key…')
        remove_button.clicked.connect(parent.remove_key)
        soloist_controls.addWidget(key_button)
        soloist_controls.addWidget(remove_button)
        layout.addLayout(soloist_controls)
        layout.addWidget(QLabel('These actions use the saved connection. Save connection changes first.'))
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

    def accept(self):
        if self.parentWidget().session or self.parentWidget().busy - {'poll'}:
            plain_information(self, 'Pi operation in progress',
                              'Wait for the current Pi operation before saving connection changes.')
            return
        super().accept()


class TrayApp(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.config = load_config()
        self.events = Events()
        self.events.done.connect(self.job_done)
        self.events.failed.connect(self.job_failed)
        self.events.pairing.connect(self.pair_event)
        self.busy = set()
        self.session = None
        self.expires = 0
        self.dialogs = {}
        self.snapshot = None
        self.closing = False
        self.power_state = None
        self.details_requested = False
        self.music_window = None
        self.generation = 0
        self.setWindowTitle('Pi Audio Tray · ' + VERSION)
        self.setWindowIcon(icon())
        self.setMinimumWidth(780)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(14)
        header = QHBoxLayout()
        logo = QLabel()
        logo_image = icon('#22c58b').pixmap(96, 96)
        logo_image.setDevicePixelRatio(2)
        logo.setPixmap(logo_image)
        header.addWidget(logo)
        heading = QVBoxLayout()
        title = QLabel('Pi Audio')
        title.setStyleSheet('font-size: 25px; font-weight: 600;')
        heading.addWidget(title)
        subtitle = QLabel('Receiver control & system health')
        heading.addWidget(subtitle)
        header.addLayout(heading)
        header.addStretch()
        version = QLabel('v' + VERSION)
        header.addWidget(version)
        layout.addLayout(header)
        self.rows = {}
        cards = QHBoxLayout()
        cards.setSpacing(14)
        groups = [
            ('Receiver', 'receiver', [
                ('pi', 'Raspberry Pi', 'receiver'), ('spotify', 'Spotify Connect', 'music'),
                ('bluetooth', 'Bluetooth', 'bluetooth'), ('audio', 'Audio output', 'audio'),
                ('devices', 'Devices', 'devices'), ('pairing', 'Pairing', 'pairing'),
                ('soloist', 'Soloist key', 'key'), ('updates', 'Updates', 'refresh')]),
            ('System', 'cpu', [
                ('temperature', 'Temperature', 'temperature'), ('cpu', 'CPU usage', 'cpu'),
                ('load', 'Load · 1 / 5 / 15', 'load'), ('memory', 'Memory', 'memory'),
                ('disk', 'Root disk', 'disk'), ('uptime', 'Uptime', 'clock')])]
        for heading_text, _heading_icon, fields in groups:
            card = QFrame()
            card.setObjectName('StatusCard')
            section = QVBoxLayout(card)
            section.setContentsMargins(16, 16, 16, 16)
            section.setSpacing(10)
            label = QLabel(heading_text)
            label.setObjectName('SectionTitle')
            section.addWidget(label)
            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(5)
            grid.setColumnStretch(2, 1)
            for index, (key, text, glyph) in enumerate(fields):
                image = QLabel()
                glyph_image = symbol(glyph, '#139b7d').pixmap(36, 36)
                glyph_image.setDevicePixelRatio(2)
                image.setPixmap(glyph_image)
                image.setFixedWidth(20)
                grid.addWidget(image, index, 0)
                label = QLabel(text)
                grid.addWidget(label, index, 1)
                value = StableLabel('—')
                self.rows[key] = value
                grid.addWidget(value, index, 2)
            section.addLayout(grid)
            section.addStretch(1)
            cards.addWidget(card, 1)
        layout.addLayout(cards)
        self.notice = StableLabel('Configure your Pi to get started.', lines=3, padding=24)
        self.notice.setObjectName('Notice')
        self.notice.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.notice)
        controls = QHBoxLayout()
        self.pair_button = QPushButton('Enable pairing · 3 min')
        self.pair_button.setObjectName('Primary')
        self.pair_button.setIcon(symbol('bluetooth', '#ffffff'))
        self.pair_button.clicked.connect(self.start_pairing)
        self.off_button = QPushButton('Disable pairing')
        self.off_button.setIcon(symbol('stop'))
        self.off_button.clicked.connect(self.stop_pairing)
        controls.addWidget(self.pair_button)
        controls.addWidget(self.off_button)
        layout.addLayout(controls)
        extras = QHBoxLayout()
        for label, glyph, callback in [('Music…', 'music', self.open_music),
                                       ('Refresh', 'refresh', self.refresh),
                                       ('Settings…', 'settings', self.settings),
                                       ('Set up Pi…', 'setup', self.setup)]:
            button = QPushButton(label)
            button.setIcon(symbol(glyph))
            button.clicked.connect(callback)
            extras.addWidget(button)
        layout.addLayout(extras)
        soloist_controls = QHBoxLayout()
        for label, glyph, callback in [('Soloist API key…', 'key', self.soloist_key),
                                       ('Update Soloist', 'refresh', self.update_soloist)]:
            button = QPushButton(label)
            button.setIcon(symbol(glyph))
            button.clicked.connect(callback)
            soloist_controls.addWidget(button)
        layout.addLayout(soloist_controls)

        self.menu = QMenu()
        self.menu.setTitle('Pi Audio Tray')
        self.menu.aboutToShow.connect(lambda: self.set_menu_open(True))
        self.menu.aboutToHide.connect(lambda: self.set_menu_open(False))
        self.menu_rows = {}
        for key in self.rows:
            item = self.menu.addAction('—')
            item.setEnabled(False)
            self.menu_rows[key] = item
        self.menu.addSeparator()
        self.on_action = self.menu.addAction(symbol('bluetooth'), 'Enable pairing · 3 min', self.start_pairing)
        self.off_action = self.menu.addAction(symbol('stop'), 'Disable pairing', self.stop_pairing)
        self.menu.addSeparator()
        self.menu.addAction(symbol('music'), 'Music…', self.open_music)
        self.menu.addAction('Open status', self.open_window)
        self.menu.addAction(symbol('refresh'), 'Refresh now', self.refresh)
        self.menu.addAction(symbol('settings'), 'Settings…', self.settings)
        self.menu.addAction('Set up a new Pi…', self.setup)
        self.menu.addAction('Update Pi helper…', self.update_helper)
        soloist_menu = self.menu.addMenu('Spotify Soloist')
        soloist_menu.addAction('Set / replace API key…', self.soloist_key)
        soloist_menu.addAction('Remove API key…', self.remove_key)
        soloist_menu.addAction('Check for updates now', self.update_soloist)
        self.menu.addSeparator()
        self.shutdown_action = self.menu.addAction(symbol('power'), 'Shut down Pi…', self.shutdown)
        self.menu.addSeparator()
        version_action = self.menu.addAction('Version ' + VERSION)
        version_action.setEnabled(False)
        self.menu.addAction('Quit', self.quit)
        self.tray = QSystemTrayIcon(icon(), self)
        self.tray.setContextMenu(self.menu)
        self.tray.setToolTip('Pi Audio Tray')
        self.tray.activated.connect(lambda reason: self.open_window()
                                   if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.update_polling()
        self.clock = QTimer(self)
        self.clock.timeout.connect(self.update_countdown)
        self.clock.start(1000)
        self.render_offline('Not connected')
        QTimer.singleShot(100, self.refresh if self.config['host'] else self.open_window)

    def job(self, kind, func):
        if kind in self.busy:
            return
        if kind not in ('poll', 'off') and self.busy - {'poll'}:
            plain_information(self, 'Pi operation in progress',
                              'Wait for the current Pi operation before starting another.')
            return
        self.busy.add(kind)
        generation = self.generation
        def worker():
            try:
                value = func()
                self.events.done.emit(kind, (generation, value))
            except Exception as exc:
                self.events.failed.emit(kind, (generation, exc))
        threading.Thread(target=worker, daemon=True).start()

    def job_done(self, kind, payload):
        generation, value = payload
        if generation == self.generation:
            self.done(kind, value)

    def job_failed(self, kind, payload):
        generation, error = payload
        if generation == self.generation:
            self.failed(kind, error)

    def details_needed(self):
        return self.menu.isVisible() or (self.isVisible() and not self.isMinimized())

    def update_polling(self, refresh=False):
        if not hasattr(self, 'timer'):
            return
        if 'provision' in self.busy:
            self.timer.stop()
            return
        seconds = self.config['poll_seconds'] if self.details_needed() else max(60, self.config['poll_seconds'])
        interval = seconds * 1000
        if not self.timer.isActive():
            self.timer.start(interval)
        elif self.timer.interval() != interval:
            self.timer.setInterval(interval)
        if refresh and self.details_needed():
            self.refresh()

    def set_menu_open(self, opened):
        # Native exported menus may emit aboutToShow without aboutToHide, and
        # their QMenu stays invisible. Never latch continuous polling from it.
        if opened:
            self.refresh(force_details=True)
        self.update_polling()
        QTimer.singleShot(0, self.update_polling)

    def showEvent(self, event):
        super().showEvent(event)
        self.update_polling(refresh=True)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.update_polling()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.update_polling(refresh=True)

    def refresh(self, force_details=False):
        if 'provision' in self.busy:
            return
        if force_details:
            self.details_requested = True
        self.update_polling()
        if self.config['host'] and 'poll' not in self.busy:
            config, detailed = self.config.copy(), self.details_requested or self.details_needed()
            self.details_requested = False
            if detailed:
                self.notice.setText('Refreshing receiver details…')
            self.job('poll', lambda: poll(config, detailed=detailed))

    def row(self, key, value):
        labels = {'pi': 'Pi', 'spotify': 'Spotify', 'bluetooth': 'Bluetooth',
                  'audio': 'BT audio', 'devices': 'Devices', 'pairing': 'Pairing',
                  'soloist': 'Soloist', 'updates': 'Updates', 'temperature': 'Temperature',
                  'cpu': 'CPU', 'load': 'Load', 'memory': 'Memory', 'disk': 'Disk', 'uptime': 'Uptime'}
        self.rows[key].setText(value)
        self.menu_rows[key].setText(labels[key] + ': ' + value)

    def render_offline(self, message):
        self.snapshot = None
        if self.power_state:
            self.power_state = 'disconnected'
            message = ('Pi unreachable after shutdown request. Check its activity LED before removing power. '
                       'Reconnect power to start it again.')
        self.row('pi', 'Offline / unavailable')
        for key in self.rows:
            if key == 'pi':
                continue
            self.row(key, 'Unknown')
        self.tray.setIcon(icon('#e07878'))
        self.tray.setToolTip('Pi Audio Tray · unavailable')
        self.notice.setText(message)
        self.enable_controls()

    def enable_controls(self):
        can_start = bool(self.snapshot) and self.session is None and not self.busy - {'poll'} and not self.power_state
        self.on_action.setEnabled(can_start)
        self.pair_button.setEnabled(can_start)
        can_stop = bool(self.snapshot) or self.session is not None
        self.off_action.setEnabled(can_stop)
        self.off_button.setEnabled(can_stop)
        self.shutdown_action.setEnabled(can_start)

    def done(self, kind, data):
        self.busy.discard(kind)
        if kind == 'poll':
            if self.power_state == 'disconnected':
                self.power_state = None
            self.snapshot = data
            services, adapter = data['services'], data['adapter']
            backend = data.get('spotify_backend', 'raspotify')
            audio_services = ('pipewire', 'wireplumber') if backend == 'soloist' else ('bluealsa', 'bluealsa-aplay')
            healthy = adapter.get('Powered') and all(services.get(s) == 'active' for s in (backend, 'bluetooth', *audio_services))
            self.tray.setIcon(icon('#55d9ad' if healthy else '#e8b75f'))
            self.tray.setToolTip('Pi Audio Tray · ' + ('services running' if healthy else 'check services'))
            self.row('pi', 'Online · ' + self.config['host'])
            if not data.get('_detailed', True):
                if data.get('soloist', {}).get('update_failed'):
                    self.tray.setIcon(icon('#e8b75f'))
                if self.power_state:
                    self.notice.setText('Shutdown requested. Waiting for the Pi to disconnect. Reconnect power to start it again.')
                else:
                    self.notice.setText('Background health checked ' + time.strftime('%H:%M:%S') + ' · Open the window or tray menu for details.')
                self.enable_controls()
                # The window/menu may have opened while the light poll was pending.
                if self.details_requested or self.details_needed():
                    self.refresh()
                return
            self.details_requested = False
            self.row('spotify', services.get(backend, 'unknown') + ' · ' + backend.title())
            self.row('bluetooth', 'Powered on' if adapter.get('Powered') else 'Powered off / unavailable')
            self.row('audio', 'Ready' if all(services.get(s) == 'active' for s in audio_services) else 'Service stopped')
            soloist = data.get('soloist', {})
            if backend == 'soloist':
                key_state = 'Key configured' if soloist.get('key_configured') else 'API key needed'
                days = soloist.get('days_remaining')
                self.row('soloist', key_state + (f' · build valid {days} days' if days is not None else ''))
            else:
                self.row('soloist', 'Not installed')
            if soloist.get('update_failed'):
                self.row('updates', 'Last update failed — check the Pi')
                self.tray.setIcon(icon('#e8b75f'))
            else:
                self.row('updates', 'Automatic checks enabled' if soloist.get('automatic_updates') else 'Not fully configured')
            self.row('devices', ', '.join(d['name'] for d in data['devices']) or 'None')
            self.row('pairing', 'Enabled' if adapter.get('Pairable') else 'Disabled')
            stats = data.get('stats', {})
            temperature, cpu = stats.get('temperature_c'), stats.get('cpu_percent')
            self.row('temperature', f'{temperature:.1f} °C' if temperature is not None else 'Unavailable')
            window = stats.get('cpu_window_seconds')
            self.row('cpu', (f'{cpu:.1f} %' + (f' · {window:.1f}s avg' if window else ''))
                     if cpu is not None else 'Unavailable')
            load = stats.get('load')
            self.row('load', ' / '.join(f'{v:.2f}' for v in load) if load else 'Unavailable')
            for key, prefix in (('memory', 'memory'), ('disk', 'disk')):
                total, used = stats.get(prefix + '_total'), stats.get(prefix + '_used')
                self.row(key, f'{used / 2**30:.2f} / {total / 2**30:.2f} GiB · {used / total:.0%}'
                         if total and used is not None else 'Unavailable')
            uptime = stats.get('uptime_seconds')
            if uptime is not None:
                days, hours, minutes = uptime // 86400, uptime // 3600 % 24, uptime // 60 % 60
                self.row('uptime', f'{days}d {hours}h {minutes}m')
            else:
                self.row('uptime', 'Unavailable')
            self.notice.setText('Updated ' + time.strftime('%H:%M:%S') + ' · Spotify status shows the service, not track playback.')
            if self.power_state:
                self.notice.setText('Shutdown requested. Waiting for the Pi to disconnect. Reconnect power to start it again.')
            self.update_countdown()
            self.enable_controls()
        elif kind == 'shutdown':
            self.power_state = 'requested'
            self.notice.setText('Shutdown requested. Waiting for the Pi to disconnect. Reconnect power to start it again.')
            self.enable_controls()
            self.refresh()
        else:
            if kind == 'setup':
                self.notice.setText('Pi helper installed. Checking connection…')
            self.refresh()

    def failed(self, kind, error):
        self.busy.discard(kind)
        if isinstance(error, UnknownHost):
            # Never silently trust an unknown server or a changed key.
            self.timer.stop()
            reply = plain_question(self, 'Verify the Pi SSH host key',
                f'Host: {error.hostname}\n\n{error.fingerprint}\n\n'
                'Compare this fingerprint with the Pi before trusting it.\nTrust this host key?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                try:
                    trust_host(error)
                    self.refresh()
                except (OSError, ValueError) as exc:
                    self.notice.setText(display_text(exc))
            self.update_polling()
        elif kind == 'poll':
            self.render_offline(str(error))
        else:
            if kind == 'shutdown':
                self.notice.setText('Shutdown was not confirmed. Refresh the Pi status before retrying.')
                self.enable_controls()
            plain_warning(self, 'Pi Audio Tray', str(error))

    def open_music(self):
        if not self.config['host']:
            self.settings()
            return
        if self.music_window is None:
            self.music_window = MusicWindow(self)
        self.music_window.showNormal()
        self.music_window.raise_()
        self.music_window.activateWindow()

    def open_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def settings(self):
        if self.session or self.busy - {'poll'}:
            plain_information(self, 'Pi operation in progress',
                              'Finish pairing or the current Pi operation before changing the connection.')
            return
        dialog = Settings(self.config, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            config = dialog.values()
            if not config['host'] or not config['username']:
                plain_warning(self, 'Settings', 'Enter a hostname and SSH username.')
                return
            try:
                save_config(config)
            except (OSError, ValueError, TypeError) as exc:
                plain_warning(self, 'Settings', display_text(exc))
                return
            if self.music_window is not None:
                self.music_window.hide()
            self.config = config
            self.power_state = None
            self.generation += 1
            self.busy.clear()
            self.render_offline('Connecting…')
            self.update_polling()
            self.refresh()

    def setup(self):
        if not self.config['host'] or not self.config['username']:
            self.settings()
            return
        if self.session or self.busy - {'poll'} or self.power_state:
            plain_information(self, 'Pi operation active', 'Finish the current Pi operation before setup.')
            return
        from setup_ui import SetupDialog
        self.busy.add('provision')
        if self.music_window is not None:
            self.music_window.hide()
        self.timer.stop()
        dialog = SetupDialog(self.config, self)
        try:
            dialog.exec()
            if dialog.installed_plan and dialog.installed_plan['web']:
                self.config['music_web_url'] = 'https://' + dialog.installed_plan['hostname'] + '/'
                save_config(self.config)
        finally:
            self.busy.discard('provision')
            self.update_polling()
            self.refresh()

    def update_helper(self):
        if not self.config['host']:
            self.settings()
            return
        if self.session:
            plain_information(self, 'Pairing active', 'Disable pairing before updating the helper.')
            return
        if plain_question(self, 'Set up Pi helper',
            'Install the Bluetooth helper and Python D-Bus libraries on your Pi?\n\n'
            'This uses SSH and requires passwordless sudo on the Pi.\n'
            'Your audio services and their settings remain in place.') != QMessageBox.StandardButton.Yes:
            return
        self.notice.setText('Installing helper on the Pi…')
        config = self.config.copy()
        self.job('setup', lambda: install_helper(config))

    def soloist_key(self, parent=None):
        key, accepted = QInputDialog.getText(parent or self, 'Spotify Soloist API key',
            'Paste your personal Soloist API key.\nIt is sent directly to your Pi over SSH.',
            QLineEdit.EchoMode.Password)
        if accepted and key.strip():
            config = self.config.copy()
            self.job('key', lambda: store_soloist_key(config, key.strip()))

    def remove_key(self):
        if plain_question(self, 'Remove Soloist API key',
            'Stop Soloist and remove its API key from the Pi?\n'
            'This does not revoke the key at Spotify.') == QMessageBox.StandardButton.Yes:
            config = self.config.copy()
            self.job('key', lambda: remove_soloist_key(config))

    def update_soloist(self):
        self.notice.setText('Checking the official Soloist download for an update…')
        config = self.config.copy()
        self.job('update', lambda: update_soloist(config))

    def shutdown(self):
        if not self.snapshot or self.session or self.busy - {'poll'} or self.power_state:
            plain_information(self, 'Pi shutdown unavailable',
                              'Connect to the Pi and finish pairing or the current operation first.')
            return
        config, generation = self.config.copy(), self.generation
        if plain_question(self, 'Shut down Pi?',
            f"Shut down {config['host']}?\n\n"
            'Audio playback and SSH connections will stop.\n'
            'Wait for the activity LED to stop before unplugging.\n'
            'You will need to reconnect power to start the Pi again.') != QMessageBox.StandardButton.Yes:
            return
        if config != self.config or generation != self.generation:
            plain_information(self, 'Connection changed', 'Review the current Pi connection before requesting shutdown.')
            return
        if self.session or self.busy - {'poll'} or self.power_state:
            return
        self.notice.setText('Requesting Pi shutdown…')
        self.job('shutdown', lambda: shutdown_pi(config))
        self.enable_controls()

    def start_pairing(self):
        if self.session or self.busy - {'poll'} or self.power_state:
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
        if self.session is None or event.get('session_id') != self.session.session_id:
            return
        kind = event['event']
        if kind == 'ready':
            self.expires = event['expires']
            self.tray.showMessage('Bluetooth pairing enabled', 'Choose your Pi on the device you want to pair. The window closes in 3 minutes.')
            self.refresh()
        elif kind == 'confirm':
            if len(self.dialogs) >= 8:
                self.stop_pairing()
                self.notice.setText('Too many pairing requests; closing pairing.')
                return
            session = self.session
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
                if self.dialogs.get(token) is dialog and self.session is session:
                    self.dialogs.pop(token, None)
                    try:
                        session.send(token=token, action='approve' if result == QMessageBox.StandardButton.Yes else 'reject')
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
        if self.music_window is not None:
            self.music_window.hide()
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
    if '--soloist-key' in sys.argv:
        key, accepted = QInputDialog.getText(None, 'Spotify Soloist — geschützte Schlüsseleingabe',
            'Persönlichen Soloist-API-Schlüssel hier einfügen.\n'
            'Er wird direkt per SSH auf dem Pi gespeichert, nicht im Chat oder Repository.',
            QLineEdit.EchoMode.Password)
        if not accepted or not key.strip():
            return 1
        try:
            store_soloist_key(load_config(), key.strip())
        except Exception as exc:
            plain_warning(None, 'Soloist-Einrichtung', str(exc))
            return 1
        plain_information(None, 'Soloist-Einrichtung',
            'Der Schlüssel wurde geschützt auf dem Pi gespeichert. Soloist wird gestartet.')
        return 0
    if '--self-test' in sys.argv:
        from PySide6.QtCore import qVersion
        assert not icon().isNull()
        import provision
        from setup_ui import SetupDialog
        for name in provision.PI_FILES:
            assert (provision.ROOT / 'pi' / name).is_file(), name
        for name in provision.TOP_FILES:
            assert (provision.ROOT / name).is_file(), name
        setup_check = SetupDialog({'host': '', 'username': ''})
        setup_check.deleteLater()
        previous = os.environ.get('XDG_CONFIG_HOME')
        try:
            with tempfile.TemporaryDirectory(prefix='pi-audio-self-test-') as folder:
                os.environ['XDG_CONFIG_HOME'] = folder
                save_config(load_config())
                with instance_lock_file() as test_lock:
                    lock_instance(test_lock)
        finally:
            if previous is None:
                os.environ.pop('XDG_CONFIG_HOME', None)
            else:
                os.environ['XDG_CONFIG_HOME'] = previous
        print(json.dumps({'gui': 'ok', 'qt': qVersion(),
                          'tray_available': QSystemTrayIcon.isSystemTrayAvailable()}))
        return 0
    app.setApplicationName('Pi Audio Tray')
    app.setApplicationVersion(VERSION)
    app.setQuitOnLastWindowClosed(False)
    try:
        lock = instance_lock_file()
    except (OSError, ValueError) as exc:
        plain_warning(None, 'Cannot secure local settings', str(exc))
        return 78
    try:
        lock_instance(lock)
    except BlockingIOError:
        plain_information(None, 'Pi Audio Tray', 'Pi Audio Tray is already running. Open it from the tray icon.')
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
