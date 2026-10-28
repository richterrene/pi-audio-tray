#!/usr/bin/env python3
"""Build in a neutral directory so local usernames never enter compiled paths."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from version import VERSION
from packaging_support import prepare_source

ROOT = Path(__file__).resolve().parent
RUNTIME_URL = 'https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64'
RUNTIME_SHA256 = '2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d'


def main():
    if os.uname().machine != 'x86_64':
        raise SystemExit('This build script targets x86_64 Linux.')
    work = Path(tempfile.mkdtemp(prefix='pi-audio-tray-build-', dir='/tmp'))
    try:
        source = prepare_source(work)
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
            '--name', 'pi-audio-tray', '--onedir', '--windowed',
            '--add-data', 'translations:translations', '--add-data', 'pi:pi', '--add-data', 'setup_plan.py:.', '--add-data', 'pi_policy.py:.', '--add-data', 'pi_audio.py:.', '--add-data', 'pi_helper.py:.', '--add-data', 'pi_music.py:.', '--add-data', 'pi_upnp.py:.', '--add-data', 'LICENSE:.',
            '--add-data', 'THIRD_PARTY.md:.', '--add-data', 'licenses:licenses',
            '--add-data', 'assets/pi-audio-tray.svg:assets', '--add-data', 'assets/flags:assets/flags',
            '--distpath', str(work / 'frozen'), '--workpath', str(work / 'pyinstaller'),
            '--specpath', str(source), 'app.py'], cwd=source, check=True,
            env={**os.environ, 'PYINSTALLER_CONFIG_DIR': str(work / 'cache')})
        appdir = work / 'PiAudioTray.AppDir'
        appdir.mkdir()
        shutil.copytree(work / 'frozen/pi-audio-tray', appdir / 'usr/bin')
        shutil.copy2(ROOT / 'assets/pi-audio-tray.svg', appdir / 'pi-audio-tray.svg')
        shutil.copy2(ROOT / 'assets/pi-audio-tray.desktop', appdir / 'pi-audio-tray.desktop')
        (appdir / '.DirIcon').symlink_to('pi-audio-tray.svg')
        (appdir / 'AppRun').write_text('#!/bin/sh\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec "$HERE/usr/bin/pi-audio-tray" "$@"\n')
        (appdir / 'AppRun').chmod(0o755)
        runtime = work / 'runtime'
        cached = os.environ.get('APPIMAGE_RUNTIME')
        if cached:
            shutil.copyfile(cached, runtime)
        else:
            urllib.request.urlretrieve(RUNTIME_URL, runtime)
        if hashlib.sha256(runtime.read_bytes()).hexdigest() != RUNTIME_SHA256:
            raise SystemExit('AppImage runtime checksum mismatch')
        squashfs = work / 'payload.squashfs'
        subprocess.run(['mksquashfs', str(appdir), str(squashfs), '-noappend',
                        '-all-root', '-comp', 'gzip', '-processors', '2', '-no-progress'], check=True)
        destination = ROOT / f'dist/PiAudioTray-{VERSION}-x86_64.AppImage'
        destination.parent.mkdir(exist_ok=True)
        with destination.open('wb') as out:
            for part in (runtime, squashfs):
                with part.open('rb') as f:
                    shutil.copyfileobj(f, out)
        destination.chmod(0o755)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        destination.with_suffix('.AppImage.sha256').write_text(digest + '  ' + destination.name + '\n')
        print('Built', destination)
    finally:
        shutil.rmtree(work)


if __name__ == '__main__':
    main()
