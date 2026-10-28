"""Desktop side of the fixed audio add-on protocol over verified SSH."""
import json
import re

from core import connect, run
import provision
from pi_audio import validate_settings, STATES

HELPER = '/usr/local/lib/pi-audio-tray/pi-audio'
PI_FILES = ('install-airplay.py', 'setup-receiver.py', 'pi-audio-airplay.service')
TOP_FILES = ('pi_audio.py',)


def validate_status(value):
    if not isinstance(value, dict) or set(value) != {'protocol', 'installed', 'configured', 'name', 'state', 'enabled', 'pipewire', 'version'}:
        raise ValueError('Invalid audio status')
    if type(value['protocol']) is not int or value['protocol'] != 1:
        raise ValueError('Unsupported audio protocol')
    if any(type(value[key]) is not bool for key in ('installed', 'configured', 'enabled')):
        raise ValueError('Invalid audio status flags')
    if any(not isinstance(value[key], str) or value[key] not in STATES for key in ('state', 'pipewire')):
        raise ValueError('Invalid audio service state')
    if not isinstance(value['version'], str) or len(value['version']) > 256 or any(not c.isprintable() for c in value['version']):
        raise ValueError('Invalid audio version')
    if not isinstance(value['name'], str) or (value['name'] and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,49}', value['name'])):
        raise ValueError('Invalid AirPlay name')
    return value


def request(config, name, settings=None):
    if name not in ('status', 'configure', 'enable', 'disable'):
        raise ValueError('Unsupported audio action')
    if name == 'configure':
        settings = validate_settings(settings)
    elif settings is not None:
        raise ValueError('Unexpected audio settings')
    client = connect(config)
    try:
        return validate_status(json.loads(run(client, HELPER + ' ' + name,
            input_data=json.dumps(settings) if settings is not None else None, timeout=60)))
    finally:
        client.close()


def install(config, password, progress):
    # No secrets in this plan/command line. AirPlay credentials use the normal-user helper later.
    return provision.administrator_install(config, {}, password, progress,
                                          'install-airplay.py', PI_FILES, TOP_FILES,
                                          failure_log='/var/lib/pi-audio-airplay/install.log')
