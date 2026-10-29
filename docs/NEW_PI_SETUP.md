# Set up a new Pi remotely (preview)

**Set up a new Pi…** installs a receiver over the same verified SSH connection
used by the tray app. It is included in Linux, macOS and Windows packages.
This is a preview: transport, installer stages and GUI safeguards are covered by
automated fixtures, but a complete installation on a spare blank Pi has not yet
been performed. Do not use it to migrate a working receiver.

This is **not a universal Raspberry Pi installer**. The current audio deployment
was tested on a Pi 3 Model B+ with 1 GB RAM and its headphone output. Other ARM64
boards, low-memory models and DACs need individual validation. See the README's
[board compatibility and hardware caveats](../README.md#which-raspberry-pis-are-supported).
The check does not test audible playback, hardware capacity or reboot persistence.
Bluetooth is mandatory in the base setup and needs an enabled controller; the
configured profile receives audio from phones rather than transmitting to a
Bluetooth speaker. A 32-bit userspace is unsupported even on 64-bit hardware.
Version 0.7.1 checks the package architecture and Python bitness as well as the
kernel architecture. This fixes 0.7.0's incorrect acceptance of a 64-bit kernel
with 32-bit userspace, reproduced in our [virtual image tests](VIRTUAL_TESTS.md).
Verify `dpkg --print-architecture` returns `arm64` on the Pi; `uname -m` alone does
not establish OS compatibility.

## Before starting

1. Flash **Raspberry Pi OS Lite 64-bit / Debian 13 Trixie** with Raspberry Pi
   Imager. Enable networking, a normal user, SSH and your public SSH key.
   The computer running the tray app must already have the matching private key
   or an SSH agent. SSH login password authentication is not implemented.
2. Boot the Pi and configure its host, SSH username and key in tray **Settings**.
   Verify its SSH fingerprint independently when prompted. Do not approve an
   unexpected replacement key. The Pi needs internet access for package downloads.
3. Connect the intended audio hardware. Pi 3B+ headphones are the established
   deployment; other ARM64 Pi models/DACs/HDMI need deployment testing. Raspberry
   Pi OS audio overlays and hardware enabling are outside the wizard.
4. If selecting USB music, attach a separate **FAT32 USB** drive containing a
   `Music` directory. The wizard selects by filesystem UUID, rejects root/boot
   storage, mounts read-only and never formats a drive. Other filesystems and
   MPD without USB storage are not offered by this first version.
5. Generate your own key in [Spotify's Soloist dashboard](https://developer.spotify.com/dashboard/soloist),
   review Spotify's terms, and ensure your account meets its requirements. The
   checkbox acknowledges the official download; your key is entered separately.

## What gets installed

The base installs normal-user **Soloist**, **PipeWire / WirePlumber**, **BlueZ**,
Avahi name discovery, tray status/pairing helpers and the restricted pairing and
Soloist-update sudo rules. User lingering enables audio at boot without an SSH
session. Bluetooth is powered on; discovery/pairing are disabled at startup and
after Bluetooth service restarts. Pairing still requires the tray's explicit,
time-limited pairing action. No Raspotify or BlueALSA migration is attempted.
Soloist is downloaded directly on the Pi from Spotify's official HTTPS CDN,
using the existing bounded ARM64 updater; its binary is never bundled in the app.
Its update timer is enabled even if system updates are deselected, since Soloist
builds expire after 90 days.

| Choice | Result |
| --- | --- |
| System updates (default on) | Trixie package updates around 03:00; conditional reboot at 05:30, in the Pi's local timezone, only if required and known updaters are idle |
| Process privacy (default on) | Persistent `/proc` `hidepid=2`; hides other accounts' process details, including Soloist key arguments; root and the audio account retain access |
| Tray shutdown (default on) | Exact noninteractive permission for `systemctl --no-block poweroff` |
| MPD / USB / UPnP | Private Unix-socket MPD, read-only USB library and existing desktop UPnP discovery/browsing; no public MPD TCP listener |
| HTTPS player | Requires MPD; myMPD on loopback, authenticated nginx on private IPv4 LAN ports 443/8443, port 80 GET redirect, remembered secure cookie login and local CA renewal |
| Cockpit | Minimal `cockpit-ws` / `cockpit-system`, bound to **127.0.0.1:9090**; no NetworkManager installation requested; access via SSH tunnel |

The web choice adds the existing fingerprint-verified, package-pinned third-party
myMPD repository. See [music security and certificate setup](LOCAL_MUSIC.md).
The wizard does not expose services to the public internet, configure router
forwarding, install a media server on your desktop or import a CA into clients.

## Existing receivers

Use **Check / repair existing Pi components…** for additive maintenance of an
existing managed receiver. The fresh wizard remains protected against overwriting
completed/custom installations. See [component inspection and repair](PI_COMPONENTS.md).
Uninstalling is not offered; unselected maintenance components are left alone.

## Small screens and release status

The form scrolls to keep the Pi identity, progress log and Close visible in small
work areas. Long choice labels wrap; Tab scrolls focused controls into view.
The [stable-release checklist](RELEASE_READINESS.md) still requires a physical
fresh-install test; the layout fix does not promote installation out of preview.

## Run the wizard

Choose **Set up a new Pi… → Check Pi**. The read-only inventory reports the OS,
architecture, model, account and USB candidates. Existing receiver configuration,
custom user audio configuration, or installed competing music/web packages stop
fresh installation. The root installer rechecks compatibility after authentication.

Select components, set the Pi DNS hostname for the web certificate, and enter the
**Pi user's sudo password** in the masked field. Leave it empty only if the
account already has administrator sudo without a password. Review the concrete
plan and choose Yes. Keep the app and connection open until it finishes. Ordinary
monitoring pauses during setup; one in-flight request can still finish.

After completion, use **Refresh audio outputs → Use output**, then **Set Soloist
API key…** in the same wizard (or key settings later). WirePlumber saves the
default output; changing sinks never starts playback. Hardware and master volume
are left unchanged. MPD and Soloist start with their player volume at 100%; lower
your amplifier before first playback and test audibility yourself. Pairing/audio
service readiness is not proof of working speakers.

For the web player, open `https://YOUR-PI-NAME/`. The installer generates a new,
random **music** web password on each fresh Pi. No password from another Pi is
copied. Retrieve `~/.config/pi-audio-tray/music-web-login.txt` over your verified
SSH/SFTP connection into a private local file; retrieve `music-ca.crt` separately
and verify its fingerprint on the Pi before trusting it. This public certificate
is safe to transfer to phones; **never transfer the CA private key**. See
[certificate instructions](LOCAL_MUSIC.md). The wizard does not display or save
the web password locally.

For Cockpit, forward a local port using your normal SSH options:

```sh
ssh -N -L 127.0.0.1:9090:127.0.0.1:9090 YOUR_USER@YOUR_PI
```

Add your usual `-i` and `-p` options as needed. Open `http://localhost:9090` while
the tunnel is running. Cockpit uses the Pi's account credentials; SSH keys alone
do not create a Cockpit password. Pick another local port if 9090 is occupied.
The tray app does not yet manage the Cockpit tunnel itself.

## Security and recovery

The setup bundle has an explicit manifest of reviewed source/configuration files;
it never uploads the working directory, desktop settings, SSH keys or existing
Soloist credentials. Files are staged in a random remote directory, mode 700,
with files 600. Administrator authentication is sent only on the SSH channel's
stdin without a PTY; it is absent from command arguments, environment, progress
and saved configuration. The GUI clears the field before confirmation. Like
other Python/Qt apps, it cannot guarantee cryptographic erasure of in-memory
string copies. The ordinary SSH account and desktop app are trusted administrators
for the duration of installation, and the password must authorize full setup.
A sudo policy requiring a TTY is unsupported; do not weaken it just for this app.

The root installer snapshots the bundle under root-private
`/var/lib/pi-audio-setup`, locks against concurrent runs, validates the plan and
account, checks service ports/USB and installs root-owned helpers. It validates
exact sudo rules before enabling them. **Pre-existing sudo permissions are not
removed**; many Pi images already grant broad administrator rights. This wizard
is not an account lockdown tool. Soloist still receives its API key via argv,
so process privacy is recommended on a shared Pi.

Progress contains fixed stage labels. Detailed package/service diagnostics stay
in root-private `/var/lib/pi-audio-setup/install.log`. State is in `state.json`
in the same root-private directory. Installation is **not transactional**:
package, service, mount and permission changes completed before a failure remain.
It does not silently roll back package upgrades, remove user data, or reboot.
Closing is disabled while a task is active; a crash/network failure can still
leave an administrator process running. Do not blindly rerun after a timeout.

An administrator should first inspect:

```sh
sudo cat /var/lib/pi-audio-setup/state.json
sudo less /var/lib/pi-audio-setup/install.log
```

Do not post these files publicly without removing host/account/storage details.
After confirming no installer or package manager is still running, use **Retry an
incomplete wizard installation** with the **identical component choices, hostname,
USB UUID and SSH account**. The root-owned state rejects changed plans or an
already completed setup. Completed stages are skipped; the interrupted stage
runs again. Some failures still need manual service/package recovery. Do not
remove state merely to bypass compatibility checks. A fresh OS is the cleanest
recovery option for an abandoned new installation.

The fstab before process privacy is retained as `fstab.before-privacy`; USB setup
keeps `/etc/fstab.pi-audio-backup`. Revert only the relevant entries after review,
not an entire older file after other mount changes. To undo live process privacy,
use `sudo mount -o remount,hidepid=0 /proc` and remove `hidepid=2` from the single
`/proc` fstab entry. Removing the desktop app does not uninstall Pi services.
