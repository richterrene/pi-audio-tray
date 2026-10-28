#!/usr/bin/python3 -I
"""Opt in to authenticated web shutdown on an existing Pi Audio HTTPS player.

Run with sudo and --hostname PI.local. All fixed manifest files must be beside
this installer. Retains the existing password, sessions, certificates and audio.
"""
import argparse
import fcntl
import grp
import http.client
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import ssl
import stat
import subprocess
import tempfile
from urllib.parse import urlencode

STATE = Path('/var/lib/pi-audio-web-power')
FILES = ('install-web-power.py', 'install-web-login.py', 'web_login.py', 'web_login_config.py',
         'web_power.py', 'pi-audio-power.socket', 'pi-audio-power@.service', 'pi-audio-web-poweroff.service')
TARGETS = ('/usr/local/lib/pi-audio-tray/web-power.py', '/etc/systemd/system/pi-audio-power.socket',
           '/etc/systemd/system/pi-audio-power@.service', '/etc/systemd/system/pi-audio-web-poweroff.service')


def run(*args, **options):
    return subprocess.run(args, check=True, timeout=45, **options)


def trusted(path):
    for part in (path, *path.parents):
        if part.is_symlink() or (part.exists() and (part.stat().st_uid != 0 or part.stat().st_mode & 0o022)):
            raise ValueError('Unsafe installation path')


def verify_https():
    # Disposable local session, never an existing browser's token or password.
    # Avoid evicting any real browser if the bounded session store is full.
    helper = """import importlib.util,sys
s=importlib.util.spec_from_file_location('login','/usr/local/lib/pi-audio-tray/web-login.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
a=m.Sessions('/var/lib/pi-audio-login','/etc/pi-audio-tray/music/htpasswd')
if sys.argv[1]=='new':
 with a.db() as db:count=db.execute('SELECT count(*) FROM sessions').fetchone()[0]
 print('skip' if count>=127 else a.create(60))
else:a.remove(sys.stdin.read(128).strip())
"""
    command = ['/usr/sbin/runuser','-u','pi-audio-auth','--','/usr/bin/python3','-I','-c',helper]
    context = ssl.create_default_context(cafile='/etc/pi-audio-tray/music/tls/ca.pem')
    def request(method, headers=None, fields=None):
        connection = http.client.HTTPSConnection('127.0.0.1',443,context=context,timeout=5)
        try:
            body = None if fields is None else urlencode(fields)
            headers = dict(headers or {})
            if fields is not None:headers['Content-Type']='application/x-www-form-urlencoded'
            connection.request(method,'/power',body,headers)
            response=connection.getresponse()
            return response.status,response.getheaders(),response.read(16384).decode()
        finally:connection.close()
    if request('GET')[0] != 303:
        raise RuntimeError('Anonymous shutdown page was not refused')
    session = run(*command,'new',capture_output=True,text=True).stdout.strip()
    if session == 'skip':
        print('Authenticated health check skipped to preserve existing browser sessions.')
        return
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}',session):
        raise RuntimeError('Disposable session check failed')
    try:
        jar='__Host-pi_audio_session='+session
        status,headers,body=request('GET',{'Cookie':jar})
        if status!=200 or 'Shut down this Pi?' not in body:
            raise RuntimeError('Authenticated shutdown confirmation was unavailable')
        csrf=re.search(r'name="csrf" value="([A-Za-z0-9_-]{43})"',body)
        form=next((v.split(';',1)[0] for k,v in headers if k.lower()=='set-cookie'),None)
        if not csrf or not form:
            raise RuntimeError('Confirmation form lacked CSRF protection')
        status,_,_=request('POST',{'Cookie':jar+'; '+form,'Origin':'https://127.0.0.1'},
                           {'csrf':csrf[1],'confirm':'invalid'})
        if status!=403:
            raise RuntimeError('Invalid shutdown confirmation was not refused')
    finally:
        run(*command,'remove',input=session,text=True,capture_output=True)
    print('HTTPS login/confirmation boundaries verified; no valid shutdown POST was sent.')


