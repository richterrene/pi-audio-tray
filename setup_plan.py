"""Small, strict schema shared by the desktop and privileged setup program."""
import re

FIELDS = {'music', 'web', 'updates', 'cockpit', 'shutdown', 'privacy', 'usb_uuid', 'hostname'}
COMPONENTS = {'receiver', 'music', 'web', 'updates', 'cockpit', 'shutdown', 'privacy'}


def validate_maintenance(value):
    if not isinstance(value, dict) or set(value) != {'operation', 'components', 'options'} or value['operation'] != 'maintain':
        raise ValueError('Invalid maintenance request')
    selected = value['components']
    if not isinstance(selected, list) or not selected or any(not isinstance(x, str) or x not in COMPONENTS for x in selected) or len(set(selected)) != len(selected):
        raise ValueError('Select supported components to install or repair')
    options = validate_plan(value['options'])
    # Checkboxes express actions, not desired absence. Dependencies are explicit.
    for key in ('music', 'web', 'updates', 'cockpit', 'shutdown', 'privacy'):
        expected = key in selected or (key == 'music' and 'web' in selected)
        if options[key] != expected:
            raise ValueError('Component choices and options differ')
    return {'operation': 'maintain', 'components': sorted(selected), 'options': options}


def validate_plan(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError('Invalid setup plan')
    for name in ('music', 'web', 'updates', 'cockpit', 'shutdown', 'privacy'):
        if type(value[name]) is not bool:
            raise ValueError('Invalid component selection')
    if not isinstance(value['hostname'], str) or not re.fullmatch(
            r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*', value['hostname']) or len(value['hostname']) > 253:
        raise ValueError('Enter the Pi DNS hostname, for example audio-pi.local')
    if not isinstance(value['usb_uuid'], str):
        raise ValueError('Invalid USB selection')
    if value['music']:
        if not re.fullmatch(r'[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}', value['usb_uuid']):
            raise ValueError('Select a FAT32 USB drive with a Music directory')
    elif value['usb_uuid']:
        raise ValueError('USB selection requires music playback')
    if value['web'] and not value['music']:
        raise ValueError('The web player requires MPD music playback')
    return dict(value)
