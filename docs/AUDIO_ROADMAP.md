# Audio feature parity: scope, dependencies and acceptance

This is a staged development target, not a claim of moOde/Volumio compatibility
or feature parity. The first implementation adds classic AirPlay; AirPlay 2,
system-wide DSP and synchronized multiroom remain planned. Streaming-service
licensing and third-party commercial features cannot be reproduced merely by
adding a UI.

## 1. AirPlay receiver

The app's **Maintenance → Audio integrations…** installs Debian's Shairport Sync
as an optional add-on to an existing supported Pi Audio receiver. Set a receiver
name and a separate AirPlay password, save, then enable. The password is requested
by Apple clients when connecting. Disable stops the listener and its boot startup.
Closing the desktop app leaves an enabled receiver running.

Debian 13 currently supplies Shairport Sync 4.3.7 with classic AirPlay and the
PulseAudio backend. The integration uses that backend through the audio account's
existing PipeWire server. It does not claim AirPlay 2, video, photo or HomeKit
support. Shairport Sync's supported protocols depend on its compile-time options;
package version alone does not establish AirPlay 2 capability.

Installation requires Debian/Pi OS 13 ARM64, the existing Pi Audio audio-account
marker and an active user PipeWire service. The fresh-Pi wizard must run first on
a blank receiver. Existing unrelated Shairport installations are refused rather
than overwritten. The distro's system Shairport service is masked before package
installation so that package scripts cannot start a competing ALSA receiver.
The new user unit is initially disabled. Package updates use normal APT policy.

Installation uses the app's reviewed SSH/sudo setup transport. No new runtime
sudo permission is granted. Names/passwords are configured separately as the
normal audio account; passwords are not passed in command arguments or retained
in desktop settings. Receiver settings are in
`~/.config/pi-audio-tray/airplay.conf` (directory 0700/file 0600).

The add-on follows the current default PipeWire output. Existing Soloist, MPD and
Bluetooth services are not restarted. Sources can mix: automatic exclusive-source
selection is not implemented. Stop other sources before testing AirPlay. Changing
AirPlay settings while active restarts only its receiver after confirmation.
Initial installation does not change master volume or start music.

AirPlay discovery/receiving opens LAN services through Shairport/Avahi. A password
limits playback access; it is not a substitute for isolating an untrusted network.
No firewall rule, router mapping or internet access configuration is added.
There is no automatic port-conflict takeover. A service failure is visible in the
dialog; inspect `journalctl --user -u pi-audio-airplay` on the Pi. Root installation
diagnostics are in `/var/lib/pi-audio-airplay/install.log`. A partial install may
be retried with the same audio account; no automatic package rollback is promised.

Verification required before calling the feature hardware-validated:

- Real iPhone/iPad audio, password rejection and successful authentication.
- Reboot startup, disable persistence, output selection and receiver disappearance.
- Regression playback with Soloist, MPD and Bluetooth; no unintended volume jump.
- Package installation/retry on a clean receiver, and collision refusal.

### AirPlay 2 follow-up

AirPlay 2 is possible in upstream [Shairport Sync](https://github.com/mikebrady/shairport-sync).
It needs a compatible build and [NQPTP](https://github.com/mikebrady/nqptp)
for network timing. The Debian package inspected on the development receiver
does not include it. Use a pinned, reproducible build/package with an explicit
update policy, rather than compiling an unpinned branch on users' receivers.
Keep timing-service privileges separate from the unprivileged audio process.
Review authentication/pairing separately: classic AirPlay's password mechanism
must not be assumed to apply identically to AirPlay 2. Validate Apple multiroom
behavior, sender compatibility and output latency with physical receivers.

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
