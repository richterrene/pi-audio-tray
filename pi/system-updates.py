#!/usr/bin/python3 -I
"""Cached read-only update inventory and two administrator-installed operations."""
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import tempfile
import time

STATE = Path('/var/lib/pi-audio-updates')
STATUS = STATE / 'status.json'
HELPER = Path('/usr/local/lib/pi-audio-tray/pi-system-updates')
SETUP_STATE = Path('/var/lib/pi-audio-setup')


def pending():
    result = subprocess.run(['/usr/bin/apt', 'list', '--upgradable'],
                            capture_output=True, text=True, timeout=90, check=False,
                            env={**os.environ, 'LC_ALL': 'C'})
    if result.returncode or len(result.stdout) > 1024**2:
        raise RuntimeError('Could not calculate cached package updates')
    values = []
    for line in result.stdout.splitlines():
        match = re.match(r'^([A-Za-z0-9][A-Za-z0-9+.:_-]{0,127})/\S+ (\S{1,128}) \S+ \[upgradable from: ([^\]\n]{1,128})\]', line)
        if match:
            values.append({'name': match.group(1), 'current': match.group(3), 'candidate': match.group(2)})
    if len(values) > 4096:
        raise ValueError('Too many pending packages')
    return values[:256], len(values)


def status():
    cached = {}
    try:
        if STATUS.stat().st_size <= 1024**2:
            cached = json.loads(STATUS.read_text())
    except (FileNotFoundError, PermissionError, ValueError):
        pass
    packages, total = pending()
    reboot = cached.get('reboot_required')
    if (cached.get('checked_at') or 0) < time.time() - time.monotonic(): reboot = None
    if Path('/run/reboot-required').exists(): reboot = True
    stamp = Path('/var/lib/apt/periodic/update-success-stamp')
    checked = max(cached.get('checked_at') or 0, int(stamp.stat().st_mtime) if stamp.is_file() else 0) or None
    return {'protocol': 1, 'packages': packages, 'total': total, 'truncated': total > len(packages), 'checked_at': checked,
            'installed_at': cached.get('installed_at'), 'reboot_required': reboot,
            'control_installed': HELPER.is_file(), 'policy_configured': Path('/etc/apt/apt.conf.d/52pi-audio-upgrades').is_file() and Path('/usr/bin/unattended-upgrade').is_file()}


def directory(path, mode):
    for part in (path, *path.parents):
        if part.is_symlink() or (part.exists() and (part.stat().st_uid != 0 or part.stat().st_mode & 0o022)):
            raise ValueError('Unsafe update state path')
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    os.chmod(path, mode)


def save(value):
    fd, temporary = tempfile.mkstemp(prefix='.status-', dir=STATE)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o644)
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, STATUS)
    finally:
        Path(temporary).unlink(missing_ok=True)


def emit(step, message):
    print(json.dumps({'step': step, 'message': message}), flush=True)


def operate(operation):
    if os.geteuid() != 0 or operation not in ('check', 'install'):
        raise ValueError('Unsupported update operation')
    user = pwd.getpwuid(int(os.environ.get('SUDO_UID', '0')))
    owner = Path('/etc/pi-audio-tray/soloist-user')
    if user.pw_uid < 1000 or owner.is_symlink() or not owner.is_file() or owner.stat().st_uid != 0 or owner.stat().st_mode & 0o022 or owner.read_text().strip() != user.pw_name:
        raise ValueError('Use the receiver audio account')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('VERSION_ID', '').strip('"') != '13' or release.get('ID', '').strip('"') not in ('debian', 'raspbian'):
        raise ValueError('Requires Debian/Pi OS 13')
    directory(STATE, 0o755)
    private = STATE / 'private'
    directory(private, 0o700)
    directory(SETUP_STATE, 0o700)
    lock = os.open(SETUP_STATE / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    log = private / 'update.log'
    if log.is_file() and not log.is_symlink() and log.stat().st_size > 4 * 1024**2:
        os.replace(log, private / 'update.log.1')
    fd = os.open(log, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as output:
        env = {**os.environ, 'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'DEBIAN_FRONTEND': 'noninteractive'}
        emit('refresh', 'Refreshing signed package indexes')
        subprocess.run(['/usr/bin/apt-get', '-o', 'DPkg::Lock::Timeout=60', '-o', 'APT::Update::Error-Mode=any', 'update'], stdin=subprocess.DEVNULL, stdout=output, stderr=output, check=True, timeout=1200, env=env)
        if operation == 'install':
            if not Path('/etc/apt/apt.conf.d/52pi-audio-upgrades').is_file():
                raise ValueError('Install/repair the updates component first')
            reboot_config = subprocess.check_output(['/usr/bin/apt-config', 'shell', 'PI_AUDIO_REBOOT', 'Unattended-Upgrade::Automatic-Reboot'], text=True, timeout=10)
            if reboot_config.strip() not in ('', "PI_AUDIO_REBOOT='false'", "PI_AUDIO_REBOOT='0'"):
                raise ValueError('Disable unattended automatic reboots before on-demand updates; retain the conditional reboot timer')
            emit('install', 'Installing updates allowed by the Pi unattended-upgrades policy; held/excluded packages may remain')
            subprocess.run(['/usr/bin/unattended-upgrade', '--verbose'], stdin=subprocess.DEVNULL, stdout=output, stderr=output, check=True, timeout=2400, env=env)
        value = status()
        value['checked_at'] = int(time.time())
        if operation == 'install': value['installed_at'] = int(time.time())
        reboot = Path('/run/reboot-required').exists()
        if Path('/usr/sbin/needrestart').is_file():
            result = subprocess.run(['/usr/sbin/needrestart', '-b', '-r', 'l', '-k'], capture_output=True, text=True, timeout=120, check=False, env=env)
            match = re.search(r'^NEEDRESTART-KSTA:\s*(\d+)', result.stdout, re.MULTILINE)
            if match: reboot = reboot or int(match.group(1)) >= 2
        value['reboot_required'] = reboot
        save({key: value[key] for key in ('checked_at', 'installed_at', 'reboot_required')})
        emit('complete', 'System update operation finished. Refresh the list to see remaining held or excluded packages. No reboot was requested.')


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--status']:
            print(json.dumps(status()))
        elif len(sys.argv) == 2:
            operate(sys.argv[1])
        else:
            raise ValueError('Unsupported update request')
    except Exception:
        emit('error', 'System update operation failed or another installer is active. An administrator can inspect /var/lib/pi-audio-updates/private/update.log.')
        sys.exit(1)
