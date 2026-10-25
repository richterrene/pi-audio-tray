# Third-party components

The AppImage bundles Python, Qt/PySide6 Essentials, Shiboken, Paramiko and its
dependencies using PyInstaller. Their bundled distribution license files are
included under `usr/bin/_internal/licenses` in the extracted AppImage.

- Python: https://www.python.org/psf/license/
- Qt and PySide6: LGPLv3/GPLv3 or commercial licensing, https://doc.qt.io/qtforpython-6/licenses.html
- Paramiko: LGPLv2.1, https://github.com/paramiko/paramiko
- PyInstaller: GPL with a bundling exception, https://pyinstaller.org/en/stable/license.html
- AppImage runtime: MIT, https://github.com/AppImage/type2-runtime/blob/20251108/LICENSE

Qt shared libraries remain separate files in the AppImage. To inspect or replace
them for debugging modifications, use `--appimage-extract` and run `squashfs-root/AppRun`.
The source and pinned versions for building the application are in this repository.
No endorsement by Raspberry Pi, Spotify, or Bandcamp is implied.
