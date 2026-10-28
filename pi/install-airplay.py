#!/usr/bin/python3 -I
"""Install an optional AirPlay receiver variant; leave it disabled until configured."""
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

FILES = ('install-airplay.py', 'setup-receiver.py', 'pi-audio-airplay.service', 'pi_audio.py',
         'airplay2-install.py', 'airplay-build.py', 'pi-audio-nqptp.service', 'pi_policy.py')
STATE = Path('/var/lib/pi-audio-airplay')
SETUP_STATE = Path('/var/lib/pi-audio-setup')
LOG_READY = False


def main():
    global LOG_READY
    os.environ.update(PATH='/usr/sbin:/usr/bin:/sbin:/bin', DEBIAN_FRONTEND='noninteractive')
    os.umask(0o077)
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise ValueError('Run the bundled installer through Pi Audio Tray')
    plan = json.loads(sys.argv[1])
    if plan == {}:
        plan = {'mode': 'classic'}
    if not isinstance(plan, dict) or set(plan) != {'mode'} or plan['mode'] not in ('classic', 'airplay2'):
        raise ValueError('Invalid AirPlay installation choice')
    mode = plan['mode']
    uid = int(os.environ.get('SUDO_UID', '0'))
    account = pwd.getpwuid(uid)
    if uid < 1000 or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', account.pw_name):
        raise ValueError('A normal SSH audio account is required')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    arch = subprocess.check_output(['/usr/bin/dpkg', '--print-architecture'], text=True).strip()
    model = Path('/proc/device-tree/model').read_text().strip('\0')
    if release.get('VERSION_ID', '').strip('"') != '13' or release.get('ID', '').strip('"') not in ('debian', 'raspbian') or arch != 'arm64' or 'Raspberry Pi' not in model:
        raise ValueError('Requires a Debian/Pi OS 13 ARM64 Raspberry Pi')
    audio_user = Path('/etc/pi-audio-tray/soloist-user')
    if not audio_user.is_file() or audio_user.is_symlink() or audio_user.stat().st_uid != 0 or audio_user.stat().st_mode & 0o022 or audio_user.read_text().strip() != account.pw_name:
        raise ValueError('Use the existing Pi Audio receiver audio account')
    for path in (STATE, *STATE.parents):
        if path.is_symlink() or (path.exists() and (path.stat().st_uid != 0 or path.stat().st_mode & 0o022)):
            raise ValueError('Unsafe installation path')
    STATE.mkdir(mode=0o700, exist_ok=True)
    os.chmod(STATE, 0o700)
    lock = os.open(STATE / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with tempfile.TemporaryDirectory(prefix='bundle-', dir=STATE) as folder:
        snapshot = Path(folder)
        for name in FILES:
            source = Path(__file__).resolve().parent / name
            info = source.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_size > 1024 * 1024:
                raise ValueError('Invalid installer bundle')
            shutil.copyfile(source, snapshot / name, follow_symlinks=False)
        spec = importlib.util.spec_from_file_location('receiver', snapshot / 'setup-receiver.py')
        receiver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(receiver)
        receiver.safe_dir(SETUP_STATE, 0o700)
        shared_lock = os.open(SETUP_STATE / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        fcntl.flock(shared_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        logfd = os.open(STATE / 'install.log', os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.fchmod(logfd, 0o600)
        with os.fdopen(logfd, 'w') as log:
            LOG_READY = True
            receiver.LOG = log
            usercmd = ['/usr/sbin/runuser', '-u', account.pw_name, '--', '/usr/bin/env',
                       f'XDG_RUNTIME_DIR=/run/user/{uid}', f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus']
            receiver.run(*usercmd, '/usr/bin/systemctl', '--user', 'is-active', '--quiet', 'pipewire.service')
            marker = STATE / 'owner'
            unit = Path('/usr/local/lib/systemd/user/pi-audio-airplay.service')
            if marker.exists() and marker.read_text().strip() != account.pw_name:
                raise ValueError('AirPlay belongs to another audio account')
            if not marker.exists():
                # Do not take over an existing third-party Shairport installation.
                if unit.exists() or Path('/usr/bin/shairport-sync').exists() or Path('/etc/shairport-sync.conf').exists() or Path('/etc/systemd/system/shairport-sync.service').is_symlink():
                    raise ValueError('Existing Shairport installation requires manual integration')
                receiver.write(marker, account.pw_name + '\n', 0o600)
            if mode == 'classic':
                receiver.emit('packages', 'Installing optional classic AirPlay package')
                receiver.run('/usr/bin/systemctl', 'mask', 'shairport-sync.service')
                receiver.run('/usr/bin/apt-get', 'update')
                receiver.run('/usr/bin/apt-get', 'install', '-y', '--no-remove', '--no-install-recommends', 'shairport-sync')
                version = subprocess.check_output(['/usr/bin/shairport-sync', '-V'], text=True, timeout=10)
                if '-pa-' not in version or 'airplay2' in version.lower():
                    raise ValueError('This integration requires the classic AirPlay PulseAudio build')
                unit_text = (snapshot / 'pi-audio-airplay.service').read_text()
            else:
                spec = importlib.util.spec_from_file_location('airplay2_install', snapshot / 'airplay2-install.py')
                airplay2 = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(airplay2)
                destination = airplay2.prepare(receiver, snapshot, STATE)
                unit_text = (snapshot / 'pi-audio-airplay.service').read_text().replace(
                    '/usr/bin/shairport-sync -c %h/.config/pi-audio-tray/airplay.conf',
                    str(destination / 'shairport-sync') + ' -c %h/.config/pi-audio-tray/airplay2.conf').replace('pipewire-pulse.service', 'pipewire.service')
                rules = ''.join(f'{account.pw_name} ALL=(root) NOPASSWD: /usr/bin/systemctl {operation} --now pi-audio-nqptp.service\n' for operation in ('enable', 'disable'))
                candidate = STATE / 'sudoers'
                receiver.write(candidate, rules, 0o440)
                receiver.run('/usr/sbin/visudo', '-cf', str(candidate))
                receiver.write('/etc/sudoers.d/pi-audio-airplay2', rules, 0o440)
            # Installation/variant changes always leave receiving disabled, even
            # if an earlier owned installation was enabled. Other sources remain.
            if unit.exists():
                receiver.run(*usercmd, '/usr/bin/systemctl', '--user', 'disable', '--now', 'pi-audio-airplay.service')
            if (STATE / 'airplay2-owned').exists():
                receiver.run('/usr/bin/systemctl', 'disable', '--now', 'pi-audio-nqptp.service')
            receiver.write('/usr/local/lib/pi-audio-tray/pi-audio', (snapshot / 'pi_audio.py').read_text(), 0o755)
            receiver.write('/usr/local/lib/pi-audio-tray/pi_policy.py', (snapshot / 'pi_policy.py').read_text(), 0o755)
            receiver.write('/etc/pi-audio-tray/airplay-mode', mode + '\n')
            receiver.write(unit, unit_text)
            receiver.run('/usr/bin/systemctl', 'daemon-reload')
            receiver.run(*usercmd, '/usr/bin/systemctl', '--user', 'daemon-reload')
            receiver.emit('complete', 'AirPlay installed. Set its name and password, then enable it. Existing audio services were not restarted.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Exceptions raised inside main have already closed the context-managed log.
        # Append a fixed failure marker; credentials never pass through this installer.
        if LOG_READY:
            try:
                import traceback
                fd = os.open(STATE / 'install.log', os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, 'w') as log:
                    traceback.print_exc(file=log)
            except OSError:
                pass
        print('AirPlay installation failed; inspect /var/lib/pi-audio-airplay/install.log as administrator.', file=sys.stderr)
        sys.exit(1)
