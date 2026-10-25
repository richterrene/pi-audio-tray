import importlib.util
import os
from pathlib import Path
import sys
import types
import tempfile
import json
import unittest
from unittest.mock import Mock, patch


class DBusError(Exception): pass


class DBusObject:
    def __init__(self, *args): pass


def load_helper():
    dbus = types.ModuleType('dbus')
    service = types.ModuleType('dbus.service')
    service.Object = DBusObject
    def method(*args, **kwargs):
        def decorate(func):
            func.test_sender_keyword = kwargs.get('sender_keyword')
            return func
        return decorate
    service.method = method
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
        bus = Mock()
        bus.get_name_owner.return_value = ':1.42'
        return helper.PairingAgent(bus, Mock(), Mock())

    def test_non_audio_service_is_rejected_even_for_a_trusted_device(self):
        agent, ok, fail = self.agent(), Mock(), Mock()
        agent.AuthorizeService('/device', '00001124-0000-1000-8000-00805f9b34fb', ok, fail, sender=':1.42')
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


    def test_all_agent_methods_require_the_bus_provided_bluez_sender(self):
        for name, arguments in (
                ('RequestConfirmation', ('/device', 123456, Mock(), Mock())),
                ('RequestAuthorization', ('/device', Mock(), Mock())),
                ('AuthorizeService', ('/device', next(iter(helper.AUDIO_SERVICES)), Mock(), Mock())),
                ('Cancel', ()), ('Release', ()), ('RequestPinCode', ('/device',)),
                ('RequestPasskey', ('/device',)), ('DisplayPinCode', ('/device', '1234')),
                ('DisplayPasskey', ('/device', 123456, 0))):
            agent = self.agent()
            callback = getattr(agent, name)
            self.assertEqual(callback.test_sender_keyword, 'sender')
            with patch.object(helper, 'emit') as emit:
                with self.assertRaises(helper.Rejected): callback(*arguments, sender=':1.99')
                with self.assertRaises(helper.Rejected): callback(*arguments)
                emit.assert_not_called()
            agent.loop.quit.assert_not_called()
            self.assertFalse(agent.pending)

    def test_bluez_can_cancel_and_release_its_agent(self):
        agent, fail = self.agent(), Mock()
        agent.pending = {'1': (Mock(), fail, '/device')}
        with patch.object(helper, 'emit'):
            agent.Cancel(sender=':1.42')
            agent.Release(sender=':1.42')
        fail.assert_called_once()
        self.assertFalse(agent.pending)
        self.assertTrue(agent.closing)
        agent.loop.quit.assert_called_once()

    def test_approval_after_pairing_off_is_rejected(self):
        agent, ok, fail = self.agent(), Mock(), Mock()
        agent.pending = {'1': (ok, fail, '/device')}
        agent.props.Get.return_value = False
        agent.command({'action': 'approve', 'token': '1'})
        ok.assert_not_called()
        fail.assert_called_once()
        self.assertFalse(agent.approved)
        agent.loop.quit.assert_called_once()

    def test_stop_cancels_requests_and_late_approval_cannot_succeed(self):
        agent, ok, fail = self.agent(), Mock(), Mock()
        agent.pending = {'1': (ok, fail, '/device')}
        with patch.object(helper, 'emit', side_effect=BrokenPipeError):
            agent.command({'action': 'stop'})
        agent.command({'action': 'approve', 'token': '1'})
        ok.assert_not_called()
        fail.assert_called_once()
        agent.loop.quit.assert_called_once()

    @unittest.skipIf(os.name == 'nt', 'Pi pairing lock is Linux-only; tested on Linux')
    def test_failed_pairing_closure_does_not_trust_approved_devices(self):
        bus, props, manager, loop = Mock(), Mock(), Mock(), Mock()
        bus.get_name_owner.return_value = ':1.42'
        def interface(obj, name):
            return manager if name == 'org.bluez.AgentManager1' else Mock()
        agent = self.agent()
        agent.approved = {'/approved-device'}
        with patch.object(helper.os, 'open', return_value=99), \
             patch.object(helper.os, 'fdopen', return_value=Mock()), \
             patch('fcntl.flock'), patch.object(helper.dbus, 'SystemBus', create=True, return_value=bus), \
             patch.object(helper, 'adapter', return_value=props), \
             patch.object(helper.GLib, 'MainLoop', return_value=loop), \
             patch.object(helper.dbus, 'Interface', side_effect=interface) as make_interface, \
             patch.object(helper, 'PairingAgent', return_value=agent), \
             patch.object(helper, 'close_pairing', side_effect=RuntimeError('not closed')), \
             patch.object(helper.signal, 'SIGTERM'), patch.object(helper, 'emit'):
            with self.assertRaisesRegex(RuntimeError, 'not closed'): helper.pair()
        self.assertFalse(any(call.args[1] == helper.PROPS for call in make_interface.call_args_list))

    def test_root_status_cannot_execute_the_downloaded_client(self):
        with patch.object(helper.sys, 'argv', ['helper', 'status']), patch.object(helper.os, 'geteuid', return_value=0, create=True), \
             patch.object(helper, 'status') as status:
            with self.assertRaises(RuntimeError): helper.main()
            status.assert_not_called()


