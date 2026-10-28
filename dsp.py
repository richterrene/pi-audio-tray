"""Validated on-demand EQ requests and the fixed administrator deployment bundle."""
import json

from core import connect, display_text, run
import dsp_config
import provision

HELPER = '/usr/local/lib/pi-audio-tray/pi-dsp'
STATES = {'active', 'inactive', 'failed', 'activating', 'deactivating', 'unknown'}
PI_FILES = ('install-dsp.py', 'setup-receiver.py', 'pi-audio-dsp.service')
TOP_FILES = ('pi_dsp.py', 'dsp_config.py')


def validate_status(value):
    fields = {'protocol', 'installed', 'supported', 'state', 'settings', 'configuration_error',
              'outputs', 'target_available', 'active', 'processing', 'headroom_db'}
    if not isinstance(value, dict) or set(value) != fields or type(value['protocol']) is not int or value['protocol'] != 1:
        raise ValueError('Invalid equalizer status')
    if any(type(value[k]) is not bool for k in ('installed', 'supported', 'configuration_error', 'target_available', 'active', 'processing')) or not isinstance(value['state'], str) or value['state'] not in STATES:
        raise ValueError('Invalid equalizer state')
    settings = dsp_config.validate(value['settings']) if value['settings'] is not None else None
    if type(value['headroom_db']) is not int or value['headroom_db'] != (dsp_config.headroom(settings) if settings else 0):
        raise ValueError('Invalid headroom')
    if not isinstance(value['outputs'], list) or len(value['outputs']) > 32:
        raise ValueError('Invalid output list')
    names = set()
    for output in value['outputs']:
        if not isinstance(output, dict) or set(output) != {'name', 'description', 'default'}:
            raise ValueError('Invalid physical output')
        dsp_config.validate({'enabled': False, 'target': output['name'], 'gains': [0] * 10, 'preamp': 0})
        if output['name'] in names or type(output['default']) is not bool or not isinstance(output['description'], str) or not output['description'] or display_text(output['description'], 200) != output['description']:
            raise ValueError('Invalid output description')
        names.add(output['name'])
    available = bool(settings and settings['target'] in names)
    if value['target_available'] != available or (value['processing'] and not value['active']):
        raise ValueError('Inconsistent equalizer status')
    if value['active'] and not (value['installed'] and value['supported'] and available and settings['enabled'] and value['state'] == 'active'):
        raise ValueError('Inconsistent active equalizer')
    return value


def request(config, action='status', settings=None):
    if action not in ('status', 'configure', 'bypass'):
        raise ValueError('Unsupported equalizer action')
    if action == 'configure':
        settings = dsp_config.validate(settings)
    elif settings is not None:
        raise ValueError('Unexpected equalizer settings')
    client = connect(config)
    try:
        if action == 'status':
            command, data = '/usr/bin/python3 -I - status', (provision.ROOT / 'pi_dsp.py').read_text()
        else:
            command = HELPER + ' ' + action
            data = json.dumps(settings) if settings is not None else None
        return validate_status(json.loads(run(client, command, input_data=data, timeout=60)))
    finally:
        client.close()


def install(config, password, progress):
    return provision.administrator_install(config, {}, password, progress, 'install-dsp.py',
        PI_FILES, TOP_FILES, failure_log='/var/lib/pi-audio-dsp/install.log')
