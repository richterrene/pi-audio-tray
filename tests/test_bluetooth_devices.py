import copy
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import bluetooth_schema as schema
import bluetooth_devices
import pi_bluetooth

ADAPTER = '10:20:30:40:50:60'
MAC = 'AA:BB:CC:DD:EE:FF'
APATH = '/org/bluez/hci0'
DPATH = APATH + '/dev_AA_BB_CC_DD_EE_FF'


def snapshot():
    return {'protocol': 1, 'adapter': ADAPTER, 'powered': True, 'discovering': False,
        'devices': [{'address': MAC, 'name': 'Studio headphones', 'role': 'output', 'paired': True,
                     'connected': True, 'automatic': False, 'paused': False}],
        'outputs': [{'name': 'alsa_output.example', 'description': 'Speakers', 'default': True}], 'degraded': False}


class Protocol(unittest.TestCase):
    def test_rejects_commands_addresses_and_boolean_coercion(self):
        for value in ({'action': 'shell'}, {'action': 'status', 'address': MAC},
            {'action': 'connect', 'adapter': ADAPTER, 'address': MAC + ';id'},
            {'action': 'automatic', 'adapter': ADAPTER, 'address': MAC, 'enabled': 1},
            {'action': 'output', 'name': 'sink;id'}, {'action': 'output', 'name': 'pi_audio.dsp.input'}):
            with self.subTest(value=value), self.assertRaises(ValueError): schema.request(value)

    def test_response_rejects_malformed_duplicate_and_control_names(self):
        for edit in (lambda s: s.update(protocol=True), lambda s: s['devices'][0].update(connected=1),
                     lambda s: s['devices'].append(s['devices'][0]),
                     lambda s: s['devices'][0].update(name='<b>fake</b>\u202e')):
            value = snapshot(); edit(value)
            with self.assertRaises(ValueError): schema.status(value)
        self.assertEqual(schema.status(snapshot()), snapshot())

    def test_role_classification_and_display_sanitization(self):
        self.assertEqual(schema.role([schema.AUDIO_SOURCE]), 'source')
        self.assertEqual(schema.role([schema.AUDIO_OUTPUT]), 'output')
        self.assertEqual(schema.role([schema.AUDIO_OUTPUT, schema.AUDIO_SOURCE]), 'both')
        self.assertEqual(schema.device_name('Phone\n\u202e'), 'Phone')

    def test_ssh_uses_stdin_not_device_command_interpolation_and_always_closes(self):
        client = Mock()
        with patch.object(bluetooth_devices, 'connect', return_value=client), patch.object(bluetooth_devices, 'run', return_value=json.dumps(snapshot())) as run:
            value = {'action': 'disconnect', 'adapter': ADAPTER, 'address': MAC}
            bluetooth_devices.request({}, value)
            self.assertEqual(run.call_args.args[1], bluetooth_devices.HELPER + ' request')
            self.assertEqual(json.loads(run.call_args.kwargs['input_data']), value)
        client.close.assert_called_once()

    def test_manual_connection_survives_auto_off_until_disconnected(self):
        policy = schema.ConnectionPolicy()
        self.assertTrue(policy.blocked(MAC, False, False))
        policy.connect(MAC)
        self.assertFalse(policy.blocked(MAC, False, False))
        self.assertFalse(policy.blocked(MAC, False, True))
        self.assertTrue(policy.blocked(MAC, False, False))

    def test_disconnect_suspends_auto_until_explicit_connect_or_restart(self):
        policy = schema.ConnectionPolicy()
        policy.disconnect(MAC)
        self.assertTrue(policy.blocked(MAC, True, False))
        policy.connect(MAC)
        self.assertFalse(policy.blocked(MAC, True, False))
        self.assertFalse(schema.ConnectionPolicy().blocked(MAC, True, False))

    def test_disabling_automatic_keeps_current_connection(self):
        policy = schema.ConnectionPolicy()
        policy.preference(MAC, True)
        self.assertFalse(policy.blocked(MAC, False, True))
        self.assertTrue(policy.blocked(MAC, False, False))

    def test_installer_bundle_is_complete_and_no_new_runtime_sudo(self):
        for name in bluetooth_devices.PI_FILES:
            self.assertTrue((bluetooth_devices.provision.ROOT / 'pi' / name).is_file())
        for name in bluetooth_devices.TOP_FILES:
            self.assertTrue((bluetooth_devices.provision.ROOT / name).is_file())
        self.assertNotIn('sudo', Path(pi_bluetooth.__file__).read_text().split('import json')[1])
        unit = (bluetooth_devices.provision.ROOT / 'pi/pi-audio-bluetooth.service').read_text()
        self.assertIn('Type=dbus\nBusName=org.piaudiotray.Bluetooth1', unit)
        self.assertIn('PrivateUsers=yes', unit)
        self.assertIn('CapabilityBoundingSet=\n', unit)