class LightweightHealth(unittest.TestCase):
    def test_background_health_never_collects_stats_or_runs_soloist_version(self):
        def command(args, **kwargs):
            if args[:3] == ['systemctl', 'show', '--no-pager']:
                return types.SimpleNamespace(stdout='Id=bluetooth.service\nActiveState=active\n')
            if '--user' in args:
                return types.SimpleNamespace(stdout='Id=soloist.service\nActiveState=active\nLoadState=loaded\n')
            if 'is-active' in args:
                return types.SimpleNamespace(stdout='active\nactive\n')
            if 'show' in args:
                return types.SimpleNamespace(stdout='Result=success\nResult=success\n')
            raise AssertionError('Unexpected command during lightweight health check')
        with patch.object(helper.dbus, 'SystemBus', create=True), \
             patch.object(helper, 'objects', return_value={}), \
             patch.object(helper.subprocess, 'run', side_effect=command) as run, \
             patch.object(helper, 'collect_stats') as stats, \
             patch.object(helper.Path, 'home') as home, patch.object(helper, 'emit') as emit:
            helper.status(detailed=False)
        stats.assert_not_called()
        home.assert_not_called()
        self.assertFalse(any(call.args[0][0].endswith('soloist') for call in run.call_args_list))
        self.assertEqual(emit.call_args.kwargs['stats'], {})
        self.assertEqual(emit.call_args.kwargs['devices'], [])
        self.assertEqual(emit.call_args.kwargs['spotify_backend'], 'soloist')


@unittest.skipIf(os.name == 'nt', 'Linux /proc sensors are validated on Linux')
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
        with patch.object(helper.Path, 'read_text', read), patch.object(helper, 'cpu_usage', return_value=(75, 1)), \
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



@unittest.skipIf(os.name == 'nt', 'Pi CPU cache uses Linux file APIs; tested on Linux')
class CpuSampling(unittest.TestCase):
    def test_average_between_refreshes_includes_work_outside_the_old_short_window(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(helper, 'cpu_ticks', side_effect=[(200, 80), (400, 230), (800, 530)]), \
             patch.object(helper.Path, 'read_text', return_value='test-boot'), \
             patch.object(helper.time, 'monotonic', side_effect=[0, 1, 11]), \
             patch.object(helper.time, 'sleep') as sleep:
            cache = Path(temp) / 'stats'
            self.assertEqual(helper.cpu_usage(cache), (25.0, 1))
            self.assertEqual(helper.cpu_usage(cache), (25.0, 10))
            sleep.assert_called_once_with(1)
            self.assertEqual((cache / 'cpu.json').stat().st_mode & 0o777, 0o600)

    def test_reboot_or_counter_reset_replaces_the_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp)
            (cache / 'cpu.json').write_text(json.dumps({'boot': 'old-boot', 'total': 9000, 'idle': 8000, 'time': 0}))
            with patch.object(helper, 'cpu_ticks', side_effect=[(100, 50), (500, 350)]), \
                 patch.object(helper.Path, 'read_text', return_value='new-boot'), \
                 patch.object(helper.time, 'monotonic', side_effect=[10, 11]), \
                 patch.object(helper.time, 'sleep'):
                self.assertEqual(helper.cpu_usage(cache), (25.0, 1))

    def test_cache_symlink_cannot_overwrite_an_unrelated_file(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / 'stats'
            cache.mkdir()
            victim = Path(temp) / 'victim'
            victim.write_text('preserve')
            (cache / 'cpu.json').symlink_to(victim)
            with patch.object(helper, 'cpu_ticks', side_effect=[(100, 50), (500, 450)]), \
                 patch.object(helper.Path, 'read_text', return_value='test-boot'), \
                 patch.object(helper.time, 'monotonic', side_effect=[0, 1]), \
                 patch.object(helper.time, 'sleep'):
                self.assertEqual(helper.cpu_usage(cache), (0.0, 1))
            self.assertEqual(victim.read_text(), 'preserve')
            self.assertFalse((cache / 'cpu.json').is_symlink())

if __name__ == '__main__': unittest.main()
