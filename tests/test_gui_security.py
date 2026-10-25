import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from app import TrayApp, Settings


class PairingDialogs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'XDG_CONFIG_HOME': self.temp.name})
        self.env.start()
        self.window = TrayApp(self.app)
        self.window.timer.stop()
        self.window.clock.stop()
        self.session = types.SimpleNamespace(session_id='current', send=Mock())
        self.window.session = self.session

    def tearDown(self):
        self.window.session = None
        for dialog in list(self.window.dialogs.values()): dialog.reject()
        self.window.dialogs.clear()
        self.window.hide()
        self.window.tray.hide()
        self.env.stop()
        self.temp.cleanup()

    def confirmation(self, session_id='current'):
        return {'event': 'confirm', 'session_id': session_id, 'token': '1', 'kind': 'passkey',
                'name': '<img src="https://example.invalid/image">', 'address': '00:11:22:33:44:55', 'code': '123456'}

    def test_stale_session_events_are_ignored(self):
        self.window.pair_event(self.confirmation('old'))
        self.assertFalse(self.window.dialogs)

    def test_dialog_is_plain_text_and_does_not_auto_approve(self):
        self.window.pair_event(self.confirmation())
        dialog = self.window.dialogs['1']
        self.assertEqual(dialog.textFormat(), Qt.TextFormat.PlainText)
        self.session.send.assert_not_called()
        dialog.done(QMessageBox.StandardButton.Yes)
        self.session.send.assert_called_once_with(token='1', action='approve')

    def test_canceled_request_cannot_later_be_approved(self):
        self.window.pair_event(self.confirmation())
        dialog = self.window.dialogs['1']
        self.window.pair_event({'event': 'canceled', 'session_id': 'current', 'token': '1'})
        dialog.done(QMessageBox.StandardButton.Yes)
        self.session.send.assert_not_called()

    def test_old_dialog_cannot_send_to_new_session_or_remove_its_dialog(self):
        self.window.pair_event(self.confirmation())
        old_dialog = self.window.dialogs['1']
        other = types.SimpleNamespace(session_id='new', send=Mock())
        self.window.session = other
        self.window.pair_event(self.confirmation('new'))
        new_dialog = self.window.dialogs['1']
        old_dialog.done(QMessageBox.StandardButton.Yes)
        self.assertIs(self.window.dialogs['1'], new_dialog)
        other.send.assert_not_called()
        self.session.send.assert_not_called()

    def test_old_connection_job_result_is_ignored(self):
        self.window.generation = 2
        with patch.object(self.window, 'done') as done:
            self.window.job_done('poll', (1, {'untrusted': 'old server'}))
            done.assert_not_called()

    def test_connection_cannot_be_saved_while_a_key_operation_is_in_progress(self):
        settings = Settings(self.window.config, self.window)
        self.window.busy.add('key')
        with patch('app.plain_information') as message:
            settings.accept()
        message.assert_called_once()
        self.assertNotEqual(settings.result(), settings.DialogCode.Accepted)

    def test_connection_cannot_be_saved_during_pairing(self):
        settings = Settings(self.window.config, self.window)
        with patch('app.plain_information') as message:
            settings.accept()
        message.assert_called_once()
        self.assertNotEqual(settings.result(), settings.DialogCode.Accepted)

    def test_pairing_cannot_start_during_helper_installation(self):
        self.window.session = None
        self.window.busy.add('setup')
        with patch('app.PairSession') as session:
            self.window.start_pairing()
        session.assert_not_called()

    def test_mutations_cannot_overlap_but_emergency_pairing_off_is_allowed(self):
        self.window.busy.add('update')
        with patch('app.plain_information') as message, patch('app.threading.Thread') as thread:
            self.window.job('key', Mock())
            thread.assert_not_called()
            message.assert_called_once()
            self.window.job('off', Mock())
            thread.assert_called_once()

    def test_all_requested_pi_statistics_are_displayed(self):
        self.window.config['host'] = 'audio-pi.example'
        self.window.done('poll', {'services': {'soloist': 'active', 'bluetooth': 'active',
            'pipewire': 'active', 'wireplumber': 'active'}, 'adapter': {'Powered': True},
            'devices': [], 'spotify_backend': 'soloist', 'soloist': {}, 'stats': {
                'cpu_percent': 12.5, 'cpu_window_seconds': 10, 'temperature_c': 43.1, 'load': [0.4, 0.3, 0.2],
                'memory_total': 1024**3, 'memory_used': 512 * 1024**2,
                'disk_total': 16 * 1024**3, 'disk_used': 4 * 1024**3, 'uptime_seconds': 90061}})
        self.assertEqual(self.window.rows['temperature'].text(), '43.1 °C')
        self.assertEqual(self.window.rows['cpu'].text(), '12.5 % · 10.0s avg')
        self.assertEqual(self.window.rows['load'].text(), '0.40 / 0.30 / 0.20')
        self.assertIn('50%', self.window.rows['memory'].text())
        self.assertIn('25%', self.window.rows['disk'].text())
        self.assertEqual(self.window.rows['uptime'].text(), '1d 1h 1m')


if __name__ == '__main__': unittest.main()
