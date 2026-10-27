"""Runs inside an official Pi OS image under QEMU, never on the desktop/Pi."""
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import unittest

ROOT = Path('/opt/pi-audio-lab')
sys.path.insert(0, str(ROOT))


def main():
    started = time.monotonic()
    report = {'kernel': platform.machine(),
              'userland': subprocess.check_output(['/usr/bin/dpkg', '--print-architecture'], text=True).strip(),
              'model': Path('/proc/device-tree/model').read_text().rstrip('\0'),
              'python': platform.python_version(), 'pid1': 'test init (not systemd)',
              'hardware_tests': 'not performed', 'full_installer': 'not executed'}
    probe = subprocess.run(['/usr/sbin/runuser', '-u', 'pi-lab', '--',
            '/usr/bin/python3', '-I', str(ROOT / 'pi/setup-probe.py')],
            capture_output=True, text=True, timeout=60)
    if probe.returncode:
        report['probe_error'] = probe.stderr[-2048:]
    else:
        inventory = json.loads(probe.stdout)
        report['probe'] = {key: inventory[key] for key in
                           ('supported', 'reasons', 'architecture', 'os', 'uid')}
    suite = unittest.TestLoader().loadTestsFromNames([
        'tests.test_pi_helper', 'tests.test_maintenance', 'tests.test_web_login'])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    report['tests'] = {'run': result.testsRun, 'failures': len(result.failures),
                       'errors': len(result.errors), 'skipped': len(result.skipped)}
    # Exercise the real dbus-python/GLib agent only if the image supplies them.
    available = all(importlib.util.find_spec(name) is not None for name in ('dbus', 'gi'))
    if available and Path('/usr/bin/dbus-run-session').exists():
        bus = subprocess.run(['/usr/bin/dbus-run-session', '--', '/usr/bin/python3',
                              '-I', str(ROOT / 'tools/check_dbus_agent.py')],
                             capture_output=True, text=True, timeout=60)
        report['real_dbus_agent'] = {'passed': bus.returncode == 0,
                                     'output': (bus.stdout + bus.stderr)[-2048:]}
    else:
        report['real_dbus_agent'] = {'skipped': 'Image lacks dbus-python/GLib or dbus-run-session'}
    report['duration_seconds'] = round(time.monotonic() - started, 1)
    print('PI_AUDIO_LAB_RESULT=' + json.dumps(report), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('PI_AUDIO_LAB_RESULT=' + json.dumps({'error': type(error).__name__ + ': ' + str(error)}), flush=True)
        raise
