"""Shared, neutral-path source and license staging for desktop bundles."""
import importlib.metadata
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent


def prepare_source(work):
    source = work / 'src'
    source.mkdir()
    for name in ('app.py', 'core.py', 'pi_helper.py', 'version.py', 'LICENSE', 'THIRD_PARTY.md'):
        shutil.copy2(ROOT / name, source / name)
    (source / 'assets').mkdir()
    shutil.copy2(ROOT / 'assets/pi-audio-tray.svg', source / 'assets/pi-audio-tray.svg')
    licenses = source / 'licenses'
    licenses.mkdir()
    for candidate in (Path(sys.base_prefix) / 'LICENSE.txt',
                      Path(sys.base_prefix) / f'lib/python{sys.version_info.major}.{sys.version_info.minor}/LICENSE.txt'):
        if candidate.exists():
            shutil.copy2(candidate, licenses / 'Python-LICENSE.txt')
            break
    for distribution in importlib.metadata.distributions():
        for item in distribution.files or []:
            text = str(item)
            if '.dist-info/licenses/' in text and not item.is_absolute() and '..' not in item.parts:
                destination = licenses / text
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(distribution.locate_file(item), destination)
    return source
