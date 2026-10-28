#!/usr/bin/python3 -I
"""Optional event-driven one-source policy, as the unprivileged audio account."""
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

CONFIG = Path.home() / '.config/pi-audio-tray/source-policy.json'
STATE = Path('/run/user') / str(getattr(os, 'getuid', lambda: 0)()) / 'pi-audio-policy.json'
UNIT = 'pi-audio-policy.service'


def read_private(path, default):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return default
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 16384:
            raise ValueError('Invalid private policy file')
        return json.loads(stream.read(16385))


def settings():
    value = read_private(CONFIG, {'exclusive': False})
    if not isinstance(value, dict) or set(value) != {'exclusive'} or type(value['exclusive']) is not bool:
        raise ValueError('Invalid source policy')
    return value


def write_private(path, value):
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink():
            raise ValueError('Symbolic-link policy paths are unsupported')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.stat().st_uid != os.getuid():
        raise ValueError('Policy directory must belong to the audio account')
    os.chmod(path.parent, 0o700)
    fd, temp = tempfile.mkstemp(prefix='.policy-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def run(*args, timeout=4):
    result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False)
    return result.returncode == 0


def stream_mute(identifier, muted):
    if type(identifier) is not int or not 0 < identifier < 2**31 or type(muted) is not bool:
        raise ValueError('Invalid stream mute')
    # WirePlumber remembers ordinary mute as the user's player preference.
    # softMute is separate and is not stored by its stream-restore policy.
    return run('/usr/bin/pw-cli', 'set-param', str(identifier), 'Props',
               json.dumps({'softMute': muted}, separators=(',', ':')))


def nodes(objects):
    result = {}
    for item in objects:
        if not isinstance(item, dict) or item.get('type') != 'PipeWire:Interface:Node': continue
        info = item.get('info') or {}
        if not isinstance(info, dict): continue
        props = info.get('props') or {}
        if not isinstance(props, dict): continue
        identifier, serial = item.get('id'), props.get('object.serial')
        if type(identifier) is not int or not 0 < identifier < 2**31 or type(serial) is not int or not 0 < serial < 2**63:
            continue
        source = ''
        name, app = props.get('node.name', ''), props.get('application.name', '')
        if not isinstance(name, str) or not isinstance(app, str): continue
        if props.get('media.class') == 'Stream/Output/Audio':
            if app == 'Spotify' or name == 'spotify': source = 'spotify'
            elif app == 'Music Player Daemon' or name.startswith('mpd.'): source = 'mpd'
            elif app in ('Pi Audio AirPlay', 'Shairport Sync'): source = 'airplay'
        match = re.match(r'^bluez_(?:input|playback)\.([A-Fa-f0-9_]{17})(?:\.|$)', name)
        if match:
            source = 'bluetooth:' + match.group(1).upper()
        if not source:
            continue
        values = (info.get('params') or {}).get('Props', [])
        muted = any(value.get('mute') is True or value.get('softMute') is True for value in values)
        result[serial] = {'id': identifier, 'serial': serial, 'source': source,
                          'running': info.get('state') == 'running', 'muted': muted}
        if len(result) > 128: raise ValueError('Too many managed audio streams')
    return result


def pause(source):
    try:
        if source == 'mpd':
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(2)
                sock.connect('/run/pi-audio-mpd/socket')
                if not sock.recv(4096).startswith(b'OK MPD '): return False
                sock.sendall(b'pause 1\n')
                return sock.recv(4096).startswith(b'OK')
        if source == 'spotify':
            endpoint = spotify_endpoint()
            return bool(endpoint) and run('/usr/local/bin/soloist', 'ctl', 'pause', '--ws', endpoint)
        import dbus
        if source.startswith('bluetooth:'):
            bus = dbus.SystemBus()
            owner = bus.get_name_owner('org.bluez')
            objects = dbus.Interface(bus.get_object(owner, '/'), 'org.freedesktop.DBus.ObjectManager').GetManagedObjects(timeout=2)
            if len(objects) > 512: return False
            target = '/dev_' + source.split(':', 1)[1] + '/'
            for path, interfaces in objects.items():
                if target in str(path) and re.fullmatch(r'/org/bluez/hci\d+/dev_(?:[A-F0-9]{2}_){5}[A-F0-9]{2}/player\d+', str(path)) and 'org.bluez.MediaPlayer1' in interfaces:
                    dbus.Interface(bus.get_object(owner, path), 'org.bluez.MediaPlayer1').Pause(timeout=2)
                    return True
        elif source == 'airplay':
            bus = dbus.SessionBus()
            for name in bus.list_names():
                if name.startswith('org.mpris.MediaPlayer2.ShairportSync'):
                    owner = bus.get_name_owner(name)
                    dbus.Interface(bus.get_object(owner, '/org/mpris/MediaPlayer2'), 'org.mpris.MediaPlayer2.Player').Pause(timeout=2)
                    return True
    except Exception:
        pass  # Unsupported sender controls fall back to the already-applied mute.
    return False


