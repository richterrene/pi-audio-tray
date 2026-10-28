# Audio expansion: current scope and remaining parity work

The 0.8.0 preview adds classic AirPlay and an AirPlay 2 installation option.
It does not claim moOde/Volumio compatibility or full feature parity. System-wide
DSP and synchronized Snapcast multiroom remain planned.

## 1. AirPlay receiver

Use **Maintenance → Audio integrations…** on an existing supported Pi Audio
receiver. Choose classic AirPlay or AirPlay 2, enter the Pi sudo password and
review installation. Then set the receiver name and a separate AirPlay password,
save, and enable. Disable stops receiving and boot startup; in AirPlay 2 mode it
also disables the dedicated timing service. Closing the tray leaves enabled
receiving running. Master output selection/volume stays separate.

Classic mode uses Debian 13's Shairport Sync package through PipeWire's PulseAudio
compatibility server. The competing distribution system service is masked before
installation. Unrelated existing installations are refused.

AirPlay 2 downloads **Shairport Sync 5.6.2** and **NQPTP 1.2.9** from fixed official
GitHub release URLs, verifying pinned SHA-256 hashes before extraction. It installs
build dependencies with APT, then compiles as a dedicated unprivileged, nologin
build account at reduced priority. Upstream configure/make/install recipes and
compiled binaries are never executed as root on the Pi. Only verified regular
ARM64 executables and license text are copied into root-owned versioned paths.
The build needs at least 1 GiB free space and can take several minutes.

AirPlay 2 uses native PipeWire and explicitly selects the AirPlay 2 service type;
there is no silent fallback to classic mode if timing fails. It shares the normal
audio account and default output with existing players. Shairport 5.6.2 supports
password authentication in this mode; actual Apple-client authentication/playback
still needs physical testing. This is audio receiving, not screen/video mirroring.

NQPTP uses its own DynamicUser system service, with only CAP_NET_BIND_SERVICE,
for UDP timing ports 319/320 and a localhost UDP control port 9000. The installer
checks conflicts and refuses unrelated NQPTP installations. Shared-memory timing
requires a shared /dev/shm; it must not be hidden behind PrivateDevices/PrivateIPC.
The audio account receives exactly two sudo commands to enable/disable this fixed
root-owned service with --now. No general systemctl privilege is added.

Existing owned variants can be repaired or switched. Compilation/package checks
happen before stopping the old receiver. The final switch stops receiving and
leaves it disabled until the user enables it. Separate 0600 settings files
`airplay.conf` and `airplay2.conf` preserve each variant's name/password. Media,
Soloist credentials, Bluetooth bonds and other audio configuration are not removed.
Old source binaries/packages and the narrow timing permission remain; there is no
uninstaller. Installation is not a transaction or automatic rollback mechanism.

Source builds are **not updated by APT**. A newer tray app can carry reviewed new
versions/checksums; rerun its installer to apply them. There is no unreviewed
latest-branch compiler or unattended source updater. Classic package updates use
APT; Soloist's build-expiry updater is separate.

Settings travel as bounded JSON over SSH stdin as the normal account. They are
not put in command arguments or desktop storage. Root-private installation logs
are `/var/lib/pi-audio-airplay/install.log`. Read user receiver failures with
`journalctl --user -u pi-audio-airplay`; timing failures are in the system journal
for `pi-audio-nqptp`. No firewall or router mappings are added. A playback password
is not a general network security boundary. NQPTP's upstream shared-memory/control
interface also assumes trusted local users and is not multi-tenant isolation.

### Build/test evidence and remaining validation

A Debian 13 x86_64 container compiled both pinned releases as an unprivileged
user. Their shared-memory ABI is 11. An actual two-client timing-isolation test
passes using sizes/offsets compiled from that header. Upstream's bundled 1.2.9
Python test still hard-codes ABI 10/72 bytes; its unmodified run fails that ABI
assertion. Our integration harness uses the real ABI-11 layout (88 bytes, offsets
8/48) rather than waiving the compatibility check. The source builder also rejects
mismatched ABI versions before compiling.

The AirPlay source workflow builds on native ARM64 Debian 13 and exercises the
same timing harness. See its actual run result before claiming native validation.
None of these checks proves audible playback, Apple password prompts, physical
multiroom synchronization or reboot startup on every Pi/DAC. Those hardware tests
remain required. Do not start playback on a user's amplifier as an unattended test.

Upstream: [Shairport Sync](https://github.com/mikebrady/shairport-sync),
[NQPTP](https://github.com/mikebrady/nqptp).

## 2. Shared routing and DSP

Target one explicit, reversible PipeWire graph shared by Soloist, MPD, Bluetooth
and AirPlay. First ship bypass, bounded preamp/EQ settings and reliable restoration
of the previous hardware output. Then evaluate CamillaDSP for convolution/room
correction and more advanced routing.

[CamillaDSP](https://github.com/HEnquist/camilladsp) has multiple audio backends;
verify features of the pinned release/build rather than assuming that upstream
development documentation describes a distro binary.

Acceptance requires no graph feedback loop, no double playback, output hotplug
recovery, persistence, bounded gain/headroom, supported sample rates, and CPU/xrun
measurements on the Pi 3 B+. A failed DSP process must offer a working bypass.
DSP processing means no promise of bit-perfect output. Arbitrary executable
filter/plugin uploads and a publicly exposed DSP control socket are out of scope.

## 3. Synchronized multiroom

Use [Snapcast](https://github.com/snapcast/snapcast) for a central audio stream and
time-synchronized clients. Named Pi profiles remain connection profiles; they are
not speaker groups. Introduce explicit server/client roles, speaker discovery,
group membership and per-room volume/mute, with SSH-mediated administration.

All rooms, including the server's own speaker, must use the synchronized client
path. Direct local playback alongside a buffered remote path creates an audible
offset. Feed the intended audio bus into Snapserver, not the hardware output
monitor or a bus containing Snapclient's return signal. Set a clear pre-/post-DSP
boundary; room correction is normally specific to each receiving speaker/room.

Do not assume the Debian 0.31 package has the same capture backends as upstream's
latest development branch. Check its compiled features before choosing native
PipeWire capture or a bounded PCM/FIFO bridge. Spotify distribution must respect
Spotify's current terms; technical transport capability does not establish
permission for a service/use case.

Acceptance requires two physical receivers: synchronized local/remote audio,
mixed sample rates, source changes, muted rooms, reconnect after Wi-Fi loss,
server outage, reboot and failed group operations. Measure rather than claim
timing accuracy. No internet-exposed control API or unauthenticated remote-shell
surface; document media transport confidentiality and allowed LAN peers before
opening listeners. Bluetooth output latency needs separate testing.

## Release policy and remaining parity gaps

Each stage needs capability checks, safe settings migration, native desktop tests,
Pi-side tests, documentation and physical playback evidence. A working AirPlay
package does not certify an untested DAC or Pi model. Hardware tests must not
silently begin playback on a user's amplifier.

Other parity gaps remain separate: SMB/NFS mount management, radio catalogues,
saved playlists, richer library search/artwork, and additional licensed services.
The desktop companion remains the primary product; integrating established audio
engines avoids maintaining our own streaming protocols or synchronization engine.
