#!/usr/bin/python3 -I
"""Administrator-only fresh receiver installation; no credentials in arguments."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parent
STATE = Path('/var/lib/pi-audio-setup')
LOG = None


def module(name):
    spec = importlib.util.spec_from_file_location(name, SOURCE / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def emit(step, message):
    print(json.dumps({'step': step, 'message': message}), flush=True)


def run(*args, timeout=1200, **kwargs):
    return subprocess.run(args, check=True, stdin=subprocess.DEVNULL,
                          stdout=LOG, stderr=LOG, timeout=timeout, **kwargs)


def safe_dir(path, mode=None):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symbolic-link installation paths are unsupported')
    path.mkdir(parents=True, exist_ok=True, mode=mode or 0o755)
    for p in (path, *path.parents):
        st = p.stat()
        if st.st_uid != 0 or st.st_mode & 0o022:
            raise ValueError('Root installation paths must be administrator-owned')
    if mode is not None:
        os.chmod(path, mode)
    return path


def write(path, text, mode=0o644):
    path = Path(path)
    safe_dir(path.parent)
    if path.is_symlink():
        raise ValueError('Refusing symbolic-link destination')
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.setup-')
    try:
        with os.fdopen(fd, 'w') as out:
            os.fchmod(out.fileno(), mode)
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def install(name, destination, mode=0o644):
    write(destination, (SOURCE / name).read_text(), mode)


def main():
    global SOURCE, LOG
    os.environ.update(PATH='/usr/sbin:/usr/bin:/sbin:/bin', DEBIAN_FRONTEND='noninteractive')
    os.umask(0o077)
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise ValueError('Run through the tray setup wizard as an administrator')
    uid = int(os.environ.get('SUDO_UID', '0'))
    account = pwd.getpwuid(uid)
    if uid < 1000 or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', account.pw_name):
        raise ValueError('A normal SSH audio account is required')
    # Snapshot all reviewed files before executing further installation steps.
    safe_dir(STATE, 0o700)
    lock = os.open(STATE / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fd = os.open(STATE / 'install.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    LOG = os.fdopen(fd, 'w')
    with tempfile.TemporaryDirectory(prefix='bundle-', dir=STATE) as snapshot:
        for path in SOURCE.iterdir():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_size > 1024 * 1024:
                raise ValueError('Invalid setup bundle')
            shutil.copyfile(path, Path(snapshot) / path.name, follow_symlinks=False)
        SOURCE = Path(snapshot)
        plan = module('setup_plan').validate_plan(json.loads(sys.argv[1]))
        probe = module('setup-probe').inventory()
        # A failed partial installation may be resumed only with the identical plan/account.
        identity = {'user': account.pw_name, 'plan': plan}
        state_file = STATE / 'state.json'
        state = json.loads(state_file.read_text()) if state_file.exists() else None
        if state and (state.get('identity') != identity or state.get('complete')):
            raise ValueError('Setup already completed or a different plan is pending; integrate manually')
        if not state and not probe['supported']:
            raise ValueError('; '.join(probe['reasons']))
        # Never bypass OS/architecture/account checks when resuming.
        if probe['architecture'] != 'aarch64' or 'Raspberry Pi' not in probe['model'] or not Path('/etc/os-release').read_text().count('VERSION_ID="13"'):
            raise ValueError('Unsupported receiver')
        home = Path(account.pw_dir)
        if home.is_symlink() or any(c.isspace() or c in '"%\\' for c in str(home)):
            raise ValueError('Unsupported audio home directory')
        for port in ([80, 443, 8443, 8080] if plan['web'] else []) + ([9090] if plan['cockpit'] else []):
            if not state:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                    listener.bind(('127.0.0.1', port))
        if plan['music']:
            device = Path('/dev/disk/by-uuid') / plan['usb_uuid']
            if not device.exists() or subprocess.check_output(['/usr/sbin/blkid', '-s', 'TYPE', '-o', 'value', str(device)], text=True).strip() != 'vfat':
                raise ValueError('Selected FAT32 USB drive is absent')
            transports = subprocess.check_output(['/usr/bin/lsblk', '-s', '-n', '-o', 'TRAN', str(device)], text=True).split()
            mounts = subprocess.check_output(['/usr/bin/lsblk', '-n', '-o', 'MOUNTPOINTS', str(device)], text=True).split()
            if 'usb' not in transports or any(p == '/' or p.startswith('/boot') for p in mounts):
                raise ValueError('Select a separate USB music drive, not boot or root storage')
        state = state or {'identity': identity, 'done': [], 'complete': False}
        # Root-private log is never streamed to clients; public events contain fixed labels only.
        def stage(name, action):
            if name in state['done']:
                return
            emit(name, 'Installing: ' + name)
            action()
            state['done'].append(name)
            write(state_file, json.dumps(state), 0o600)
        write(state_file, json.dumps(state), 0o600)
        usercmd = ['runuser', '-u', account.pw_name, '--', 'env', f'XDG_RUNTIME_DIR=/run/user/{uid}', f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus']
        def user(*args):
            run(*usercmd, *args)
        def userfile(name, destination, mode=0o644):
            # Execute file placement as the account: never follow user-controlled home paths as root.
            user('mkdir', '-p', str(destination.parent))
            with tempfile.NamedTemporaryFile(dir='/tmp', prefix='pi-audio-owned-', delete=False) as temp:
                temp.write((SOURCE / name).read_bytes())
                os.fchown(temp.fileno(), uid, account.pw_gid)
            try:
                user('install', '-m', oct(mode)[2:], temp.name, str(destination))
            finally:
                os.unlink(temp.name)
        def packages():
            run('apt-get', 'update')
            run('apt-get', 'install', '-y', '--no-install-recommends', 'sudo', 'ca-certificates', 'bluez', 'rfkill', 'python3-dbus', 'python3-gi', 'pipewire', 'pipewire-pulse', 'wireplumber', 'pipewire-bin', 'libspa-0.2-bluetooth', 'alsa-utils', 'avahi-daemon')
        stage('base packages', packages)
        def receiver():
            root = safe_dir('/usr/local/lib/pi-audio-tray')
            for source, target, mode in [('pi_helper.py', 'pi-helper', 0o755), ('pi_music.py', 'pi-music', 0o755), ('pi_upnp.py', 'pi_upnp.py', 0o644), ('soloist-launch.py', 'soloist-launch', 0o755), ('soloist-update.py', 'soloist-update', 0o755)]:
                install(source, root / target, mode)
            write('/etc/pi-audio-tray/soloist-user', account.pw_name + '\n')
            for name in ('soloist-update.service', 'soloist-update.timer'):
                install(name, '/etc/systemd/system/' + name)
            user('install', '-d', '-m', '700', str(home / '.config/soloist'))
            userfile('soloist.service', home / '.config/systemd/user/soloist.service')
            userfile('50-audio-receiver.conf', home / '.config/wireplumber/wireplumber.conf.d/50-audio-receiver.conf')
            run('usermod', '-aG', 'audio,bluetooth', account.pw_name)
            run('loginctl', 'enable-linger', account.pw_name)
            run('systemctl', 'start', f'user@{uid}.service')
            user('systemctl', '--user', 'daemon-reload')
            user('systemctl', '--user', 'enable', '--now', 'pipewire.socket', 'pipewire-pulse.socket', 'wireplumber.service')
            user('systemctl', '--user', 'enable', 'soloist.service')
            run('systemctl', 'enable', '--now', 'bluetooth.service', 'avahi-daemon.service')
            run('rfkill', 'unblock', 'bluetooth')
            write('/etc/systemd/system/pi-audio-bluetooth-privacy.service', '[Unit]\nDescription=Keep Bluetooth pairing disabled at startup\nRequires=bluetooth.service\nAfter=bluetooth.service\nPartOf=bluetooth.service\n[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/usr/bin/bluetoothctl power on\nExecStart=/usr/bin/bluetoothctl pairable off\nExecStart=/usr/bin/bluetoothctl discoverable off\n[Install]\nWantedBy=bluetooth.service\n')
            run('systemctl', 'daemon-reload')
            run('systemctl', 'enable', '--now', 'pi-audio-bluetooth-privacy.service')
            run('systemctl', 'enable', '--now', 'soloist-update.timer')
            run('systemctl', 'start', 'soloist-update.service')
        stage('audio receiver and Soloist', receiver)
        def permissions():
            rules = [f'{account.pw_name} ALL=(root) NOPASSWD: /usr/local/lib/pi-audio-tray/pi-helper pair', f'{account.pw_name} ALL=(root) NOPASSWD: /usr/local/lib/pi-audio-tray/pi-helper off', f'{account.pw_name} ALL=(root) NOPASSWD: /usr/bin/systemctl start soloist-update.service']
            if plan['shutdown']:
                rules.append(f'{account.pw_name} ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block poweroff')
            candidate = STATE / 'sudoers'
            write(candidate, '\n'.join(rules) + '\n', 0o440)
            run('visudo', '-cf', str(candidate))
            write('/etc/sudoers.d/pi-audio-tray', candidate.read_text(), 0o440)
        stage('restricted runtime permissions', permissions)
        if plan['updates']:
            def updates():
                run('apt-get', 'install', '-y', '--no-install-recommends', 'unattended-upgrades', 'needrestart')
                for name, dest in [('52pi-audio-upgrades', '/etc/apt/apt.conf.d/52pi-audio-upgrades'), ('needrestart.conf', '/etc/needrestart/conf.d/pi-audio.conf'), ('apt-daily-timer.conf', '/etc/systemd/system/apt-daily.timer.d/pi-audio.conf'), ('apt-upgrade-timer.conf', '/etc/systemd/system/apt-daily-upgrade.timer.d/pi-audio.conf'), ('reboot-if-needed.py', '/usr/local/lib/pi-audio-tray/reboot-if-needed'), ('pi-audio-reboot.service', '/etc/systemd/system/pi-audio-reboot.service'), ('pi-audio-reboot.timer', '/etc/systemd/system/pi-audio-reboot.timer')]:
                    install(name, dest, 0o755 if name.endswith('.py') else 0o644)
                run('systemctl', 'daemon-reload')
                run('systemctl', 'enable', '--now', 'apt-daily.timer', 'apt-daily-upgrade.timer', 'pi-audio-reboot.timer')
                run('systemctl', 'restart', 'apt-daily.timer', 'apt-daily-upgrade.timer')
            stage('system updates and conditional 05:30 reboot', updates)
        if plan['music']:
            stage('MPD and selected music services', lambda: run('/usr/bin/python3', '-I', str(SOURCE / 'install-music.py'), '--user', account.pw_name, '--uuid', plan['usb_uuid'], '--hostname', plan['hostname'], *([] if plan['web'] else ['--no-web'])))
        if plan['cockpit']:
            def cockpit():
                # Mask socket before package scripts can open a LAN listener.
                run('systemctl', 'mask', 'cockpit.socket')
                run('apt-get', 'install', '-y', '--no-install-recommends', 'cockpit-ws', 'cockpit-system')
                write('/etc/systemd/system/cockpit.socket.d/pi-audio.conf', '[Socket]\nListenStream=\nListenStream=127.0.0.1:9090\n')
                run('systemctl', 'daemon-reload')
                run('systemctl', 'unmask', 'cockpit.socket')
                run('systemctl', 'enable', '--now', 'cockpit.socket')
            stage('Cockpit (SSH tunnel only)', cockpit)
        if plan['privacy']:
            def privacy():
                fstab = Path('/etc/fstab')
                lines = fstab.read_text().splitlines()
                matches = [i for i, line in enumerate(lines) if line.strip() and not line.lstrip().startswith('#') and len(line.split()) >= 4 and line.split()[1] == '/proc']
                if len(matches) > 1:
                    raise ValueError('Multiple /proc entries require manual configuration')
                if not (STATE / 'fstab.before-privacy').exists():
                    write(STATE / 'fstab.before-privacy', fstab.read_text(), 0o600)
                if matches:
                    fields = lines[matches[0]].split()
                    if fields[2] != 'proc':
                        raise ValueError('Unsupported /proc mount')
                    options = [o for o in fields[3].split(',') if not o.startswith('hidepid=')]
                    fields[3] = ','.join([*options, 'hidepid=2'])
                    lines[matches[0]] = '\t'.join(fields)
                else:
                    lines.append('proc /proc proc defaults,hidepid=2 0 0')
                write(fstab, '\n'.join(lines) + '\n')
                run('mount', '-o', 'remount,hidepid=2', '/proc')
            stage('process privacy (hidepid=2)', privacy)
        emit('verify', 'Verifying service startup')
        run('systemctl', 'is-active', '--quiet', 'bluetooth.service')
        user('systemctl', '--user', 'is-active', '--quiet', 'pipewire.service', 'wireplumber.service')
        if plan['music']:
            run('systemctl', 'is-active', '--quiet', 'pi-audio-mpd.service')
        if plan['web']:
            run('systemctl', 'is-active', '--quiet', 'pi-audio-web.service', 'pi-audio-login.service', 'pi-audio-mympd.service')
        state['complete'] = True
        write(state_file, json.dumps(state), 0o600)
        emit('complete', 'Installed. Set your Soloist API key in Settings, then choose an audio output below.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        if LOG is not None:
            import traceback
            traceback.print_exc(file=LOG)
            LOG.flush()
        # APT and service diagnostics stay in a root-private log. Never echo arbitrary exception data.
        emit('error', 'Setup stopped. Check /var/lib/pi-audio-setup/install.log as administrator. A partial installation is preserved; retry only with the same choices.')
        sys.exit(1)
