"""Configuration and SSH transport. No credentials or machine defaults in source."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import stat
import sys
import tempfile
import time
import queue
import re
import unicodedata
import math
import uuid

import paramiko
from platform_support import secure_directory, private_file, open_instance_lock

REMOTE_HELPER = '/usr/local/lib/pi-audio-tray/pi-helper'
DEFAULTS = {'host': '', 'username': '', 'port': 22, 'key_file': '',
            'host_key_alias': '', 'poll_seconds': 10}
MAX_OUTPUT = 128 * 1024
MAX_PAIR_LINE = 8192


def display_text(value, limit=1024):
    """Untrusted device names/errors must not inject control or bidi characters."""
    return ''.join(c for c in str(value) if c.isprintable()
                   and unicodedata.category(c) != 'Cf')[:limit]


def atomic_private_write(path, data):
    fd, temp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            private_file(f.fileno(), Path(temp))
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def instance_lock_file():
    return open_instance_lock(config_dir() / 'instance.lock')


def config_dir():
    if os.environ.get('XDG_CONFIG_HOME'):
        root = Path(os.environ['XDG_CONFIG_HOME']) / 'pi-audio-tray'
    elif sys.platform == 'win32':
        root = (Path(os.environ['APPDATA']) if os.environ.get('APPDATA') else Path.home() / 'AppData/Roaming') / 'Pi Audio Tray'
    elif sys.platform == 'darwin':
        root = Path.home() / 'Library/Application Support/Pi Audio Tray'
    else:
        root = Path.home() / '.config/pi-audio-tray'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    secure_directory(root)
    return root


def load_config():
    try:
        data = json.loads((config_dir() / 'config.json').read_text())
        return validate_config({**DEFAULTS, **data})
    except (OSError, ValueError, TypeError):
        return DEFAULTS.copy()


def validate_config(data):
    if not isinstance(data, dict):
        raise ValueError('Invalid connection settings')
    result = {key: data.get(key, value) for key, value in DEFAULTS.items()}
    for key in ('host', 'username', 'key_file', 'host_key_alias'):
        if not isinstance(result[key], str) or len(result[key]) > 4096 or any(
                not c.isprintable() or unicodedata.category(c) == 'Cf' for c in result[key]):
            raise ValueError('Invalid connection settings')
        result[key] = result[key].strip()
    for key in ('host', 'host_key_alias', 'username'):
        if any(c.isspace() or c in ',#' for c in result[key]):
            raise ValueError('Hostnames, aliases, and usernames cannot contain whitespace, commas, or #')
    result['port'] = int(result['port'])
    result['poll_seconds'] = int(result['poll_seconds'])
    if not 1 <= result['port'] <= 65535 or not 5 <= result['poll_seconds'] <= 300:
        raise ValueError('Invalid port or refresh interval')
    return result


def save_config(data):
    data = validate_config(data)
    root = config_dir()
    atomic_private_write(root / 'config.json', (json.dumps(data, indent=2) + '\n').encode())


class UnknownHost(Exception):
    def __init__(self, hostname, key):
        self.hostname, self.key = hostname, key
        self.fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
        super().__init__(f'Unknown SSH host key: {self.fingerprint}')


class CheckHostKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        raise UnknownHost(hostname, key)


def trust_host(error):
    path = config_dir() / 'known_hosts'
    keys = paramiko.HostKeys()
    if path.exists():
        if path.is_symlink():
            raise ValueError('Refusing a symbolic-link host-key store')
        keys.load(str(path))
    existing = keys.lookup(error.hostname)
    if existing and any(key != error.key for key in existing.values()):
        raise ValueError('A different key is already trusted for this host; refusing replacement')
    keys.add(error.hostname, error.key.get_name(), error.key)
    contents = ''.join(paramiko.hostkeys.HostKeyEntry([host], key).to_line()
                       for host, entries in keys.items() for key in entries.values())
    atomic_private_write(path, contents.encode())


def connect(config):
    config = validate_config(config)
    if not config['host'] or not config['username']:
        raise ValueError('Open Settings and enter the Pi host and SSH username.')
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    local_keys = config_dir() / 'known_hosts'
    if local_keys.exists():
        client.load_host_keys(str(local_keys))
    client.set_missing_host_key_policy(CheckHostKey())
    sock = socket.create_connection((config['host'], config['port']), timeout=8)
    try:
        client.connect(hostname=config['host_key_alias'] or config['host'],
                       port=config['port'], username=config['username'], sock=sock,
                       key_filename=os.path.expanduser(config['key_file']) if config['key_file'] else None,
                       allow_agent=True, look_for_keys=not bool(config['key_file']),
                       timeout=8, auth_timeout=8, banner_timeout=8)
        client.get_transport().set_keepalive(5)
        return client
    except Exception:
        client.close()
        sock.close()
        raise


def run(client, command, timeout=20):
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    stdin.close()
    channel = stdout.channel
    output, errors = bytearray(), bytearray()
    deadline = time.monotonic() + timeout
    channel.settimeout(0.25)
    try:
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError('Remote command exceeded its time limit')
            progressed = False
            for ready, receive, buffer in ((channel.recv_ready, channel.recv, output),
                                            (channel.recv_stderr_ready, channel.recv_stderr, errors)):
                if ready():
                    buffer.extend(receive(16384))
                    progressed = True
                    if len(output) + len(errors) > MAX_OUTPUT:
                        raise RuntimeError('Remote command exceeded its output limit')
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            if not progressed:
                time.sleep(0.02)
        if channel.recv_exit_status():
            raise RuntimeError(display_text(errors.decode('utf-8', errors='replace').strip()
                or output.decode('utf-8', errors='replace').strip() or 'Remote command failed', 2048))
        return output.decode('utf-8', errors='replace')
    finally:
        channel.close()


def validate_status(data):
    if not isinstance(data, dict) or data.get('protocol') != 1:
        raise ValueError('Invalid status protocol; update the Pi helper')
    services, adapter = data.get('services'), data.get('adapter')
    devices, soloist = data.get('devices'), data.get('soloist', {})
    if not isinstance(services, dict) or len(services) > 32 or not isinstance(adapter, dict):
        raise ValueError('Invalid service status')
    if not isinstance(devices, list) or len(devices) > 32 or not isinstance(soloist, dict):
        raise ValueError('Invalid device status')
    states = {'active', 'inactive', 'failed', 'activating', 'deactivating', 'reloading', 'maintenance', 'unknown'}
    if any(not isinstance(k, str) or len(k) > 64 or not isinstance(v, str) or v not in states
           for k, v in services.items()):
        raise ValueError('Invalid service state')
    backend = data.get('spotify_backend', 'raspotify')
    if backend not in ('raspotify', 'soloist'):
        raise ValueError('Invalid audio backend')
    clean_devices = []
    for device in devices:
        if not isinstance(device, dict) or not isinstance(device.get('name'), str):
            raise ValueError('Invalid Bluetooth device')
        clean_devices.append({'name': display_text(device['name'], 128)})
    clean_adapter = {}
    for key in ('Powered', 'Pairable', 'Discoverable'):
        value = adapter.get(key, False)
        if not isinstance(value, bool):
            raise ValueError('Invalid adapter flag')
        clean_adapter[key] = value
    clean_soloist = {}
    for key in ('key_configured', 'automatic_updates', 'update_failed'):
        value = soloist.get(key, False)
        if not isinstance(value, bool):
            raise ValueError('Invalid Soloist flag')
        clean_soloist[key] = value
    days = soloist.get('days_remaining')
    if days is not None:
        if type(days) is not int or not 0 <= days <= 90:
            raise ValueError('Invalid build lifetime')
        clean_soloist['days_remaining'] = days
    raw_stats = data.get('stats', {})
    if not isinstance(raw_stats, dict):
        raise ValueError('Invalid Pi statistics')
    stats = {}
    for key, minimum, maximum in (('cpu_percent', 0, 100), ('temperature_c', -50, 150),
                                    ('cpu_window_seconds', 0, 3600)):
        value = raw_stats.get(key)
        if value is not None:
            if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError('Invalid Pi statistic: ' + key)
            stats[key] = value
    load = raw_stats.get('load')
    if load is not None:
        if (not isinstance(load, list) or len(load) != 3 or any(type(v) not in (int, float)
                or not math.isfinite(v) or not 0 <= v <= 1000000 for v in load)):
            raise ValueError('Invalid load averages')
        stats['load'] = load
    for key in ('cpu_count', 'uptime_seconds', 'memory_total', 'memory_used', 'disk_total', 'disk_used'):
        if key in raw_stats:
            value = raw_stats[key]
            if type(value) is not int or not 0 <= value < 2**63:
                raise ValueError('Invalid Pi statistic: ' + key)
            stats[key] = value
    for prefix in ('memory', 'disk'):
        if prefix + '_used' in stats or prefix + '_total' in stats:
            total, used = stats.get(prefix + '_total', 0), stats.get(prefix + '_used', -1)
            if not 0 <= used <= total or total <= 0:
                raise ValueError('Invalid usage statistics')
    if 'cpu_count' in stats and not 1 <= stats['cpu_count'] <= 4096:
        raise ValueError('Invalid CPU count')
    return {'protocol': 1, 'services': services, 'adapter': clean_adapter,
            'devices': clean_devices, 'spotify_backend': backend, 'soloist': clean_soloist, 'stats': stats}


def poll(config, detailed=True):
    client = connect(config)
    try:
        command = ' status' if detailed else ' health'
        result = validate_status(json.loads(run(client, REMOTE_HELPER + command)))
        result['_detailed'] = bool(detailed)
        return result
    finally:
        client.close()


def disable_pairing(config):
    client = connect(config)
    try:
        run(client, 'sudo -n ' + REMOTE_HELPER + ' off')
    finally:
        client.close()


def store_soloist_key(config, key):
    if (not isinstance(key, str) or not key or len(key.encode('utf-8')) > 16383
            or any(not c.isprintable() or c.isspace() for c in key)):
        raise ValueError('Enter a printable Soloist API key without whitespace (maximum 16383 UTF-8 bytes).')
    client = connect(config)
    try:
        with client.open_sftp() as sftp:
            folder = sftp.normalize('.') + '/.config/soloist'
            sftp.chmod(folder, 0o700)
            temp = folder + '/.api-key-' + uuid.uuid4().hex
            try:
                with sftp.open(temp, 'wx') as f:
                    f.chmod(0o600)
                    f.write(key + '\n')
                sftp.posix_rename(temp, folder + '/api-key')
            finally:
                try:
                    sftp.remove(temp)
                except OSError:
                    pass
        run(client, 'systemctl --user restart soloist.service')
    finally:
        client.close()


def remove_soloist_key(config):
    client = connect(config)
    try:
        run(client, 'systemctl --user stop soloist.service')
        with client.open_sftp() as sftp:
            path = sftp.normalize('.') + '/.config/soloist/api-key'
            try:
                sftp.remove(path)
            except FileNotFoundError:
                pass
    finally:
        client.close()


def update_soloist(config):
    client = connect(config)
    try:
        run(client, 'sudo -n systemctl start soloist-update.service', timeout=190)
    finally:
        client.close()


def shutdown_pi(config):
    """Request graceful shutdown; never force poweroff or ignore update failures."""
    client = connect(config)
    try:
        states = run(client, '/usr/bin/systemctl show --property=ActiveState --value '
                     'apt-daily.service apt-daily-upgrade.service soloist-update.service '
                     '2>/dev/null || true').split()
        if len(states) != 3 or any(state not in ('inactive', 'failed') for state in states):
            raise RuntimeError('A Pi update is running, or its state could not be verified. Wait and try again.')
        run(client, 'sudo -n /usr/bin/systemctl --no-block poweroff', timeout=15)
    finally:
        client.close()


def install_helper(config):
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    data = (root / 'pi_helper.py').read_bytes()
    client = connect(config)
    temp = '/tmp/pi-audio-tray-' + uuid.uuid4().hex
    try:
        with client.open_sftp() as sftp:
            with sftp.open(temp, 'wx') as f:
                f.chmod(0o600)
                f.write(data)
        run(client, 'sudo -n apt-get install -y python3-dbus python3-gi', timeout=180)
        run(client, 'sudo -n install -d -m 755 /usr/local/lib/pi-audio-tray && '
            'sudo -n install -o root -g root -m 755 ' + shlex.quote(temp) + ' ' + REMOTE_HELPER)
    finally:
        try:
            with client.open_sftp() as sftp:
                sftp.remove(temp)
        except Exception:
            pass
        client.close()


def validate_pair_event(data):
    if not isinstance(data, dict):
        raise ValueError('Invalid pairing message')
    kind = data.get('event')
    if kind not in ('ready', 'confirm', 'canceled', 'display', 'error', 'closed'):
        raise ValueError('Unknown pairing message')
    result = {'event': kind}
    if kind == 'ready':
        expires = data.get('expires')
        if type(expires) is not int or not 0 < expires < 2**48:
            raise ValueError('Invalid pairing expiration')
        result['expires'] = expires
    if kind in ('confirm', 'canceled'):
        token = data.get('token')
        if not isinstance(token, str) or not re.fullmatch(r'[0-9]{1,20}', token):
            raise ValueError('Invalid pairing request identifier')
        result['token'] = token
    if kind == 'confirm':
        address = data.get('address')
        if not isinstance(address, str) or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', address):
            raise ValueError('Invalid Bluetooth address')
        if data.get('kind') not in ('passkey', 'authorize', 'service') or not isinstance(data.get('name'), str):
            raise ValueError('Invalid pairing request')
        result.update(address=address, name=display_text(data['name'], 128), kind=data['kind'])
        if data['kind'] == 'passkey':
            code = data.get('code')
            if not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code):
                raise ValueError('Invalid Bluetooth confirmation code')
            result['code'] = code
    if kind == 'display':
        result['code'] = display_text(data.get('code', ''), 16)
    if kind == 'error':
        result['message'] = display_text(data.get('message', 'Pairing failed'))
    return result


class PairSession:
    def __init__(self, config, event):
        self.config, self.event = config.copy(), event
        self.client = self.channel = self.input = None
        self.stopping = False
        self.session_id = uuid.uuid4().hex
        self.outgoing = queue.Queue(maxsize=16)

    def emit(self, event):
        self.event({**event, 'session_id': self.session_id})

    def run(self):
        try:
            self.client = connect(self.config)
            self.input, stdout, stderr = self.client.exec_command(
                'sudo -n ' + REMOTE_HELPER + ' pair', timeout=210)
            self.channel = stdout.channel
            self.channel.settimeout(0.25)
            deadline = time.monotonic() + 200
            buffer, errors = bytearray(), bytearray()
            total = 0
            reported_error = False
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Pairing session exceeded its time limit')
                while not self.outgoing.empty():
                    message = self.outgoing.get_nowait()
                    self.channel.sendall((json.dumps(message) + '\n').encode())
                if self.channel.recv_ready():
                    chunk = self.channel.recv(4096)
                    total += len(chunk)
                    buffer.extend(chunk)
                    while b'\n' in buffer:
                        line, _, tail = buffer.partition(b'\n')
                        buffer[:] = tail
                        if len(line) > MAX_PAIR_LINE:
                            raise ValueError('Pairing message exceeded its size limit')
                        event = validate_pair_event(json.loads(line))
                        reported_error |= event['event'] == 'error'
                        self.emit(event)
                if self.channel.recv_stderr_ready():
                    chunk = self.channel.recv_stderr(4096)
                    total += len(chunk)
                    errors.extend(chunk)
                if total > MAX_OUTPUT or len(buffer) > MAX_PAIR_LINE:
                    raise ValueError('Pairing session exceeded its output limit')
                if self.channel.exit_status_ready() and not self.channel.recv_ready() and not self.channel.recv_stderr_ready():
                    if buffer:
                        raise ValueError('Incomplete pairing message')
                    break
                time.sleep(0.02)
            if self.channel.recv_exit_status() and not reported_error:
                raise RuntimeError(display_text(errors.decode('utf-8', errors='replace')) or 'Pairing helper failed')
        except Exception as exc:
            self.emit({'event': 'error', 'message': display_text(exc)})
        finally:
            if self.client:
                self.client.close()
            self.emit({'event': 'ended'})

    def send(self, **message):
        if message.get('action') not in ('stop', 'approve', 'reject'):
            raise ValueError('Invalid pairing action')
        if message['action'] != 'stop' and not re.fullmatch(r'[0-9]{1,20}', str(message.get('token', ''))):
            raise ValueError('Invalid pairing request identifier')
        try:
            self.outgoing.put_nowait(message)
        except queue.Full:
            raise RuntimeError('Too many pending pairing actions') from None

    def stop(self):
        self.stopping = True
        self.send(action='stop')
