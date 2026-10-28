#!/usr/bin/python3 -I
"""Normal-user EQ controls. Fixed builtin graph, no default-device/volume changes."""
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

UNIT = 'pi-audio-dsp.service'
HELPER = Path('/usr/local/lib/pi-audio-tray/pi-dsp')
PLAN = Path('/usr/local/lib/pi-audio-tray/dsp_config.py')
UNIT_FILE = Path('/usr/local/lib/systemd/user') / UNIT
DIRECTORY = Path.home() / '.config/pi-audio-tray'
SETTINGS = DIRECTORY / 'dsp.json'
GRAPH = DIRECTORY / 'dsp.conf'
INPUT, OUTPUT = 'pi_audio.dsp.input', 'pi_audio.dsp.output'
STATES = {'active', 'inactive', 'failed', 'activating', 'deactivating', 'unknown'}


def plan():
    spec = importlib.util.spec_from_file_location('dsp_config', PLAN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command(*args, check=True, timeout=12):
    value = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, check=check, timeout=timeout)
    if len(value.stdout) + len(value.stderr) > 4 * 1024**2:
        raise ValueError('DSP response exceeds size limit')
    return value


def secure_directory():
    for part in (DIRECTORY, *DIRECTORY.parents):
        if part.is_symlink():
            raise ValueError('Symbolic-link DSP paths are unsupported')
    DIRECTORY.mkdir(parents=True, exist_ok=True, mode=0o700)
    if DIRECTORY.stat().st_uid != os.getuid():
        raise ValueError('DSP configuration must belong to the audio account')
    os.chmod(DIRECTORY, 0o700)


def read_file(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 65536:
            raise ValueError('Invalid private DSP file')
        return stream.read(65537)


def write_file(path, data):
    secure_directory()
    fd, temporary = tempfile.mkstemp(prefix='.dsp-', dir=DIRECTORY)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def graph():
    value = json.loads(command('/usr/bin/pw-dump').stdout)
    if not isinstance(value, list) or len(value) > 4096:
        raise ValueError('Invalid audio graph')
    return [x for x in value if isinstance(x, dict) and x.get('type') == 'PipeWire:Interface:Node']


def properties(node):
    return (node.get('info') or {}).get('props') or {}


def outputs(nodes):
    found = []
    default = command('/usr/bin/wpctl', 'inspect', '@DEFAULT_AUDIO_SINK@', check=False).stdout
    match = re.search(r'^id (\d+), type PipeWire:Interface:Node', default)
    default_id = int(match.group(1)) if match else None
    for node in nodes:
        props = properties(node)
        name = props.get('node.name', '')
        if (props.get('media.class') != 'Audio/Sink' or type(props.get('device.id')) is not int
                or props.get('node.virtual') in (True, 'true') or props.get('audio.channels') not in (2, '2')
                or not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.:-]{0,199}', name)
                or name.startswith('pi_audio.')):
            continue
        description = str(props.get('node.description') or 'Audio output')
        description = ''.join(c for c in description if c.isprintable())[:200] or 'Audio output'
        found.append({'name': name, 'description': description, 'default': node.get('id') == default_id})
    if len(found) > 32 or len({x['name'] for x in found}) != len(found):
        raise ValueError('Ambiguous physical audio outputs')
    return found


def service_state():
    value = command('/usr/bin/systemctl', '--user', 'is-active', UNIT, check=False).stdout.strip()
    return value if value in STATES else 'unknown'


def supported():
    # Match the installer's deliberately narrow platform boundary and probe the
    # actual distro feature scripts, rather than accepting a kernel version.
    try:
        release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
        model = Path('/proc/device-tree/model').read_text().strip('\0')
        platform_ok = (release.get('ID', '').strip(chr(34)) in ('debian', 'raspbian')
                       and release.get('VERSION_ID', '').strip(chr(34)) == '13'
                       and os.uname().machine == 'aarch64' and 'Raspberry Pi' in model)
    except (OSError, ValueError):
        return False
    return platform_ok and all(Path(p).is_file() for p in (
        '/usr/bin/pipewire', '/usr/bin/wpctl', '/usr/bin/pw-dump',
        '/usr/lib/aarch64-linux-gnu/pipewire-0.3/libpipewire-module-filter-chain.so',
        '/usr/share/wireplumber/scripts/lib/filter-utils.lua'))


