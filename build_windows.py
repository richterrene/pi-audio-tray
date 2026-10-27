#!/usr/bin/env python3
"""Build a native x64 portable ZIP without personal paths or credentials."""
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

from packaging_support import prepare_source
from platform_support import secure_directory
from version import VERSION

ROOT = Path(__file__).resolve().parent


def create_icon(source, destination):
    from PySide6.QtCore import QBuffer, QIODevice, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    app = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(source / 'assets/pi-audio-tray.svg'))
    frames = []
    for size in (16, 32, 48, 64, 128, 256):
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, 'PNG'):
            raise RuntimeError('Could not render Windows icon')
        frames.append((size, bytes(buffer.data())))
    offset = 6 + 16 * len(frames)
    contents = bytearray(struct.pack('<HHH', 0, 1, len(frames)))
    for size, data in frames:
        contents.extend(struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    for size, data in frames:
        contents.extend(data)
    destination.write_bytes(contents)


def main():
    if sys.platform != 'win32' or struct.calcsize('P') != 8:
        raise SystemExit('Build this package on native 64-bit Windows.')
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    build_root = Path(os.environ.get('PI_AUDIO_BUILD_ROOT', r'C:\PiAudioBuild'))
    build_root.mkdir(parents=True, exist_ok=True)
    secure_directory(build_root)
    work = Path(tempfile.mkdtemp(prefix='build-', dir=build_root))
    try:
        source = prepare_source(work)
        icon = work / 'PiAudioTray.ico'
        create_icon(source, icon)
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
            '--name', 'Pi Audio Tray', '--onedir', '--windowed', '--icon', str(icon),
            '--add-data', 'pi:pi', '--add-data', 'setup_plan.py:.', '--add-data', 'pi_helper.py:.', '--add-data', 'pi_music.py:.', '--add-data', 'pi_upnp.py:.', '--add-data', 'LICENSE:.',
            '--add-data', 'THIRD_PARTY.md:.', '--add-data', 'licenses:licenses',
            '--add-data', 'assets/pi-audio-tray.svg:assets',
            '--distpath', str(work / 'frozen'), '--workpath', str(work / 'pyinstaller'),
            '--specpath', str(source), 'app.py'], cwd=source, check=True,
            env={**os.environ, 'PYINSTALLER_CONFIG_DIR': str(work / 'cache')})
        app_folder = work / 'frozen/Pi Audio Tray'
        executable = app_folder / 'Pi Audio Tray.exe'
        data = executable.read_bytes()
        pe_offset = struct.unpack_from('<I', data, 0x3c)[0]
        if data[pe_offset:pe_offset + 4] != b'PE\0\0' or struct.unpack_from('<H', data, pe_offset + 4)[0] != 0x8664:
            raise RuntimeError('Expected a Windows x86_64 executable')
        subprocess.run([str(executable), '--self-test'], check=True, timeout=45,
                       env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'})
        destination = ROOT / f'dist/PiAudioTray-{VERSION}-Windows-x86_64.zip'
        destination.parent.mkdir(exist_ok=True)
        shutil.make_archive(str(destination.with_suffix('')), 'zip', work / 'frozen', 'Pi Audio Tray')
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        destination.with_suffix('.zip.sha256').write_text(digest + '  ' + destination.name + '\n')
        print('Built', destination)
    finally:
        shutil.rmtree(work)


if __name__ == '__main__': main()
