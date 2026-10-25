#!/usr/bin/python3 -I
"""Called only by the 05:30 timer. --check-only never reboots."""
from pathlib import Path
import subprocess
import sys


def main():
    result = subprocess.run(['/usr/sbin/needrestart', '-b', '-k', '-r', 'l'],
                            capture_output=True, text=True, timeout=60, check=True)
    fields = dict(line.split(': ', 1) for line in result.stdout.splitlines() if ': ' in line)
    state = fields.get('NEEDRESTART-KSTA', '0')
    required = Path('/run/reboot-required').exists() or state in ('2', '3')
    print('Reboot required: ' + ('yes' if required else 'no') + '; kernel status: ' + state)
    if '--check-only' in sys.argv or not required:
        return
    # Never interrupt a package installation or client replacement.
    busy = subprocess.run(['/usr/bin/systemctl', 'is-active', '--quiet', 'apt-daily.service',
        'apt-daily-upgrade.service', 'soloist-update.service'], timeout=10).returncode == 0
    if busy:
        print('Update still running; postponing reboot until the next 05:30 window.')
        return
    subprocess.run(['/usr/bin/systemctl', 'reboot'], check=True, timeout=10)


if __name__ == '__main__':
    main()
