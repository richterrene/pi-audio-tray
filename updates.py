"""Read-only package inventory and exactly two installed maintenance actions."""
import json
import re

from core import connect, run
import provision


def validate(value):
    fields = {'protocol', 'packages', 'total', 'truncated', 'checked_at', 'installed_at', 'reboot_required', 'control_installed', 'policy_configured'}
    if not isinstance(value, dict) or set(value) != fields or type(value['protocol']) is not int or value['protocol'] != 1:
        raise ValueError('Invalid update inventory')
    if type(value['total']) is not int or not 0 <= value['total'] <= 4096 or not isinstance(value['packages'], list) or len(value['packages']) > 256 or len(value['packages']) > value['total']:
        raise ValueError('Invalid pending update count')
    if any(type(value[key]) is not bool for key in ('truncated', 'control_installed', 'policy_configured')) or value['truncated'] != (len(value['packages']) < value['total']):
        raise ValueError('Invalid update flags')
    for key in ('checked_at', 'installed_at'):
        if value[key] is not None and (type(value[key]) is not int or not 0 < value[key] < 2**53):
            raise ValueError('Invalid update timestamp')
    if value['reboot_required'] is not None and type(value['reboot_required']) is not bool:
        raise ValueError('Invalid reboot status')
    for row in value['packages']:
        if not isinstance(row, dict) or set(row) != {'name', 'current', 'candidate'}:
            raise ValueError('Invalid package update')
        if not isinstance(row['name'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9+.:_-]{0,127}', row['name']):
            raise ValueError('Invalid package name')
        if any(not isinstance(row[key], str) or len(row[key]) > 128 or any(not c.isprintable() for c in row[key]) for key in ('current', 'candidate')):
            raise ValueError('Invalid package version')
    return value


def check(config):
    client = connect(config)
    try:
        source = (provision.ROOT / 'pi/system-updates.py').read_text()
        return validate(json.loads(run(client, '/usr/bin/python3 -I - --status', input_data=source, timeout=110)))
    finally:
        client.close()


def operate(config, operation, progress):
    if operation not in ('check', 'install'):
        raise ValueError('Unsupported system update operation')
    client = connect(config)
    try:
        channel = client.get_transport().open_session(timeout=10)
        try:
            channel.exec_command('/usr/bin/sudo -n /usr/local/lib/pi-audio-tray/pi-system-updates ' + operation)
            channel.shutdown_write()
            provision.read_progress(channel, progress, '/var/lib/pi-audio-updates/private/update.log')
        finally:
            channel.close()
    finally:
        client.close()
