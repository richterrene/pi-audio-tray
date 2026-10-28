#!/usr/bin/python3 -I
"""Unprivileged, bounded audio add-on protocol. No arbitrary commands or graphs."""
import json
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

UNIT = 'pi-audio-airplay.service'
MODE_FILE = Path('/etc/pi-audio-tray/airplay-mode')
AIRPLAY2 = Path('/usr/local/lib/pi-audio-tray/airplay2/shairport-5.6.2-nqptp-1.2.9/shairport-sync')
TIMING = 'pi-audio-nqptp.service'
ROOT = Path.home() / '.config/pi-audio-tray'
STATES = {'active', 'inactive', 'failed', 'activating', 'deactivating', 'unknown'}


def command(*args):
    result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=20, check=False)
    if len(result.stdout) > 8192 or len(result.stderr) > 8192:
        raise ValueError('Audio command exceeded output limit')
    return result


def state(unit, system=False):
    value = command('/usr/bin/systemctl', *([] if system else ['--user']), 'is-active', unit).stdout.strip()
    return value if value in STATES else 'unknown'


def mode():
    value = MODE_FILE.read_text().strip() if MODE_FILE.is_file() else 'classic'
    if value not in ('classic', 'airplay2'):
        raise ValueError('Invalid installed AirPlay mode')
    return value


def config_path():
    return ROOT / ('airplay2.conf' if mode() == 'airplay2' else 'airplay.conf')


def status():
    selected = mode()
    binary = AIRPLAY2 if selected == 'airplay2' else Path('/usr/bin/shairport-sync')
    version = command(str(binary), '-V').stdout.strip() if binary.is_file() else ''
    installed = Path('/usr/local/lib/systemd/user/' + UNIT).is_file()
    name = ''
    try:
        with config_path().open() as stream:
            text = stream.read(4097)
        if len(text) <= 4096:
            match = re.search(r'^ name = "([A-Za-z0-9][A-Za-z0-9 ._-]{0,49})";$', text, re.MULTILINE)
            name = match.group(1) if match else ''
    except OSError:
        pass
    return {'protocol': 1, 'installed': installed,
            'configured': config_path().is_file(), 'mode': selected,
            'timing': state(TIMING, system=True) if selected == 'airplay2' else 'unknown',
            'name': name,
            'state': state(UNIT) if installed else 'unknown',
            'enabled': command('/usr/bin/systemctl', '--user', 'is-enabled', UNIT).stdout.strip() == 'enabled' if installed else False,
            'pipewire': state('pipewire.service'),
            'version': version[:256]}


def validate_settings(value):
    if not isinstance(value, dict) or set(value) != {'name', 'password'}:
        raise ValueError('Invalid AirPlay settings')
    if not isinstance(value['name'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,49}', value['name']):
        raise ValueError('Use 1–50 ASCII letters, digits, spaces, dots, underscores or hyphens for the AirPlay name')
    password = value['password']
    if not isinstance(password, str) or not 8 <= len(password) <= 128 or any(not 33 <= ord(c) <= 126 for c in password):
        raise ValueError('Use an AirPlay password of 8–128 printable ASCII characters without spaces')
    return value


def configuration(value):
    value = validate_settings(value)
    # json.dumps escapes quotes and backslashes; controls/non-ASCII were rejected.
    ap2 = mode() == 'airplay2'
    backend = 'pipewire' if ap2 else 'pa'
    return ('general = {\n name = ' + json.dumps(value['name']) + ';\n password = ' +
            json.dumps(value['password']) + ';\n output_backend = "' + backend + '";\n mdns_backend = "avahi";\n' +
            (' service_type = "airplay2";\n' if ap2 else '') + '};\n' +
            backend + ' = { application_name = "Pi Audio AirPlay"; };\n' +
            'metadata = { enabled = "no"; };\n')


def configure(value):
    text = configuration(value)  # Validate before touching the filesystem.
    for parent in (ROOT, *ROOT.parents):
        if parent.is_symlink():
            raise ValueError('Symbolic-link configuration paths are unsupported')
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    if ROOT.stat().st_uid != os.getuid():
        raise ValueError('Audio configuration must belong to the audio account')
    os.chmod(ROOT, 0o700)
    destination = config_path()
    if destination.is_symlink():
        raise ValueError('Symbolic-link configuration files are unsupported')
    fd, temporary = tempfile.mkstemp(prefix='.airplay-', dir=ROOT)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def action(name, settings=None):
    if name == 'configure':
        configure(settings)
        # Saving settings is separate from enabling a LAN listener.
        if state(UNIT) == 'active':
            result = command('/usr/bin/systemctl', '--user', 'restart', UNIT)
            if result.returncode:
                raise RuntimeError('AirPlay restart failed; check the user service journal on the Pi')
    elif name in ('enable', 'disable'):
        if name == 'enable' and not config_path().is_file():
            raise ValueError('Set the AirPlay name and password before enabling')
        timing_was_enabled = False
        if mode() == 'airplay2' and name == 'enable':
            timing_was_enabled = command('/usr/bin/systemctl', 'is-enabled', TIMING).stdout.strip() == 'enabled'
            result = command('/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'enable', '--now', TIMING)
            if result.returncode or state(TIMING, system=True) != 'active':
                if not timing_was_enabled:
                    command('/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'disable', '--now', TIMING)
                raise RuntimeError('AirPlay 2 timing service is not ready')
        result = command('/usr/bin/systemctl', '--user', name, '--now', UNIT)
        if result.returncode:
            if mode() == 'airplay2' and name == 'enable' and not timing_was_enabled:
                command('/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'disable', '--now', TIMING)
            raise RuntimeError('AirPlay service operation failed; check the user service journal on the Pi')
        if mode() == 'airplay2' and name == 'disable':
            if command('/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'disable', '--now', TIMING).returncode:
                raise RuntimeError('AirPlay stopped but its timing service could not be disabled')
    else:
        raise ValueError('Unsupported audio action')
    return status()


def main():
    if os.geteuid() == 0:
        raise ValueError('Run audio controls as the normal audio account')
    os.environ['PATH'] = '/usr/sbin:/usr/bin:/sbin:/bin'
    if len(sys.argv) != 2:
        raise ValueError('One audio action is required')
    name = sys.argv[1]
    if name in ('policy-status', 'policy-configure'):
        spec = importlib.util.spec_from_file_location('pi_policy', Path(__file__).with_name('pi_policy.py'))
        policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(policy)
        if name == 'policy-status':
            result = policy.status()
        else:
            raw = sys.stdin.buffer.read(4097)
            if len(raw) > 4096: raise ValueError('Policy request exceeds size limit')
            result = policy.configure(json.loads(raw))
    elif name == 'status':
        result = status()
    else:
        data = sys.stdin.buffer.read(4097)
        if len(data) > 4096:
            raise ValueError('Audio request exceeded size limit')
        result = action(name, json.loads(data) if data else None)
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Never echo settings, passwords or third-party diagnostics to the desktop.
        print('Audio operation failed. Check configuration and the Pi user service journal.', file=sys.stderr)
        sys.exit(1)
