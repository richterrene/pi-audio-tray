#!/usr/bin/env python3
"""Capture the real Qt widgets with fictional connection data and no network."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_SCALE_FACTOR'] = '1'
SCREENSHOT_CONFIG = tempfile.TemporaryDirectory(prefix='pi-audio-screenshot-config-')
os.environ['XDG_CONFIG_HOME'] = SCREENSHOT_CONFIG.name

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt
from app import Settings, TrayApp
from music_ui import MusicWindow
from network_ui import NetworkWindow
from setup_ui import SetupDialog
from profile_ui import ProfilesDialog
import profiles
from i18n import set_language


def capture(widget, path):
    widget.ensurePolished()
    if widget.layout() is not None:
        widget.layout().activate()
    widget.resize(widget.size().expandedTo(widget.sizeHint()))
    QApplication.processEvents()
    # Render at 2x without shrinking the offscreen screen's logical geometry.
    # Scaling the whole virtual screen can force menus into multiple columns.
    pixmap = QPixmap(widget.size() * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    widget.render(pixmap)
    pixmap.save(str(path))


def main():
    app = QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet('QWidget { font-size: 13px; } QPushButton { padding: 7px 12px; } QLineEdit, QSpinBox { padding: 5px; }')
    set_language('en')
    profiles.rename('default', 'Living room')
    profiles.add('Study')
    window = TrayApp(app)
    window.timer.stop()
    window.clock.stop()
    window.config.update(host='audio-pi.local', username='audio', key_file='~/.ssh/id_ed25519', music_web_url='https://audio-pi.local/')
    window.done('poll', {
        'services': {'soloist': 'active', 'bluetooth': 'active', 'pipewire': 'active', 'wireplumber': 'active'},
        'adapter': {'Powered': True, 'Pairable': False, 'Discoverable': False},
        'devices': [{'name': 'Phone'}], 'spotify_backend': 'soloist',
        'soloist': {'key_configured': True, 'days_remaining': 88, 'automatic_updates': True, 'update_failed': False},
        'stats': {'temperature_c': 43.1, 'cpu_percent': 12.5, 'cpu_count': 4, 'cpu_window_seconds': 10,
                  'load': [0.42, 0.31, 0.22], 'memory_used': 320 * 1024**2,
                  'memory_total': 900 * 1024**2, 'disk_used': 3 * 1024**3,
                  'disk_total': 15 * 1024**3, 'uptime_seconds': 183600}
    })
    window.notice.setText('Demo connection · Service status is separate from track playback.')
    window.resize(560, window.sizeHint().height())
    window.show()
    app.processEvents()
    output = ROOT / 'assets/screenshots'
    output.mkdir(parents=True, exist_ok=True)
    capture(window, output / 'status.png')
    with patch('app.autostart_enabled', return_value=False):
        settings = Settings(window.config, window)
    settings.setMinimumWidth(590)
    settings.show()
    app.processEvents()
    capture(settings, output / 'settings.png')
    settings.hide()
    profile_dialog = ProfilesDialog(window)
    profile_dialog.show()
    app.processEvents()
    capture(profile_dialog, output / 'pi-profiles.png')
    profile_dialog.hide()
    set_language('de')
    with patch('app.autostart_enabled', return_value=False):
        translated = Settings({**window.config, 'language': 'de'}, window)
    translated.setMinimumWidth(590)
    translated.show()
    app.processEvents()
    capture(translated, output / 'settings-german.png')
    translated.hide()
    set_language('en')
    window.menu.ensurePolished()
    window.menu.resize(window.menu.sizeHint())
    capture(window.menu, output / 'tray-menu.png')

    def capture_confirmation(dialog):
        dialog.show()
        app.processEvents()
        capture(dialog, output / 'shutdown-confirmation.png')
        dialog.hide()
        return QMessageBox.StandardButton.No

    # Exercise the actual confirmation text and capture its real widget, then
    # cancel. SSH is also blocked so screenshot generation cannot reach a Pi.
    with patch.object(QMessageBox, 'exec', capture_confirmation), \
         patch('core.connect', side_effect=AssertionError('Screenshots cannot connect to a receiver')):
        window.shutdown()
    with patch.object(MusicWindow, 'request'):
        music = MusicWindow(window)
        music.show()
        music.timer.stop()
        music.path = 'Example Artist/First Album'
        music.operation_done(({'action': 'browse', 'path': music.path, 'offset': 0}, {'items': [
            {'file': 'Example Artist/First Album/01 - Morning.mp3', 'Title': 'Morning', 'Artist': 'Example Artist'},
            {'file': 'Example Artist/First Album/02 - Home.mp3', 'Title': 'Home', 'Artist': 'Example Artist'},
            {'file': 'Example Artist/First Album/03 - Evening.mp3', 'Title': 'Evening', 'Artist': 'Example Artist'}], 'more': False},
            {'status': {'state': 'pause', 'volume': '100'}, 'current': {'Title': 'Morning', 'Artist': 'Example Artist'}}, window.config.copy()), None)
        app.processEvents()
        capture(music, output / 'local-music.png')
        music.tabs.setCurrentIndex(1)
        music.operation_done(({'action': 'queue', 'offset': 0}, {'items': [
            {'file': 'Example Artist/First Album/01 - Morning.mp3', 'Title': 'Morning', 'Artist': 'Example Artist', 'Id': '1'},
            {'file': 'Example Artist/First Album/02 - Home.mp3', 'Title': 'Home', 'Artist': 'Example Artist', 'Id': '2'},
            {'file': 'http://192.168.1.20:8201/MediaItems/3.flac', 'Title': 'Evening', 'Artist': 'Example Artist', 'Id': '3'}], 'more': False},
            {'status': {'state': 'pause', 'volume': '100'}, 'current': {'Title': 'Morning', 'Artist': 'Example Artist'}}, window.config.copy()), None)
        music.queue.setCurrentRow(1)
        app.processEvents()
        capture(music, output / 'music-queue.png')
        music.hide()
    with patch.object(NetworkWindow, 'request'):
        network = NetworkWindow(window)
        network.show()
        source = {'id': 'a' * 32, 'name': 'Home Music Server', 'address': '192.168.1.20', 'enabled': True}
        network.operation_done(({'action': 'network_sources'}, window.config.copy(), window.generation,
                                {'sources': [source]}), None)
        network.server = source
        network.history = [('0', 'Library'), ('albums', 'Albums'), ('first', 'First Album')]
        network.operation_done(({'action': 'network_browse', 'server': source['id'], 'object': 'first', 'offset': 0},
                                window.config.copy(), window.generation,
                                {'items': [{'object': f'track-{i}', 'Title': title, 'Artist': 'Example Artist'}
                                           for i, title in enumerate(('Morning', 'Home', 'Evening'))], 'more': False}), None)
        app.processEvents()
        capture(network, output / 'network-music.png')
        network.hide()
    setup = SetupDialog(window.config, window)
    setup.inventory({'supported': True, 'reasons': [], 'model': 'Raspberry Pi 3 Model B+', 'os': 'Debian 13 (Trixie)', 'architecture': 'aarch64', 'user': 'audio', 'disks': {'blockdevices': [{'name': 'sda', 'tran': 'usb', 'children': [{'name': 'sda1', 'fstype': 'vfat', 'uuid': 'ABCD-1234', 'mountpoints': []}]}]}})
    setup.install_button.setEnabled(setup.checked)
    setup.music.setChecked(True)
    setup.web.setChecked(True)
    setup.cockpit.setChecked(True)
    setup.show()
    app.processEvents()
    capture(setup, output / 'new-pi-setup.png')
    setup.hide()
    print('Captured desktop and music windows with fictional data only.')


if __name__ == '__main__':
    with patch('core.connect', side_effect=AssertionError('Screenshots cannot connect to a receiver')), \
         patch.object(TrayApp, 'refresh', lambda self, **kwargs: None):
        main()
