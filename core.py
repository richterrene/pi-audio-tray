"""Configuration and SSH transport. No credentials or machine defaults in source."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import sys
import uuid

import paramiko

REMOTE_HELPER = '/usr/local/lib/pi-audio-tray/pi-helper'
DEFAULTS = {'host': '', 'username': '', 'port': 22, 'key_file': '',
            'host_key_alias': '', 'poll_seconds': 10}


def config_dir():
    root = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'pi-audio-tray'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def load_config():
    try:
        data = json.loads((config_dir() / 'config.json').read_text())
        return validate_config({**DEFAULTS, **data})
    except (OSError, ValueError, TypeError):
        return DEFAULTS.copy()


def validate_config(data):
    result = {key: data.get(key, value) for key, value in DEFAULTS.items()}
    for key in ('host', 'username', 'key_file', 'host_key_alias'):
        if not isinstance(result[key], str) or any(ord(c) < 32 for c in result[key]):
            raise ValueError('Invalid connection settings')
        result[key] = result[key].strip()
    result['port'] = int(result['port'])
    result['poll_seconds'] = int(result['poll_seconds'])
    if not 1 <= result['port'] <= 65535 or not 5 <= result['poll_seconds'] <= 300:
        raise ValueError('Invalid port or refresh interval')
    return result


def save_config(data):
    data = validate_config(data)
    root = config_dir()
    tmp = root / 'config.json.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    tmp.replace(root / 'config.json')


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
        keys.load(str(path))
    keys.add(error.hostname, error.key.get_name(), error.key)
    keys.save(str(path))
    path.chmod(0o600)


def connect(config):
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
    output = stdout.read().decode('utf-8', errors='replace')
    error = stderr.read().decode('utf-8', errors='replace')
    if stdout.channel.recv_exit_status():
        raise RuntimeError(error.strip() or output.strip() or 'Remote command failed')
    return output


def poll(config):
    client = connect(config)
    try:
        result = json.loads(run(client, REMOTE_HELPER + ' status'))
        if result.get('protocol') != 1:
            raise RuntimeError(result.get('message', 'Please install or update the Pi helper.'))
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
    if not key or len(key) > 16384 or any(c.isspace() for c in key):
        raise ValueError('Please enter a valid Soloist API key without whitespace.')
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


def install_helper(config):
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    data = (root / 'pi_helper.py').read_bytes()
    client = connect(config)
    temp = '/tmp/pi-audio-tray-' + uuid.uuid4().hex
    try:
        with client.open_sftp() as sftp:
            with sftp.open(temp, 'wx') as f:
                f.write(data)
            sftp.chmod(temp, 0o600)
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


class PairSession:
    def __init__(self, config, event):
        self.config, self.event = config.copy(), event
        self.client = self.channel = self.input = None
        self.stopping = False

    def run(self):
        try:
            self.client = connect(self.config)
            self.input, stdout, stderr = self.client.exec_command(
                'sudo -n ' + REMOTE_HELPER + ' pair', timeout=210)
            self.channel = stdout.channel
            if self.stopping:
                self.send(action='stop')
            for line in stdout:
                self.event(json.loads(line))
            error = stderr.read().decode('utf-8', errors='replace')
            if self.channel.recv_exit_status():
                raise RuntimeError(error.strip() or 'Pairing helper failed')
        except Exception as exc:
            self.event({'event': 'error', 'message': str(exc)})
        finally:
            if self.client:
                self.client.close()
            self.event({'event': 'ended'})

    def send(self, **message):
        if self.input:
            self.input.write(json.dumps(message) + '\n')
            self.input.flush()

    def stop(self):
        self.stopping = True
        self.send(action='stop')
