"""Strict, on-demand component inventory and additive maintenance transport."""
import json
import re

from core import connect, run
import provision
from setup_plan import validate_maintenance

IDS = ('spotify', 'bluetooth', 'helpers', 'music', 'web', 'updates', 'cockpit', 'privacy', 'shutdown', 'airplay')
ACTIONS = {'receiver', 'music', 'web', 'updates', 'cockpit', 'privacy', 'shutdown', 'audio'}
MAPPING = {name: 'receiver' if name in ('spotify', 'bluetooth', 'helpers') else 'audio' if name == 'airplay' else name for name in IDS}


def validate(value):
    if not isinstance(value, dict) or set(value) != {'protocol', 'managed', 'supported', 'rows', 'usb_uuid', 'hostname', 'model', 'architecture', 'os', 'disks'} or type(value['protocol']) is not int or value['protocol'] != 1:
        raise ValueError('Invalid component inventory')
    if any(type(value[key]) is not bool for key in ('managed', 'supported')):
        raise ValueError('Invalid component ownership/platform flags')
    if not isinstance(value['rows'], list) or len(value['rows']) != len(IDS):
        raise ValueError('Invalid component rows')
    seen = set()
    for row in value['rows']:
        if not isinstance(row, dict) or set(row) != {'id', 'action', 'installed', 'configured', 'running', 'managed', 'details'}:
            raise ValueError('Invalid component facts')
        if not isinstance(row['id'], str) or row['id'] not in IDS or row['id'] in seen or not isinstance(row['action'], str) or row['action'] not in ACTIONS:
            raise ValueError('Invalid component identity')
        seen.add(row['id'])
        if row['action'] != MAPPING[row['id']]:
            raise ValueError('Invalid component repair mapping')
        if any(row[key] is not None and type(row[key]) is not bool for key in ('installed', 'configured', 'running')) or type(row['managed']) is not bool:
            raise ValueError('Invalid component check result')
        if not isinstance(row['details'], str) or len(row['details']) > 2048 or any(not c.isprintable() for c in row['details']):
            raise ValueError('Invalid component detail')
    for key, limit in (('usb_uuid', 9), ('hostname', 253), ('model', 200), ('architecture', 32), ('os', 200)):
        if not isinstance(value[key], str) or len(value[key]) > limit or any(not c.isprintable() for c in value[key]):
            raise ValueError('Invalid component inventory metadata')
    if value['usb_uuid'] and not re.fullmatch(r'[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}', value['usb_uuid']):
        raise ValueError('Invalid music filesystem UUID')
    if not isinstance(value['disks'], dict) or set(value['disks']) != {'blockdevices'} or not isinstance(value['disks']['blockdevices'], list) or len(value['disks']['blockdevices']) > 64:
        raise ValueError('Invalid disk inventory')
    count = 0
    def visit(devices, depth=0):
        nonlocal count
        if not isinstance(devices, list) or depth > 8:
            raise ValueError('Invalid disk tree')
        for device in devices:
            count += 1
            if count > 256 or not isinstance(device, dict):
                raise ValueError('Invalid disk tree')
            for key in ('name', 'type', 'fstype', 'uuid', 'tran'):
                field = device.get(key)
                if field is not None and (not isinstance(field, str) or len(field) > 256 or any(not c.isprintable() for c in field)):
                    raise ValueError('Invalid disk metadata')
            if not isinstance(device.get('mountpoints', []), list) or any(p is not None and (not isinstance(p, str) or len(p) > 1024) for p in device.get('mountpoints', [])):
                raise ValueError('Invalid mount inventory')
            visit(device.get('children', []), depth + 1)
    visit(value['disks']['blockdevices'])
    return value


def check(config):
    client = connect(config)
    try:
        source = (provision.ROOT / 'pi/component-probe.py').read_text()
        return validate(json.loads(run(client, '/usr/bin/python3 -I -', input_data=source, timeout=90)))
    finally:
        client.close()


def maintain(config, request, password, progress):
    request = validate_maintenance(request)
    return provision.administrator_install(config, request, password, progress,
                                          'setup-receiver.py', provision.PI_FILES, provision.TOP_FILES)
