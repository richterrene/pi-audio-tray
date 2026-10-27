#!/usr/bin/env python3
"""Boot disposable official Pi OS images, without host root/devices/network.

Linux + Python 3.12+ + Docker daemon access are required. No application secrets
or real SD-card images are used. Downloaded images stay in the selected cache.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent.parent.parent
HERE = Path(__file__).resolve().parent
RUNNER = 'pi-audio-virtual-runner:local'
# Exact official releases, not mutable *_latest redirects.
IMAGES = {
    'trixie-arm64': {'url': 'https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2026-10-06/2026-10-06-raspios-trixie-arm64-lite.img.xz', 'sha256': '483db18a48da399b5b7022ffae9a07bc6d99daab30e593aedac09b69ff422843'},
    'trixie-armhf': {'url': 'https://downloads.raspberrypi.com/raspios_lite_armhf/images/raspios_lite_armhf-2026-10-06/2026-10-06-raspios-trixie-armhf-lite.img.xz', 'sha256': '2c302771f34c67a0b7ae7aa2e0dcdd3eac660504be59193c011ba79d412e1632'},
}
CASES = {
    'arm64-pi3': ('trixie-arm64', 'raspi3b', 'kernel8.img', 'bcm2710-rpi-3-b.dtb', 'arm64', True),
    'armhf-pi3-mixed': ('trixie-armhf', 'raspi3b', 'kernel8.img', 'bcm2710-rpi-3-b.dtb', 'armhf', False),
}
PAYLOAD = ('pi_helper.py', 'pi/setup-probe.py', 'pi/web_login.py', 'pi/web_login_config.py',
           'pi/reboot-if-needed.py', 'pi/renew-music-tls.py',
           'tests/test_pi_helper.py', 'tests/test_maintenance.py', 'tests/test_web_login.py',
           'tools/check_dbus_agent.py')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def command(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def download(profile, cache):
    info = IMAGES[profile]
    target = cache / info['url'].rsplit('/', 1)[-1]
    if not target.exists():
        temporary = target.with_suffix('.part')
        try:
            with urllib.request.urlopen(info['url'], timeout=60) as source, temporary.open('wb') as out:
                count = 0
                while chunk := source.read(1024 * 1024):
                    count += len(chunk)
                    if count > 2 * 1024**3:
                        raise ValueError('Compressed image exceeds 2 GiB')
                    out.write(chunk)
            if digest(temporary) != info['sha256']:
                raise ValueError('Image checksum mismatch')
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    if target.is_symlink() or digest(target) != info['sha256']:
        raise ValueError('Cached image checksum mismatch')
    return target


def sparse_copy(source, destination, length):
    remaining = length
    while remaining:
        chunk = source.read(min(4 * 1024 * 1024, remaining))
        if not chunk:
            raise ValueError('Truncated image')
        if chunk.count(0) == len(chunk):
            destination.seek(len(chunk), 1)
        else:
            destination.write(chunk)
        remaining -= len(chunk)


def partitions(path):
    with path.open('rb') as stream:
        mbr = stream.read(512)
    if len(mbr) != 512 or mbr[510:] != b'\x55\xaa':
        raise ValueError('Expected an MBR Pi image')
    entries = [(mbr[446 + i * 16 + 4], *struct.unpack_from('<II', mbr, 446 + i * 16 + 8)) for i in range(4)]
    if entries[0][0] not in (0x0b, 0x0c) or entries[1][0] != 0x83:
        raise ValueError('Unexpected Pi image partitions')
    size = path.stat().st_size
    for _, start, count in entries[:2]:
        if not start or not count or (start + count) * 512 > size:
            raise ValueError('Invalid partition boundaries')
    if entries[0][1] + entries[0][2] > entries[1][1]:
        raise ValueError('Overlapping image partitions')
    return entries[:2]


def runner(cache, args, **kwargs):
    return command(['docker', 'run', '--rm', '--network', 'none', '--read-only',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--user', f'{os.getuid()}:{os.getgid()}', '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m',
        '-v', f'{cache}:/lab:rw', RUNNER, *args], **kwargs)


def prepare_inside(case):
    """Called inside the unprivileged runner; tools never access block devices."""
    profile, machine, kernel, dtb, _, _ = CASES[case]
    cache, folder = Path('/lab'), Path('/lab') / case
    folder.mkdir(exist_ok=True)
    compressed = cache / IMAGES[profile]['url'].rsplit('/', 1)[-1]
    pristine = compressed.with_suffix('')
    if not pristine.exists():
        with gzip_or_xz(compressed) as source, pristine.open('wb') as out:
            count = 0
            while chunk := source.read(4 * 1024 * 1024):
                count += len(chunk)
                if count > 8 * 1024**3:
                    raise ValueError('Expanded image exceeds 8 GiB')
                out.write(chunk)
    boot, root = partitions(pristine)
    root_path = folder / 'root.ext4'
    with pristine.open('rb') as source, root_path.open('wb') as out:
        source.seek(root[1] * 512)
        out.truncate(root[2] * 512)
        sparse_copy(source, out, root[2] * 512)
    fat = str(pristine) + '@@' + str(boot[1] * 512)
    for source, target in ((kernel, 'kernel.img'), (dtb, 'pi.dtb')):
        command(['mcopy', '-o', '-i', fat, '::' + source, str(folder / target)])
    k = folder / 'kernel.img'
    with k.open('rb') as stream:
        compressed_kernel = stream.read(2) == b'\x1f\x8b'
    if compressed_kernel:
        with gzip.open(k, 'rb') as source, (folder / 'kernel').open('wb') as out:
            shutil.copyfileobj(source, out)
    else:
        shutil.copyfile(k, folder / 'kernel')
    # Model the SDHCI bus actually wired by QEMU. Disable unimplemented onboard
    # Bluetooth/watchdog; this deliberately does not validate those peripherals.
    def prop(node, key, value):
        command(['fdtput', '-ts', str(folder / 'pi.dtb'), node, key, value])
    if machine == 'raspi3b':
        prop('/aliases', 'serial0', '/soc/serial@7e201000')
        prop('/aliases', 'serial1', '/soc/serial@7e215040')
        prop('/soc/serial@7e201000/bluetooth', 'status', 'disabled')
        prop('/soc/mmcnr@7e300000', 'status', 'disabled')
    command(['fdtput', '-d', str(folder / 'pi.dtb'), '/soc/serial@7e201000', 'skip-init'])
    prop('/soc/mmc@7e202000', 'status', 'disabled')
    prop('/soc/mmc@7e300000', 'status', 'okay')
    prop('/aliases', 'mmc0', '/soc/mmc@7e300000')
    prop('/soc/watchdog@7e100000', 'status', 'disabled')
    for key in ('dmas', 'dma-names'):
        command(['fdtput', '-d', str(folder / 'pi.dtb'), '/soc/mmc@7e300000', key])
    # Source is copied explicitly into /lab/payload by the desktop runner.
    payload = cache / 'payload'
    script = ['mkdir /opt', 'mkdir /opt/pi-audio-lab']
    for name in ('pi', 'tests', 'tools'):
        script.append('mkdir /opt/pi-audio-lab/' + name)
    for name in PAYLOAD:
        script.append(f'write {payload / name} /opt/pi-audio-lab/{name}')
    script += [f'write {payload / "guest_checks.py"} /opt/pi-audio-lab/guest_checks.py',
               f'write {payload / "guest-init.sh"} /pi-audio-lab-init',
               'set_inode_field /pi-audio-lab-init mode 0100755']
    instructions = folder / 'debugfs.txt'
    instructions.write_text('\n'.join(script) + '\n')
    check = command(['debugfs', '-w', '-f', str(instructions), str(root_path)], capture_output=True, text=True)
    # debugfs may return zero on command errors: verify every written file.
    for name in (*PAYLOAD, 'guest_checks.py'):
        check = command(['debugfs', '-R', 'stat /opt/pi-audio-lab/' + name, str(root_path)], capture_output=True, text=True)
        if not re.search(r'Inode:\s+\d+', check.stdout):
            raise ValueError('Guest payload file missing: ' + name)
    base = folder / 'base.raw'
    with base.open('wb') as out, pristine.open('rb') as source:
        out.truncate(4 * 1024**3)
        sparse_copy(source, out, root[1] * 512)
        with root_path.open('rb') as source:
            sparse_copy(source, out, root[2] * 512)
    root_path.unlink()
    overlay = folder / 'run.qcow2'
    overlay.unlink(missing_ok=True)
    command(['qemu-img', 'create', '-f', 'qcow2', '-F', 'raw', '-b', str(base), str(overlay)])


def gzip_or_xz(path):
    import lzma
    return lzma.open(path, 'rb')


def run_case(case, cache, timeout):
    profile, machine, _, _, expected_arch, expected_supported = CASES[case]
    download(profile, cache)
    runner(cache, ['python3', '/lab/run.py', '--prepare', case])
    folder = cache / case
    name = 'pi-audio-lab-' + uuid.uuid4().hex[:12]
    binary = 'qemu-system-arm' if machine == 'raspi2b' else 'qemu-system-aarch64'
    qemu = [binary, '-machine', machine, '-smp', '4', '-m', '1G',
            '-display', 'none', '-serial', 'stdio', '-monitor', 'none',
            '-kernel', f'/lab/{case}/kernel', '-dtb', f'/lab/{case}/pi.dtb',
            '-drive', f'file=/lab/{case}/run.qcow2,if=sd,format=qcow2',
            '-append', 'console=ttyAMA0,115200 earlycon=pl011,mmio32,0x3f201000 keep_bootcon root=/dev/mmcblk0p2 rootwait rw init=/pi-audio-lab-init loglevel=4 panic=0']
    args = ['docker', 'run', '--rm', '--init', '--name', name,
        '--label', 'io.pi-audio-tray.virtual-lab=1', '--network', 'none', '--read-only',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--memory', '2g', '--cpus', '3', '--pids-limit', '128',
        '--user', f'{os.getuid()}:{os.getgid()}', '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m',
        '-v', f'{cache}:/lab:rw', RUNNER, *qemu]
    log = folder / 'serial.log'
    started, report, complete = time.monotonic(), None, False
    try:
        with log.open('wb') as out:
            process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
            while process.poll() is None:
                if log.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError('Guest serial output exceeded 8 MiB')
                text = log.read_text(errors='replace')
                complete = 'PI_AUDIO_LAB_FINISHED' in text
                if complete:
                    break
                if time.monotonic() - started > timeout:
                    raise TimeoutError('Guest boot/test timeout; inspect ' + str(log))
                time.sleep(0.5)
    finally:
        # Stop only this random, task-owned VM; never enumerate/stop other containers.
        subprocess.run(['docker', 'stop', '-t', '2', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        if 'process' in locals():
            process.wait(timeout=15)
    text = log.read_text(errors='replace')
    complete = 'PI_AUDIO_LAB_FINISHED' in text
    for line in text.splitlines():
        if line.startswith('PI_AUDIO_LAB_RESULT='):
            report = json.loads(line.split('=', 1)[1])
    if not complete or report is None:
        raise RuntimeError('Guest did not complete; inspect ' + str(log))
    tests = report.get('tests', {})
    probe = report.get('probe', {})
    passed = (report.get('userland') == expected_arch
        and probe.get('supported') is expected_supported
        and tests.get('run', 0) > 0 and not tests.get('failures') and not tests.get('errors')
        and report.get('real_dbus_agent', {}).get('passed', True))
    report.update(case=case, expected_supported=expected_supported, passed=passed,
                  image_sha256=IMAGES[profile]['sha256'])
    (folder / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=Path.home() / '.cache/pi-audio-virtual-tests')
    parser.add_argument('--cases', nargs='+', choices=CASES, default=list(CASES))
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--build', action='store_true', help='Build the unprivileged QEMU runner first')
    parser.add_argument('--prepare', choices=CASES, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 30 <= args.timeout <= 3600:
        parser.error('Choose a timeout between 30 and 3600 seconds')
    if args.prepare:
        if Path(__file__).resolve() != Path('/lab/run.py') or not Path('/.dockerenv').exists():
            parser.error('Internal preparation is allowed only in the isolated runner')
        prepare_inside(args.prepare)
        return
    if sys.platform != 'linux':
        parser.error('This lab runner is currently tested only on Linux with Docker')
    if os.geteuid() == 0:
        parser.error('Run the lab as a normal user with existing Docker access, not sudo/root')
    cache = args.cache.expanduser().resolve()
    if any(c in str(cache) for c in (':', '\n', '\r', ' ')):
        parser.error('Choose a cache path without spaces, colons or line breaks')
    if cache == ROOT or cache in ROOT.parents:
        parser.error('Use a dedicated lab cache, not the source/home root')
    marker = cache / '.pi-audio-virtual-lab'
    if cache.exists() and any(cache.iterdir()) and not marker.is_file():
        parser.error('Cache must be empty or an existing marked Pi Audio virtual lab')
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(cache, 0o700)
    marker.write_text('Pi Audio disposable virtual test cache, version 1\n')
    if args.build:
        command(['docker', 'build', '-t', RUNNER, str(HERE)])
    payload = cache / 'payload'
    payload.mkdir(exist_ok=True)
    for name in PAYLOAD:
        path = payload / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    for name in ('guest_checks.py', 'guest-init.sh'):
        shutil.copyfile(HERE / name, payload / name)
    shutil.copyfile(Path(__file__), cache / 'run.py')
    results = []
    for case in args.cases:
        try:
            result = run_case(case, cache, args.timeout)
        except Exception as error:
            result = {'case': case, 'passed': False, 'error': str(error)}
            print(json.dumps(result), flush=True)
        results.append(result)
    (cache / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    if not all(result['passed'] for result in results):
        raise SystemExit('Some virtual checks failed; see results.json and per-case serial logs')


if __name__ == '__main__':
    main()
