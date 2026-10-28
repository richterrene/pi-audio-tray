"""Master volume bounds, receiver identity and responsive slider coalescing."""
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import volume

ROOT = Path(__file__).resolve().parents[1]


def status(**changes):
    return {'id': 50, 'serial': 100, 'cookie': 123, 'volume': 70, 'muted': False, 'amplified': False, 'name': 'Example DAC', **changes}


class MasterVolume(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location('volume_receiver_test', ROOT / 'pi/volume-control.py')
        value = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(value)
        return value

    def test_bounds_and_schema_reject_amplification_before_connect(self):
        for value in (-1, 101, True, '80', 1.5):
            with patch.object(volume, 'connect') as connect, self.assertRaises(ValueError):
                volume.request({}, {'action': 'volume', 'value': value, 'id': 50, 'serial': 100, 'cookie': 123})
            connect.assert_not_called()

    def test_changed_default_output_or_restarted_server_cannot_be_adjusted(self):
        receiver = self.module()
        for current in (status(id=51), status(serial=101), status(cookie=999)):
            with patch.object(receiver, 'status', return_value=current), patch.object(receiver, 'run') as run, self.assertRaises(ValueError):
                receiver.operate({'action': 'volume', 'value': 80, 'id': 50, 'serial': 100, 'cookie': 123})
            run.assert_not_called()

    def test_volume_and_mute_are_separate_fixed_node_commands(self):
        receiver = self.module()
        for action, value, expected in (('volume', 80, ('set-volume', '0.8')), ('mute', True, ('set-mute', '1'))):
            with patch.object(receiver, 'status', return_value=status()), patch.object(receiver, 'run') as run:
                receiver.operate({'action': action, 'value': value, 'id': 50, 'serial': 100, 'cookie': 123})
            run.assert_called_once_with('/usr/bin/wpctl', expected[0], '50', expected[1])

    def test_amplified_output_cannot_be_unmuted_before_reduction(self):
        receiver = self.module()
        with patch.object(receiver, 'status', return_value=status(amplified=True)), patch.object(receiver, 'run') as run, self.assertRaises(ValueError):
            receiver.operate({'action': 'mute', 'value': False, 'id': 50, 'serial': 100, 'cookie': 123})
        run.assert_not_called()

    def test_status_transport_is_normal_user_and_closes_connection(self):
        client = Mock()
        with patch.object(volume, 'connect', return_value=client), patch.object(volume, 'run', return_value=json.dumps(status())) as run:
            volume.request({})
        self.assertTrue(run.call_args.args[1].startswith('/usr/bin/python3 -I - '))
        self.assertNotIn('sudo', run.call_args.args[1])
        client.close.assert_called_once()


class Slider(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_read_does_not_write_and_drag_coalesces_to_latest_value(self):
        from maintenance_ui import VolumeDialog
        dialog = VolumeDialog({'host': 'audio-pi.local', 'username': 'audio'})
        with patch.object(volume, 'request') as request:
            dialog.show_status(status())
            dialog.controls()
            request.assert_not_called()
        dialog.slider.setValue(20)
        dialog.slider.setValue(40)
        dialog.slider.setValue(60)
        self.assertEqual(dialog.pending['volume']['value'], 60)
        self.assertEqual(len(dialog.pending), 1)
        self.assertTrue(dialog.slider.isEnabled())
        dialog.debounce.stop()
        dialog.pending.clear()
        dialog.deleteLater()

    def test_stale_reply_does_not_move_slider_over_pending_user_change(self):
        from maintenance_ui import VolumeDialog
        dialog = VolumeDialog({'host': 'audio-pi.local', 'username': 'audio'})
        dialog.show_status(status())
        dialog.slider.setValue(90)
        dialog.show_status(status(volume=30))
        self.assertEqual(dialog.slider.value(), 90)
        self.assertEqual(dialog.pending['volume']['value'], 90)
        dialog.debounce.stop()
        dialog.pending.clear()
        dialog.deleteLater()


if __name__ == '__main__': unittest.main()
