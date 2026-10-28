#!/usr/bin/python3 -I
"""Build reviewed AirPlay 2 sources as an unprivileged account, never make install."""
import hashlib
import io
import os
import re
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import urllib.request

SOURCES = {
    'shairport-sync': ('5.6.2', '0747706e19a64ec550ec8a6a5c26bf8e231a4d032de31644f5a5daa0632c41c4'),
    'nqptp': ('1.2.9', '6aa1d73b8ee760bf19378c319db0d0537a867ff961764b150e19f87d96a02210'),
}
BUILD_ID = 'shairport-5.6.2-nqptp-1.2.9'
PACKAGES = ('build-essential', 'autoconf', 'automake', 'libtool', 'pkg-config',
            'libpopt-dev', 'libconfig-dev', 'libavahi-client-dev', 'libssl-dev',
            'libsoxr-dev', 'libplist-dev', 'libsodium-dev', 'uuid-dev', 'libgcrypt20-dev',
            'xxd', 'libplist-utils', 'libavutil-dev', 'libavcodec-dev', 'libavformat-dev',
            'libpipewire-0.3-dev', 'ca-certificates', 'avahi-daemon')


def extract(data, name, destination):
    version, digest = SOURCES[name]
    if len(data) > 5 * 1024**2 or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('AirPlay source checksum mismatch')
    prefix = name + '-' + version
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        members = archive.getmembers()
        if len(members) > 1000:
            raise ValueError('Too many source files')
        seen = set()
        for entry in members:
            path = PurePosixPath(entry.name)
            total += entry.size
            if (not path.parts or path.parts[0] != prefix or path.is_absolute() or '..' in path.parts
                    or entry.name in seen or len(entry.name) > 256 or entry.size > 4 * 1024**2
                    or total > 32 * 1024**2 or not (entry.isdir() or entry.isfile())):
                raise ValueError('Unsupported source archive entry')
            seen.add(entry.name)
        # The freshly created workspace has no user-provided links or files.
        archive.extractall(destination, members=members, filter='data')
    return destination / prefix


def build(work):
    if os.geteuid() == 0:
        raise ValueError('Compile as an unprivileged build account')
    directories = {}
    for name, (version, _) in SOURCES.items():
        url = f'https://codeload.github.com/mikebrady/{name}/tar.gz/refs/tags/{version}'
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(5 * 1024**2 + 1)
        directories[name] = extract(data, name, work)
    versions = [re.search(r'#define NQPTP_SHM_STRUCTURES_VERSION (\d+)', (folder / 'nqptp-shm-structures.h').read_text()).group(1) for folder in directories.values()]
    if len(set(versions)) != 1:
        raise ValueError('Shairport Sync and NQPTP timing ABI versions differ')
    for name in ('nqptp', 'shairport-sync'):
        folder = directories[name]
        def run(*args):
            subprocess.run(args, cwd=folder, check=True, stdin=subprocess.DEVNULL, timeout=1800)
        run('/usr/bin/autoreconf', '-fi')
        flags = ('--with-airplay-2', '--with-pipewire', '--with-avahi', '--with-ssl=openssl', '--with-soxr') if name == 'shairport-sync' else ()
        run(str(folder / 'configure'), '--prefix=/usr/local/lib/pi-audio-tray/airplay2/' + BUILD_ID, *flags)
        run('/usr/bin/make', '-j2')
        result = subprocess.run([str(folder / name), '-V'], check=True,
                                capture_output=True, text=True, timeout=10)
        if SOURCES[name][0] not in result.stdout:
            raise ValueError('Unexpected built program version')
        if name == 'shairport-sync' and ('AirPlay2' not in result.stdout or 'PipeWire' not in result.stdout):
            raise ValueError('AirPlay 2 / PipeWire support is missing')
    print('Verified pinned AirPlay 2 and NQPTP builds.', flush=True)


if __name__ == '__main__':
    build(Path(sys.argv[1]).resolve())
