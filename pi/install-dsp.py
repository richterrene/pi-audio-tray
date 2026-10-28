#!/usr/bin/python3 -I
"""Deploy fixed, unprivileged DSP controls; leave equalization bypassed."""
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

STATE = Path('/var/lib/pi-audio-dsp')
FILES = ('install-dsp.py', 'setup-receiver.py', 'pi-audio-dsp.service', 'pi_dsp.py', 'dsp_config.py')
LOG_READY = False
UNIT_FILE = Path('/usr/local/lib/systemd/user/pi-audio-dsp.service')
HELPER = Path('/usr/local/lib/pi-audio-tray/pi-dsp')
REQUIRED_FILES = ('/usr/lib/aarch64-linux-gnu/pipewire-0.3/libpipewire-module-filter-chain.so',
                  '/usr/share/wireplumber/scripts/lib/filter-utils.lua')


def main():
    global LOG_READY
    os.environ.update(PATH='/usr/sbin:/usr/bin:/sbin:/bin')
    os.umask(0o077)
    if os.geteuid() != 0 or len(sys.argv) != 2 or json.loads(sys.argv[1]) != {}:
        raise ValueError('Use the bundled DSP installer')
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
            raise ValueError('Unsafe DSP installation path')
    STATE.mkdir(mode=0o700, exist_ok=True)
    os.chmod(STATE, 0o700)
    with tempfile.TemporaryDirectory(prefix='bundle-', dir=STATE) as folder:
        snapshot = Path(folder)
        for name in FILES:
            path = Path(__file__).resolve().parent / name
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_size > 1024**2:
                raise ValueError('Invalid DSP bundle')
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
    for path in REQUIRED_FILES:
        if not Path(path).is_file():
            raise ValueError('Install the supported PipeWire/WirePlumber components first')
    unit = UNIT_FILE
    helper = HELPER
    marker = STATE / 'owner'
    if marker.is_file():
        if marker.read_text().strip() != account.pw_name:
            raise ValueError('DSP belongs to another audio account')
    elif unit.exists() or helper.exists():
        raise ValueError('Unowned DSP files require manual integration')
    receiver.write(marker, account.pw_name + '\n', 0o600)
    receiver.emit('deploy', 'Installing fixed DSP helper and user service; EQ stays bypassed')
    if unit.exists():
        receiver.run(*user, '/usr/bin/systemctl', '--user', 'disable', '--now', 'pi-audio-dsp.service')
    receiver.public_dir(unit.parent)
    receiver.public_dir(helper.parent)
    for name, destination, mode in (
        ('pi_dsp.py', helper, 0o755),
        ('dsp_config.py', helper.with_name('dsp_config.py'), 0o644),
        ('pi-audio-dsp.service', unit, 0o644)):
        receiver.write(destination, (snapshot / name).read_text(), mode)
    receiver.run(*user, '/usr/bin/systemctl', '--user', 'daemon-reload')
    receiver.emit('complete', 'Equalizer installed and bypassed. Saved settings and existing audio services were preserved.')



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
        print('Equalizer installation failed; inspect /var/lib/pi-audio-dsp/install.log as administrator.', file=sys.stderr)
        sys.exit(1)
