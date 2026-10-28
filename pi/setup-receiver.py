#!/usr/bin/python3 -I
"""Administrator-only fresh installation or additive maintenance; no credentials in arguments."""
import fcntl
import importlib.util
import ipaddress
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
    for p in (path, *path.parents):
        if p.exists() and (p.stat().st_uid != 0 or p.stat().st_mode & 0o022):
            raise ValueError('Root installation paths must be administrator-owned')
    missing = []
    parent = path
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        requested = mode if directory == path and mode is not None else 0o755
        directory.mkdir(mode=requested)
        # A private installer umask must not make public unit/binary directories
        # inaccessible. Existing private ancestors are deliberately untouched.
        os.chmod(directory, requested)
    if mode is not None:
        os.chmod(path, mode)
    return path


def public_dir(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Unsupported public installation directory')
    roots = (Path('/usr/local/lib/pi-audio-tray'), Path('/usr/local/lib/systemd'))
    root = next((base for base in roots if path.is_relative_to(base)), None)
    if root is None:
        raise ValueError('Unsupported public installation directory')
    for directory in reversed((path, *[p for p in path.parents if p.is_relative_to(root)])):
        safe_dir(directory, 0o755)
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
        request = json.loads(sys.argv[1])
        maintenance = isinstance(request, dict) and request.get('operation') == 'maintain'
        if maintenance:
            request = module('setup_plan').validate_maintenance(request)
            plan = request['options']
            selected = set(request['components'])
            owner = Path('/etc/pi-audio-tray/soloist-user')
            if owner.is_symlink() or not owner.is_file() or owner.stat().st_uid != 0 or owner.stat().st_mode & 0o022 or owner.read_text().strip() != account.pw_name:
                raise ValueError('Maintenance requires an existing Pi Audio receiver owned by this audio account; use fresh setup otherwise')
            original_state = STATE / 'state.json'
            if original_state.exists() and not json.loads(original_state.read_text()).get('complete'):
                raise ValueError('Finish the original interrupted fresh installation before maintenance')
        else:
            plan = module('setup_plan').validate_plan(request)
            selected = {'receiver', 'shutdown'}
        probe = module('setup-probe').inventory()
        # A failed partial installation may be resumed only with the identical plan/account.
        identity = {'user': account.pw_name, 'plan': request if maintenance else plan}
        state_file = STATE / ('maintenance.json' if maintenance else 'state.json')
        state = json.loads(state_file.read_text()) if state_file.exists() else None
        if maintenance and state and state.get('complete'):
            state = None  # Explicit repair may be repeated; never skip a completed prior repair.
        if maintenance and state and state.get('identity') != identity:
            previous = Path(tempfile.mkdtemp(prefix='previous-maintenance-', dir=STATE))
            write(previous / 'state.json', json.dumps(state), 0o600)
            state = None  # Additive maintenance can change its plan after a failed run.
        if state and (state.get('identity') != identity or state.get('complete')):
            raise ValueError('Setup already completed or a different plan is pending; integrate manually')
        if not maintenance and not state and not probe['supported']:
            raise ValueError('; '.join(probe['reasons']))
        # Never bypass OS/architecture/account checks when resuming.
        release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
        if probe['userspace_architecture'] != 'arm64' or probe['python_bits'] != 64 or probe['architecture'] != 'aarch64' or 'Raspberry Pi' not in probe['model'] or release.get('VERSION_ID', '').strip('"') != '13' or release.get('ID', '').strip('"') not in ('debian', 'raspbian'):
            raise ValueError('Unsupported receiver')
        home = Path(account.pw_dir)
        if home.is_symlink() or any(c.isspace() or c in '"%\\' for c in str(home)):
            raise ValueError('Unsupported audio home directory')
        if maintenance:
            # Only named app-owned configuration paths are replaced. Preserve
            # a root-private snapshot and require existing credential material.
            safe_dir('/etc/pi-audio-tray')
            safe_dir('/usr/local/lib/pi-audio-tray')
            cockpit_owned = any(Path('/etc/systemd/system/cockpit.socket.d/' + name).is_file() for name in ('pi-audio.conf', 'pi-audio-local.conf'))
            if plan['cockpit'] and not cockpit_owned:
                check = subprocess.run(['/usr/bin/dpkg-query', '-W', '-f=${Status}', 'cockpit-ws'], capture_output=True, text=True, timeout=10)
                if check.returncode == 0 and check.stdout == 'install ok installed':
                    raise ValueError('Existing unmanaged Cockpit requires manual integration')
            if plan['web'] and Path('/etc/systemd/system/pi-audio-web.service').exists():
                for relative in ('htpasswd', 'tls/ca.pem', 'tls/ca.key', 'tls/server.pem', 'tls/server.key', 'tls/server.csr', 'tls/extensions'):
                    path = Path('/etc/pi-audio-tray/music') / relative
                    if not path.is_file() or path.is_symlink():
                        raise ValueError('Restore missing web credentials/certificates from backup before repair')
                try:
                    ipaddress.ip_address(plan['hostname'])
                    check_name = '-verify_ip'
                except ValueError:
                    check_name = '-verify_hostname'
                run('openssl', 'verify', '-no_check_time', check_name, plan['hostname'], '-CAfile', '/etc/pi-audio-tray/music/tls/ca.pem', '/etc/pi-audio-tray/music/tls/server.pem')
            if plan['music'] and Path('/etc/systemd/system/pi-audio-mpd.service').is_file():
                unit_text = Path('/etc/systemd/system/pi-audio-mpd.service').read_text()
                if not re.search(r'^User=' + re.escape(account.pw_name) + r'$', unit_text, re.MULTILINE):
                    raise ValueError('Existing MPD service belongs to another audio account')
            if plan['music']:
                fstab = Path('/etc/fstab').read_text()
                matches = re.findall(r'^UUID=([^\s]+)\s+/srv/pi-audio-music\s+', fstab, re.MULTILINE)
                if matches and (len(matches) != 1 or matches[0].casefold() != plan['usb_uuid'].casefold()):
                    raise ValueError('Repair must use the existing USB filesystem UUID')
            backup = Path(tempfile.mkdtemp(prefix='configuration-backup-', dir=STATE))
            paths = [Path('/etc/fstab'), *Path('/etc/systemd/system').glob('pi-audio-*'),
                     *Path('/etc/systemd/system').glob('soloist-update.*'),
                     Path('/etc/systemd/system/cockpit.socket.d/pi-audio.conf'),
                     Path('/etc/sudoers.d/pi-audio-tray'), Path('/etc/pi-audio-tray/music/mpd.conf'),
                     Path('/etc/pi-audio-tray/music/nginx.conf')]
            for index, path in enumerate(paths):
                if path.is_file() and not path.is_symlink():
                    shutil.copyfile(path, backup / (str(index) + '-' + path.name), follow_symlinks=False)
        for port in ([80, 443, 8443, 8080] if plan['web'] else []) + ([9090] if plan['cockpit'] else []):
            owned_port = maintenance and ((port == 9090 and cockpit_owned) or
                         (port != 9090 and Path('/etc/systemd/system/pi-audio-web.service').exists()))
            if not state and not owned_port:
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
            if maintenance:
                user_backup = home / '.local/state/pi-audio-tray/setup-backups' / backup.name
                user('install', '-d', '-m', '700', str(user_backup))
                exists = subprocess.run([*usercmd, '/usr/bin/test', '-f', str(destination)], stdin=subprocess.DEVNULL, stdout=LOG, stderr=LOG, timeout=10).returncode == 0
                if exists:
                    user('/usr/bin/cp', '--no-clobber', '--', str(destination), str(user_backup / name))
            with tempfile.NamedTemporaryFile(dir='/tmp', prefix='pi-audio-owned-', delete=False) as temp:
                temp.write((SOURCE / name).read_bytes())
                os.fchown(temp.fileno(), uid, account.pw_gid)
            try:
                user('install', '-m', oct(mode)[2:], temp.name, str(destination))
            finally:
                os.unlink(temp.name)
        def packages():
            run('apt-get', 'update')
            run('apt-get', 'install', '-y', '--reinstall', '--no-remove', '--no-install-recommends', 'sudo', 'ca-certificates', 'bluez', 'rfkill', 'python3-dbus', 'python3-gi', 'pipewire', 'pipewire-pulse', 'wireplumber', 'pipewire-bin', 'libspa-0.2-bluetooth', 'alsa-utils', 'avahi-daemon')
        if 'receiver' in selected:
            stage('base packages', packages)
        def receiver():
            policy_enabled = False
            if maintenance and Path('/usr/local/lib/systemd/user/pi-audio-policy.service').is_file():
                policy_enabled = subprocess.run([*usercmd, '/usr/bin/systemctl', '--user', 'is-enabled', '--quiet', 'pi-audio-policy.service'], stdin=subprocess.DEVNULL, stdout=LOG, stderr=LOG, timeout=10).returncode == 0
                user('systemctl', '--user', 'stop', 'pi-audio-policy.service')
            root = safe_dir('/usr/local/lib/pi-audio-tray')
            for source, target, mode in [('pi_helper.py', 'pi-helper', 0o755), ('pi_music.py', 'pi-music', 0o755), ('pi_upnp.py', 'pi_upnp.py', 0o644), ('pi_audio.py', 'pi-audio', 0o755), ('pi_policy.py', 'pi_policy.py', 0o755), ('soloist-launch.py', 'soloist-launch', 0o755), ('soloist-update.py', 'soloist-update', 0o755)]:
                install(source, root / target, mode)
            write('/etc/pi-audio-tray/soloist-user', account.pw_name + '\n')
            for name in ('soloist-update.service', 'soloist-update.timer'):
                install(name, '/etc/systemd/system/' + name)
            user('install', '-d', '-m', '700', str(home / '.config/soloist'))
            userfile('soloist.service', home / '.config/systemd/user/soloist.service')
            public_dir('/usr/local/lib/systemd/user')
            install('pi-audio-policy.service', '/usr/local/lib/systemd/user/pi-audio-policy.service')
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
            if maintenance:
                user('systemctl', '--user', 'restart', 'pipewire.service', 'pipewire-pulse.service', 'wireplumber.service')
                has_key = subprocess.run([*usercmd, '/usr/bin/test', '-s', str(home / '.config/soloist/api-key')], stdin=subprocess.DEVNULL, stdout=LOG, stderr=LOG, timeout=10).returncode == 0
                if has_key:
                    user('systemctl', '--user', 'restart', 'soloist.service')
                mpd_unit = Path('/etc/systemd/system/pi-audio-mpd.service')
                if mpd_unit.is_file() and re.search(r'^User=' + re.escape(account.pw_name) + r'$', mpd_unit.read_text(), re.MULTILINE):
                    run('systemctl', 'try-restart', 'pi-audio-mpd.service')
                if policy_enabled:
                    user('systemctl', '--user', 'start', 'pi-audio-policy.service')
        if 'receiver' in selected:
            stage('audio receiver and Soloist', receiver)
        def permissions():
            rules = [f'{account.pw_name} ALL=(root) NOPASSWD: /usr/local/lib/pi-audio-tray/pi-helper pair', f'{account.pw_name} ALL=(root) NOPASSWD: /usr/local/lib/pi-audio-tray/pi-helper off', f'{account.pw_name} ALL=(root) NOPASSWD: /usr/bin/systemctl start soloist-update.service']
            if plan['shutdown']:
                rules.append(f'{account.pw_name} ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block poweroff')
            candidate = STATE / 'sudoers'
            previous = Path('/etc/sudoers.d/pi-audio-tray')
            existing = previous.read_text() if maintenance and previous.is_file() and not previous.is_symlink() else ''
            write(candidate, existing.rstrip() + ('\n' if existing else '') + '\n'.join(rule for rule in rules if rule not in existing.splitlines()) + '\n', 0o440)
            run('visudo', '-cf', str(candidate))
            write('/etc/sudoers.d/pi-audio-tray', candidate.read_text(), 0o440)
        if 'receiver' in selected or 'shutdown' in selected:
            stage('restricted runtime permissions', permissions)
        if plan['updates']:
            def updates():
                run('apt-get', 'install', '-y', '--reinstall', '--no-remove', '--no-install-recommends', 'unattended-upgrades', 'needrestart')
                if maintenance:
                    install('soloist-update.py', '/usr/local/lib/pi-audio-tray/soloist-update', 0o755)
                    for name in ('soloist-update.service', 'soloist-update.timer'):
                        install(name, '/etc/systemd/system/' + name)
                for name, dest in [('52pi-audio-upgrades', '/etc/apt/apt.conf.d/52pi-audio-upgrades'), ('needrestart.conf', '/etc/needrestart/conf.d/pi-audio.conf'), ('apt-daily-timer.conf', '/etc/systemd/system/apt-daily.timer.d/pi-audio.conf'), ('apt-upgrade-timer.conf', '/etc/systemd/system/apt-daily-upgrade.timer.d/pi-audio.conf'), ('reboot-if-needed.py', '/usr/local/lib/pi-audio-tray/reboot-if-needed'), ('pi-audio-reboot.service', '/etc/systemd/system/pi-audio-reboot.service'), ('pi-audio-reboot.timer', '/etc/systemd/system/pi-audio-reboot.timer')]:
                    install(name, dest, 0o755 if name.endswith('.py') else 0o644)
                install('system-updates.py', '/usr/local/lib/pi-audio-tray/pi-system-updates', 0o755)
                rules = ''.join(f'{account.pw_name} ALL=(root) NOPASSWD: /usr/local/lib/pi-audio-tray/pi-system-updates {operation}\n' for operation in ('check', 'install'))
                candidate = STATE / 'update-sudoers'
                write(candidate, rules, 0o440)
                run('visudo', '-cf', str(candidate))
                write('/etc/sudoers.d/pi-audio-updates', rules, 0o440)
                run('systemctl', 'daemon-reload')

            stage('system updates and conditional 05:30 reboot', updates)
        if plan['music']:
            stage('MPD and selected music services', lambda: run('/usr/bin/python3', '-I', str(SOURCE / 'install-music.py'), '--user', account.pw_name, '--uuid', plan['usb_uuid'], '--hostname', plan['hostname'], *([] if plan['web'] else ['--no-web'])))
        if plan['cockpit']:
            def cockpit():
                # Mask socket before package scripts can open a LAN listener.
                run('systemctl', 'mask', 'cockpit.socket')
                run('apt-get', 'install', '-y', '--reinstall', '--no-remove', '--no-install-recommends', 'cockpit-ws', 'cockpit-system')
                write('/etc/systemd/system/cockpit.socket.d/pi-audio.conf', '[Socket]\nListenStream=\nListenStream=127.0.0.1:9090\n')
                run('systemctl', 'daemon-reload')
                run('systemctl', 'unmask', 'cockpit.socket')
                run('systemctl', 'enable', '--now', 'cockpit.socket')
            stage('Cockpit (SSH tunnel only)', cockpit)
            if maintenance:
                run('systemctl', 'restart', 'cockpit.socket')
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
        if 'receiver' in selected:
            run('systemctl', 'is-active', '--quiet', 'bluetooth.service')
            user('systemctl', '--user', 'is-active', '--quiet', 'pipewire.service', 'wireplumber.service')
        if plan['music']:
            run('systemctl', 'is-active', '--quiet', 'pi-audio-mpd.service')
            if maintenance and not plan['web']:
                run('systemctl', 'restart', 'pi-audio-mpd.service')
        if plan['web']:
            run('systemctl', 'is-active', '--quiet', 'pi-audio-web.service', 'pi-audio-login.service', 'pi-audio-mympd.service')
        # Arm automatic maintenance only after every selected component verifies.
        if plan['updates']:
            run('systemctl', 'enable', '--now', 'apt-daily.timer', 'apt-daily-upgrade.timer', 'pi-audio-reboot.timer')
            if maintenance:
                run('systemctl', 'enable', '--now', 'soloist-update.timer')
            run('systemctl', 'restart', 'apt-daily.timer', 'apt-daily-upgrade.timer')
        state['complete'] = True
        write(state_file, json.dumps(state), 0o600)
        emit('complete', 'Selected components installed/repaired. Existing credentials and media were preserved.' if maintenance else 'Installed. Set your Soloist API key in Settings, then choose an audio output below.')


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
