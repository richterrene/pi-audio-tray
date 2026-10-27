"""Small, strict schema shared by the desktop and privileged setup program."""
import re

FIELDS = {'music', 'web', 'updates', 'cockpit', 'shutdown', 'privacy', 'usb_uuid', 'hostname'}


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
