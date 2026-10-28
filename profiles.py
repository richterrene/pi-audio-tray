"""Private named receiver profiles; one selected connection at a time."""
import json
import re
import stat
import unicodedata
import uuid

import core
from platform_support import reject_link

MAX_PROFILES = 32


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 80 or any(not c.isprintable() or unicodedata.category(c) == 'Cf' for c in value):
        raise ValueError('Profile names must contain 1–80 printable characters')
    return value.strip()


def validate(data):
    if not isinstance(data, dict) or set(data) != {'version', 'active', 'language', 'profiles'} or data['version'] != 1:
        raise ValueError('Invalid Pi profiles file')
    rows = data['profiles']
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_PROFILES:
        raise ValueError('Keep between 1 and 32 Pi profiles')
    language = core.validate_config({'language': data['language']})['language']
    result, identifiers, names = [], set(), set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'id', 'name', 'connection'}:
            raise ValueError('Invalid Pi profile')
        identifier, name = row['id'], _name(row['name'])
        if not isinstance(identifier, str) or not re.fullmatch(r'default|[0-9a-f]{32}', identifier) or identifier in identifiers or name.casefold() in names:
            raise ValueError('Pi profiles need unique names and identifiers')
        connection = core.validate_config(row['connection'])
        connection.pop('language')
        result.append({'id': identifier, 'name': name, 'connection': connection})
        identifiers.add(identifier)
        names.add(name.casefold())
    if data['active'] not in identifiers:
        raise ValueError('Selected Pi profile is missing')
    return {'version': 1, 'active': data['active'], 'language': language, 'profiles': result}


def load():
    path = core.config_dir() / 'profiles.json'
    reject_link(path, missing_ok=True)
    try:
        info = path.lstat()
    except FileNotFoundError:
        connection = core.load_legacy_config()
        language = connection.pop('language', 'en')
        return {'version': 1, 'active': 'default', 'language': language,
                'profiles': [{'id': 'default', 'name': 'My Pi', 'connection': connection}]}
    if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
        raise ValueError('Pi profiles must be a small regular file, not a link')
    return validate(json.loads(path.read_text(encoding='utf-8')))


def save(data):
    data = validate(data)
    path = core.config_dir() / 'profiles.json'
    reject_link(path, missing_ok=True)
    core.atomic_private_write(path, (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode())
    return data


def current_config():
    data = load()
    row = next(row for row in data['profiles'] if row['id'] == data['active'])
    return core.validate_config({**row['connection'], 'language': data['language']})


def save_current_config(config):
    config = core.validate_config(config)
    data = load()
    if not (core.config_dir() / 'profiles.json').exists():
        # Establish the old authoritative state before touching its legacy
        # export. A failed first migration must not select an unsaved receiver.
        save(data)
    data['language'] = config['language']
    for row in data['profiles']:
        if row['id'] == data['active']:
            row['connection'] = {key: value for key, value in config.items() if key != 'language'}
    # Preserve the old file for tools/older app versions. profiles.json remains
    # authoritative if a crash occurs between the two atomic file writes.
    core.atomic_private_write(core.config_dir() / 'config.json', (json.dumps(config, indent=2) + '\n').encode())
    save(data)


def activate(identifier):
    data = load()
    if identifier not in {row['id'] for row in data['profiles']}:
        raise ValueError('Unknown Pi profile')
    data['active'] = identifier
    row = next(row for row in data['profiles'] if row['id'] == identifier)
    config = core.validate_config({**row['connection'], 'language': data['language']})
    core.atomic_private_write(core.config_dir() / 'config.json', (json.dumps(config, indent=2) + '\n').encode())
    save(data)
    return config


def add(name):
    data = load()
    identifier = uuid.uuid4().hex
    connection = core.DEFAULTS.copy()
    connection.pop('language')
    data['profiles'].append({'id': identifier, 'name': _name(name), 'connection': connection})
    save(data)
    return identifier


def rename(identifier, name):
    data = load()
    row = next((row for row in data['profiles'] if row['id'] == identifier), None)
    if row is None:
        raise ValueError('Unknown Pi profile')
    row['name'] = _name(name)
    save(data)


def remove(identifier):
    data = load()
    if identifier == data['active']:
        raise ValueError('Switch to another Pi before removing this profile')
    rows = [row for row in data['profiles'] if row['id'] != identifier]
    if len(rows) == len(data['profiles']):
        raise ValueError('Unknown Pi profile')
    data['profiles'] = rows
    save(data)
