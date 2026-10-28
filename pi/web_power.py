#!/usr/bin/python3 -I
"""Socket-activated, fixed shutdown broker. No shell, unit names or paths from HTTP."""
from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import sys

LOCK = Path('/var/lib/pi-audio-setup/lock')
APT_LOCKS = ('/var/lib/dpkg/lock-frontend', '/var/lib/dpkg/lock', '/var/lib/apt/lists/lock')
UPDATES = ('apt-daily.service', 'apt-daily-upgrade.service', 'soloist-update.service')
COMMAND = ('/usr/bin/systemctl', '--check-inhibitors=yes', '--no-block', 'poweroff')
QUEUE = ('/usr/bin/systemctl', '--no-block', 'start', 'pi-audio-web-poweroff.service')


@contextmanager
def guard():
    import fcntl
    for path in (LOCK, *LOCK.parents):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Unsafe shutdown lock')
    fd = os.open(LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    with ExitStack() as stack:
        held = stack.enter_context(os.fdopen(fd, 'rb'))
        info = os.fstat(held.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Invalid shutdown lock')
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for path in APT_LOCKS:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            package_lock = stack.enter_context(os.fdopen(fd, 'rb'))
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError('Invalid package-manager lock')
            fcntl.lockf(package_lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        check = subprocess.run(['/usr/bin/systemctl', 'show', '--property=ActiveState', '--value', *UPDATES],
                               check=True, capture_output=True, text=True, timeout=3)
        states = check.stdout.split()
        if len(states) != len(UPDATES) or any(s not in ('inactive', 'failed') for s in states):
            raise BlockingIOError('Updates in progress or state unknown')
        yield


def dispatch(data, uid, allowed_uid):
    if uid != allowed_uid:
        return 'denied'
    if data not in (b'check\n', b'poweroff\n'):
        return 'denied'
    try:
        with guard():
            if data == b'poweroff\n':
                subprocess.run(QUEUE, check=True, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        return 'accepted' if data == b'poweroff\n' else 'ready'
    except (OSError, ValueError, subprocess.SubprocessError):
        return 'busy'


def main():
    if os.geteuid() != 0:
        raise ValueError('Root broker only')
    if sys.argv[1:] == ['--execute']:
        # Recheck after the response-delivery delay, immediately before poweroff.
        with guard():
            subprocess.run(COMMAND, check=True, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        return
    if sys.argv[1:]:
        raise ValueError('Unsupported broker invocation')
    import pwd
    allowed_uid = pwd.getpwnam('pi-audio-auth').pw_uid
    if not 100 <= allowed_uid < 1000:
        raise ValueError('Unexpected authentication account')
    with socket.socket(fileno=0) as connection:
        connection.settimeout(5)
        _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        data = b''
        while len(data) <= 32:
            part = connection.recv(33 - len(data))
            if not part:
                break
            data += part
        result = dispatch(data, uid, allowed_uid)
        connection.sendall((json.dumps({'status': result}) + '\n').encode())


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Web shutdown request failed.', file=sys.stderr)
        sys.exit(1)
