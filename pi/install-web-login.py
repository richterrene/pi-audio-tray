#!/usr/bin/python3 -I
"""Upgrade an existing Pi Audio HTTPS proxy to cookie login and standard ports.

Run as administrator with --hostname PI.local, with web_login.py and
web_login_config.py alongside this file. Preserves the current password and CA.
"""
import argparse
import grp
import http.client
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import ssl
import subprocess
import tempfile
import time


def run(*args):
    return subprocess.run(args, check=True)


def write(path, data, mode=0o644):
    path = Path(path)
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError('Refusing symbolic-link installation path')
    if any(parent.stat().st_uid != 0 or parent.stat().st_mode & 0o022 for parent in path.parents):
        raise ValueError('Installation ancestors must be root-owned and non-writable')
    fd, temporary = tempfile.mkstemp(prefix='.login-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    os.environ['PATH'] = '/usr/sbin:/usr/bin:/sbin:/bin'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hostname', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Administrator authentication required')
    source = Path(__file__).parent
    spec = importlib.util.spec_from_file_location('web_login_config', source / 'web_login_config.py')
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)
    addresses = subprocess.check_output(['hostname', '-I'], text=True).split()
    names = config.hosts(args.hostname, addresses)
    root = Path('/etc/pi-audio-tray/music')
    for path in (root, Path('/usr/local/lib/pi-audio-tray')):
        if any(parent.is_symlink() or parent.stat().st_uid != 0 or parent.stat().st_mode & 0o022 for parent in (path, *path.parents)):
            raise ValueError('Installation ancestors must be root-owned and non-writable')
    for name in ('htpasswd', 'tls/server.pem', 'tls/server.key', 'nginx.conf'):
        if not (root / name).is_file() or (root / name).is_symlink():
            raise ValueError('Existing Pi Audio HTTPS setup is required')
    # Refuse occupied standard ports on first migration, before changing services.
    if 'auth_request /_session_check;' not in (root / 'nginx.conf').read_text():
        for port in (80, 443):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as check:
                check.bind(('0.0.0.0', port))
    try:
        user = pwd.getpwnam('pi-audio-auth')
        extra_groups = [group.gr_name for group in grp.getgrall() if user.pw_name in group.gr_mem and group.gr_name != 'www-data']
        if not 100 <= user.pw_uid < 1000 or user.pw_gid != grp.getgrnam('www-data').gr_gid or user.pw_shell != '/usr/sbin/nologin' or extra_groups:
            raise ValueError('Unexpected existing authentication account')
    except KeyError:
        run('useradd', '--system', '--no-create-home', '--home-dir', '/nonexistent', '--shell', '/usr/sbin/nologin', '--gid', 'www-data', 'pi-audio-auth')
    backup = root / 'nginx.before-cookie-login.conf'
    if not backup.exists():
        shutil.copy2(root / 'nginx.conf', backup)
    previous = (root / 'nginx.conf').read_text()
    os.chown(root / 'htpasswd', 0, grp.getgrnam('www-data').gr_gid)
    os.chmod(root / 'htpasswd', 0o640)
    write('/usr/local/lib/pi-audio-tray/web-login.py', (source / 'web_login.py').read_text())
    static = Path('/usr/local/share/pi-audio-tray')
    static.mkdir(mode=0o755, exist_ok=True)
    write(static / 'session-worker.js', config.SESSION_WORKER)
    write('/etc/systemd/system/pi-audio-login.service', config.LOGIN_UNIT)
    write(root / 'login.json', json.dumps({'origins': config.origins(names)}) + '\n')
    dropin = Path('/etc/systemd/system/pi-audio-web.service.d')
    dropin.mkdir(mode=0o755, exist_ok=True)
    previous_dependency = (dropin / 'login.conf').read_text() if (dropin / 'login.conf').exists() else None
    write(dropin / 'login.conf', config.WEB_DEPENDENCY)
    write(root / 'nginx.conf', config.nginx_config(args.hostname, addresses))
    try:
        run('/usr/sbin/nginx', '-t', '-c', str(root / 'nginx.conf'))
    except Exception:
        write(root / 'nginx.conf', previous)
        if previous_dependency is None:
            (dropin / 'login.conf').unlink(missing_ok=True)
        else:
            write(dropin / 'login.conf', previous_dependency)
        raise
    run('systemctl', 'daemon-reload')
    try:
        run('systemctl', 'enable', 'pi-audio-login.service')
        run('systemctl', 'restart', 'pi-audio-login.service')
        for _ in range(50):
            if Path('/run/pi-audio-login/auth.sock').exists():
                break
            time.sleep(.1)
        else:
            raise RuntimeError('Login service did not create its private socket')
        run('systemctl', 'is-active', '--quiet', 'pi-audio-login.service')
        run('systemctl', 'reload-or-restart', 'pi-audio-web.service')
        context = ssl.create_default_context(cafile=str(root / 'tls/ca.pem'))
        for attempt in range(20):
            try:
                connection = http.client.HTTPSConnection('127.0.0.1', 443, context=context, timeout=2)
                connection.request('GET', '/login')
                response = connection.getresponse()
                healthy = response.status == 200 and b'name="csrf"' in response.read(16384)
                connection.close()
                if healthy:
                    break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(.5)
        else:
            raise RuntimeError('HTTPS login health check failed')
    except Exception:
        write(root / 'nginx.conf', previous)
        if previous_dependency is None:
            (dropin / 'login.conf').unlink(missing_ok=True)
        else:
            write(dropin / 'login.conf', previous_dependency)
        run('systemctl', 'daemon-reload')
        run('systemctl', 'reload-or-restart', 'pi-audio-web.service')
        raise
    print('Browser login installed. Port 80 redirects to HTTPS 443; 8443 remains compatible. Existing password and certificate preserved.')


if __name__ == '__main__':
    main()
