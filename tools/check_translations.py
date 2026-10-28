#!/usr/bin/env python3
"""Extract fixed UI templates and verify data-only translation catalogs."""
import ast
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import i18n


def sources():
    result = {'active', 'inactive', 'failed', 'unknown', 'play', 'pause', 'stop',
              'Save', 'Cancel', 'Yes', 'No', 'OK'}
    for filename in ('app.py', 'music_ui.py', 'network_ui.py', 'setup_ui.py', 'profile_ui.py', 'ui_style.py', 'audio_ui.py', 'component_ui.py', 'maintenance_ui.py'):
        for node in ast.walk(ast.parse((ROOT / filename).read_text(encoding='utf-8'))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'tr' and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    result.add(first.value)
    return result


def main():
    expected = sources()
    if '--update-template' in sys.argv:
        (i18n.ROOT / 'en.json').write_text(json.dumps({'language': 'en', 'name': 'English', 'country': 'GB',
            'messages': {key: key for key in sorted(expected)}}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    failed = False
    for path in sorted(i18n.ROOT.glob('*.json')):
        catalog = i18n.read_catalog(path)
        missing = expected - catalog['messages'].keys()
        extra = catalog['messages'].keys() - expected
        print(f'{path.stem}: {len(expected) - len(missing)}/{len(expected)}; missing={len(missing)}, obsolete={len(extra)}')
        failed |= bool(missing or extra)
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
