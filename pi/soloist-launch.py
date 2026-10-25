#!/usr/bin/python3 -I
"""Read a bounded private key file; keep the optional control server disabled."""
import os
from pathlib import Path
import stat
import sys


def load_key(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > 16384):
            raise ValueError('API key must be a private, regular file owned by the service user')
        raw = os.read(fd, 16385)
        if len(raw) != info.st_size:
            raise ValueError('Invalid API key file size')
    finally:
        os.close(fd)
    try:
        key = raw.decode('utf-8').strip()
    except UnicodeError:
        raise ValueError('Invalid API key file') from None
    if not key or len(raw) > 16384 or any(not c.isprintable() or c.isspace() for c in key):
        raise ValueError('Invalid API key file')
    return key


def main():
    if os.geteuid() == 0:
        print('Soloist must run as a non-root service user', file=sys.stderr)
        return 78
    root = Path.home() / '.config/soloist'
    try:
        key = load_key(root / 'api-key')
        namefile = root / 'device-name'
        name = 'Pi Audio'
        if namefile.exists():
            fd = os.open(namefile, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 512:
                    raise ValueError('Invalid device name file')
                name = os.read(fd, 513).decode('utf-8').strip()
            finally:
                os.close(fd)
        if not name or len(name) > 128 or not name.isprintable():
            raise ValueError('Invalid device name')
    except (OSError, ValueError) as exc:
        print('Soloist configuration unavailable: ' + str(exc), file=sys.stderr)
        return 78
    # Upstream only supports a key argument. See SECURITY.md for the remaining
    # local process-list exposure; never log this argv or enable --ws by default.
    os.execv('/usr/local/bin/soloist', ['soloist', '-n', name, '-k', key, '-i', '40', '-z', '100'])


if __name__ == '__main__':
    sys.exit(main())
