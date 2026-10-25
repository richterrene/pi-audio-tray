#!/usr/bin/python3 -I
"""Pi-side JSON protocol; install root-owned before running with sudo."""
import json
import os
import signal
import sys
import subprocess
import time
import shutil
import stat
import tempfile
import math
import unicodedata
from pathlib import Path

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

ADAPTER = 'org.bluez.Adapter1'
DEVICE = 'org.bluez.Device1'
PROPS = 'org.freedesktop.DBus.Properties'
AGENT = 'org.bluez.Agent1'
SERVICES = ('raspotify', 'bluetooth', 'bluealsa', 'bluealsa-aplay')
AUDIO_SERVICES = {f'0000{value}-0000-1000-8000-00805f9b34fb'
                  for value in ('110a', '110b', '110c', '110d', '110e', '110f')}


def safe_name(value):
    return ''.join(c for c in str(value) if c.isprintable()
                   and unicodedata.category(c) != 'Cf')[:128]


def cpu_ticks():
    values = [int(v) for v in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
    return sum(values), values[3] + values[4]


def cpu_usage(cache_dir=None):
    """Average between polls; first poll/cache failures use a one-second sample."""
    def sample():
        total, idle = cpu_ticks()
        return {'total': total, 'idle': idle, 'time': time.monotonic(),
                'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}

    def percentage(before, after):
        total = after['total'] - before['total']
        idle = after['idle'] - before['idle']
        window = after['time'] - before['time']
        if total <= 0 or not 0 <= idle <= total or not 0 < window <= 3600:
            return None, None
        return round(100 * (1 - idle / total), 1), round(window, 2)

    before, current = None, sample()
    folder = cache_dir if cache_dir is not None else Path.home() / '.cache/pi-audio-tray'
    directory = None
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        if os.fstat(directory).st_uid != os.getuid():
            raise ValueError('Unsafe CPU sample cache')
        os.fchmod(directory, 0o700)
        try:
            fd = os.open('cpu.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 4096:
                    raise ValueError('Unsafe CPU sample file')
                stored = json.loads(stream.read(4097))
            if (isinstance(stored, dict) and stored.get('boot') == current['boot']
                    and all(type(stored.get(k)) is int and stored[k] >= 0 for k in ('total', 'idle'))
                    and type(stored.get('time')) in (int, float) and 0 <= stored['time'] <= 10**12
                    and math.isfinite(stored['time'])
                    and 1 <= current['time'] - stored['time'] <= 3600):
                before = stored
        except (OSError, ValueError, TypeError):
            pass
        if before is None or percentage(before, current)[0] is None:
            before = current
            time.sleep(1)
            current = sample()
        # Exclusive private temp creation and atomic replacement never follow
        # an existing cache-file link. Concurrent readers see a complete sample.
        fd, temp = tempfile.mkstemp(prefix='.cpu-', dir=folder)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(current, stream)
            os.replace(Path(temp).name, 'cpu.json', src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            try:
                os.unlink(Path(temp).name, dir_fd=directory)
            except FileNotFoundError:
                pass
    except (OSError, ValueError, TypeError):
        if before is None:
            before = current
            time.sleep(1)
            current = sample()
    finally:
        if directory is not None:
            os.close(directory)
    return percentage(before, current)


def collect_stats():
    result = {}
    try:
        result['cpu_percent'], result['cpu_window_seconds'] = cpu_usage()
    except (OSError, ValueError, IndexError):
        result['cpu_percent'] = None
    try:
        result['temperature_c'] = round(float(Path('/sys/class/thermal/thermal_zone0/temp').read_text()) / 1000, 1)
    except (OSError, ValueError):
        result['temperature_c'] = None
    try:
        result['load'] = list(os.getloadavg())
        result['cpu_count'] = os.cpu_count() or 1
    except OSError:
        pass
    try:
        memory = {}
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, value = line.split(':', 1)
            memory[key] = int(value.split()[0]) * 1024
        total = memory['MemTotal']
        result['memory_total'] = total
        result['memory_used'] = max(0, total - memory['MemAvailable'])
    except (OSError, ValueError, KeyError, IndexError):
        pass
    try:
        disk = shutil.disk_usage('/')
        result.update(disk_total=disk.total, disk_used=disk.used)
        result['uptime_seconds'] = int(float(Path('/proc/uptime').read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        pass
    return result


def close_pairing(props):
    # Attempt both switches even if the first D-Bus operation fails.
    for name in ('Pairable', 'Discoverable'):
        try:
            props.Set(ADAPTER, name, dbus.Boolean(False))
        except dbus.DBusException:
            pass
    failed = []
    for name in ('Pairable', 'Discoverable'):
        try:
            if props.Get(ADAPTER, name):
                failed.append(name)
        except dbus.DBusException:
            failed.append(name)
    if failed:
        raise RuntimeError('Could not confirm pairing is disabled: ' + ', '.join(failed))


def emit(**event):
    print(json.dumps(event), flush=True)


def objects(bus):
    return dbus.Interface(bus.get_object('org.bluez', '/'),
                          'org.freedesktop.DBus.ObjectManager').GetManagedObjects()


def adapter(bus):
    for path, interfaces in objects(bus).items():
        if ADAPTER in interfaces:
            return dbus.Interface(bus.get_object('org.bluez', path), PROPS)
    raise RuntimeError('No Bluetooth adapter found')


def status():
    bus = dbus.SystemBus()
    devices, adapters = [], []
    for path, interfaces in objects(bus).items():
        if ADAPTER in interfaces:
            p = interfaces[ADAPTER]
            adapters.append({k: bool(p.get(k, False)) for k in
                             ('Powered', 'Pairable', 'Discoverable')})
        if DEVICE in interfaces:
            p = interfaces[DEVICE]
            if p.get('Connected'):
                devices.append({'name': safe_name(p.get('Alias', 'Bluetooth device')),
                                'address': str(p.get('Address', ''))})
    result = subprocess.run(['systemctl', 'show', '--no-pager', '-p', 'Id',
                             '-p', 'ActiveState', *[s + '.service' for s in SERVICES]],
                            capture_output=True, text=True, timeout=8, check=True)
    services = {}
    for block in result.stdout.strip().split('\n\n'):
        values = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        services[values.get('Id', '').removesuffix('.service')] = values.get('ActiveState', 'unknown')
    user_result = subprocess.run(['systemctl', '--user', 'show', '--no-pager',
        '-p', 'Id', '-p', 'ActiveState', '-p', 'LoadState', 'soloist.service',
        'pipewire.service', 'wireplumber.service'], capture_output=True, text=True, timeout=8)
    backend = 'raspotify'
    for block in user_result.stdout.strip().split('\n\n'):
        values = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        name = values.get('Id', '').removesuffix('.service')
        if values.get('LoadState') == 'loaded':
            services[name] = values.get('ActiveState', 'unknown')
            if name == 'soloist':
                backend = 'soloist'
    soloist = {'key_configured': (Path.home() / '.config/soloist/api-key').exists()}
    if backend == 'soloist':
        import re
        version = subprocess.run(['/usr/local/bin/soloist', '--version'],
            capture_output=True, text=True, timeout=5).stdout.strip()
        soloist['version'] = version
        match = re.search(r'build (\d+)', version)
        if match:
            soloist['days_remaining'] = max(0, int((int(match[1]) + 90 * 86400 - time.time()) / 86400))
    timers = subprocess.run(['systemctl', 'is-active', 'soloist-update.timer',
        'apt-daily-upgrade.timer'], capture_output=True, text=True, timeout=5).stdout.splitlines()
    soloist['automatic_updates'] = len(timers) == 2 and all(s == 'active' for s in timers)
    update_result = subprocess.run(['systemctl', 'show', '--no-pager', '-p', 'Result',
        'soloist-update.service', 'apt-daily-upgrade.service'],
        capture_output=True, text=True, timeout=5)
    soloist['update_failed'] = any(line.startswith('Result=') and line != 'Result=success'
        for line in update_result.stdout.splitlines())
    emit(protocol=1, services=services, adapter=adapters[0] if adapters else {},
         devices=devices, spotify_backend=backend, soloist=soloist,
         stats=collect_stats(),
         uptime=int(float(open('/proc/uptime').read().split()[0])))


class Rejected(dbus.DBusException):
    _dbus_error_name = 'org.bluez.Error.Rejected'


class PairingAgent(dbus.service.Object):
    def __init__(self, bus, loop, props):
        super().__init__(bus, '/org/piaudiotray/agent')
        self.bus, self.loop, self.props = bus, loop, props
        self.bluez_owner = str(bus.get_name_owner('org.bluez'))
        self.closing = False
        self.pending = {}
        self.counter = 0
        self.approved = set()

    def check_sender(self, sender):
        # Unique names are assigned by the bus, never accepted from method arguments.
        if sender != self.bluez_owner:
            raise Rejected('Only the registered BlueZ daemon may call this agent')

    def cancel_pending(self):
        pending, self.pending = self.pending, {}
        for token, (_, fail, _) in pending.items():
            try:
                fail(Rejected('Pairing canceled'))
            except dbus.DBusException:
                pass
            try:
                emit(event='canceled', token=token)
            except OSError:
                pass  # SSH output may already be closed; still close the adapter.

    def stop(self):
        self.closing = True
        self.cancel_pending()
        self.loop.quit()

    def request(self, device, kind, ok, fail, code=None):
        if self.closing or len(self.pending) >= 8:
            fail(Rejected('Too many pending pairing requests'))
            return
        self.counter += 1
        token = str(self.counter)
        self.pending[token] = (ok, fail, str(device))
        p = dbus.Interface(self.bus.get_object('org.bluez', device), PROPS).GetAll(DEVICE)
        emit(event='confirm', token=token, kind=kind, code=code,
             name=safe_name(p.get('Alias', 'Bluetooth device')), address=str(p.get('Address', '')))

    @dbus.service.method(AGENT, in_signature='ou', out_signature='', async_callbacks=('ok', 'fail'), sender_keyword='sender')
    def RequestConfirmation(self, device, passkey, ok, fail, sender=None):
        self.check_sender(sender)
        self.request(device, 'passkey', ok, fail, f'{passkey:06d}')

    @dbus.service.method(AGENT, in_signature='o', out_signature='', async_callbacks=('ok', 'fail'), sender_keyword='sender')
    def RequestAuthorization(self, device, ok, fail, sender=None):
        self.check_sender(sender)
        self.request(device, 'authorize', ok, fail)

    @dbus.service.method(AGENT, in_signature='os', out_signature='', async_callbacks=('ok', 'fail'), sender_keyword='sender')
    def AuthorizeService(self, device, uuid, ok, fail, sender=None):
        self.check_sender(sender)
        if str(uuid).lower() not in AUDIO_SERVICES:
            fail(Rejected('Only Bluetooth audio services are permitted by this agent'))
            return
        p = dbus.Interface(self.bus.get_object('org.bluez', device), PROPS)
        if p.Get(DEVICE, 'Trusted') or str(device) in self.approved:
            ok()
        else:
            self.request(device, 'service', ok, fail)

    @dbus.service.method(AGENT, in_signature='', out_signature='', sender_keyword='sender')
    def Cancel(self, sender=None):
        self.check_sender(sender)
        self.cancel_pending()

    @dbus.service.method(AGENT, in_signature='', out_signature='', sender_keyword='sender')
    def Release(self, sender=None):
        self.check_sender(sender)
        self.stop()

    @dbus.service.method(AGENT, in_signature='o', out_signature='s', sender_keyword='sender')
    def RequestPinCode(self, device, sender=None):
        self.check_sender(sender)
        raise Rejected('Legacy PIN devices are not supported')

    @dbus.service.method(AGENT, in_signature='o', out_signature='u', sender_keyword='sender')
    def RequestPasskey(self, device, sender=None):
        self.check_sender(sender)
        raise Rejected('Passkey entry is not supported')

    @dbus.service.method(AGENT, in_signature='os', out_signature='', sender_keyword='sender')
    def DisplayPinCode(self, device, pincode, sender=None):
        self.check_sender(sender)
        emit(event='display', code=str(pincode))

    @dbus.service.method(AGENT, in_signature='ouq', out_signature='', sender_keyword='sender')
    def DisplayPasskey(self, device, passkey, entered, sender=None):
        self.check_sender(sender)
        emit(event='display', code=f'{passkey:06d}')

    def command(self, message):
        if not isinstance(message, dict):
            raise ValueError('Invalid pairing command')
        if message.get('action') == 'stop':
            self.stop()
        elif (message.get('action') in ('approve', 'reject')
              and isinstance(message.get('token'), str) and message['token'] in self.pending):
            ok, fail, device = self.pending.pop(message['token'])
            if message.get('action') == 'approve':
                if self.closing or not all(self.props.Get(ADAPTER, flag)
                                           for flag in ('Pairable', 'Discoverable')):
                    fail(Rejected('Pairing is no longer enabled'))
                    self.stop()
                    return
                self.approved.add(device)
                ok()
            else:
                fail(Rejected('Rejected by user'))


def pair():
    # A process lock also prevents two desktops from taking over one another.
    import fcntl
    fd = os.open('/run/pi-audio-tray-pairing.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    lock = os.fdopen(fd, 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    props = adapter(bus)
    loop = GLib.MainLoop()
    manager = dbus.Interface(bus.get_object('org.bluez', '/org/bluez'), 'org.bluez.AgentManager1')
    agent = PairingAgent(bus, loop, props)
    buffer = bytearray()

    def read_input(fd, condition):
        if condition & (GLib.IO_HUP | GLib.IO_ERR):
            loop.quit()
            return False
        data = os.read(fd, 8192)
        if not data:
            loop.quit()
            return False
        buffer.extend(data)
        if len(buffer) > 8192:
            emit(event='error', message='Pairing command exceeded its size limit')
            loop.quit()
            return False
        while b'\n' in buffer:
            line, _, tail = buffer.partition(b'\n')
            buffer[:] = tail
            try:
                agent.command(json.loads(line))
            except (ValueError, TypeError):
                emit(event='error', message='Invalid pairing command')
                loop.quit()
                return False
        return True

    old_timeout = props.Get(ADAPTER, 'DiscoverableTimeout')
    old_pair_timeout = props.Get(ADAPTER, 'PairableTimeout')
    try:
        manager.RegisterAgent('/org/piaudiotray/agent', 'DisplayYesNo')
        manager.RequestDefaultAgent('/org/piaudiotray/agent')
        props.Set(ADAPTER, 'Powered', dbus.Boolean(True))
        props.Set(ADAPTER, 'PairableTimeout', dbus.UInt32(180))
        props.Set(ADAPTER, 'DiscoverableTimeout', dbus.UInt32(180))
        props.Set(ADAPTER, 'Pairable', dbus.Boolean(True))
        props.Set(ADAPTER, 'Discoverable', dbus.Boolean(True))
        GLib.io_add_watch(sys.stdin.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, read_input)
        GLib.timeout_add_seconds(180, lambda: (loop.quit(), False)[1])
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (loop.quit(), False)[1])
        emit(event='ready', expires=int(time.time()) + 180)
        loop.run()
    finally:
        agent.closing = True
        agent.cancel_pending()
        close_error = None
        try:
            close_pairing(props)
        except RuntimeError as exc:
            close_error = exc
        # Trust only devices whose request this user approved and which completed pairing.
        for device in agent.approved if close_error is None else ():
            try:
                p = dbus.Interface(bus.get_object('org.bluez', device), PROPS)
                if p.Get(DEVICE, 'Paired'):
                    p.Set(DEVICE, 'Trusted', dbus.Boolean(True))
            except dbus.DBusException:
                pass
        if close_error is None:
            for name, value in (('DiscoverableTimeout', old_timeout), ('PairableTimeout', old_pair_timeout)):
                try:
                    props.Set(ADAPTER, name, value)
                except dbus.DBusException:
                    pass
        try:
            manager.UnregisterAgent('/org/piaudiotray/agent')
        except dbus.DBusException:
            pass
        if close_error:
            raise close_error
        emit(event='closed')


def main():
    os.environ['PATH'] = '/usr/sbin:/usr/bin:/sbin:/bin'
    action = sys.argv[1] if len(sys.argv) == 2 else ''
    if action == 'status':
        if os.geteuid() == 0:
            raise RuntimeError('Run status as the normal SSH user, not root')
        status()
    elif action == 'pair':
        if os.geteuid() != 0:
            raise RuntimeError('Pairing requires the privileged helper')
        pair()
    elif action == 'off':
        if os.geteuid() != 0:
            raise RuntimeError('Pairing requires the privileged helper')
        props = adapter(dbus.SystemBus())
        close_pairing(props)
        emit(event='closed')
    else:
        raise SystemExit('Usage: pi-helper status|pair|off')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        emit(event='error', message=str(exc))
        sys.exit(1)
