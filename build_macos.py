#!/usr/bin/env python3
"""Build a macOS menu-bar .app and distributable ZIP on a native Mac runner."""
import hashlib
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile

from packaging_support import prepare_source
from version import VERSION

ROOT = Path(__file__).resolve().parent


def main():
    if sys.platform != 'darwin':
        raise SystemExit('Build this bundle on macOS; Linux cross-builds are not supported.')
    arch = platform.machine()
    if arch not in ('arm64', 'x86_64'):
        raise SystemExit('Unsupported desktop architecture: ' + arch)
    work = Path(tempfile.mkdtemp(prefix='pi-audio-tray-macos-', dir='/tmp'))
    try:
        source = prepare_source(work)
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtGui import QPainter, QPixmap
        from PySide6.QtCore import Qt
        from PySide6.QtSvg import QSvgRenderer
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        renderer = QSvgRenderer(str(source / 'assets/pi-audio-tray.svg'))
        iconset = work / 'PiAudioTray.iconset'
        iconset.mkdir()
        for base in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                pixmap = QPixmap(base * scale, base * scale)
                pixmap.fill(Qt.GlobalColor.transparent)
                painter = QPainter(pixmap)
                renderer.render(painter)
                painter.end()
                suffix = '@2x' if scale == 2 else ''
                pixmap.save(str(iconset / f'icon_{base}x{base}{suffix}.png'))
        icns = work / 'PiAudioTray.icns'
        subprocess.run(['iconutil', '-c', 'icns', str(iconset), '-o', str(icns)], check=True)
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
            '--name', 'Pi Audio Tray', '--onedir', '--windowed', '--icon', str(icns),
            '--osx-bundle-identifier', 'org.piaudiotray.desktop', '--target-arch', arch,
            '--add-data', 'translations:translations', '--add-data', 'pi:pi', '--add-data', 'setup_plan.py:.', '--add-data', 'pi_helper.py:.', '--add-data', 'pi_music.py:.', '--add-data', 'pi_upnp.py:.', '--add-data', 'LICENSE:.',
            '--add-data', 'THIRD_PARTY.md:.', '--add-data', 'licenses:licenses',
            '--add-data', 'assets/pi-audio-tray.svg:assets', '--add-data', 'assets/flags:assets/flags',
            '--distpath', str(work / 'frozen'), '--workpath', str(work / 'pyinstaller'),
            '--specpath', str(source), 'app.py'], cwd=source, check=True,
            env={**os.environ, 'PYINSTALLER_CONFIG_DIR': str(work / 'cache')})
        bundle = work / 'frozen/Pi Audio Tray.app'
        info = bundle / 'Contents/Info.plist'
        with info.open('rb') as f:
            values = plistlib.load(f)
        values.update(CFBundleShortVersionString=VERSION, CFBundleVersion=VERSION,
                      LSUIElement=True, LSMinimumSystemVersion='15.0')
        with info.open('wb') as f:
            plistlib.dump(values, f)
        # Ad-hoc signing makes the bundle internally consistent. This is NOT
        # Developer ID signing or Apple notarization and is labelled as such.
        subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(bundle)], check=True)
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(bundle)], check=True)
        subprocess.run([str(bundle / 'Contents/MacOS/Pi Audio Tray'), '--self-test'],
                       check=True, env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'})
        destination = ROOT / f'dist/PiAudioTray-{VERSION}-macOS-{arch}.app.zip'
        destination.parent.mkdir(exist_ok=True)
        subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent',
                        str(bundle), str(destination)], check=True)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        destination.with_suffix('.zip.sha256').write_text(digest + '  ' + destination.name + '\n')
        print('Built', destination)
    finally:
        shutil.rmtree(work)


if __name__ == '__main__':
    main()