def status():
    installed = HELPER.is_file() and PLAN.is_file() and UNIT_FILE.is_file()
    settings, invalid = None, False
    try:
        saved = read_file(SETTINGS)
        if saved is not None and installed:
            settings = plan().validate(json.loads(saved))
    except (OSError, ValueError, TypeError):
        invalid = True
    nodes = graph()
    physical = outputs(nodes)
    filters = {properties(n).get('node.name'): n for n in nodes
               if properties(n).get('node.name') in (INPUT, OUTPUT)}
    available = bool(settings and any(n['name'] == settings['target'] for n in physical))
    state = service_state() if installed else 'inactive'
    compatible = supported()
    active = bool(installed and compatible and settings and settings['enabled'] and available and state == 'active'
                  and set(filters) == {INPUT, OUTPUT}
                  and properties(filters[INPUT]).get('filter.smart') in (True, 'true')
                  and properties(filters[INPUT]).get('filter.smart.disabled') in (False, 'false'))
    return {'protocol': 1, 'installed': installed, 'supported': compatible, 'state': state,
            'settings': settings, 'configuration_error': invalid, 'outputs': physical,
            'target_available': available, 'active': active,
            'processing': bool(active and filters[OUTPUT].get('info', {}).get('state') == 'running'),
            'headroom_db': plan().headroom(settings) if settings else 0}


def configure(value):
    module = plan()
    value = module.validate(value)
    if not HELPER.is_file() or not UNIT_FILE.is_file() or not supported():
        raise ValueError('Install the supported DSP helper first')
    if value['enabled'] and value['target'] not in {x['name'] for x in outputs(graph())}:
        raise ValueError('The selected physical stereo output is unavailable')
    secure_directory()
    import fcntl
    fd = os.open(DIRECTORY / 'dsp.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = {path: read_file(path) for path in (SETTINGS, GRAPH)}
        was_active = service_state() == 'active'
        was_enabled = command('/usr/bin/systemctl', '--user', 'is-enabled', UNIT, check=False).stdout.strip() in ('enabled', 'enabled-runtime')
        # Keep the last saved preferences as a private, bounded recovery copy.
        if previous[SETTINGS] is not None:
            write_file(DIRECTORY / 'dsp.previous.json', previous[SETTINGS])
        try:
            write_file(GRAPH, module.configuration(value).encode())
            write_file(SETTINGS, json.dumps(value).encode())
            if value['enabled']:
                command('/usr/bin/systemctl', '--user', 'reset-failed', UNIT, check=False)
                command('/usr/bin/systemctl', '--user', 'enable', UNIT)
                command('/usr/bin/systemctl', '--user', 'restart', UNIT)
                deadline = time.monotonic() + 7
                while time.monotonic() < deadline:
                    result = status()
                    if result['active']:
                        return result
                    if result['state'] == 'failed':
                        break
                    time.sleep(.2)
                raise RuntimeError('DSP did not become ready; the graph is being restored')
            command('/usr/bin/systemctl', '--user', 'disable', '--now', UNIT)
            return status()
        except Exception:
            command('/usr/bin/systemctl', '--user', 'stop', UNIT, check=False)
            for path, data in previous.items():
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    write_file(path, data)
            command('/usr/bin/systemctl', '--user', 'enable' if was_enabled else 'disable', UNIT, check=False)
            if was_active and previous[GRAPH] is not None:
                command('/usr/bin/systemctl', '--user', 'reset-failed', UNIT, check=False)
                command('/usr/bin/systemctl', '--user', 'start', UNIT, check=False)
            raise


def bypass():
    # Stop works even with missing hardware or unreadable/corrupt preferences.
    # The smart filter disappears and WirePlumber reconnects directly.
    secure_directory()
    import fcntl
    fd = os.open(DIRECTORY / 'dsp.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        command('/usr/bin/systemctl', '--user', 'disable', '--now', UNIT)
        try:
            saved = read_file(SETTINGS)
            value = plan().validate(json.loads(saved)) if saved is not None else None
        except (OSError, ValueError, TypeError):
            value = None
        if value is not None:
            value['enabled'] = False
            write_file(GRAPH, plan().configuration(value).encode())
            write_file(SETTINGS, json.dumps(value).encode())
        return status()


def main():
    if os.geteuid() == 0:
        raise ValueError('Run DSP control as the audio account')
    if sys.argv[1:] == ['status']:
        return status()
    if sys.argv[1:] == ['bypass']:
        return bypass()
    if sys.argv[1:] != ['configure']:
        raise ValueError('Unsupported DSP action')
    raw = sys.stdin.buffer.read(4097)
    if len(raw) > 4096:
        raise ValueError('DSP settings exceed size limit')
    return configure(json.loads(raw))


if __name__ == '__main__':
    try:
        print(json.dumps(main()))
    except Exception:
        print('Equalizer operation failed. Check its saved configuration and user service journal; bypass remains available.', file=sys.stderr)
        sys.exit(1)
