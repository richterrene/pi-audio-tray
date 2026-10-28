import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
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

    def prepare_second_pi(self):
        import core, profiles
        first = {**self.window.config, 'host': 'first-pi.example', 'username': 'first'}
        core.save_config(first)
        identifier = profiles.add('Second Pi')
        profiles.activate(identifier)
        core.save_config({**first, 'host': 'second-pi.example', 'username': 'second'})
        profiles.activate('default')
        self.window.config = first
        self.window.reload_profiles()
        return identifier

    def test_profile_switch_discards_old_requests_and_hides_music(self):
        identifier = self.prepare_second_pi()
        self.window.session = None
        self.window.busy = {'poll'}
        old_generation = self.window.generation
        self.window.music_window = Mock()
        with patch.object(self.window, 'refresh'):
            self.window.switch_profile(identifier)
        self.assertEqual(self.window.config['host'], 'second-pi.example')
        self.assertEqual(self.window.generation, old_generation + 1)
        self.assertIsNone(self.window.snapshot)
        self.window.music_window.hide.assert_called_once()
        with patch.object(self.window, 'done') as done:
            self.window.job_done('poll', (old_generation, {'old': True}))
        done.assert_not_called()

    def test_profile_switch_is_blocked_during_pairing_or_mutations(self):
        import profiles
        identifier = self.prepare_second_pi()
        for busy, session in ((set(), self.session), ({'key'}, None), ({'shutdown'}, None)):
            self.window.busy, self.window.session = busy, session
            with patch('app.plain_information'):
                self.window.switch_profile(identifier)
            self.assertEqual(profiles.load()['active'], 'default')
            self.assertEqual(self.window.config['host'], 'first-pi.example')

    def test_another_pi_can_be_selected_after_shutdown_disconnects(self):
        identifier = self.prepare_second_pi()
        self.window.session = None
        self.window.power_state = 'disconnected'
        with patch.object(self.window, 'refresh'):
            self.window.switch_profile(identifier)
        self.assertIsNone(self.window.power_state)
        self.assertEqual(self.window.config['host'], 'second-pi.example')

    def test_settings_reads_os_autostart_without_changing_it(self):
        with patch('app.autostart_enabled', return_value=True), patch('app.set_autostart') as register:
            settings = Settings(self.window.config, self.window)
            self.assertTrue(settings.autostart.isChecked())
            settings.reject()
        register.assert_not_called()

    def test_autostart_saved_only_on_save_and_errors_are_visible(self):
        self.window.session = None
        dialog = Mock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.values.return_value = {**self.window.config, 'host': 'audio-pi.example', 'username': 'audio'}
        dialog.autostart.isEnabled.return_value = True
        dialog.autostart.isChecked.return_value = True
        with patch('app.Settings', return_value=dialog), patch('app.save_config'), \
             patch('app.set_autostart', side_effect=OSError('Registration is read-only')) as register, \
             patch('app.plain_warning') as warning, patch.object(self.window, 'refresh'):
            self.window.settings()
            register.assert_called_once_with(True)
            self.assertEqual(warning.call_args.args[1], 'Autostart could not be changed')
        dialog.exec.return_value = QDialog.DialogCode.Rejected
        with patch('app.Settings', return_value=dialog), patch('app.set_autostart') as register:
            self.window.settings()
        register.assert_not_called()

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

    def test_hidden_window_uses_light_poll_and_at_least_a_minute_interval(self):
        self.window.config['host']='audio-pi.example'
        with patch.object(self.window,'isVisible',return_value=False), \
             patch.object(self.window,'job') as job, patch('app.poll') as poll:
            self.window.update_polling()
            self.assertEqual(self.window.timer.interval(),60000)
            self.window.refresh()
            job.call_args.args[1]()
        poll.assert_called_once_with(self.window.config,detailed=False)

    def test_minimized_window_uses_background_polling(self):
        with patch.object(self.window,'isVisible',return_value=True), \
             patch.object(self.window,'isMinimized',return_value=True):
            self.assertFalse(self.window.details_needed())
            self.window.update_polling()
        self.assertEqual(self.window.timer.interval(),60000)

    def test_open_menu_requests_details_and_visible_menu_uses_foreground_interval(self):
        with patch.object(self.window,'isVisible',return_value=False), \
             patch.object(self.window.menu,'isVisible',return_value=True), patch.object(self.window,'refresh') as refresh:
            self.window.set_menu_open(True)
            refresh.assert_called_once_with(force_details=True)
            self.assertTrue(self.window.details_needed())
            self.assertEqual(self.window.timer.interval(),10000)
        with patch.object(self.window,'isVisible',return_value=False), patch.object(self.window.menu,'isVisible',return_value=False):
            self.window.set_menu_open(False)
            self.assertFalse(self.window.details_needed())
            self.assertEqual(self.window.timer.interval(),60000)

    def test_native_menu_without_hide_signal_does_not_latch_detailed_polling(self):
        self.window.config['host']='audio-pi.example'
        with patch.object(self.window,'isVisible',return_value=False), \
             patch.object(self.window.menu,'isVisible',return_value=False), \
             patch.object(self.window,'job') as job, patch('app.poll') as poll:
            self.window.set_menu_open(True)
            job.call_args.args[1]()
            self.assertTrue(poll.call_args.kwargs['detailed'])
            self.window.refresh()
            job.call_args.args[1]()
            self.assertFalse(poll.call_args.kwargs['detailed'])
            self.assertEqual(self.window.timer.interval(),60000)

    def test_visible_window_uses_configured_detailed_interval(self):
        with patch.object(self.window,'isVisible',return_value=True), \
             patch.object(self.window,'isMinimized',return_value=False):
            self.window.config['poll_seconds']=15
            self.window.update_polling()
            self.assertEqual(self.window.timer.interval(),15000)
            self.assertTrue(self.window.details_needed())

    def test_light_reply_does_not_clear_cached_metrics_and_open_window_gets_full_refresh(self):
        self.window.rows['cpu'].setText('12.5 %')
        health={'_detailed':False,'services':{},'adapter':{},'devices':[],'soloist':{}}
        with patch.object(self.window,'details_needed',return_value=True), patch.object(self.window,'refresh') as refresh:
            self.window.done('poll',health)
        self.assertEqual(self.window.rows['cpu'].text(),'12.5 %')
        refresh.assert_called_once()

    def prepare_shutdown(self):
        self.window.session = None
        self.window.snapshot = {'online': True}
        self.window.config.update(host='audio-pi.example', username='audio')

    def test_canceling_shutdown_does_not_start_a_job(self):
        self.prepare_shutdown()
        with patch('app.plain_question', return_value=QMessageBox.StandardButton.No), \
             patch.object(self.window, 'job') as job:
            self.window.shutdown()
        job.assert_not_called()

    def test_shutdown_requires_confirmation_and_uses_the_confirmed_connection(self):
        self.prepare_shutdown()
        with patch('app.plain_question', return_value=QMessageBox.StandardButton.Yes) as question, \
             patch.object(self.window, 'job') as job, patch('app.shutdown_pi') as shutdown:
            self.window.shutdown()
            self.assertIn('audio-pi.example', question.call_args.args[2])
            self.assertIn('reconnect power', question.call_args.args[2])
            kind, command = job.call_args.args
            self.assertEqual(kind, 'shutdown')
            command()
            shutdown.assert_called_once_with(self.window.config)

    def test_connection_change_during_confirmation_cannot_shutdown_another_pi(self):
        self.prepare_shutdown()
        def confirm(*args):
            self.window.config['host'] = 'different-pi.example'
            self.window.generation += 1
            return QMessageBox.StandardButton.Yes
        with patch('app.plain_question', side_effect=confirm), patch('app.plain_information'), \
             patch.object(self.window, 'job') as job:
            self.window.shutdown()
        job.assert_not_called()

    def test_shutdown_is_blocked_during_update_or_pairing(self):
        self.prepare_shutdown()
        for kind in ('update', 'pairing'):
            self.window.busy = {'update'} if kind == 'update' else set()
            self.window.session = self.session if kind == 'pairing' else None
            with patch('app.plain_information'), patch('app.plain_question') as question:
                self.window.shutdown()
            question.assert_not_called()

    def test_shutdown_request_is_not_reported_as_verified_poweroff(self):
        self.prepare_shutdown()
        with patch.object(self.window, 'refresh'):
            self.window.done('shutdown', None)
        self.assertEqual(self.window.power_state, 'requested')
        self.assertIn('Waiting', self.window.notice.text())
        self.window.render_offline('SSH failed')
        self.assertEqual(self.window.power_state, 'disconnected')
        self.assertIn('Check its activity LED', self.window.notice.text())
        self.assertFalse(self.window.shutdown_action.isEnabled())

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
