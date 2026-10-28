"""Privileged deployment around an explicitly unprivileged pinned-source build."""
import importlib.util
import errno
import os
from pathlib import Path
import pwd
import socket
import stat
import tempfile

TIMING = 'pi-audio-nqptp.service'
BUILD_USER = 'pi-audio-build'


def ports_available():
    for family, host in ((socket.AF_INET, '0.0.0.0'), (socket.AF_INET6, '::')):
        for port in (319, 320, 9000):
            try:
                sock = socket.socket(family, socket.SOCK_DGRAM)
            except OSError as exc:
                if family == socket.AF_INET6 and exc.errno == errno.EAFNOSUPPORT:
                    break
                raise
            with sock:
                if family == socket.AF_INET6:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                sock.bind((('127.0.0.1' if family == socket.AF_INET else '::1') if port == 9000 else host, port))


def build_account(receiver, state):
    marker = state / 'build-user-owned'
    try:
        user = pwd.getpwnam(BUILD_USER)
        if not marker.is_file() or not 100 <= user.pw_uid < 1000 or user.pw_shell != '/usr/sbin/nologin' or user.pw_dir != '/nonexistent':
            raise ValueError('Unexpected existing build account')
    except KeyError:
        receiver.run('/usr/sbin/useradd', '--system', '--no-create-home', '--home-dir', '/nonexistent', '--shell', '/usr/sbin/nologin', BUILD_USER)
        receiver.write(marker, BUILD_USER + '\n', 0o600)
        user = pwd.getpwnam(BUILD_USER)
    return user


def read_artifact(path, uid, binary=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        limit = 16 * 1024**2 if binary else 128 * 1024
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or not 1 <= info.st_size <= limit:
            raise ValueError('Invalid build artifact')
        data = stream.read(limit + 1)
    if len(data) != info.st_size:
        raise ValueError('Build artifact changed size')
    if binary and (len(data) < 64 or data[:7] != b'\x7fELF\x02\x01\x01' or data[18:20] != b'\xb7\x00' or data[16:18] not in (b'\x02\x00', b'\x03\x00')):
        raise ValueError('Expected an ARM64 executable')
    return data


def prepare(receiver, snapshot, state):
    import shutil
    if shutil.disk_usage('/var/tmp').free < 1024**3:
        raise ValueError('AirPlay 2 building requires at least 1 GiB free disk space')
    if Path('/usr/bin/nqptp').exists() or Path('/usr/local/bin/nqptp').exists():
        raise ValueError('An unrelated NQPTP installation requires manual integration')
    # Do not steal PTP/control ports. An already-owned active timing service may
    # keep running while its replacement is compiled.
    import subprocess
    active = subprocess.run(['/usr/bin/systemctl', 'is-active', '--quiet', TIMING], check=False).returncode == 0
    if not active:
        ports_available()
    elif not (state / 'airplay2-owned').is_file():
        raise ValueError('Unexpected existing timing service')
    spec = importlib.util.spec_from_file_location('airplay_build', snapshot / 'airplay-build.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    receiver.emit('build', 'Building checksum-pinned AirPlay 2 and NQPTP as an unprivileged account; this can take several minutes')
    receiver.run('/usr/bin/apt-get', 'update')
    receiver.run('/usr/bin/apt-get', 'install', '-y', '--no-remove', '--no-install-recommends', *builder.PACKAGES)
    user = build_account(receiver, state)
    script = '/usr/local/lib/pi-audio-tray/airplay-build'
    receiver.write(script, (snapshot / 'airplay-build.py').read_text(), 0o755)
    destination = Path('/usr/local/lib/pi-audio-tray/airplay2') / builder.BUILD_ID
    receiver.safe_dir(destination)
    with tempfile.TemporaryDirectory(prefix='pi-audio-build-', dir='/var/tmp') as folder:
        os.chown(folder, user.pw_uid, user.pw_gid)
        receiver.run('/usr/sbin/runuser', '-u', BUILD_USER, '--', '/usr/bin/env', '-i',
                     'PATH=/usr/bin:/bin', 'LANG=C.UTF-8',
                     '/usr/bin/nice', '-n', '10', '/usr/bin/python3', '-I', script, folder, timeout=3000)
        for name, (version, _) in builder.SOURCES.items():
            path = Path(folder) / (name + '-' + version)
            data = read_artifact(path / name, user.pw_uid, binary=True)
            # Use a root-owned temporary file and atomic replacement; never run
            # a compiled program or upstream make-install recipe as root.
            fd, temp = tempfile.mkstemp(prefix='.binary-', dir=destination)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    os.fchmod(stream.fileno(), 0o755)
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, destination / name)
            finally:
                Path(temp).unlink(missing_ok=True)
            license_file = path / ('COPYING' if (path / 'COPYING').exists() else 'LICENSE')
            receiver.write(destination / (name + '-COPYING'), read_artifact(license_file, user.pw_uid).decode())
    receiver.write('/etc/systemd/system/' + TIMING, (snapshot / TIMING).read_text())
    receiver.write(state / 'airplay2-owned', builder.BUILD_ID + '\n', 0o600)
    # Exactly two runtime operations, no general systemctl or package privilege.
    return destination
