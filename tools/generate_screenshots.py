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


def capture(widget, path):
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
    window = TrayApp(app)
    window.timer.stop()
    window.clock.stop()
    window.config.update(host='audio-pi.local', username='audio', key_file='~/.ssh/id_ed25519')
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
    settings = Settings(window.config, window)
    settings.setMinimumWidth(590)
    settings.show()
    app.processEvents()
    capture(settings, output / 'settings.png')
    settings.hide()
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
            {'status': {'state': 'pause', 'volume': '30'}, 'current': {'Title': 'Morning', 'Artist': 'Example Artist'}}, window.config.copy()), None)
        app.processEvents()
        capture(music, output / 'local-music.png')
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
    print('Captured desktop and music windows with fictional data only.')


if __name__ == '__main__':
    with patch('core.connect', side_effect=AssertionError('Screenshots cannot connect to a receiver')), \
         patch.object(TrayApp, 'refresh', lambda self, **kwargs: None):
        main()
