# Windows distribution

## Package and installation

The Windows x64 portable ZIP targets Windows 11. Extract **the entire ZIP** to a
folder you control, then run `Pi Audio Tray/Pi Audio Tray.exe`. Keep the adjacent
`_internal` folder intact. Python and Qt are bundled; administrator access is not
needed for normal desktop operation. There is no installer. Optional login
autostart is available in Settings (off by default); it registers only this
user's HKCU Run value. Keep the extracted folder in a permanent location.
You can also create a normal shortcut to the executable.

Native builds, regression tests, Windows ACL/locking tests and the packaged
self-test run on a Windows Server 2022 x64 GitHub runner. Interactive Windows 11
tray behavior and SSH against a physical Pi still need user validation. ARM64
and 32-bit Windows packages are not provided or claimed as tested.

The package is **unsigned** and has no verified publisher identity. Windows may
show an unknown-publisher/reputation warning. Verify the GitHub release origin
and accompanying SHA-256 before deciding whether to run it. Do not disable
Defender or SmartScreen globally. Signing with a Windows code-signing certificate
is a future release step; no certificate or private signing key is in this repo.

## SSH and receiver setup

Use the same Linux Pi setup and exact sudo rules as on other desktops. Enter the
actual Pi hostname/IP, username and private-key path in Settings. An example path
is `C:\Users\your-user\.ssh\id_ed25519`. Keys stay in place and are never bundled.
Unknown host keys require independent fingerprint verification; changed keys fail.
OpenSSH host aliases, ProxyJump and ProxyCommand configuration are not imported.

Passphrase-protected keys need a compatible SSH agent; the GUI has no passphrase
or SSH password prompt. Agent compatibility has not been validated on a user's
Windows desktop. Paramiko handles SSH directly; an OpenSSH client installation is
not required for an unencrypted key file. The Soloist API key stays on the Pi.

## Local storage and security

Default settings and approved host keys live in `%APPDATA%\Pi Audio Tray`.
`XDG_CONFIG_HOME`, when explicitly set, overrides this to its `pi-audio-tray`
subdirectory. Use a trusted local filesystem/profile with Windows ACL support;
FAT/exFAT or shared/network configuration locations are not supported as private
storage. The executable ZIP can be kept elsewhere; settings do not live next to it.

The app uses native Windows security APIs to set a protected DACL permitting only
the current user and LocalSystem. It validates ownership before changing access;
current-user ownership and the privileged Administrators owner are accepted.
Administrators can still take ownership/access data, and same-user malware can
read process memory. POSIX chmod bits are not treated as Windows privacy controls.
System DLL lookup is restricted to System32. Links/reparse points at managed
storage boundaries are rejected. Atomic private writes and a nonblocking Windows
byte-range instance lock replace the POSIX-only mechanisms. ACL failures stop
operation rather than silently using broad inherited permissions.

The instance lock file contains only a lock byte. It is not a credential. The
packaged self-test exercises private temporary storage and locking without
reading the real configuration or contacting a Pi. See [SECURITY.md](../SECURITY.md)
for the full threat model and remaining limitations.

## Building

Use native Windows x64, Python 3.12 and a reviewed checkout:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip==26.2.0
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m unittest discover -s tests -v
.venv\Scripts\python build_windows.py
```

The default private build workspace is `C:\PiAudioBuild`. The current account
must be able to create/use it; provision a suitable dedicated directory first
if needed. `PI_AUDIO_BUILD_ROOT` can select another trusted, neutral build path.
Avoid personal profile paths when distributing builds. The build script copies
only whitelisted sources, renders the icon, bundles the app with PyInstaller,
checks the PE architecture, runs the packaged self-test, and writes a ZIP and
SHA-256 file under `dist`. CI retains its exact Windows dependency inventory and
known-advisory result alongside the package artifacts.
