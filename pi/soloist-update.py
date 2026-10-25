#!/usr/bin/python3
"""Fetch official Soloist builds on the Pi; never copy binaries into this repo."""
import fcntl
import hashlib
import io
import os
from pathlib import Path
import pwd
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request

URL = 'https://soloist-builds.spotifycdn.com/soloist_release_arm64.tar.gz'
DEST = Path('/usr/local/bin/soloist')


def main():
    lock = open('/run/soloist-update.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    cache = Path('/var/cache/pi-audio-tray')
    cache.mkdir(mode=0o755, parents=True, exist_ok=True)
    headers = {}
    etag = cache / 'soloist.etag'
    if etag.exists() and DEST.exists():
        headers['If-None-Match'] = etag.read_text().strip()
    try:
        with urllib.request.urlopen(urllib.request.Request(URL, headers=headers), timeout=60) as response:
            archive = response.read(128 * 1024 * 1024 + 1)
            new_etag = response.headers.get('ETag', '')
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            print('Soloist is current.')
            return
        raise
    if len(archive) > 128 * 1024 * 1024:
        raise RuntimeError('Unexpected archive size')
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
        candidates = [m for m in tar.getmembers() if m.isfile() and Path(m.name).name == 'soloist']
        if len(candidates) != 1 or candidates[0].size > 256 * 1024 * 1024:
            raise RuntimeError('Unexpected archive contents')
        binary = tar.extractfile(candidates[0]).read()
    if binary[:4] != b'\x7fELF' or binary[18:20] != b'\xb7\x00':
        raise RuntimeError('Expected an ARM64 Linux executable')
    changed = not DEST.exists() or hashlib.sha256(binary).digest() != hashlib.sha256(DEST.read_bytes()).digest()
    if changed:
        fd, temp = tempfile.mkstemp(prefix='.soloist-update-', dir=DEST.parent)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(binary)
            os.chmod(temp, 0o755)
            result = subprocess.run([temp, '--version'], capture_output=True, text=True, timeout=15, check=True)
            if not result.stdout.startswith('soloist '):
                raise RuntimeError('Unexpected executable version')
            if DEST.exists():
                import shutil
                shutil.copy2(DEST, DEST.with_name('soloist.previous'))
            os.replace(temp, DEST)
            print('Installed ' + result.stdout.strip())
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    if new_etag:
        etag.write_text(new_etag)
    user = Path('/etc/pi-audio-tray/soloist-user').read_text().strip()
    uid = pwd.getpwnam(user).pw_uid
    if changed:
        control = ['runuser', '-u', user, '--', 'env', f'XDG_RUNTIME_DIR=/run/user/{uid}',
                   'systemctl', '--user']
        if subprocess.run([*control, 'is-enabled', '--quiet', 'soloist.service']).returncode == 0:
            # Also recover a service that already exited due to build expiry.
            subprocess.run([*control, 'restart', 'soloist.service'], check=True)
    else:
        print('Soloist is current.')


if __name__ == '__main__':
    main()
