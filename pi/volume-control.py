#!/usr/bin/python3 -I
"""Normal-user master volume, limited to the verified current PipeWire sink."""
import json
import math
import re
import subprocess
import sys


def run(*args):
    result = subprocess.run(args, check=True, capture_output=True, text=True, timeout=10)
    if len(result.stdout) > 65536: raise ValueError('Audio output response exceeds limit')
    return result.stdout


def status():
    data = run('/usr/bin/wpctl', 'inspect', '@DEFAULT_AUDIO_SINK@')
    identifier = re.search(r'^id (\d+), type PipeWire:Interface:Node', data)
    serial = re.search(r'\bobject.serial = "(\d+)"', data)
    if not identifier or not serial or 'media.class = "Audio/Sink"' not in data:
        raise ValueError('No verified default audio output')
    identifier, serial = int(identifier.group(1)), int(serial.group(1))
    cookie = re.search(r'^\s*cookie:\s*(\d+)\s*$', run('/usr/bin/pw-cli', 'info', '0'), re.MULTILINE)
    if not cookie: raise ValueError('Could not identify the audio server')
    value = run('/usr/bin/wpctl', 'get-volume', str(identifier))
    match = re.fullmatch(r'Volume:\s*([0-9]+(?:\.[0-9]+)?)(\s+\[MUTED\])?\s*', value)
    if not match: raise ValueError('Invalid output volume response')
    level = float(match.group(1))
    if not math.isfinite(level) or not 0 <= level <= 10: raise ValueError('Invalid output volume')
    name = re.search(r'\bnode.description = "([^\n]*)"', data)
    return {'id': identifier, 'serial': serial, 'cookie': int(cookie.group(1)), 'volume': min(100, round(level * 100)),
            'muted': bool(match.group(2)), 'amplified': level > 1,
            'name': (name.group(1) if name else 'Default audio output')[:256]}


def operate(value):
    if value == {'action': 'status'}: return status()
    if not isinstance(value, dict) or set(value) != {'action', 'id', 'serial', 'cookie', 'value'} or value['action'] not in ('volume', 'mute'):
        raise ValueError('Invalid volume request')
    if type(value['id']) is not int or not 0 < value['id'] < 2**31 or type(value['serial']) is not int or not 0 < value['serial'] < 2**63:
        raise ValueError('Invalid audio output identity')
    if value['action'] == 'volume' and (type(value['value']) is not int or not 0 <= value['value'] <= 100):
        raise ValueError('Volume must be between 0 and 100')
    if value['action'] == 'mute' and type(value['value']) is not bool:
        raise ValueError('Invalid mute state')
    if type(value['cookie']) is not int or not 0 <= value['cookie'] < 2**32: raise ValueError('Invalid audio server identity')
    current = status()
    if (current['id'], current['serial'], current['cookie']) != (value['id'], value['serial'], value['cookie']):
        raise ValueError('Default output changed; refresh before changing its volume')
    if value['action'] == 'mute' and value['value'] is False and current['amplified']:
        raise ValueError('Reduce output to 100 percent or less before unmuting')
    if value['action'] == 'volume': run('/usr/bin/wpctl', 'set-volume', str(current['id']), str(value['value'] / 100))
    else: run('/usr/bin/wpctl', 'set-mute', str(current['id']), '1' if value['value'] else '0')
    return status()


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2: raise ValueError('One volume request is required')
        print(json.dumps(operate(json.loads(sys.argv[1]))))
    except Exception:
        print('Volume operation failed. PipeWire must be available; refresh if the default audio output changed.', file=sys.stderr)
        sys.exit(1)