class RoutingGuard:
    """Distinguish our filter rewiring from a listener starting another source."""
    def __init__(self):
        self.filters = None
        self.until = 0

    def active(self, objects):
        now = time.monotonic()
        filters = []
        for item in objects:
            if not isinstance(item, dict) or item.get('type') != 'PipeWire:Interface:Node':
                continue
            info = item.get('info') or {}
            props = info.get('props') if isinstance(info, dict) else None
            if not isinstance(props, dict): continue
            name, serial = props.get('node.name'), props.get('object.serial')
            if name in ('pi_audio.dsp.input', 'pi_audio.dsp.output') and type(serial) is int and 0 < serial < 2**63:
                filters.append((name, serial))
        filters = tuple(sorted(filters))
        if self.filters is not None and filters != self.filters:
            self.until = now + 2
        self.filters = filters
        # Configure/bypass already hold this private normal-user lock. The graph
        # fingerprint also covers service restart/crash outside those commands.
        path = CONFIG.with_name('dsp.lock')
        try:
            import fcntl
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as lock:
                info = os.fstat(lock.fileno())
                if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        self.until = now + 2
        except OSError:
            pass  # Missing/unsafe lock never grants access or disables policy.
        return now < self.until


class Arbiter:
    def __init__(self, mute, stop):
        self.mute, self.stop = mute, stop
        self.previous, self.owned = {}, {}
        self.winner = None
        self.initialized = False
        self.degraded = False
        self.unmuting = set()

    def update(self, current, preferred=None, routing=False):
        self.owned = {serial: value for serial, value in self.owned.items() if serial in current}
        self.unmuting.intersection_update(current)
        fresh = [node for serial, node in current.items() if node['running'] and
                 (not node['muted'] or serial in self.owned) and
                 (not self.previous.get(serial, {}).get('running') or
                  (self.previous.get(serial, {}).get('muted') and not node['muted'] and serial not in self.owned and serial not in self.unmuting))]
        self.unmuting = {serial for serial in self.unmuting if current[serial]['muted']}
        if routing:
            fresh = []  # Graph resume is not a fresh play command during EQ rewiring.
        if fresh:
            self.winner = max(fresh, key=lambda node: node['serial'])['source']
        elif not self.initialized:
            active = [node for node in current.values() if node['running'] and not node['muted']]
            if active: self.winner = max(active, key=lambda node: node['serial'])['source']
        if preferred and any(node['running'] and node['source'] == preferred for node in current.values()):
            self.winner = preferred
        self.initialized = True
        losers = set()
        self.degraded = False
        for serial, node in current.items():
            if node['source'] == self.winner:
                if serial in self.owned:
                    if self.mute(node['id'], False):
                        self.owned.pop(serial)
                        self.unmuting.add(serial)
                    else: self.degraded = True
            elif node['running']:
                if not node['muted']:
                    if self.mute(node['id'], True): self.owned[serial] = node['id']
                    else: self.degraded = True
                losers.add(node['source'])
        if fresh or preferred:
            for source in losers: self.stop(source)
        self.previous = {serial: node.copy() for serial, node in current.items()}
        return {'muted': [{'serial': serial, 'id': identifier} for serial, identifier in sorted(self.owned.items())],
                'mute_control': 'soft', 'winner': self.winner or '', 'playing': any(node['running'] and node['source'] == self.winner for node in current.values()), 'degraded': self.degraded}


