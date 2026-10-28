# macOS distribution

## Packages and requirements

The build workflow creates separate application ZIPs:

- `PiAudioTray-VERSION-macOS-arm64.app.zip` for Apple Silicon.
- `PiAudioTray-VERSION-macOS-x86_64.app.zip` for Intel Macs.

The bundle sets **macOS 15** as its minimum OS. Older macOS releases are not
claimed as supported. Build/smoke-test results do not replace testing menu-bar
behavior, SSH authentication, and pairing on an actual user's Mac.

The receiver still runs Linux. Installing the desktop app on a Mac does not
change the Pi's architecture requirements or the ARM64 Soloist updater.

## Installation

Optional login autostart is available in Settings (off by default). It creates
a per-user LaunchAgent for the next login. Move the app into Applications and
reopen it before enabling this option; temporary/translocated locations are
refused. macOS background-item controls may independently block launch.

Extract the matching ZIP and move **Pi Audio Tray.app** to Applications. Launch
it and enter the Pi's host, username and SSH key in Settings. The app normally
stays in the menu bar rather than the Dock. Use **Open status** to show its window.
Keys remain on the Mac; an SSH agent can handle encrypted private keys.

Configuration is stored under `~/Library/Application Support/Pi Audio Tray`,
unless `XDG_CONFIG_HOME` is explicitly set. Soloist API keys are stored on the Pi,
not in that local directory or the bundle.

## Signing status

Initial builds use **ad-hoc signing** to keep the app bundle's signatures
internally valid. They do **not** identify an Apple-verified publisher and are
**not notarized by Apple**. macOS may block a quarantined download. Follow Apple's
[instructions for apps from unidentified developers](https://support.apple.com/guide/mac-help/open-a-mac-app-from-an-unidentified-developer-mh40616/mac)
only after verifying the download's origin and checksum. Do not disable
Gatekeeper globally.

For normal Developer ID distribution, a maintainer needs membership of the Apple
Developer Program and a **Developer ID Application** certificate. The future
signed release process must sign the nested code, enable the hardened runtime
with appropriate entitlements, submit the archive to Apple's notarization
service, and staple the accepted ticket. This workflow does not implement those
steps or include signing credentials. Never commit certificates, private keys,
Apple credentials, or provisioning profiles.

Mac App Store distribution is a separate project: it needs App Store signing,
sandbox/file-access work, store metadata, and review. The current GitHub bundles
are not represented as App Store-ready builds.

## Building

On a native ARM64 or Intel Mac with Python 3.12 and Xcode command-line tools:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip==26.2.0
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python build_macos.py
```

For Intel Macs, current cryptography packages may build from source. Install
Homebrew's `openssl@3` and Rust, and use static linking to avoid a mismatch with
Python's separate OpenSSL library in the frozen app:

```sh
brew install openssl@3 rust
OPENSSL_DIR="$(brew --prefix openssl@3)" OPENSSL_STATIC=1 .venv/bin/python -m pip install --no-cache-dir --force-reinstall -r requirements.txt
```

Run this dependency installation before building the bundle. The CI workflow
already applies the static-link settings.

The script renders an `.icns` icon from the shared SVG, bundles Python/Qt/SSH
libraries, sets the app's version and menu-bar behavior, verifies the ad-hoc
signature, runs a headless GUI smoke test, and exports a ZIP plus SHA-256 checksum.
It does not read local Pi configuration or SSH keys when building.

## Using your own Mac from another computer

Development/editing can happen on Linux or Windows. A native Mac is needed for
the build and macOS-specific tests; it may be a GitHub-hosted Mac or your own.

For a personal build machine, enable **System Settings → General → Sharing →
Remote Login** on the Mac for a dedicated build account and configure SSH-key
access from your development computer. Install Xcode command-line tools and
Python on the Mac, and check out the repository there. Full Xcode is needed for
native Swift/Xcode projects and their tests; this Python/Qt project uses the
command-line tools plus its Python dependencies. See [Apple's Remote Login guide](https://support.apple.com/guide/mac-help/mchlp1066/mac).

After configuring a `build-mac` SSH alias, a build can be started from Linux:

```sh
ssh build-mac 'cd ~/src/pi-audio-tray && .venv/bin/python -m unittest discover -s tests -v && .venv/bin/python build_macos.py'
scp 'build-mac:src/pi-audio-tray/dist/*.app.zip' ./
```

Prepare the checkout, virtual environment, and build prerequisites first. The
Mac must be powered on and reachable; allow it to stay awake during long jobs.
Real UI testing may require a logged-in graphical session or a person at the Mac.

A [GitHub self-hosted runner](https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/add-runners)
is an alternative for regular builds. Use trusted/private workflows for a
personal Mac; do not run untrusted pull-request code on a machine containing
signing credentials or personal data. GitHub-hosted macOS runners remain the
default for this repository.
