"""Bounded master-volume requests using fixed normal-user PipeWire commands."""
import json
import shlex
from core import connect, run
import provision


def validate_request(value):
    if value == {'action': 'status'}: return value
    if not isinstance(value, dict) or set(value) != {'action', 'id', 'serial', 'cookie', 'value'} or value['action'] not in ('volume', 'mute'):
        raise ValueError('Invalid volume action')
    if type(value['id']) is not int or not 0 < value['id'] < 2**31 or type(value['serial']) is not int or not 0 < value['serial'] < 2**63:
        raise ValueError('Invalid volume output identity')
    if value['action'] == 'volume' and (type(value['value']) is not int or not 0 <= value['value'] <= 100): raise ValueError('Invalid volume')
    if value['action'] == 'mute' and type(value['value']) is not bool: raise ValueError('Invalid mute')
    if type(value['cookie']) is not int or not 0 <= value['cookie'] < 2**32: raise ValueError('Invalid audio server identity')
    return value


def validate_status(value):
    if not isinstance(value, dict) or set(value) != {'id', 'serial', 'cookie', 'volume', 'muted', 'amplified', 'name'}:
        raise ValueError('Invalid master-volume status')
    validate_request({'action': 'volume', 'id': value['id'], 'serial': value['serial'], 'cookie': value['cookie'], 'value': value['volume']})
    if type(value['muted']) is not bool or type(value['amplified']) is not bool or not isinstance(value['name'], str) or len(value['name']) > 256 or any(not c.isprintable() for c in value['name']):
        raise ValueError('Invalid audio output description')
    return value


def request(config, value=None):
    value = validate_request({'action': 'status'} if value is None else value)
    client = connect(config)
    try:
        source = (provision.ROOT / 'pi/volume-control.py').read_text()
        command = '/usr/bin/python3 -I - ' + shlex.quote(json.dumps(value, separators=(',', ':')))
        return validate_status(json.loads(run(client, command, input_data=source, timeout=40)))
    finally:
        client.close()
