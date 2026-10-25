#!/usr/bin/env python3
"""Capture the real Qt widgets with fictional connection data and no network."""
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_SCALE_FACTOR'] = '2'
os.environ['XDG_CONFIG_HOME'] = tempfile.mkdtemp(prefix='pi-audio-screenshot-config-')

from PySide6.QtWidgets import QApplication
from app import Settings, TrayApp


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
        'soloist': {'key_configured': True, 'days_remaining': 88, 'automatic_updates': True, 'update_failed': False}
    })
    window.notice.setText('Demo connection · Service status is separate from track playback.')
    window.resize(560, window.sizeHint().height())
    window.show()
    app.processEvents()
    output = ROOT / 'assets/screenshots'
    output.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(output / 'status.png'))
    settings = Settings(window.config, window)
    settings.setMinimumWidth(590)
    settings.show()
    app.processEvents()
    settings.grab().save(str(output / 'settings.png'))
    print('Captured status and settings screenshots using fictional data only.')


if __name__ == '__main__':
    main()
