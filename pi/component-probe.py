#!/usr/bin/python3 -I
"""On-demand dependency/configuration inventory. Never read keys or passwords."""
import json
import os
from pathlib import Path
import platform
import pwd
import re
import subprocess

PACKAGES = ('pipewire', 'pipewire-pulse', 'wireplumber', 'bluez', 'python3-dbus',
            'python3-gi', 'mpd', 'mpc', 'mympd', 'nginx', 'unattended-upgrades',
            'needrestart', 'cockpit-ws', 'cockpit-system', 'shairport-sync')
STATES = {'active', 'inactive', 'failed', 'activating', 'deactivating', 'unknown'}


def run(*args):
    result = subprocess.run(args, capture_output=True, text=True,
                            stdin=subprocess.DEVNULL, timeout=12, check=False)
    if len(result.stdout) + len(result.stderr) > 64 * 1024:
        raise ValueError('Inventory output exceeds size limit')
    return result


def present(path):
    try:
        return Path(path).is_file()
    except PermissionError:
        return None


def all_present(paths):
    values = [present(path) for path in paths]
    return False if False in values else (None if None in values else True)


def services(names, user=False):
    if not names:
        return {}
    result = run('/usr/bin/systemctl', *(['--user'] if user else []), 'show',
                 '--property=Id,ActiveState,LoadState', '--', *names)
    values = {}
    for block in result.stdout.strip().split('\n\n'):
        fields = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        if fields.get('Id') in names:
            values[fields['Id']] = fields.get('ActiveState') if fields.get('ActiveState') in STATES else 'unknown'
    return {name: values.get(name, 'unknown') for name in names}