@unittest.skipUnless(os.name == 'posix', 'Receiver settings require POSIX file permissions')
class Settings(unittest.TestCase):
    def test_roundtrip_private_and_link_rejection(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(pi_bluetooth, 'DIRECTORY', Path(folder) / '.config/pi-audio-tray'):
            value = {ADAPTER + '/' + MAC: False}
            pi_bluetooth.save_preferences(value)
            self.assertEqual(pi_bluetooth.preferences(), value)
            path = pi_bluetooth.DIRECTORY / 'bluetooth.json'
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.unlink(); path.symlink_to(Path(folder) / 'elsewhere')
            with self.assertRaises(OSError): pi_bluetooth.preferences()

    def test_rejects_invalid_or_non_private_preferences(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(pi_bluetooth, 'DIRECTORY', Path(folder)):
            path = Path(folder) / 'bluetooth.json'
            path.write_text('{"bad": true}')
            path.chmod(0o600)
            with self.assertRaises(ValueError): pi_bluetooth.preferences()
            path.write_text('{}'); path.chmod(0o644)
            with self.assertRaises(ValueError): pi_bluetooth.preferences()


class Manager(unittest.TestCase):
    def setUp(self):
        self.objects = {APATH: {pi_bluetooth.ADAPTER: {'Address': ADAPTER, 'Powered': True, 'Pairable': True, 'Discoverable': True}},
                        DPATH: {pi_bluetooth.DEVICE: {'Address': MAC, 'Alias': 'Headphones', 'Adapter': APATH, 'Paired': True,
                            'Connected': False, 'Trusted': True, 'Blocked': False, 'UUIDs': [schema.AUDIO_OUTPUT]}}}
        self.device = self.objects[DPATH][pi_bluetooth.DEVICE]
        self.controller = Mock()
        self.controller.Set.side_effect = lambda interface, key, value, **kw: self.device.update({key: value})
        self.controller.GetAll.side_effect = lambda *args, **kw: self.device
        self.controller.Connect.side_effect = lambda **kw: self.device.update(Connected=True)
        self.controller.Disconnect.side_effect = lambda **kw: self.device.update(Connected=False)
        with patch.object(pi_bluetooth, 'preferences', return_value={}):
            self.manager = pi_bluetooth.Manager(Mock(), Mock(), types.SimpleNamespace(Boolean=bool))
        self.manager.objects = lambda: self.objects
        self.manager.props = lambda path: self.controller
        self.manager.interface = lambda path, name: self.controller
        self.manager.outputs = lambda: []

    def action(self, action, **extra):
        return self.manager.operate({'action': action, 'adapter': ADAPTER, 'address': MAC, **extra})

    @patch.object(pi_bluetooth, 'save_preferences')
    def test_auto_off_manual_connect_disconnect_and_reconnect_suppression(self, save):
        self.action('automatic', enabled=False)
        self.assertTrue(self.device['Blocked'])
        self.action('connect')
        self.assertTrue(self.device['Connected'])
        self.assertFalse(self.device['Blocked'])
        self.action('disconnect')
        self.assertFalse(self.device['Connected'])
        self.assertTrue(self.device['Blocked'])
        self.manager.reconcile()
        self.assertFalse(self.device['Connected'])

    @patch.object(pi_bluetooth, 'save_preferences')
    def test_changed_adapter_or_missing_device_never_targets_another(self, save):
        self.objects[APATH][pi_bluetooth.ADAPTER]['Address'] = MAC
        with self.assertRaises(ValueError): self.action('disconnect')
        self.controller.Disconnect.assert_not_called()

    @patch.object(pi_bluetooth, 'save_preferences')
    def test_pair_requires_approved_window_and_audio_output(self, save):
        self.device['Paired'] = False
        self.objects[APATH][pi_bluetooth.ADAPTER]['Pairable'] = False
        with self.assertRaises(ValueError): self.action('pair')
        self.controller.Pair.assert_not_called()
        self.objects[APATH][pi_bluetooth.ADAPTER]['Pairable'] = True
        self.device['UUIDs'] = []
        with self.assertRaises(ValueError): self.action('pair')
        self.assertTrue(self.device['Blocked'])

    def test_short_disconnect_edge_ends_manual_lease(self):
        key = ADAPTER + '/' + MAC
        self.manager.saved[key] = False
        self.manager.policy.connect(key)
        self.device['Connected'] = True
        self.manager.reconcile()
        self.manager.schedule(pi_bluetooth.DEVICE, {'Connected': False}, [], path=DPATH)
        # Simulate a peer reconnecting before the queued inventory executes.
        self.manager.reconcile()
        self.assertTrue(self.device['Blocked'])

    def test_output_disappearance_never_moves_streams(self):
        self.manager.command = Mock()
        with self.assertRaises(ValueError): self.manager.set_output('missing_sink')
        self.manager.command.assert_not_called()

    def test_output_change_preserves_enabled_eq_curve_and_skips_filter_stream(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(pi_bluetooth, 'ROOT', Path(folder)):
            (Path(folder) / 'pi-dsp').touch()
            self.manager.outputs = lambda: [{'name': 'bluez_output.example'}]
            settings = {'enabled': True, 'target': 'alsa_output.example', 'gains': [1]*10, 'preamp': -2}
            def command(*args, data=None):
                if args[-1] == 'status': return json.dumps({'settings': settings})
                if args[-1] == 'sink-inputs': return json.dumps([
                    {'index': 1, 'properties': {'node.name': 'spotify'}},
                    {'index': 2, 'properties': {'node.name': 'pi_audio.dsp.output'}}])
                return '{}'
            self.manager.command = Mock(side_effect=command)
            self.manager.set_output('bluez_output.example')
            configure = [c for c in self.manager.command.call_args_list if c.args[-1] == 'configure'][0]
            self.assertEqual(json.loads(configure.kwargs['data']), {**settings, 'target': 'bluez_output.example'})
            moves = [c for c in self.manager.command.call_args_list if 'move-sink-input' in c.args]
            self.assertEqual(len(moves), 1)
            self.assertEqual(moves[0].args[-2:], ('1', 'bluez_output.example'))

    def test_bluez_failure_reports_degraded_and_retries(self):
        self.manager.objects = Mock(side_effect=RuntimeError('BlueZ restarting'))
        self.assertTrue(self.manager.reconcile())
        self.assertTrue(self.manager.degraded)
        self.manager.objects = lambda: self.objects
        self.manager.reconcile()
        self.assertFalse(self.manager.degraded)

class Gui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def wait(self, predicate):
        import time
        limit = time.monotonic() + 3
        while not predicate() and time.monotonic() < limit:
            self.app.processEvents(); time.sleep(.005)
        self.assertTrue(predicate())

    def test_selection_filter_status_and_explicit_disconnect(self):
        from bluetooth_ui import BluetoothDialog
        dialog = BluetoothDialog({'host': 'audio-pi.local', 'username': 'audio'})
        value = snapshot()
        value['devices'].append({**value['devices'][0], 'address': '11:22:33:44:55:66', 'name': 'Phone', 'role': 'source'})
        dialog.show_status(value)
        dialog.table.selectRow(0)
        self.assertTrue(dialog.disconnect_button.isEnabled())
        dialog.mode.setCurrentIndex(dialog.mode.findData('source'))
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertEqual(dialog.table.item(0, 0).text(), 'Phone')
        dialog.table.selectRow(0)
        with patch.object(bluetooth_devices, 'request', return_value=value) as request:
            dialog.disconnect_button.click()
            self.wait(lambda: dialog.worker is None)
        self.assertEqual(request.call_args.args[1]['address'], '11:22:33:44:55:66')
        self.assertEqual(request.call_args.args[1]['action'], 'disconnect')
        dialog.deleteLater()

    def test_worker_drains_then_close_button_and_x_work(self):
        import threading
        from bluetooth_ui import BluetoothDialog
        for mode in ('button', 'x'):
            entered, release = threading.Event(), threading.Event()
            def request(*args):
                entered.set(); release.wait(2); return snapshot()
            dialog = BluetoothDialog({'host': 'audio-pi.local', 'username': 'audio'})
            with patch.object(bluetooth_devices, 'request', side_effect=request):
                dialog.show(); dialog.refresh()
                self.wait(entered.is_set)
                self.assertTrue(dialog.table.isEnabled())
                if mode == 'button': dialog.close_button.click()
                else: dialog.close()
                self.assertTrue(dialog.close_after)
                release.set()
                self.wait(lambda: dialog.worker is None and not dialog.isVisible())
                self.assertFalse(dialog.timer.isActive())
            dialog.deleteLater()

    def test_close_rejects_child_pairing_confirmations_before_deletion(self):
        from PySide6.QtWidgets import QWidget, QDialog
        from bluetooth_ui import BluetoothDialog
        parent = QWidget()
        parent.session = object()
        parent.stop_pairing = Mock()
        dialog = BluetoothDialog({'host': 'audio-pi.local', 'username': 'audio'}, parent)
        box = QDialog(dialog)
        parent.dialogs = {'1': box}
        box.show(); dialog.show()
        dialog.close_button.click()
        self.assertFalse(box.isVisible())
        self.assertEqual(parent.dialogs, {})
        parent.stop_pairing.assert_called_once()
        dialog.deleteLater(); parent.deleteLater()

    def test_hidden_dialog_does_not_poll(self):
        from bluetooth_ui import BluetoothDialog
        dialog = BluetoothDialog({'host': 'audio-pi.local', 'username': 'audio'})
        dialog.show(); dialog.hide()
        with patch.object(bluetooth_devices, 'request') as request:
            dialog.refresh()
        request.assert_not_called()
        self.assertFalse(dialog.timer.isActive())
        dialog.deleteLater()

    def test_failed_read_does_not_leave_close_guard(self):
        from bluetooth_ui import BluetoothDialog
        dialog = BluetoothDialog({'host': 'audio-pi.local', 'username': 'audio'})
        with patch.object(bluetooth_devices, 'request', side_effect=RuntimeError('Fixture error')):
            dialog.show(); dialog.refresh()
            self.wait(lambda: dialog.worker is None)
        dialog.close_button.click()
        self.assertFalse(dialog.isVisible())
        dialog.deleteLater()
