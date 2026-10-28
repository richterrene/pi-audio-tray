"""Pinned SSH transport for one-time administrator setup. No saved passwords."""
import json
from pathlib import Path
import shlex
import sys
import time
import uuid

from core import connect, run
from setup_plan import validate_plan

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
# Explicit manifest: never upload a checkout, local configuration or __pycache__.
PI_FILES = ('setup-probe.py', 'setup-receiver.py', 'soloist-launch.py',
    'soloist.service', 'soloist-update.py', 'soloist-update.service', 'soloist-update.timer',
    '50-audio-receiver.conf', '52pi-audio-upgrades', 'needrestart.conf',
    'apt-daily-timer.conf', 'apt-upgrade-timer.conf', 'reboot-if-needed.py',
    'pi-audio-reboot.service', 'pi-audio-reboot.timer', 'install-music.py',
    'install-web-login.py', 'web_login.py', 'web_login_config.py', 'renew-music-tls.py')
TOP_FILES = ('setup_plan.py', 'pi_helper.py', 'pi_music.py', 'pi_upnp.py')


def preflight(config):
    client = connect(config)
    try:
        source = (ROOT / 'pi/setup-probe.py').read_text()
        result = json.loads(run(client, '/usr/bin/python3 -I -', input_data=source, timeout=45))
        if not isinstance(result, dict) or type(result.get('supported')) is not bool:
            raise ValueError('Invalid setup inventory')
        return result
    finally:
        client.close()


def install(config, plan, password, progress):
    plan = validate_plan(plan)
    return administrator_install(config, plan, password, progress,
                                 'setup-receiver.py', PI_FILES, TOP_FILES)


def administrator_install(config, plan, password, progress, installer, pi_files, top_files,
                          failure_log='/var/lib/pi-audio-setup/install.log'):
    """Run a caller-selected, bundled installer; never accept filenames from the Pi/UI."""
    if '\n' in password or '\r' in password or len(password) > 1024:
        raise ValueError('Unsupported administrator password')
    client = connect(config)
    stage = '/tmp/pi-audio-setup-' + uuid.uuid4().hex
    try:
        with client.open_sftp() as sftp:
            sftp.get_channel().settimeout(20)
            sftp.mkdir(stage, mode=0o700)
            for name in (*pi_files, *top_files):
                source = ROOT / ('pi/' + name if name in pi_files else name)
                with sftp.file(stage + '/' + name, 'wx') as target:
                    target.write(source.read_bytes())
                sftp.chmod(stage + '/' + name, 0o600)
        # No PTY: sudo cannot echo the secret. No secret is in argv, env, or log.
        command = 'sudo -S -p "" -- /usr/bin/python3 -I ' + shlex.quote(stage + '/' + installer) + ' ' + shlex.quote(json.dumps(plan, separators=(',', ':')))
        channel = client.get_transport().open_session(timeout=10)
        try:
            channel.exec_command(command)
            channel.sendall((password + '\n').encode())
            channel.shutdown_write()
            buffer = bytearray()
            total = 0
            complete = False
            deadline = time.monotonic() + 3600
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Setup exceeded one hour; inspect the Pi before retrying')
                if channel.recv_ready():
                    data = channel.recv(8192)
                    total += len(data)
                    buffer.extend(data)
                    if total > 128 * 1024 or len(buffer) > 16384:
                        raise ValueError('Setup exceeded its progress output limit')
                    while b'\n' in buffer:
                        line, _, rest = buffer.partition(b'\n')
                        buffer = bytearray(rest)
                        item = json.loads(line)
                        if not isinstance(item, dict) or not isinstance(item.get('message'), str) or len(item['message']) > 2048:
                            raise ValueError('Invalid setup progress')
                        progress(item['message'])
                        complete = complete or item.get('step') == 'complete'
                # Do not forward sudo errors or arbitrary program diagnostics.
                if channel.recv_stderr_ready():
                    total += len(channel.recv_stderr(8192))
                    if total > 128 * 1024:
                        raise ValueError('Setup exceeded its output limit')
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                time.sleep(0.05)
            if channel.recv_exit_status() != 0 or not complete:
                raise RuntimeError('Setup did not complete. Check administrator authentication and the root-private log ' + failure_log + '. Do not reimage or remove the partial setup state.')
        finally:
            channel.close()
    finally:
        # Remove only this operation's explicit files. Never use recursive shell deletion.
        try:
            with client.open_sftp() as sftp:
                sftp.get_channel().settimeout(10)
                for name in (*pi_files, *top_files):
                    try:
                        sftp.remove(stage + '/' + name)
                    except OSError:
                        pass
                sftp.rmdir(stage)
        except Exception:
            # Connection loss must not mask the original installation outcome.
            pass
        finally:
            client.close()


# Filter on the Pi, so the desktop receives no full PipeWire graph or process metadata.
OUTPUT_PROBE = """import json, subprocess
result = subprocess.run(['pw-dump'], capture_output=True, timeout=12, check=True)
if len(result.stdout) > 4 * 1024 * 1024:
    raise ValueError('PipeWire graph exceeds size limit')
result = json.loads(result.stdout)
print(json.dumps([[str(n['id']), str(n['info']['props'].get('node.description', 'Audio output'))[:256]]
    for n in result if n.get('type') == 'PipeWire:Interface:Node'
    and n.get('info', {}).get('props', {}).get('media.class') == 'Audio/Sink'
    and type(n.get('id')) is int and 0 < n['id'] < 2**31][:64]))
"""


def _outputs(client):
    values = json.loads(run(client, '/usr/bin/python3 -I -', input_data=OUTPUT_PROBE))
    if not isinstance(values, list) or len(values) > 64:
        raise ValueError('Invalid audio output inventory')
    for value in values:
        if not isinstance(value, list) or len(value) != 2 or not all(isinstance(v, str) for v in value):
            raise ValueError('Invalid audio output')
        identifier = value[0]
        if not identifier.isascii() or not identifier.isdecimal() or not 0 < int(identifier) < 2**31 or len(value[1]) > 256:
            raise ValueError('Invalid audio output')
    return values


def audio_outputs(config):
    client = connect(config)
    try:
        return _outputs(client)
    finally:
        client.close()


def select_output(config, identifier):
    if not isinstance(identifier, str) or not identifier.isascii() or not identifier.isdecimal() or not 0 < int(identifier) < 2**31:
        raise ValueError('Invalid output selection')
    client = connect(config)
    try:
        # Recheck the node before using a stale discovery result.
        if not any(node[0] == identifier for node in _outputs(client)):
            raise ValueError('Audio output changed; refresh the output list')
        run(client, 'wpctl set-default ' + identifier + ' && wpctl set-mute ' + identifier + ' 0')
    finally:
        client.close()
