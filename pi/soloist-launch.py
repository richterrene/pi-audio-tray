#!/usr/bin/python3
"""Start the official client using a private, local key file."""
import os
from pathlib import Path
import sys

root = Path.home() / '.config/soloist'
try:
    keyfile = root / 'api-key'
    if keyfile.stat().st_mode & 0o077:
        raise ValueError('API key file must have mode 600')
    key = keyfile.read_text().strip()
    if not key or len(key) > 16384 or any(c.isspace() for c in key):
        raise ValueError('Invalid API key file')
    namefile = root / 'device-name'
    name = namefile.read_text().strip() if namefile.exists() else 'Pi Audio'
except (OSError, ValueError) as exc:
    print('Soloist configuration unavailable: ' + str(exc), file=sys.stderr)
    sys.exit(78)

os.execv('/usr/local/bin/soloist', ['soloist', '-n', name, '-k', key,
    '-i', '40', '-z', '100', '--ws', '127.0.0.1:0'])