def graph_snapshot():
    value = subprocess.run(['/usr/bin/pw-dump'], capture_output=True, check=True, timeout=10)
    if len(value.stdout) > 4 * 1024**2: raise ValueError('Audio graph exceeds size limit')
    objects = json.loads(value.stdout)
    cookie = next((item.get('info', {}).get('cookie') for item in objects if item.get('type') == 'PipeWire:Interface:Core' and isinstance(item.get('info'), dict)), None)
    return cookie, nodes(objects)


def snapshot():
    return graph_snapshot()[1]


def spotify_endpoint():
    try:
        values = []
        for name in ('ws.addr', 'ws.port'):
            fd = os.open(Path.home() / '.local/state/soloist' / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 64:
                    return ''
                values.append(stream.read(65).decode().strip())
        address, port = values
        if address == '127.0.0.1' and port.isascii() and port.isdecimal() and 1 <= int(port) <= 65535:
            return '127.0.0.1:' + port
    except (OSError, UnicodeError):
        pass
    return ''


class SpotifyTrace:
    """Observe play/pause even if Spotify keeps its PipeWire node running."""
    def __init__(self):
        self.process = None
        self.retry = 0
        self.buffer = b''
        self.discarding = False
        self.active, self.playing = None, None

    def attach(self):
        if self.process and self.process.poll() is not None:
            self.close()
            self.retry = time.monotonic() + 5
        if not self.process and time.monotonic() >= self.retry:
            self.retry = time.monotonic() + 5
            endpoint = spotify_endpoint()
            if endpoint:
                self.process = subprocess.Popen(['/usr/local/bin/soloist', 'ctl', 'trace', '--ws', endpoint], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def close(self):
        if self.process:
            self.process.terminate()
            try: self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait(timeout=2)
            self.process.stdout.close()
        self.process = None
        self.buffer = b''
        self.discarding = False

    def feed(self, data):
        self.buffer += data
        values = []
        while b'\n' in self.buffer:
            line, _, self.buffer = self.buffer.partition(b'\n')
            if self.discarding or len(line) > 65536:
                self.discarding = False
                continue
            try:
                message = json.loads(line.split(b' ', 1)[1] if line[:1].isdigit() else line)
                kind = message.get('type')
                if kind in ('auth_state', 'device_changed', 'playback_state') and type(message.get('is_active')) is bool:
                    self.active = message['is_active']
                if kind in ('playback_state', 'playback_changed') and message.get('status') in ('playing', 'paused', 'idle', 'buffering'):
                    self.playing = message['status'] == 'playing'
                if self.active is False: values.append(False)
                elif self.playing is not None: values.append(self.playing)
            except (ValueError, IndexError, AttributeError):
                continue
        if len(self.buffer) > 65536:
            self.buffer = b''
            self.discarding = True
        return values


class BluetoothEvents:
    """Use BlueZ playback/transport events when a paused node remains running."""
    def __init__(self):
        self.context, self.bus, self.match = None, None, None
        self.owner, self.retry = '', 0
        self.events = []

    def changed(self, interface, changed, invalidated, path=None):
        match = re.fullmatch(r'/org/bluez/hci\d+/dev_((?:[A-F0-9]{2}_){5}[A-F0-9]{2})(?:/[^\s]{0,200})?', str(path))
        if not match: return
        state = None
        if interface == 'org.bluez.MediaPlayer1' and str(changed.get('Status', '')) in ('playing', 'paused', 'stopped'):
            state = str(changed['Status']) == 'playing'
        elif interface == 'org.bluez.MediaTransport1' and str(changed.get('State', '')) in ('active', 'idle'):
            state = str(changed['State']) == 'active'
        if state is not None and len(self.events) < 128:
            self.events.append(('bluetooth:' + match.group(1), state))

    def pump(self):
        if time.monotonic() >= self.retry:
            self.retry = time.monotonic() + 5
            try:
                import dbus
                from dbus.mainloop.glib import DBusGMainLoop
                from gi.repository import GLib
                if self.bus is None:
                    DBusGMainLoop(set_as_default=True)
                    self.context = GLib.MainContext.default()
                    self.bus = dbus.SystemBus()
                owner = self.bus.get_name_owner('org.bluez')
                if owner != self.owner:
                    if self.match: self.match.remove()
                    self.owner = owner
                    self.match = self.bus.add_signal_receiver(self.changed, signal_name='PropertiesChanged',
                        dbus_interface='org.freedesktop.DBus.Properties', bus_name=owner, path_keyword='path')
            except Exception:
                pass  # Graph events/muting remain available without BlueZ media control.
        if self.context:
            for _ in range(16):
                if not self.context.pending(): break
                self.context.iteration(False)
        events, self.events = self.events, []
        return events


def restore():
    saved = read_private(STATE, {})
    if not saved.get('muted'):
        STATE.unlink(missing_ok=True)
        return True
    if saved.get('mute_control') not in (None, 'soft'):
        raise ValueError('Unsupported policy mute control')
    cookie, current = graph_snapshot()
    if saved.get('cookie') is None or saved.get('cookie') != cookie:
        STATE.unlink(missing_ok=True)
        return True  # PipeWire restarted; old serial/id pairs no longer establish ownership.
    remaining = []
    for item in saved.get('muted', [])[:256]:
        if isinstance(item, dict) and type(item.get('serial')) is int and type(item.get('id')) is int:
            node = current.get(item['serial'])
            if node and node['id'] == item['id']:
                # Retain recovery compatibility with the original policy's
                # ordinary mutes; new state restores only softMute.
                restored = (stream_mute(node['id'], False) if saved.get('mute_control') == 'soft'
                            else run('/usr/bin/wpctl', 'set-mute', str(node['id']), '0'))
                if not restored: remaining.append(item)
    if remaining:
        write_private(STATE, {**saved, 'muted': remaining, 'degraded': True})
        return False
    STATE.unlink(missing_ok=True)
    return True


def serve():
    if not settings()['exclusive']: return
    if not restore(): raise RuntimeError('Could not restore previous stream mutes')
    arbiter = Arbiter(stream_mute, pause)
    routing = RoutingGuard()
    trace = SpotifyTrace()
    bluetooth = BluetoothEvents()
    playback = {}
    preferred = None
    process = subprocess.Popen(['/usr/bin/pw-dump', '-m'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    objects, buffer, previous = {}, '', None
    import codecs
    decoder = codecs.getincrementaldecoder('utf-8')()
    parser = json.JSONDecoder()
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            changed_playback = False
            for source, playing in bluetooth.pump():
                if playing and playback.get(source) is not True: preferred = source
                playback[source] = playing
                changed_playback = True
            trace.attach()
            readers = [process.stdout] + ([trace.process.stdout] if trace.process else [])
            ready = select.select(readers, [], [], 0.5)[0]
            if not ready:
                if process.poll() is not None: raise RuntimeError('Audio monitor stopped')
                if not changed_playback: continue
            if trace.process and trace.process.stdout in ready:
                data = os.read(trace.process.stdout.fileno(), 65536)
                if not data:
                    trace.close()
                    trace.retry = time.monotonic() + 5
                else:
                    for playing in trace.feed(data):
                        if playing and playback.get('spotify') is not True: preferred = 'spotify'
                        playback['spotify'] = playing
            if process.stdout not in ready:
                current = nodes(objects.values())
                for node in current.values():
                    if playback.get(node['source']) is False: node['running'] = False
                value = arbiter.update(current, preferred, routing.active(objects.values()))
                value['cookie'] = next((item.get('info', {}).get('cookie') for item in objects.values() if item.get('type') == 'PipeWire:Interface:Core'), None)
                if preferred and any(node['running'] and node['source'] == preferred for node in current.values()): preferred = None
                if value != previous: write_private(STATE, value); previous = value
                continue
            data = os.read(process.stdout.fileno(), 65536)
            if not data: raise RuntimeError('Audio monitor closed')
            buffer += decoder.decode(data)
            if len(buffer) > 4 * 1024**2: raise ValueError('Audio event exceeds size limit')
            while buffer.strip():
                buffer = buffer.lstrip()
                try: changes, length = parser.raw_decode(buffer)
                except json.JSONDecodeError: break
                buffer = buffer[length:]
                if not isinstance(changes, list) or len(changes) > 4096: raise ValueError('Invalid audio event')
                for change in changes:
                    identifier = change.get('id')
                    if change.get('info') is None:
                        objects.pop(identifier, None)
                    else:
                        old = objects.get(identifier, {})
                        old_info = old.get('info') or {}
                        info = {**old_info, **change['info']}
                        info['props'] = {**old_info.get('props', {}), **change['info'].get('props', {})}
                        objects[identifier] = {**old, **change, 'info': info}
                if len(objects) > 4096: raise ValueError('Audio graph exceeds object limit')
                current = nodes(objects.values())
                for node in current.values():
                    if playback.get(node['source']) is False: node['running'] = False
                value = arbiter.update(current, preferred, routing.active(objects.values()))
                value['cookie'] = next((item.get('info', {}).get('cookie') for item in objects.values() if item.get('type') == 'PipeWire:Interface:Core'), None)
                if preferred and any(node['running'] and node['source'] == preferred for node in current.values()): preferred = None
                if value != previous:
                    write_private(STATE, value)
                    previous = value
    finally:
        trace.close()
        process.terminate()
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=3)
        restore()


def status():
    result = subprocess.run(['/usr/bin/systemctl', '--user', 'is-active', UNIT], capture_output=True, text=True, timeout=10)
    saved = read_private(STATE, {})
    winner = saved.get('winner', '') if saved.get('playing') else ''
    if winner.startswith('bluetooth:'): winner = 'bluetooth'
    return {'installed': Path('/usr/local/lib/systemd/user/' + UNIT).is_file(),
            'exclusive': settings()['exclusive'], 'state': result.stdout.strip() if result.stdout.strip() in ('active', 'inactive', 'failed', 'activating', 'deactivating') else 'unknown',
            'source': winner if winner in ('spotify', 'mpd', 'airplay', 'bluetooth') else '',
            'degraded': bool(saved.get('degraded', False))}


def configure(value):
    if not isinstance(value, dict) or set(value) != {'exclusive'} or type(value['exclusive']) is not bool:
        raise ValueError('Invalid source policy')
    import fcntl
    lock_path = STATE.with_name('pi-audio-policy-config.lock')
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return apply_settings(value)


def apply_settings(value):
    if not isinstance(value, dict) or set(value) != {'exclusive'} or type(value['exclusive']) is not bool:
        raise ValueError('Invalid source policy')
    if not status()['installed']: raise ValueError('Install/repair the core receiver first')
    old = settings()
    current_status = status()
    if old == value and not current_status['degraded'] and current_status['state'] == ('active' if value['exclusive'] else 'inactive'): return current_status
    write_private(CONFIG, value)
    operation = 'enable' if value['exclusive'] else 'disable'
    try:
        if not value['exclusive']:
            if not run('/usr/bin/systemctl', '--user', 'disable', '--now', UNIT): raise RuntimeError('Policy stop failed')
            if not restore(): raise RuntimeError('Could not restore stream mutes')
        if not run('/usr/bin/systemctl', '--user', 'try-restart', 'soloist.service', timeout=30): raise RuntimeError('Soloist restart failed')
        if value['exclusive'] and not run('/usr/bin/systemctl', '--user', 'enable', '--now', UNIT): raise RuntimeError('Policy start failed')
    except Exception:
        write_private(CONFIG, old)
        if not old['exclusive']: run('/usr/bin/systemctl', '--user', 'disable', '--now', UNIT)
        run('/usr/bin/systemctl', '--user', 'try-restart', 'soloist.service', timeout=30)
        if old['exclusive']: run('/usr/bin/systemctl', '--user', 'enable', '--now', UNIT)
        raise
    return status()


if __name__ == '__main__':
    if os.geteuid() == 0: sys.exit('Run source policy as the audio account')
    if sys.argv[1:] == ['--restore']: restore()
    elif not sys.argv[1:]: serve()
    else: sys.exit('Unsupported policy action')
