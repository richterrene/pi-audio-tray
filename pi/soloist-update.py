#!/usr/bin/python3 -I
"""Bounded official-client updates. Downloaded code is never executed as root."""
import fcntl
import gzip
import hashlib
import io
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

URL = 'https://soloist-builds.spotifycdn.com/soloist_release_arm64.tar.gz'
DEST = Path('/usr/local/bin/soloist')
CACHE = Path('/var/cache/pi-audio-tray')
USER_FILE = Path('/etc/pi-audio-tray/soloist-user')
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_BINARY = 128 * 1024 * 1024
MAX_UNPACKED = 192 * 1024 * 1024


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urllib.parse.urlsplit(newurl)
        if (url.scheme != 'https' or url.hostname != 'soloist-builds.spotifycdn.com'
                or url.port not in (None, 443) or url.username or url.password):
            raise ValueError('Refusing a redirect outside the official HTTPS download host')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class BoundedReader:
    def __init__(self, inner, limit):
        self.inner, self.remaining = inner, limit

    def read(self, size=-1):
        request = self.remaining + 1 if size < 0 else min(size, self.remaining + 1)
        value = self.inner.read(request)
        self.remaining -= len(value)
        if self.remaining < 0:
            raise ValueError('Uncompressed archive exceeds its size limit')
        return value


def extract_binary(archive):
    if len(archive) > MAX_ARCHIVE:
        raise ValueError('Unexpected archive size')
    binary, count, total = None, 0, 0
    with gzip.GzipFile(fileobj=io.BytesIO(archive)) as gz:
        reader = BoundedReader(gz, MAX_UNPACKED)
        with tarfile.open(fileobj=reader, mode='r|') as tar:
            for member in tar:
                count += 1
                total += member.size
                path = Path(member.name)
                if (count > 64 or total > MAX_UNPACKED or len(member.name) > 256
                        or path.is_absolute() or '..' in path.parts):
                    raise ValueError('Unexpected archive contents')
                if not member.isdir() and not member.isfile():
                    raise ValueError('Archive links and special files are not permitted')
                if member.isfile() and path.name == 'soloist':
                    if binary is not None or not 64 <= member.size <= MAX_BINARY:
                        raise ValueError('Unexpected executable entry')
                    stream = tar.extractfile(member)
                    binary = stream.read(MAX_BINARY + 1)
                    if len(binary) != member.size:
                        raise ValueError('Truncated executable')
    if (binary is None or binary[:7] != b'\x7fELF\x02\x01\x01'
            or binary[18:20] != b'\xb7\x00' or binary[16:18] not in (b'\x02\x00', b'\x03\x00')):
        raise ValueError('Expected an ARM64 Linux executable')
    return binary


def trusted_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Updater files must be regular, root-owned, and not writable by others')


def digest_file(path):
    trusted_file(path)
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def atomic_write(path, data, mode=0o600):
    fd, temp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def service_account():
    trusted_file(USER_FILE)
    if USER_FILE.stat().st_size > 256:
        raise ValueError('Invalid service account configuration')
    user = USER_FILE.read_text().strip()
    if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,63}\$?', user):
        raise ValueError('Invalid service account')
    account = pwd.getpwnam(user)
    if account.pw_uid == 0:
        raise ValueError('Soloist must not run as root')
    return user, account.pw_uid


def install_binary(binary, current):
    digest = hashlib.sha256(binary).hexdigest()
    changed = current != digest
    if changed:
        if DEST.exists():
            atomic_write(DEST.with_name('soloist.previous'), DEST.read_bytes(), 0o755)
        atomic_write(DEST, binary, 0o755)
        print('Installed updated Soloist executable; SHA256: ' + digest)
    return changed, digest


def main():
    os.environ['PATH'] = '/usr/sbin:/usr/bin:/sbin:/bin'
    if os.geteuid() != 0:
        raise SystemExit('Run the updater through its system service')
    user, uid = service_account()
    lockdir = Path('/run/pi-audio-tray-update')
    lockdir.mkdir(mode=0o700, exist_ok=True)
    info = lockdir.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Unsafe updater lock directory')
    fd = os.open(lockdir / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _update(user, uid)


def _update(user, uid):
    CACHE.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = CACHE.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Unsafe update-cache directory')
    current = digest_file(DEST) if DEST.exists() else None
    etag, stored_digest = CACHE / 'soloist.etag', CACHE / 'soloist.sha256'
    headers = {}
    if etag.exists() and stored_digest.exists() and current:
        trusted_file(etag)
        trusted_file(stored_digest)
        if etag.stat().st_size > 512 or stored_digest.stat().st_size > 128:
            raise ValueError('Invalid update-cache metadata')
        if stored_digest.read_text().strip() == current:
            headers['If-None-Match'] = etag.read_text().strip()
    opener = urllib.request.build_opener(OfficialRedirect())
    try:
        with opener.open(urllib.request.Request(URL, headers=headers), timeout=20) as response:
            buffer, deadline = bytearray(), time.monotonic() + 100
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Download exceeded its time limit')
                chunk = response.read(min(65536, MAX_ARCHIVE + 1 - len(buffer)))
                if not chunk:
                    break
                buffer.extend(chunk)
                if len(buffer) > MAX_ARCHIVE:
                    raise ValueError('Download exceeds its size limit')
            new_etag = response.headers.get('ETag', '')
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and headers:
            print('Soloist is current.')
            return
        raise
    binary = extract_binary(bytes(buffer))
    changed, digest = install_binary(binary, current)
    atomic_write(stored_digest, (digest + '\n').encode())
    if new_etag and len(new_etag) <= 512 and all(c.isprintable() for c in new_etag):
        atomic_write(etag, new_etag.encode())
    if changed:
        control = ['/usr/sbin/runuser', '-u', user, '--', '/usr/bin/env',
                   f'XDG_RUNTIME_DIR=/run/user/{uid}', '/usr/bin/systemctl', '--user']
        if subprocess.run([*control, 'is-enabled', '--quiet', 'soloist.service'], timeout=10).returncode == 0:
            subprocess.run([*control, 'restart', 'soloist.service'], check=True, timeout=30)
    else:
        print('Soloist is current.')


if __name__ == '__main__':
    main()
