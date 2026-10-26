import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QPushButton
from app import TrayApp
from ui_style import StableLabel, symbol, PATHS


class StableStatusLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_refresh_and_completion_messages_do_not_move_controls_or_rows(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'XDG_CONFIG_HOME': folder}), patch.object(TrayApp, 'refresh'):
            window = TrayApp(self.app)
            window.timer.stop()
            window.clock.stop()
            window.show()
            self.app.processEvents()
            watched = [window.notice, *window.rows.values(), *window.findChildren(QPushButton)]
            before = [widget.geometry() for widget in watched]
            size = window.size()
            for message in ('Refreshing receiver details…',
                            'Updated 12:34:56 · Spotify status shows the service, not track playback.',
                            'An unexpectedly long connection error. ' * 50):
                window.notice.setText(message)
                window.row('devices', 'A long untrusted device name ' * 25)
                self.app.processEvents()
                self.assertEqual([widget.geometry() for widget in watched], before)
                self.assertEqual(window.size(), size)
            window.hide()
            window.tray.hide()

    def test_long_text_remains_accessible_without_rich_text_tooltip_injection(self):
        label = StableLabel()
        text = '<img src="https://example.invalid/image">' * 50
        label.setText(text)
        self.assertEqual(label.text(), text)
        self.assertEqual(label.accessibleDescription(), text)
        self.assertNotIn('<img', label.toolTip())
        self.assertIn('&lt;img', label.toolTip())
        for name in PATHS:
            self.assertFalse(symbol(name).isNull())
