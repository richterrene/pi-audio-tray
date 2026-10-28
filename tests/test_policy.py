"""Source arbitration events, mute ownership and the private control boundary."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import pi_policy
import audio

ROOT = Path(__file__).resolve().parents[1]


def node(serial, source, running=True, muted=False, identifier=None):
    return {'serial': serial, 'id': identifier or serial, 'source': source, 'running': running, 'muted': muted}


class Arbitration(unittest.TestCase):
    def test_new_source_mutes_and_pauses_old_without_changing_volume(self):
        mute, pause = Mock(return_value=True), Mock(return_value=True)
        engine = pi_policy.Arbiter(mute, pause)
        engine.update({10: node(10, 'spotify')})
        state = engine.update({10: node(10, 'spotify'), 20: node(20, 'mpd')})
        self.assertEqual(state['winner'], 'mpd')
        mute.assert_called_once_with(10, True)
        pause.assert_called_once_with('spotify')

    def test_fallback_stream_does_not_resume_automatically_when_winner_stops(self):
        mute = Mock(return_value=True)
        engine = pi_policy.Arbiter(mute, Mock(return_value=False))
        engine.update({10: node(10, 'airplay')})
        engine.update({10: node(10, 'airplay'), 20: node(20, 'mpd')})
        mute.reset_mock()
        state = engine.update({10: node(10, 'airplay', muted=True), 20: node(20, 'mpd', running=False)})
        self.assertFalse(state['playing'])
        self.assertEqual(state['winner'], 'mpd')
        mute.assert_not_called()
        engine.update({10: node(10, 'airplay', running=False, muted=True), 20: node(20, 'mpd', running=False)})
        state = engine.update({10: node(10, 'airplay', muted=True), 20: node(20, 'mpd', running=False)})
        self.assertEqual(state['winner'], 'airplay')
        mute.assert_called_with(10, False)

    def test_preexisting_user_mute_is_not_owned_or_cleared(self):
        mute = Mock(return_value=True)
        engine = pi_policy.Arbiter(mute, Mock())
        engine.update({10: node(10, 'spotify', muted=True), 20: node(20, 'mpd')})
        self.assertEqual(engine.owned, {})
        mute.assert_not_called()

    def test_failed_mute_is_visible_as_degraded(self):
        engine = pi_policy.Arbiter(Mock(return_value=False), Mock(return_value=False))
        engine.update({10: node(10, 'spotify')})
        state = engine.update({10: node(10, 'spotify'), 20: node(20, 'mpd')})
        self.assertTrue(state['degraded'])

    def test_node_id_reuse_does_not_restore_a_different_stream(self):
        mute = Mock(return_value=True)
        engine = pi_policy.Arbiter(mute, Mock())
        engine.update({10: node(10, 'spotify')})
        engine.update({10: node(10, 'spotify'), 20: node(20, 'mpd')})
        mute.reset_mock()
        engine.update({30: node(30, 'airplay', identifier=10), 20: node(20, 'mpd', running=False)})
        self.assertNotIn(10, engine.owned)
        self.assertFalse(any(call.args == (10, False) for call in mute.call_args_list))

    def test_policy_unmute_echo_cannot_steal_back_a_newer_source(self):
        engine = pi_policy.Arbiter(Mock(return_value=True), Mock())
        engine.update({10: node(10, 'spotify')})
        engine.update({10: node(10, 'spotify'), 20: node(20, 'mpd')})
        engine.update({10: node(10, 'spotify', running=False, muted=True), 20: node(20, 'mpd')})
        engine.update({10: node(10, 'spotify', muted=True), 20: node(20, 'mpd')})
        engine.update({10: node(10, 'spotify', muted=True), 20: node(20, 'mpd', muted=True), 30: node(30, 'airplay')})
        state = engine.update({10: node(10, 'spotify', muted=False), 20: node(20, 'mpd', muted=True), 30: node(30, 'airplay')})
        self.assertEqual(state['winner'], 'airplay')

    def test_graph_recognition_ignores_hardware_sink_and_uses_stream_serial(self):
        def obj(identifier, media, app, name):
            return {'id': identifier, 'info': {'state': 'running', 'props': {'object.serial': identifier + 100, 'media.class': media, 'application.name': app, 'node.name': name}, 'params': {'Props': [{'mute': False}]}}}
        found = pi_policy.nodes([obj(1, 'Audio/Sink', 'Spotify', 'hardware'), obj(2, 'Stream/Output/Audio', 'Spotify', 'spotify'), obj(3, 'Stream/Output/Audio', 'Music Player Daemon', 'mpd.test')])
        self.assertEqual(set(found), {102, 103})

    def test_play_event_can_reselect_existing_running_muted_spotify_node(self):
        engine = pi_policy.Arbiter(Mock(return_value=True), Mock())
        engine.update({10: node(10, 'spotify')})
        engine.update({10: node(10, 'spotify'), 20: node(20, 'mpd')})
        state = engine.update({10: node(10, 'spotify', muted=True), 20: node(20, 'mpd')}, preferred='spotify')
        self.assertEqual(state['winner'], 'spotify')

    def test_trace_uses_only_state_and_handles_split_and_oversized_lines(self):
        trace = pi_policy.SpotifyTrace()
        self.assertEqual(trace.feed(b'123 {"type":"auth_state","is_active":true}\n'), [])
        self.assertEqual(trace.feed(b'124 {"type":"playback_changed","sta'), [])
        self.assertEqual(trace.feed(b'tus":"playing"}\n'), [True])
        self.assertEqual(trace.feed(b'x' * 70000), [])
        self.assertEqual(trace.feed(b'\n125 {"type":"playback_changed","status":"paused"}\n'), [False])
        self.assertFalse(hasattr(trace, 'track'))

    def test_bluez_signal_extracts_only_valid_device_play_state(self):
        watcher = pi_policy.BluetoothEvents()
        watcher.changed('org.bluez.MediaPlayer1', {'Status': 'playing'}, [], path='/org/bluez/hci0/dev_00_11_22_33_44_55/player0')
        watcher.changed('org.bluez.MediaPlayer1', {'Status': 'playing'}, [], path='/untrusted/path')
        self.assertEqual(watcher.events, [('bluetooth:00_11_22_33_44_55', True)])


@unittest.skipIf(sys.platform == 'win32', 'POSIX receiver private files')
class PrivatePolicy(unittest.TestCase):
    def test_default_is_mix_and_settings_are_private(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(pi_policy, 'CONFIG', Path(folder).resolve() / 'config/policy.json'):
            self.assertEqual(pi_policy.settings(), {'exclusive': False})
            pi_policy.write_private(pi_policy.CONFIG, {'exclusive': True})
            self.assertEqual(pi_policy.CONFIG.stat().st_mode & 0o777, 0o600)
            self.assertEqual(pi_policy.settings(), {'exclusive': True})

    def test_restore_checks_serial_before_unmuting_and_never_touches_new_id_owner(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(pi_policy, 'STATE', Path(folder).resolve() / 'state.json'):
            pi_policy.write_private(pi_policy.STATE, {'cookie': 10, 'muted': [{'id': 10, 'serial': 100}]})
            with patch.object(pi_policy, 'graph_snapshot', return_value=(10, {200: node(200, 'mpd', identifier=10)})), patch.object(pi_policy, 'run') as run:
                self.assertTrue(pi_policy.restore())
            run.assert_not_called()

    def test_restarted_pipewire_never_restores_old_id_serial_pair(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(pi_policy, 'STATE', Path(folder).resolve() / 'state.json'):
            pi_policy.write_private(pi_policy.STATE, {'cookie': 10, 'muted': [{'id': 10, 'serial': 100}]})
            with patch.object(pi_policy, 'graph_snapshot', return_value=(99, {100: node(100, 'mpd', identifier=10)})), patch.object(pi_policy, 'run') as run:
                self.assertTrue(pi_policy.restore())
            run.assert_not_called()

    def test_launcher_keeps_default_api_off_and_initial_volume_at_100(self):
        spec = importlib.util.spec_from_file_location('policy_launcher', ROOT / 'pi/soloist-launch.py')
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        with tempfile.TemporaryDirectory() as folder, patch.object(launcher.Path, 'home', return_value=Path(folder).resolve()), patch.object(launcher, 'load_key', return_value='fixture-key'), patch.object(launcher.os, 'geteuid', return_value=1000), patch.object(launcher.os, 'execv') as execute:
            launcher.main()
            argv = execute.call_args.args[1]
            self.assertEqual(argv[argv.index('-i') + 1], '100')
            self.assertNotIn('--ws', argv)
            path = Path(folder).resolve() / '.config/pi-audio-tray/source-policy.json'
            path.parent.mkdir(parents=True)
            path.write_text('{"exclusive": true}')
            path.chmod(0o600)
            launcher.main()
            argv = execute.call_args.args[1]
            self.assertEqual(argv[argv.index('--ws') + 1], '127.0.0.1:0')


class Transport(unittest.TestCase):
    def test_bad_policy_is_rejected_before_connect(self):
        for value in ({'exclusive': 1}, {'exclusive': True, 'command': 'reboot'}):
            with patch.object(audio, 'connect') as connect, self.assertRaises(ValueError): audio.policy_request({}, value)
            connect.assert_not_called()


class PolicyPresentation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_source_status_renders_without_refresh_or_setting_changes(self):
        from maintenance_ui import PolicyDialog
        dialog = PolicyDialog({'host': 'audio-pi.local', 'username': 'audio'})
        with patch.object(audio, 'policy_request') as request:
            dialog.show_status({'installed': True, 'exclusive': True,
                                'state': 'active', 'source': 'spotify', 'degraded': False})
            dialog.controls()
            self.assertIn('spotify', dialog.message.text())
            self.assertFalse(dialog.allow.isChecked())
            request.assert_not_called()
        dialog.deleteLater()


if __name__ == '__main__': unittest.main()
