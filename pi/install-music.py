#!/usr/bin/python3 -I
"""Opt-in Debian 13 local music installer. Run as root after reviewing this file.

Requires --user AUDIO_USER --uuid USB_UUID --hostname PI.local.
Leaves distribution MPD/myMPD/nginx services masked: dedicated units are used.
Does not format storage, start playback, or change the existing audio sink.
"""
import argparse
import grp
import ipaddress
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import subprocess
import tempfile
import urllib.request

FINGERPRINT = 'A37AADC40A1CC6BEFB75372FAA09B8CCE895BD7D'
REPO = 'https://download.opensuse.org/repositories/home:/jcorporation/Debian_13/'


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def write(path, text, mode=0o644, uid=0, gid=0):
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'w') as stream:
        os.fchmod(stream.fileno(), mode)
        os.fchown(stream.fileno(), uid, gid)
        stream.write(text)


def directory(path, mode=0o755, uid=0, gid=0):
    path = Path(path)
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('Refusing a symlink directory or ancestor')
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, mode)
    os.chown(path, uid, gid)


def main():
    os.environ['PATH'] = '/usr/sbin:/usr/bin:/sbin:/bin'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', required=True)
    parser.add_argument('--uuid', required=True)
    parser.add_argument('--hostname', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Administrator authentication is required')
    if not re.fullmatch(r'[A-Za-z0-9-]{4,64}', args.uuid):
        parser.error('Invalid filesystem UUID')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', args.hostname):
        parser.error('Invalid DNS hostname')
    user = pwd.getpwnam(args.user)
    if user.pw_uid < 1000 or not re.fullmatch(r'[a-z_][a-z0-9_-]*', user.pw_name):
        parser.error('Select a normal audio user')
    if 'VERSION_ID="13"' not in Path('/etc/os-release').read_text():
        parser.error('This installer currently supports Debian/Raspberry Pi OS 13 only')
    device = Path('/dev/disk/by-uuid') / args.uuid
    if not device.exists():
        parser.error('Selected USB filesystem is absent')
    kind = subprocess.check_output(['blkid', '-s', 'TYPE', '-o', 'value', str(device)], text=True).strip()
    if kind != 'vfat':
        parser.error('This installer currently supports FAT32 USB music drives only')
    home, uid, gid = Path(user.pw_dir), user.pw_uid, user.pw_gid
    if home.is_symlink() or any(c.isspace() or c in '"%' for c in str(home)):
        parser.error('Unsupported audio home path')
    for unit in ('mpd.service', 'mpd.socket', 'mympd.service', 'nginx.service'):
        if subprocess.run(['systemctl', 'is-active', '--quiet', unit], check=False).returncode == 0:
            parser.error('Distribution music/web services already active; integrate manually instead')
    # Prevent distribution postinst scripts from exposing default listeners.
    for unit in ('mpd.service', 'mpd.socket', 'mympd.service', 'nginx.service'):
        run('systemctl', 'mask', '--now', unit)
    run('apt-get', 'update')
    run('apt-get', 'install', '-y', '--no-install-recommends', 'mpd', 'mpc', 'nginx', 'apache2-utils', 'openssl', 'gnupg', 'ca-certificates')
    with tempfile.TemporaryDirectory(prefix='pi-music-key-') as temp:
        key = Path(temp) / 'key.asc'
        key.write_bytes(urllib.request.urlopen(REPO + 'Release.key', timeout=30).read(65536))
        info = subprocess.check_output(['gpg', '--batch', '--show-keys', '--with-colons', str(key)], text=True)
        fingerprints = [line.split(':')[9] for line in info.splitlines() if line.startswith('fpr:')]
        if not fingerprints or fingerprints[0] != FINGERPRINT:
            raise ValueError('myMPD repository signing key fingerprint mismatch')
        run('gpg', '--batch', '--yes', '--dearmor', '--output', '/usr/share/keyrings/pi-audio-mympd.gpg', str(key))
    os.chmod('/usr/share/keyrings/pi-audio-mympd.gpg', 0o644)
    write('/etc/apt/sources.list.d/pi-audio-mympd.list', f'deb [signed-by=/usr/share/keyrings/pi-audio-mympd.gpg] {REPO} ./\n')
    write('/etc/apt/preferences.d/pi-audio-mympd', 'Package: mympd\nPin: origin "download.opensuse.org"\nPin-Priority: 500\n\nPackage: *\nPin: origin "download.opensuse.org"\nPin-Priority: -1\n')
    run('apt-get', 'update')
    run('apt-get', 'install', '-y', '--no-install-recommends', 'mympd')
    write('/etc/apt/apt.conf.d/53pi-audio-mympd', 'Unattended-Upgrade::Origins-Pattern { "origin=obs://build.opensuse.org/home:jcorporation/Debian_13,codename=Debian_13"; };\n')
    try:
        music_gid = grp.getgrnam('pi-audio-music').gr_gid
    except KeyError:
        run('groupadd', '--system', 'pi-audio-music')
        music_gid = grp.getgrnam('pi-audio-music').gr_gid
    try:
        pwd.getpwnam('pi-audio-web')
    except KeyError:
        run('useradd', '--system', '--gid', 'pi-audio-music', '--home-dir', '/nonexistent', '--shell', '/usr/sbin/nologin', 'pi-audio-web')
    mount = '/srv/pi-audio-music'
    if Path(mount).is_symlink():
        raise ValueError('Refusing a symlink music mount')
    if not os.path.ismount(mount):
        directory(mount)
    fstab = Path('/etc/fstab')
    marker = '# Pi Audio Tray read-only music USB'
    original = fstab.read_text()
    if marker in original and ('UUID=' + args.uuid + ' ' + mount + ' ') not in original:
        raise ValueError('Music mount already configured for a different UUID')
    if marker not in original:
        shutil.copy2(fstab, '/etc/fstab.pi-audio-backup')
        with fstab.open('a') as stream:
            stream.write(f'\n{marker}\nUUID={args.uuid} {mount} vfat ro,nosuid,nodev,noexec,utf8,uid={uid},gid={music_gid},fmask=0137,dmask=0027,nofail,x-systemd.automount,x-systemd.device-timeout=5s 0 0\n')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'start', 'srv-pi\\x2daudio\\x2dmusic.automount')
    if not Path(mount + '/Music').is_dir():
        raise RuntimeError('Expected Music directory is absent on the selected stick')
    mounted_uuid = subprocess.check_output(['findmnt', '-no', 'UUID', '--target', mount + '/Music'], text=True).strip()
    if mounted_uuid.casefold() != args.uuid.casefold():
        raise RuntimeError('Mounted music UUID differs from the selected stick')
    conf = Path('/etc/pi-audio-tray/music')
    directory(conf)
    state = home / '.local/state/pi-audio-mpd'
    for path in (home / '.local', home / '.local/state', state, state / 'playlists', home / '.config', home / '.config/pi-audio-tray'):
        directory(path, 0o700, uid, gid)
    directory('/var/lib/pi-audio-playlists', 0o2770, uid, music_gid)
    write(conf / 'mpd.conf', f'''music_directory "{mount}/Music"
playlist_directory "/var/lib/pi-audio-playlists"
db_file "{state}/database"
state_file "{state}/state"
sticker_file "{state}/stickers.sql"
bind_to_address "/run/pi-audio-mpd/socket"
auto_update "no"
restore_paused "yes"
max_playlist_length "5000"
follow_outside_symlinks "no"
follow_inside_symlinks "no"
audio_output {{
 type "pipewire"
 name "Pi Audio USB music"
 mixer_type "software"
}}
''')
    common = 'NoNewPrivileges=yes\nRestart=on-failure\nRestartSec=5\n'
    write('/etc/systemd/system/pi-audio-mpd.service', f'''[Unit]
Description=Pi Audio local USB music player
After=user@{uid}.service
Wants=user@{uid}.service
[Service]
Type=notify
User={user.pw_name}
Group=pi-audio-music
Environment=XDG_RUNTIME_DIR=/run/user/{uid}
ExecStart=/usr/bin/mpd --no-daemon /etc/pi-audio-tray/music/mpd.conf
ExecStartPost=/bin/chmod 0660 /run/pi-audio-mpd/socket
RuntimeDirectory=pi-audio-mpd
RuntimeDirectoryMode=0750
UMask=0007
ProtectSystem=strict
ReadWritePaths={state} /var/lib/pi-audio-playlists
PrivateTmp=yes
''' + common + '[Install]\nWantedBy=multi-user.target\n')
    write('/etc/systemd/system/pi-audio-mympd.service', '''[Unit]
Description=Pi Audio isolated web music controller
After=pi-audio-mpd.service
Requires=pi-audio-mpd.service
[Service]
User=pi-audio-web
Group=pi-audio-music
ExecStart=/usr/bin/mympd -w /var/lib/pi-audio-mympd -a /var/cache/pi-audio-mympd
Environment=MYMPD_HTTP=true MYMPD_SSL=false MYMPD_HTTP_HOST=127.0.0.1 MYMPD_HTTP_PORT=8080
Environment=MYMPD_SCRIPTS_EXTERNAL=false MYMPD_SCRIPTACL=-0.0.0.0/0 MYMPD_WEBRADIODB=false
Environment=MPD_HOST=/run/pi-audio-mpd/socket
StateDirectory=pi-audio-mympd
StateDirectoryMode=0700
CacheDirectory=pi-audio-mympd
CacheDirectoryMode=0700
UMask=0077
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/pi-audio-playlists
PrivateTmp=yes
PrivateDevices=yes
RestrictSUIDSGID=yes
''' + common + '[Install]\nWantedBy=multi-user.target\n')
    directory('/etc/systemd/system/pi-audio-mpd.service.d')
    write('/etc/systemd/system/pi-audio-mpd.service.d/volume.conf', '[Service]\nExecStartPost=/usr/bin/mpc -h /run/pi-audio-mpd/socket volume 100\n')
    # Dedicated CA; root owns all TLS material. No audio-user-writable root inputs.
    ssl = conf / 'tls'
    directory(ssl, 0o700)
    if not (ssl / 'server.pem').exists():
        run('openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-nodes', '-days', '3650', '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:0', '-addext', 'keyUsage=critical,keyCertSign,cRLSign', '-subj', '/CN=Pi Audio local CA', '-keyout', str(ssl / 'ca.key'), '-out', str(ssl / 'ca.pem'), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        run('openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=Pi Audio music', '-keyout', str(ssl / 'server.key'), '-out', str(ssl / 'server.csr'), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ips = []
        for value in subprocess.check_output(['hostname', '-I'], text=True).split():
            ip = ipaddress.ip_address(value)
            if ip.version == 4 and ip.is_private:
                ips.append(str(ip))
        sans = ','.join(['DNS:' + args.hostname, 'DNS:localhost', 'IP:127.0.0.1'] + ['IP:' + ip for ip in ips])
        write(ssl / 'extensions', 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=' + sans + '\n', 0o600)
        run('openssl', 'x509', '-req', '-in', str(ssl / 'server.csr'), '-CA', str(ssl / 'ca.pem'), '-CAkey', str(ssl / 'ca.key'), '-CAcreateserial', '-days', '365', '-extfile', str(ssl / 'extensions'), '-out', str(ssl / 'server.pem'), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for p in ssl.iterdir():
        os.chmod(p, 0o600)
    # A random dedicated web password, not the SSH or Soloist credential.
    credential = home / '.config/pi-audio-tray/music-web-login.txt'
    if not (conf / 'htpasswd').exists():
        password = secrets.token_urlsafe(24)
        hashed = subprocess.check_output(['htpasswd', '-ni', '-5', '-r', '10000', 'music'], input=password + '\n', text=True)
        write(conf / 'htpasswd', hashed, 0o640, 0, grp.getgrnam('www-data').gr_gid)
        write(credential, f'https://{args.hostname}:8443/\nUsername: music\nPassword: {password}\n', 0o600, uid, gid)
    write(home / '.config/pi-audio-tray/music-ca.crt', (ssl / 'ca.pem').read_text(), 0o600, uid, gid)
    addresses = [args.hostname, 'localhost', '127.0.0.1'] + [str(ipaddress.ip_address(x)) for x in subprocess.check_output(['hostname', '-I'], text=True).split() if ipaddress.ip_address(x).version == 4]
    origins = ' '.join('\"https://' + host + ':8443\" 1;' for host in addresses)
    write(conf / 'nginx.conf', f'''user www-data;
worker_processes 1;
pid /run/pi-audio-web/nginx.pid;
error_log stderr warn;
events {{ worker_connections 128; }}
http {{
 include /etc/nginx/mime.types;
 server_tokens off;
 access_log off;
 client_body_temp_path /run/pi-audio-web/body;
 proxy_temp_path /run/pi-audio-web/proxy;
 fastcgi_temp_path /run/pi-audio-web/fastcgi;
 uwsgi_temp_path /run/pi-audio-web/uwsgi;
 scgi_temp_path /run/pi-audio-web/scgi;
 limit_req_zone $binary_remote_addr zone=music_login:1m rate=10r/s;
 map $http_origin $music_origin_ok {{ default 0; "" 1; {origins} }}
 map $http_upgrade $connection_upgrade {{ default upgrade; '' close; }}
 server {{
  listen 8443 ssl;
  server_name {args.hostname};
  ssl_certificate {ssl}/server.pem;
  ssl_certificate_key {ssl}/server.key;
  ssl_protocols TLSv1.2 TLSv1.3;
  ssl_session_tickets off;
  # Private IPv4 ranges only, including loopback. No public IPv6 listener.
  allow 127.0.0.1;
  allow 192.168.0.0/16;
  allow 10.0.0.0/8;
  allow 172.16.0.0/12;
  deny all;
  auth_basic "Pi Audio music";
  auth_basic_user_file {conf}/htpasswd;
  if ($music_origin_ok = 0) {{ return 403; }}
  client_max_body_size 1m;
  add_header X-Content-Type-Options nosniff always;
  location ~ ^/(script|script-api|api/script)(/|$) {{ return 403; }}
  location / {{
   limit_req zone=music_login burst=40 nodelay;
   proxy_pass http://127.0.0.1:8080;
   proxy_http_version 1.1;
   proxy_set_header Host $http_host;
   proxy_set_header Authorization "";
   proxy_set_header Upgrade $http_upgrade;
   proxy_set_header Connection $connection_upgrade;
   proxy_set_header X-Forwarded-Proto https;
   proxy_set_header X-Forwarded-For $remote_addr;
   proxy_read_timeout 300s;
  }}
 }}
}}
''')
    write('/etc/systemd/system/pi-audio-web.service', '''[Unit]
Description=Pi Audio authenticated HTTPS music proxy
After=network.target
[Service]
Type=simple
ExecStartPre=/usr/sbin/nginx -t -c /etc/pi-audio-tray/music/nginx.conf
ExecStart=/usr/sbin/nginx -c /etc/pi-audio-tray/music/nginx.conf -g "daemon off;"
ExecReload=/usr/sbin/nginx -c /etc/pi-audio-tray/music/nginx.conf -s reload
RuntimeDirectory=pi-audio-web
RuntimeDirectoryMode=0755
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=/run/pi-audio-web
NoNewPrivileges=yes
Restart=on-failure
[Install]
WantedBy=multi-user.target
''')
    run('install', '-o', 'root', '-g', 'root', '-m', '755', str(Path(__file__).parent / 'renew-music-tls.py'), '/usr/local/lib/pi-audio-tray/renew-music-tls')
    write('/etc/systemd/system/pi-audio-music-tls.service', '[Unit]\nDescription=Renew Pi Audio local HTTPS certificate\n[Service]\nType=oneshot\nExecStart=/usr/local/lib/pi-audio-tray/renew-music-tls\nUMask=0077\nProtectHome=yes\nPrivateTmp=yes\n')
    write('/etc/systemd/system/pi-audio-music-tls.timer', '[Unit]\nDescription=Check Pi Audio HTTPS certificate daily\n[Timer]\nOnCalendar=daily\nRandomizedDelaySec=1h\nPersistent=yes\n[Install]\nWantedBy=timers.target\n')
    helper = Path(__file__).parent / 'pi_music.py'
    if not helper.is_file():
        raise RuntimeError('Place reviewed pi_music.py beside this installer')
    run('install', '-o', 'root', '-g', 'root', '-m', '755', str(helper), '/usr/local/lib/pi-audio-tray/pi-music')
    run('loginctl', 'enable-linger', user.pw_name)
    env = dict(os.environ, XDG_RUNTIME_DIR=f'/run/user/{uid}', DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{uid}/bus')
    def user_run(*command):
        run('runuser', '-u', user.pw_name, '--', *command, env=env)
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', 'pi-audio-mpd.service', 'pi-audio-mympd.service')
    run('systemctl', 'restart', 'pi-audio-mpd.service', 'pi-audio-mympd.service')
    user_run('mpc', '-h', '/run/pi-audio-mpd/socket', 'volume', '100')
    user_run('mpc', '-h', '/run/pi-audio-mpd/socket', 'update')
    run('systemctl', 'enable', '--now', 'pi-audio-music-tls.timer')
    run('systemctl', 'enable', 'pi-audio-web.service')
    run('systemctl', 'restart', 'pi-audio-web.service')
    print('Music services installed. Playback remains stopped. HTTPS login and CA are in the audio user private config directory.')


if __name__ == '__main__':
    main()