def main():
    if os.geteuid() != 0:
        raise ValueError('Administrator authentication required')
    os.umask(0o077)
    os.environ['PATH'] = '/usr/sbin:/usr/bin:/sbin:/bin'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hostname', required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    uid = int(os.environ.get('SUDO_UID', '0'))
    trusted(STATE)
    STATE.mkdir(mode=0o700, exist_ok=True); STATE.chmod(0o700)
    marker = STATE/'owner'
    if marker.exists():
        info = marker.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > 128 or marker.read_text() != 'pi-audio-web-power-v1\n':
            raise ValueError('Unrecognized power installation')
    elif any(Path(p).exists() or Path(p).is_symlink() for p in TARGETS):
        raise ValueError('Unowned power service files require manual integration')
    for target in TARGETS:
        trusted(Path(target))
    trusted(Path('/var/lib/pi-audio-setup'))
    Path('/var/lib/pi-audio-setup').mkdir(mode=0o700, exist_ok=True)
    lock = os.open('/var/lib/pi-audio-setup/lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock, 'w') as held, tempfile.TemporaryDirectory(prefix='bundle-', dir=STATE) as folder:
        info = os.fstat(held.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Unsafe setup lock')
        fcntl.flock(held, fcntl.LOCK_EX|fcntl.LOCK_NB)
        snapshot = Path(folder)
        for name in FILES:
            path = source/name; info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or not 0 < info.st_size <= 1024**2:
                raise ValueError('Invalid fixed power bundle')
            shutil.copyfile(path, snapshot/name, follow_symlinks=False)
        # Load only the private root-owned snapshot, never a mutable user module.
        spec = importlib.util.spec_from_file_location('login_install',snapshot/'install-web-login.py')
        installer = importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
        spec = importlib.util.spec_from_file_location('login_config',snapshot/'web_login_config.py')
        config = importlib.util.module_from_spec(spec);spec.loader.exec_module(config)
        config.hosts(args.hostname, [])  # Validate before changing anything.
        nginx = Path('/etc/pi-audio-tray/music/nginx.conf')
        trusted(nginx)
        current = nginx.read_text()
        name = re.search(r'(?m)^\s*server_name\s+([A-Za-z0-9.-]+)\s*;', current)
        if 'auth_request /_session_check;' not in current or not name or name[1].lower() != args.hostname.lower():
            raise ValueError('Use the existing cookie-login HTTPS hostname')
        run('/usr/bin/systemd-analyze','verify','--man=no',*(str(snapshot/name) for name in FILES[-3:]))
        account = pwd.getpwnam('pi-audio-auth')
        if not 100 <= account.pw_uid < 1000 or account.pw_gid != grp.getgrnam('www-data').gr_gid or account.pw_shell != '/usr/sbin/nologin':
            raise ValueError('Existing Pi Audio login service is required')
        backup = STATE/'before-web-power'
        if not backup.exists():
            backup.mkdir(mode=0o700)
            for name,path in (('nginx.conf','/etc/pi-audio-tray/music/nginx.conf'),
                              ('login.json','/etc/pi-audio-tray/music/login.json'),
                              ('web-login.py','/usr/local/lib/pi-audio-tray/web-login.py')):
                trusted(Path(path));shutil.copy2(path,backup/name);(backup/name).chmod(0o600)
        installer.write(marker, 'pi-audio-web-power-v1\n', 0o600)
        installer.write(TARGETS[0],(snapshot/'web_power.py').read_text())
        for name,target in zip(FILES[-3:],TARGETS[1:]):
            installer.write(target,(snapshot/name).read_text())
        was_enabled = subprocess.run(['/usr/bin/systemctl','is-enabled','--quiet','pi-audio-power.socket'],capture_output=True,timeout=10).returncode == 0
        try:
            # Existing migration checks, verifier ownership, nginx validation,
            # protected login unit and rollback are retained by this installer.
            run('/usr/bin/python3','-I',str(snapshot/'install-web-login.py'),'--hostname',args.hostname)
            run('/usr/bin/systemctl','daemon-reload')
            run('/usr/bin/systemctl','enable','--now','pi-audio-power.socket')
        except Exception:
            if not was_enabled:
                subprocess.run(['/usr/bin/systemctl','disable','--now','pi-audio-power.socket'],capture_output=True,timeout=15)
            raise
        fcntl.flock(held,fcntl.LOCK_UN)
        probe = """import socket,json
with socket.socket(socket.AF_UNIX) as s:
 s.settimeout(8);s.connect('/run/pi-audio-power/control.sock');s.sendall(b'check\\n');s.shutdown(socket.SHUT_WR)
 result=json.loads(s.recv(256));assert result.get('status') in ('ready','busy')
print('Private shutdown broker check passed; no shutdown requested.')
"""
        run('/usr/sbin/runuser','-u','pi-audio-auth','--','/usr/bin/python3','-I','-c',probe)
        verify_https()
        print('Web shutdown installed at /power. Existing login, certificate and audio configuration retained. No shutdown was performed.')


if __name__ == '__main__':
    main()
