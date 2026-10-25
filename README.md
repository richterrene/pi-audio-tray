# Pi Audio Tray

A small Linux tray app for a Raspberry Pi running Raspotify and BlueALSA.
Connect over SSH, monitor the receiver, and open or close Bluetooth pairing
without typing commands. There are no cloud services or telemetry.

## Features

- Pi reachability, Raspotify service state, Bluetooth power, BlueALSA services,
  connected device names, and pairing state.
- Three-minute pairing window with confirmation dialogs on the desktop.
- Pairing closes on timeout, normal app exit, or SSH channel closure. BlueZ's
  own pairing/discovery timeouts also limit the window if the helper crashes.
- Devices explicitly approved by the user are trusted when pairing completes
  and the session closes. Existing trusted connections survive pairing off.
- Configurable host, port, SSH username, private-key filename, known-host alias,
  and polling interval. Settings are stored locally, outside the application.
- AppImage includes the GUI, Python, and SSH libraries. No Python installation
  or tray library package is needed on the desktop.

Spotify status is the **service status**, not currently playing tracks. Bluetooth
device status indicates a connection, not proof of current audio playback.

## Run

Download the x86_64 AppImage, make it executable, and launch it:

```sh
chmod +x PiAudioTray-0.1.1-x86_64.AppImage
./PiAudioTray-0.1.1-x86_64.AppImage --show
```

Ubuntu's AppIndicator extension must be enabled for the tray icon. When no tray
is available, the status window remains usable. The build depends on normal
Linux desktop libraries (glibc, D-Bus, graphics); AppImage does not bundle a whole
operating system. Builds are tested on the build host; cross-distro portability
must be checked before claiming support for additional distributions.

Open **Settings** and enter your Pi connection. Keys stay in their original
location. Use an SSH agent for passphrase-protected keys; this app does not save
passwords or passphrases. If you connect through a different hostname from the
one already in `~/.ssh/known_hosts`, enter the old name in **Known-host alias**.
Unknown host keys require explicit fingerprint approval; changed keys are rejected.

Choose **Set up / update Pi helper** once. This installs `python3-dbus` and
`python3-gi` from the Pi's apt repositories and a root-owned helper under
`/usr/local/lib/pi-audio-tray/pi-helper`. The SSH account needs passwordless sudo
for setup and pairing. No new sudo permissions are granted by this app.
Raspberry Pi OS / Debian with BlueZ, Raspotify, and BlueALSA already configured
is required. This app does not configure the underlying audio routing.

If preferred, install manually on the Pi:

```sh
sudo apt install python3-dbus python3-gi
sudo install -d -m 755 /usr/local/lib/pi-audio-tray
sudo install -o root -g root -m 755 pi_helper.py /usr/local/lib/pi-audio-tray/pi-helper
```

Choose **Enable pairing**, pair from your phone/computer, and approve the request
only if it is your device. **Disable pairing** closes the window immediately.
New incoming requests may appear during that window, but are not auto-approved.
Legacy devices requiring PIN entry are currently unsupported.

## Local data and privacy

Configuration and app-approved host keys are stored in
`$XDG_CONFIG_HOME/pi-audio-tray` (normally `~/.config/pi-audio-tray`). That directory
is not in the repository or AppImage. Private keys and Spotify credentials are
never bundled, uploaded, or copied to the Pi. Connected device names and addresses
are shown at runtime only. There is no persistent application log.

Builds copy source into a neutral temporary path to avoid embedding local account
names in compiled Python paths. Build artifacts, keys, local configuration, and
virtual environments are excluded from Git. Do not commit your settings or runtime
screenshots if you intend to keep device names private.

## Develop and test

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
python app.py --show
```

## Build AppImage

On x86_64 Linux with `mksquashfs` installed, from the activated environment:

```sh
python build_appimage.py
```

The build downloads a versioned official AppImage runtime and verifies its pinned
SHA-256 checksum. Output and checksum are placed in `dist/`. Qt shared libraries
and third-party notices can be inspected with `--appimage-extract`.

The optional `packaging/github-actions-build.yml` template builds on Ubuntu 24.04
and uploads an artifact. To enable it later, copy it to `.github/workflows/build.yml`
using a GitHub credential with workflow permissions. It does not publish a public
release or change repository visibility.
