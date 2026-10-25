import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


class DBusError(Exception): pass


class DBusObject:
    def __init__(self, *args): pass


def load_helper():
    dbus = types.ModuleType('dbus')
    service = types.ModuleType('dbus.service')
    service.Object = DBusObject
    service.method = lambda *args, **kwargs: lambda func: func
    dbus.service = service
    dbus.DBusException = DBusError
    dbus.Boolean, dbus.UInt32 = bool, int
    dbus.Interface = Mock()
    glib = types.ModuleType('dbus.mainloop.glib')
    glib.DBusGMainLoop = Mock()
    gi = types.ModuleType('gi')
    repository = types.ModuleType('gi.repository')
    repository.GLib = Mock()
    modules = {'dbus': dbus, 'dbus.service': service, 'dbus.mainloop': types.ModuleType('dbus.mainloop'),
               'dbus.mainloop.glib': glib, 'gi': gi, 'gi.repository': repository}
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location('testable_pi_helper', Path(__file__).resolve().parents[1] / 'pi_helper.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


helper = load_helper()


class PairingSafety(unittest.TestCase):
    def agent(self):
        return helper.PairingAgent(Mock(), Mock(), Mock())

    def test_non_audio_service_is_rejected_even_for_a_trusted_device(self):
        agent, ok, fail = self.agent(), Mock(), Mock()
        agent.AuthorizeService('/device', '00001124-0000-1000-8000-00805f9b34fb', ok, fail)
        ok.assert_not_called()
        fail.assert_called_once()

    def test_pending_request_limit_prevents_dialog_flood(self):
        agent, fail = self.agent(), Mock()
        agent.pending = {str(i): None for i in range(8)}
        with patch.object(helper, 'emit') as emit:
            agent.request('/device', 'authorize', Mock(), fail)
        emit.assert_not_called()
        fail.assert_called_once()

    def test_both_pairing_switches_are_attempted_after_an_error(self):
        props = Mock()
        values = {'Pairable': True, 'Discoverable': True}
        def set_value(interface, name, value):
            if name == 'Pairable': raise DBusError('unavailable')
            values[name] = value
        props.Set.side_effect = set_value
        props.Get.side_effect = lambda interface, name: values[name]
        with self.assertRaises(RuntimeError): helper.close_pairing(props)
        self.assertFalse(values['Discoverable'])
        self.assertEqual(props.Set.call_count, 2)

    def test_pairing_is_confirmed_closed_only_after_both_flags_are_false(self):
        props = Mock()
        props.Get.return_value = False
        helper.close_pairing(props)
        self.assertEqual(props.Get.call_count, 2)

    def test_invalid_command_shape_is_rejected(self):
        with self.assertRaises(ValueError): self.agent().command([])

    def test_root_status_cannot_execute_the_downloaded_client(self):
        with patch.object(helper.sys, 'argv', ['helper', 'status']), patch.object(helper.os, 'geteuid', return_value=0), \
             patch.object(helper, 'status') as status:
            with self.assertRaises(RuntimeError): helper.main()
            status.assert_not_called()


class PiStats(unittest.TestCase):
    def test_cpu_temperature_memory_disk_and_uptime_are_collected(self):
        ticks = iter(['cpu 100 0 20 80 0 0 0 0\n', 'cpu 120 0 30 90 0 0 0 0\n'])
        def read(path, *args, **kwargs):
            name = str(path)
            if name == '/proc/stat': return next(ticks)
            if name.endswith('/temp'): return '43000'
            if name == '/proc/meminfo': return 'MemTotal: 1000 kB\nMemAvailable: 250 kB\n'
            if name == '/proc/uptime': return '90061.5 0.0'
            raise OSError('not available')
        with patch.object(helper.Path, 'read_text', read), patch.object(helper.time, 'sleep'), \
             patch.object(helper.os, 'getloadavg', return_value=(0.5, 0.3, 0.2)), \
             patch.object(helper.os, 'cpu_count', return_value=4), \
             patch.object(helper.shutil, 'disk_usage', return_value=types.SimpleNamespace(total=100000, used=25000)):
            stats = helper.collect_stats()
        self.assertEqual(stats['cpu_percent'], 75)
        self.assertEqual(stats['temperature_c'], 43)
        self.assertEqual(stats['memory_used'], 750 * 1024)
        self.assertEqual(stats['disk_used'], 25000)
        self.assertEqual(stats['uptime_seconds'], 90061)

    def test_missing_sensors_do_not_abort_status_collection(self):
        with patch.object(helper.Path, 'read_text', side_effect=OSError), \
             patch.object(helper.os, 'getloadavg', side_effect=OSError), \
             patch.object(helper.shutil, 'disk_usage', side_effect=OSError):
            self.assertEqual(helper.collect_stats(), {'cpu_percent': None, 'temperature_c': None})


if __name__ == '__main__': unittest.main()
