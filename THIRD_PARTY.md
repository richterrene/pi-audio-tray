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

The optional AirPlay installer obtains Shairport Sync from the receiver's Debian
APT repository. Its binary is not bundled in desktop releases. Package licensing,
copyright notices and source availability are supplied by that repository; see
[Shairport Sync upstream](https://github.com/mikebrady/shairport-sync).

The optional AirPlay 2 installer builds pinned Shairport Sync 5.6.2 and NQPTP
1.2.9 sources on the Pi. These programs are not bundled in desktop releases.
Both use GPLv2-or-later licensing; the installer retains each upstream COPYING
file alongside the versioned receiver binaries. The exact source URLs and
SHA-256 digests are in `pi/airplay-build.py`. Rebuilding modified versions is
possible from those sources; a newer reviewed app and explicit reinstall are
required to update the pinned builds. See [the audio roadmap](docs/AUDIO_ROADMAP.md)
for build, timing-service and validation boundaries.
