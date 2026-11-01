"""Small, shared Bluetooth protocol; never interpolate device data into commands."""
import re
import unicodedata

MAC = re.compile(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}\Z')
OUTPUT_NAME = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.:-]{0,199}\Z')
ROLES = ('source', 'output', 'both', 'unknown')
AUDIO_SOURCE = '0000110a-0000-1000-8000-00805f9b34fb'
AUDIO_OUTPUT = '0000110b-0000-1000-8000-00805f9b34fb'
MAX_DEVICES = 128


def address(value):
    if not isinstance(value, str) or not MAC.fullmatch(value):
        raise ValueError('Invalid Bluetooth address')
    return value


def device_name(value):
    return ''.join(c for c in str(value) if c.isprintable() and unicodedata.category(c) != 'Cf')[:128]


def role(uuids):
    values = {str(v).lower() for v in uuids}
    source, output = AUDIO_SOURCE in values, AUDIO_OUTPUT in values
    return 'both' if source and output else 'source' if source else 'output' if output else 'unknown'


def request(value):
    if value == {'action': 'status'}:
        return value
    if not isinstance(value, dict):
        raise ValueError('Invalid Bluetooth request')
    action = value.get('action')
    if action == 'output':
        if set(value) != {'action', 'name'} or not isinstance(value['name'], str) or not OUTPUT_NAME.fullmatch(value['name']) or value['name'].startswith('pi_audio.'):
            raise ValueError('Invalid audio output')
        return value
    keys = {'action', 'adapter'}
    if action in ('connect', 'disconnect', 'pair', 'automatic'):
        keys.add('address')
    elif action not in ('scan', 'stop-scan'):
        raise ValueError('Unsupported Bluetooth action')
    if action == 'automatic':
        keys.add('enabled')
        if type(value.get('enabled')) is not bool:
            raise ValueError('Invalid automatic connection preference')
    if set(value) != keys:
        raise ValueError('Invalid Bluetooth request fields')
    address(value['adapter'])
    if 'address' in value:
        address(value['address'])
    return value


def status(value):
    if not isinstance(value, dict) or set(value) != {'protocol', 'adapter', 'powered', 'discovering', 'devices', 'outputs', 'degraded'}:
        raise ValueError('Invalid Bluetooth status')
    if type(value['protocol']) is not int or value['protocol'] != 1:
        raise ValueError('Unsupported Bluetooth protocol')
    address(value['adapter'])
    if any(type(value[k]) is not bool for k in ('powered', 'discovering', 'degraded')):
        raise ValueError('Invalid Bluetooth adapter state')
    if not isinstance(value['devices'], list) or len(value['devices']) > MAX_DEVICES:
        raise ValueError('Invalid Bluetooth device inventory')
    if not isinstance(value['outputs'], list) or len(value['outputs']) > 32:
        raise ValueError('Invalid audio outputs')
    names = set()
    for output in value['outputs']:
        if (not isinstance(output, dict) or set(output) != {'name', 'description', 'default'}
                or not isinstance(output['name'], str) or not OUTPUT_NAME.fullmatch(output['name'])
                or output['name'].startswith('pi_audio.') or type(output['default']) is not bool
                or not isinstance(output['description'], str) or output['description'] != device_name(output['description'])
                or output['name'] in names):
            raise ValueError('Invalid audio output')
        names.add(output['name'])
    seen = set()
    for item in value['devices']:
        if not isinstance(item, dict) or set(item) != {'address', 'name', 'role', 'paired', 'connected', 'automatic', 'paused'}:
            raise ValueError('Invalid Bluetooth device state')
        mac = address(item['address'])
        if mac in seen:
            raise ValueError('Duplicate Bluetooth device')
        seen.add(mac)
        if (not isinstance(item['name'], str) or item['name'] != device_name(item['name'])
                or item['role'] not in ROLES or any(type(item[k]) is not bool for k in ('paired', 'connected', 'automatic', 'paused'))):
            raise ValueError('Invalid Bluetooth device properties')
    return value


class ConnectionPolicy:
    """Manual connects survive auto-off; explicit disconnect suppresses retries."""
    def __init__(self):
        self.manual = set()
        self.seen = set()
        self.paused = set()

    def connect(self, key):
        self.paused.discard(key)
        self.manual.add(key)

    def disconnect(self, key):
        self.manual.discard(key)
        self.seen.discard(key)
        self.paused.add(key)

    def preference(self, key, connected):
        self.paused.discard(key)
        if connected:
            self.manual.add(key)
            self.seen.add(key)

    def blocked(self, key, automatic, connected):
        if connected:
            self.seen.add(key)
        elif key in self.seen:
            self.seen.discard(key)
            self.manual.discard(key)
        return key in self.paused or (not automatic and key not in self.manual)
