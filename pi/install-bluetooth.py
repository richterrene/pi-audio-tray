#!/usr/bin/python3 -I
"""Install the per-user Bluetooth manager and both A2DP directions."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile

STATE = Path('/var/lib/pi-audio-bluetooth')
FILES = ('install-bluetooth.py', 'setup-receiver.py', 'pi-audio-bluetooth.service', 'pi_bluetooth.py', 'bluetooth_schema.py', '60-bluetooth-output.conf', 'pi_policy.py')
LOG_READY = False
UNIT_FILE = Path('/usr/local/lib/systemd/user/pi-audio-bluetooth.service')
HELPER = Path('/usr/local/lib/pi-audio-tray/pi-bluetooth')



def main():
    global LOG_READY
    os.environ.update(PATH='/usr/sbin:/usr/bin:/sbin:/bin')
    os.umask(0o077)
    if os.geteuid() != 0 or len(sys.argv) != 2 or json.loads(sys.argv[1]) != {}:
        raise ValueError('Use the bundled Bluetooth installer')
    uid = int(os.environ.get('SUDO_UID', '0'))
    account = pwd.getpwuid(uid)
    if uid < 1000 or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', account.pw_name):
        raise ValueError('Use the normal SSH audio account')
    owner = Path('/etc/pi-audio-tray/soloist-user')
    if owner.is_symlink() or not owner.is_file() or owner.stat().st_uid != 0 or owner.stat().st_mode & 0o022 or owner.read_text().strip() != account.pw_name:
        raise ValueError('Use this managed receiver audio account')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    model = Path('/proc/device-tree/model').read_text().strip('\0')
    arch = subprocess.check_output(['/usr/bin/dpkg', '--print-architecture'], text=True, timeout=10).strip()
    if release.get('ID', '').strip('"') not in ('debian', 'raspbian') or release.get('VERSION_ID', '').strip('"') != '13' or os.uname().machine != 'aarch64' or arch != 'arm64' or 'Raspberry Pi' not in model:
        raise ValueError('Requires Debian/Pi OS 13 ARM64 Raspberry Pi')
    for path in (STATE, *STATE.parents):
        if path.is_symlink() or (path.exists() and (path.stat().st_uid != 0 or path.stat().st_mode & 0o022)):
            raise ValueError('Unsafe Bluetooth installation path')
    STATE.mkdir(mode=0o700, exist_ok=True)
    os.chmod(STATE, 0o700)
    with tempfile.TemporaryDirectory(prefix='bundle-', dir=STATE) as folder:
        snapshot = Path(folder)
        for name in FILES:
            path = Path(__file__).resolve().parent / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_size > 1024**2:
                raise ValueError('Invalid Bluetooth bundle')
            shutil.copyfile(path, snapshot / name, follow_symlinks=False)
        spec = importlib.util.spec_from_file_location('receiver', snapshot / 'setup-receiver.py')
        receiver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(receiver)
        setup = receiver.safe_dir('/var/lib/pi-audio-setup', 0o700)
        lock = os.open(setup / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock, 'w') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd = os.open(STATE / 'install.log', os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w') as log:
                LOG_READY = True
                receiver.LOG = log
                deploy(receiver, account, uid, snapshot)


def deploy(receiver, account, uid, snapshot):
    user = ['/usr/sbin/runuser', '-u', account.pw_name, '--', '/usr/bin/env',
            f'XDG_RUNTIME_DIR=/run/user/{uid}', f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus']
    receiver.run(*user, '/usr/bin/systemctl', '--user', 'is-active', '--quiet', 'pipewire.service', 'wireplumber.service')
    unit = UNIT_FILE
    helper = HELPER
    marker = STATE / 'owner'
    if marker.is_file():
        if marker.read_text().strip() != account.pw_name:
            raise ValueError('Bluetooth belongs to another audio account')
    elif unit.exists() or helper.exists():
        raise ValueError('Unowned Bluetooth files require manual integration')
    receiver.write(marker, account.pw_name + '\n', 0o600)
    receiver.emit('deploy', 'Installing Bluetooth management; existing pairings are retained')
    if unit.exists():
        receiver.run(*user, '/usr/bin/systemctl', '--user', 'disable', '--now', 'pi-audio-bluetooth.service')
    receiver.public_dir(unit.parent)
    receiver.public_dir(helper.parent)
    for name, destination, mode in (
        ('pi_bluetooth.py', helper, 0o755),
        ('bluetooth_schema.py', helper.with_name('bluetooth_schema.py'), 0o644),
        ('pi_policy.py', helper.with_name('pi_policy.py'), 0o755),
        ('pi-audio-bluetooth.service', unit, 0o644)):
        receiver.write(destination, (snapshot / name).read_text(), mode)
    receiver.run(*user, '/usr/bin/systemctl', '--user', 'daemon-reload')
    # Run all writes below the user's home as that user, never as root.
    destination = str(Path(account.pw_dir) / '.config/wireplumber/wireplumber.conf.d/60-bluetooth-output.conf')
    code = ('from pathlib import Path; import os,sys,tempfile; '
            'p=Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True,mode=0o700); '
            'fd,t=tempfile.mkstemp(dir=p.parent,prefix=".bluetooth-"); '
            'os.write(fd,sys.argv[2].encode()); os.close(fd); os.replace(t,p)')
    receiver.run(*user, '/usr/bin/python3', '-I', '-c', code, destination,
                 (snapshot / '60-bluetooth-output.conf').read_text())
    receiver.run(*user, '/usr/bin/systemctl', '--user', 'restart', 'wireplumber.service')
    receiver.run(*user, '/usr/bin/systemctl', '--user', 'try-restart', 'pi-audio-policy.service')
    receiver.run(*user, '/usr/bin/systemctl', '--user', 'enable', '--now', 'pi-audio-bluetooth.service')
    receiver.emit('complete', 'Bluetooth input/output management installed. Existing pairings retained; use Connect and select an audio output explicitly.')



if __name__ == '__main__':
    try:
        main()
    except Exception:
        if LOG_READY:
            try:
                import traceback
                fd = os.open(STATE / 'install.log', os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
                with os.fdopen(fd, 'w') as log:
                    traceback.print_exc(file=log)
            except OSError:
                pass
        print('Bluetooth manager installation failed; inspect /var/lib/pi-audio-bluetooth/install.log as administrator.', file=sys.stderr)
        sys.exit(1)
