#!/usr/bin/python3 -I
"""Read-only inventory, also rechecked by the administrator installer."""
import json
import os
from pathlib import Path
import platform
import pwd
import subprocess


def inventory():
    release = {}
    for line in Path('/etc/os-release').read_text().splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            release[key] = value.strip('"')
    model = Path('/proc/device-tree/model')
    model = model.read_text().rstrip('\0') if model.exists() else ''
    user = pwd.getpwuid(int(os.environ.get('SUDO_UID', os.getuid())))
    reasons = []
    if release.get('VERSION_ID') != '13' or release.get('ID') not in ('debian', 'raspbian'):
        reasons.append('Requires Debian / Raspberry Pi OS 13 Trixie')
    if platform.machine() != 'aarch64' or 'Raspberry Pi' not in model:
        reasons.append('Requires a 64-bit ARM Raspberry Pi')
    if user.pw_uid < 1000 or not Path(user.pw_dir).is_dir():
        reasons.append('Connect as a normal user with a home directory, not root')
    conflicts = [str(p) for p in (Path('/usr/local/lib/pi-audio-tray'),
        Path('/etc/pi-audio-tray'), Path('/etc/systemd/system/cockpit.socket'),
        Path('/etc/systemd/system/pi-audio-mpd.service'), Path('/etc/systemd/system/pi-audio-web.service'), Path(user.pw_dir) / '.config/soloist',
        Path(user.pw_dir) / '.config/wireplumber', Path(user.pw_dir) / '.config/pipewire',
        Path(user.pw_dir) / '.config/systemd/user/soloist.service') if p.exists() or p.is_symlink()]
    # Installed competitors are intentionally not migrated automatically.
    for package in ('raspotify', 'bluez-alsa-utils', 'mpd', 'mympd', 'nginx', 'cockpit-ws'):
        check = subprocess.run(['/usr/bin/dpkg-query', '-W', '-f=${Status}', package], capture_output=True, text=True, timeout=10)
        if check.returncode == 0 and check.stdout == 'install ok installed':
            conflicts.append(package)
    if conflicts:
        reasons.append('Existing receiver or web services found; use manual setup / upgrades')
    devices = subprocess.run(['/usr/bin/lsblk', '-J', '-o', 'NAME,TYPE,FSTYPE,UUID,TRAN,MOUNTPOINTS'], capture_output=True, text=True, timeout=10)
    disks = json.loads(devices.stdout) if devices.returncode == 0 else {'blockdevices': []}
    return {'supported': not reasons, 'reasons': reasons, 'model': model,
        'os': release.get('PRETTY_NAME', ''), 'architecture': platform.machine(),
        'user': user.pw_name, 'uid': user.pw_uid, 'hostname': platform.node(),
        'conflicts': conflicts, 'disks': disks}


if __name__ == '__main__':
    print(json.dumps(inventory()))
