#!/usr/bin/python3 -I
"""Per-audio-account Bluetooth manager. No sudo, network listener or shell commands."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('bluetooth_schema', ROOT / 'bluetooth_schema.py')
schema = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schema)
NAME = 'org.piaudiotray.Bluetooth1'
PATH = '/org/piaudiotray/Bluetooth1'
DEVICE = 'org.bluez.Device1'
ADAPTER = 'org.bluez.Adapter1'
PROPS = 'org.freedesktop.DBus.Properties'
DIRECTORY = Path.home() / '.config/pi-audio-tray'


def preferences():
    path = DIRECTORY / 'bluetooth.json'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 16384:
            raise ValueError('Unsafe Bluetooth settings')
        value = json.loads(stream.read(16385))
    if not isinstance(value, dict) or len(value) > schema.MAX_DEVICES:
        raise ValueError('Invalid Bluetooth settings')
    for key, enabled in value.items():
        parts = key.split('/')
        if len(parts) != 2 or type(enabled) is not bool:
            raise ValueError('Invalid Bluetooth preference')
        for mac in parts: schema.address(mac)
    return value


def save_preferences(value):
    for path in (DIRECTORY.parent, DIRECTORY):
        if path.is_symlink(): raise ValueError('Linked Bluetooth settings directory')
        path.mkdir(mode=0o700, exist_ok=True)
        if path.stat().st_uid != os.getuid(): raise ValueError('Foreign Bluetooth settings directory')
    os.chmod(DIRECTORY, 0o700)
    fd, temp = tempfile.mkstemp(prefix='.bluetooth-', dir=DIRECTORY)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, DIRECTORY / 'bluetooth.json')
    finally:
        Path(temp).unlink(missing_ok=True)


class Manager:
    def __init__(self, bus, glib, dbus_module):
        self.bus, self.glib, self.dbus = bus, glib, dbus_module
        self.saved = preferences()
        self.policy = schema.ConnectionPolicy()
        self.scanning = None
        self.scan_timer = None
        self.connecting = set()
        self.retry = {}
        self.degraded = False
        self.reconciling = False
        self.refresh_pending = False
        self.device_keys = {}

    def objects(self):
        return self.dbus.Interface(self.bus.get_object('org.bluez', '/'),
            'org.freedesktop.DBus.ObjectManager').GetManagedObjects(timeout=8)

    def props(self, path):
        return self.dbus.Interface(self.bus.get_object('org.bluez', path), PROPS)

    def interface(self, path, name):
        return self.dbus.Interface(self.bus.get_object('org.bluez', path), name)

    def adapter(self, objects):
        adapters = sorted((str(p), i[ADAPTER]) for p, i in objects.items() if ADAPTER in i)
        if not adapters: raise ValueError('No Bluetooth adapter found')
        return adapters[0]

    def devices(self, objects, adapter):
        return [(str(p), i[DEVICE]) for p, i in objects.items() if DEVICE in i and str(i[DEVICE].get('Adapter')) == adapter]

    def status(self):
        objects = self.objects()
        path, props = self.adapter(objects)
        adapter = schema.address(str(props['Address']).upper())
        devices = []
        for _, item in self.devices(objects, path):
            mac = schema.address(str(item['Address']).upper())
            paired, connected = bool(item.get('Paired')), bool(item.get('Connected'))
            kind = schema.role(item.get('UUIDs', []))
            if kind == 'unknown' and not paired and not item.get('Name'): continue
            key = adapter + '/' + mac
            devices.append({'address': mac, 'name': schema.device_name(item.get('Alias', mac)), 'role': kind,
                'paired': paired, 'connected': connected,
                'automatic': self.saved.get(key, bool(item.get('Trusted')) and not bool(item.get('Blocked'))),
                'paused': key in self.policy.paused})
        devices.sort(key=lambda d: (not d['connected'], not d['paired'], d['name'].casefold(), d['address']))
        return schema.status({'protocol': 1, 'adapter': adapter, 'powered': bool(props.get('Powered')),
            'discovering': bool(props.get('Discovering')), 'devices': devices[:schema.MAX_DEVICES],
            'outputs': self.outputs(), 'degraded': self.degraded})

    def stop_scan(self):
        if self.scan_timer is not None:
            self.glib.source_remove(self.scan_timer)
            self.scan_timer = None
        path, self.scanning = self.scanning, None
        if path:
            try: self.interface(path, ADAPTER).StopDiscovery(timeout=5)
            except self.dbus.DBusException: pass
        return False

    def operate(self, value):
        schema.request(value)
        if value['action'] == 'status': return self.status()
        if value['action'] == 'output':
            self.set_output(value['name'])
            return self.status()
        objects = self.objects()
        apath, adapter = self.adapter(objects)
        if str(adapter['Address']).upper() != value['adapter']:
            raise ValueError('Bluetooth adapter changed; refresh')
        action = value['action']
        if action in ('scan', 'stop-scan'):
            if action == 'stop-scan': self.stop_scan()
            else:
                self.stop_scan()
                self.interface(apath, ADAPTER).StartDiscovery(timeout=8)
                self.scanning = apath
                self.scan_timer = self.glib.timeout_add_seconds(20, self.stop_scan)
            return self.status()
        matches = [(p, d) for p, d in self.devices(objects, apath) if str(d.get('Address')).upper() == value['address']]
        if len(matches) != 1: raise ValueError('Device disappeared; refresh')
        path, item = matches[0]
        key = value['adapter'] + '/' + value['address']
        props, device = self.props(path), self.interface(path, DEVICE)
        if key not in self.saved and len(self.saved) >= schema.MAX_DEVICES:
            raise ValueError('Too many managed Bluetooth devices')
        if action == 'pair':
            if not (adapter.get('Pairable') and adapter.get('Discoverable')):
                raise ValueError('Enable the approved pairing window first')
            if schema.role(item.get('UUIDs', [])) not in ('output', 'both', 'unknown'):
                raise ValueError('Pair this audio source from its own Bluetooth settings')
            if not item.get('Paired'): device.Pair(timeout=35)
            resolved = props.GetAll(DEVICE, timeout=5)
            if schema.role(resolved.get('UUIDs', [])) not in ('output', 'both'):
                props.Set(DEVICE, 'Blocked', self.dbus.Boolean(True), timeout=5)
                raise ValueError('The paired device does not advertise an audio output')
            props.Set(DEVICE, 'Trusted', self.dbus.Boolean(True), timeout=5)
            # New headphones require explicit Connect; pairing never changes output.
            if key not in self.saved and len(self.saved) >= schema.MAX_DEVICES:
                raise ValueError('Too many managed Bluetooth devices')
            self.saved.setdefault(key, False)
            save_preferences(self.saved)
        elif not item.get('Paired'):
            raise ValueError('Pair the device before managing it')
        elif action == 'automatic':
            if key not in self.saved and len(self.saved) >= schema.MAX_DEVICES:
                raise ValueError('Too many managed Bluetooth devices')
            previous = dict(self.saved)
            self.saved[key] = value['enabled']
            try: save_preferences(self.saved)
            except Exception:
                self.saved = previous
                raise
            self.policy.preference(key, bool(item.get('Connected')))
        elif action == 'disconnect':
            self.saved.setdefault(key, bool(item.get('Trusted')) and not bool(item.get('Blocked')))
            save_preferences(self.saved)
            self.policy.disconnect(key)
            props.Set(DEVICE, 'Blocked', self.dbus.Boolean(True), timeout=5)
            if item.get('Connected'): device.Disconnect(timeout=15)
        elif action == 'connect':
            self.saved.setdefault(key, bool(item.get('Trusted')) and not bool(item.get('Blocked')))
            save_preferences(self.saved)
            self.policy.connect(key)
            props.Set(DEVICE, 'Blocked', self.dbus.Boolean(False), timeout=5)
            props.Set(DEVICE, 'Trusted', self.dbus.Boolean(True), timeout=5)
            try: device.Connect(timeout=25)
            except Exception:
                self.policy.manual.discard(key)
                self.reconcile()
                raise
        self.reconcile()
        return self.status()

    def command(self, *args, data=None):
        result = subprocess.run(args, input=data, capture_output=True, text=True, timeout=20, check=True)
        if len(result.stdout) > 2 * 1024**2: raise ValueError('Audio response too large')
        return result.stdout

    def outputs(self):
        sinks = json.loads(self.command('/usr/bin/pactl', '-f', 'json', 'list', 'sinks'))
        default = self.command('/usr/bin/pactl', 'get-default-sink').strip()
        found = []
        for sink in sinks:
            name = sink.get('name', '')
            if (not schema.OUTPUT_NAME.fullmatch(name) or name.startswith('pi_audio.')
                    or sink.get('properties', {}).get('node.virtual') in (True, 'true')): continue
            found.append({'name': name, 'description': schema.device_name(sink.get('description', name)),
                          'default': name == default})
        if len(found) > 32: raise ValueError('Too many audio outputs')
        return found

    def set_output(self, name):
        if name not in {o['name'] for o in self.outputs()}:
            raise ValueError('Audio output disappeared; refresh')
        # Retarget an enabled managed smart EQ, keeping its curve/headroom.
        dsp = ROOT / 'pi-dsp'
        if dsp.is_file():
            state = json.loads(self.command(str(dsp), 'status'))
            settings = state.get('settings')
            if settings and settings.get('enabled'):
                self.command(str(dsp), 'configure', data=json.dumps({**settings, 'target': name}))
        self.command('/usr/bin/pactl', 'set-default-sink', name)
        inputs = json.loads(self.command('/usr/bin/pactl', '-f', 'json', 'list', 'sink-inputs'))
        failures = 0
        for stream in inputs:
            index = stream.get('index')
            if type(index) is not int or not 0 <= index < 2**32: continue
            # Filter-chain internals are managed by WirePlumber, not Pulse routing.
            props = stream.get('properties', {})
            if str(props.get('node.name', '')).startswith('pi_audio.'): continue
            try: self.command('/usr/bin/pactl', 'move-sink-input', str(index), name)
            except subprocess.CalledProcessError: failures += 1
        if failures: raise ValueError('Some streams could not move; refresh the output and restart playback')

    def schedule(self, interface, changed, invalidated, path=None):
        # Preserve short disconnect edges even if a peer reconnects before the
        # next inventory. A manual auto-off lease must end at that edge.
        key = self.device_keys.get(str(path))
        if interface == DEVICE and 'Connected' in changed and not bool(changed['Connected']) and key:
            self.policy.manual.discard(key)
            self.policy.seen.discard(key)
        if not self.refresh_pending:
            self.refresh_pending = True
            self.glib.idle_add(self.scheduled)

    def scheduled(self):
        self.refresh_pending = False
        self.reconcile()
        return False

    def reconcile(self):
        if self.reconciling: return True
        self.reconciling = True
        try:
            objects = self.objects()
            apath, adapter = self.adapter(objects)
            mac = str(adapter['Address']).upper()
            self.device_keys = {path: mac + '/' + str(item['Address']).upper()
                                for path, item in self.devices(objects, apath)}
            for path, item in self.devices(objects, apath):
                key = mac + '/' + str(item['Address']).upper()
                if key not in self.saved or not item.get('Paired'): continue
                connected = bool(item.get('Connected'))
                if connected: self.retry.pop(key, None)
                blocked = self.policy.blocked(key, self.saved[key], connected)
                if bool(item.get('Blocked')) != blocked:
                    self.props(path).Set(DEVICE, 'Blocked', self.dbus.Boolean(blocked), timeout=5)
                if blocked or connected or not adapter.get('Powered') or not self.saved[key] or key in self.connecting:
                    continue
                if time.monotonic() < self.retry.get(key, (0, 5))[0]: continue
                delay = self.retry.get(key, (0, 5))[1]
                self.retry[key] = (time.monotonic() + delay, min(300, delay * 2))
                self.props(path).Set(DEVICE, 'Trusted', self.dbus.Boolean(True), timeout=5)
                self.connecting.add(key)
                def done(*args, key=key): self.connecting.discard(key)
                self.interface(path, DEVICE).Connect(reply_handler=done, error_handler=done, timeout=20)
        except Exception:
            # BlueZ may be restarting. Report degraded policy and retry next tick.
            self.degraded = True
        else:
            self.degraded = False
        finally:
            self.reconciling = False
        return True


def daemon():
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    session = dbus.SessionBus()
    owner = dbus.service.BusName(NAME, session, do_not_queue=True)
    manager = Manager(bus, GLib, dbus)
    class Service(dbus.service.Object):
        @dbus.service.method(NAME, in_signature='s', out_signature='s')
        def Request(self, raw):
            if len(raw.encode('utf-8')) > 4096: raise ValueError('Request too large')
            return json.dumps(manager.operate(json.loads(raw)))
    service = Service(owner, PATH)
    bus.add_signal_receiver(manager.schedule, signal_name='PropertiesChanged',
        dbus_interface=PROPS, bus_name='org.bluez', path_keyword='path')
    GLib.timeout_add_seconds(15, manager.reconcile)
    manager.reconcile()
    GLib.MainLoop().run()


def main():
    if os.geteuid() == 0: raise ValueError('Run Bluetooth controls as the audio user')
    if len(sys.argv) != 2: raise ValueError('One Bluetooth mode is required')
    if sys.argv[1] == 'daemon': daemon()
    elif sys.argv[1] == 'request':
        os.environ['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=/run/user/' + str(os.getuid()) + '/bus'
        import dbus
        raw = sys.stdin.buffer.read(4097)
        if len(raw) > 4096: raise ValueError('Request too large')
        schema.request(json.loads(raw))
        result = dbus.Interface(dbus.SessionBus().get_object(NAME, PATH), NAME).Request(raw.decode(), timeout=48)
        print(json.dumps(schema.status(json.loads(result))))
    else: raise ValueError('Unsupported Bluetooth mode')


if __name__ == '__main__':
    try: main()
    except Exception:
        print('Bluetooth operation failed. Check the Bluetooth manager service, pairing window and device; refresh before retrying.', file=sys.stderr)
        sys.exit(1)