def inventory():
    home = Path.home()
    account = pwd.getpwuid(os.getuid()).pw_name
    owner = Path('/etc/pi-audio-tray/soloist-user')
    managed = bool(present(owner) and not owner.is_symlink() and owner.stat().st_uid == 0 and
                   not owner.stat().st_mode & 0o022 and owner.read_text().strip() == account)
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    model = Path('/proc/device-tree/model')
    model = model.read_text().strip('\0') if model.exists() else ''
    arch = run('/usr/bin/dpkg', '--print-architecture').stdout.strip()
    supported = arch == 'arm64' and platform.machine() == 'aarch64' and 'Raspberry Pi' in model and release.get('VERSION_ID', '').strip('"') == '13' and release.get('ID', '').strip('"') in ('debian', 'raspbian')
    package_result = run('/usr/bin/dpkg-query', '-W', '-f=${binary:Package}\t${Status}\t${Version}\n', *PACKAGES)
    packages = {}
    for line in package_result.stdout.splitlines():
        fields = line.split('\t')
        if len(fields) == 3 and fields[1] == 'install ok installed':
            name = fields[0].split(':')[0]
            if name in PACKAGES:
                packages[name] = fields[2][:100]
    system = services(['bluetooth.service', 'soloist-update.timer', 'apt-daily.timer',
                       'apt-daily-upgrade.timer', 'pi-audio-reboot.timer', 'pi-audio-mpd.service',
                       'pi-audio-mympd.service', 'pi-audio-web.service', 'pi-audio-login.service',
                       'pi-audio-music-tls.timer', 'cockpit.socket', 'pi-audio-nqptp.service'])
    user = services(['pipewire.service', 'wireplumber.service', 'soloist.service', 'pi-audio-airplay.service', 'pi-audio-dsp.service'], user=True)
    rows = []
    def row(identifier, required=(), files=(), units=(), user_units=(), action=None, owned=None, extra='', artifacts=()):
        installed = all(name in packages for name in required) if required else all_present(artifacts or files)
        config = all_present(files)
        states = {name: system.get(name, 'unknown') for name in units}
        states.update({name: user.get(name, 'unknown') for name in user_units})
        running = (False if any(value not in ('active', 'unknown') for value in states.values()) else
                   None if not states or 'unknown' in states.values() else True)
        detail = '; '.join([*(name + '=' + packages.get(name, 'missing') for name in required),
                            *(name + '=' + value for name, value in states.items()), *([extra] if extra else [])])
        rows.append({'id': identifier, 'action': action or identifier, 'installed': installed,
                     'configured': config, 'running': running,
                     'managed': managed if owned is None else owned, 'details': detail[:2048]})
    base = '/usr/local/lib/pi-audio-tray/'
    row('spotify', files=(home / '.config/systemd/user/soloist.service', home / '.config/soloist/api-key'), artifacts=('/usr/local/bin/soloist',), user_units=('soloist.service',), action='receiver', extra='Service state does not prove successful Spotify playback')
    row('bluetooth', ('bluez', 'pipewire', 'wireplumber'), (home / '.config/wireplumber/wireplumber.conf.d/50-audio-receiver.conf',), ('bluetooth.service',), ('pipewire.service', 'wireplumber.service'), action='receiver')
    row('helpers', ('python3-dbus', 'python3-gi'), (base + 'pi-helper', base + 'pi-audio', base + 'pi_policy.py', '/usr/local/lib/systemd/user/pi-audio-policy.service'), action='receiver', extra='Includes optional source-policy controls; its daemon is disabled by default in mixing mode')
    row('music', ('mpd', 'mpc'), ('/etc/pi-audio-tray/music/mpd.conf', '/etc/systemd/system/pi-audio-mpd.service', base + 'pi-music', base + 'pi_upnp.py'), ('pi-audio-mpd.service',))
    row('web', ('mympd', 'nginx'), ('/etc/pi-audio-tray/music/nginx.conf', base + 'web-login.py'), ('pi-audio-web.service', 'pi-audio-login.service', 'pi-audio-mympd.service', 'pi-audio-music-tls.timer'), extra='Private credentials/certificates are rechecked by the administrator installer; not read by this inventory')
    row('updates', ('unattended-upgrades', 'needrestart'), ('/etc/apt/apt.conf.d/52pi-audio-upgrades', '/etc/systemd/system/pi-audio-reboot.timer', base + 'pi-system-updates'), ('apt-daily.timer', 'apt-daily-upgrade.timer', 'pi-audio-reboot.timer', 'soloist-update.timer'))
    cockpit_paths = ['/etc/systemd/system/cockpit.socket.d/' + name for name in ('pi-audio.conf', 'pi-audio-local.conf')]
    cockpit_listen = run('/usr/bin/systemctl', 'show', 'cockpit.socket', '--property=Listen').stdout.strip()
    cockpit_configured = cockpit_listen == 'Listen=127.0.0.1:9090 (Stream)'
    cockpit_owned = managed and cockpit_configured and any(present(path) for path in cockpit_paths)
    row('cockpit', ('cockpit-ws', 'cockpit-system'), (), ('cockpit.socket',), owned=cockpit_owned or (managed and 'cockpit-ws' not in packages), extra='Loopback-only SSH tunnel configuration required; ' + cockpit_listen)
    rows[-1]['configured'] = cockpit_configured
    row('privacy', files=('/etc/fstab',), extra='hidepid=2 configured' if 'hidepid=2' in Path('/etc/fstab').read_text() else 'hidepid=2 is not configured')
    rows[-1]['configured'] = 'hidepid=2' in Path('/etc/fstab').read_text()
    mounted = Path('/proc/mounts').read_text()
    rows[-1]['running'] = any(len(fields) >= 4 and fields[1] == '/proc' and any(opt in ('hidepid=2', 'hidepid=invisible') for opt in fields[3].split(',')) for fields in (line.split() for line in mounted.splitlines()))
    row('shutdown', files=('/etc/sudoers.d/pi-audio-tray',), extra='Private sudo rules are verified by the administrator installer, not read by this inventory')
    row('airplay', files=('/usr/local/lib/systemd/user/pi-audio-airplay.service',), user_units=('pi-audio-airplay.service',), action='audio', extra='Use Audio integrations for classic / AirPlay 2 installation and timing status')
    airplay_binary = present('/usr/bin/shairport-sync') or present('/usr/local/lib/pi-audio-tray/airplay2/shairport-5.6.2-nqptp-1.2.9/shairport-sync')
    rows[-1]['installed'] = bool(rows[-1]['installed'] or airplay_binary)
    if airplay_binary and not rows[-1]['configured']:
        rows[-1]['managed'] = False
        rows[-1]['details'] += '; existing unowned binary requires manual integration'
    row('dsp', files=(base + 'pi-dsp', base + 'dsp_config.py', '/usr/local/lib/systemd/user/pi-audio-dsp.service'), user_units=('pi-audio-dsp.service',), action='dsp', extra='Optional; bypass is the default. Use Equalizer / DSP for settings and installation')
    fstab = Path('/etc/fstab').read_text()
    match = re.search(r'^UUID=([A-Fa-f0-9]{4}-[A-Fa-f0-9]{4})\s+/srv/pi-audio-music\s+', fstab, re.MULTILINE)
    hostname = ''
    nginx = Path('/etc/pi-audio-tray/music/nginx.conf')
    if present(nginx):
        match_host = re.search(r'\bserver_name\s+([A-Za-z0-9.-]{1,253});', nginx.read_text())
        hostname = match_host.group(1) if match_host else ''
    disks = run('/usr/bin/lsblk', '-J', '-o', 'NAME,TYPE,FSTYPE,UUID,TRAN,MOUNTPOINTS')
    return {'protocol': 1, 'managed': managed, 'supported': supported, 'rows': rows,
            'usb_uuid': match.group(1) if match else '', 'hostname': hostname,
            'model': model[:200], 'architecture': arch[:32],
            'os': release.get('PRETTY_NAME', '').strip('"')[:200],
            'disks': json.loads(disks.stdout) if disks.returncode == 0 else {'blockdevices': []}}


if __name__ == '__main__':
    print(json.dumps(inventory()))
