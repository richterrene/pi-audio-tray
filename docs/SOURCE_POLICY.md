# Mixing, exclusive source policy and master volume

**Mixing remains enabled by default.** The desktop does not silently change it.
Use **Maintenance → Playback source policy…** to disable simultaneous sources for
the selected Pi. Install/repair its **core receiver** first to deploy the helper,
user unit and updated Soloist launcher. The policy runs on the receiver, so it
also sees playback started from phones or the HTTPS player and keeps working when
the tray app is closed. Each Pi has its own private setting.

## Exclusive behavior

The latest-started recognized source wins. Supported sources are Soloist,
MPD (including USB/UPnP/web playback), Shairport Sync and incoming BlueZ audio.
The controller watches PipeWire events, Soloist playback events and available
BlueZ player/transport signals. This matters when a player leaves its audio node
running while paused. Unknown applications are not forcibly controlled.

When another source starts, losing streams are muted before pause is requested.
MPD is paused through its private Unix socket. Soloist uses its local CLI/API.
BlueZ and Shairport MPRIS pause are attempted where available. Some senders do not
support remote pause; their streams stay muted and can keep advancing. Pause and
play that sender again to claim output. The previous source is **not** automatically
resumed when the winner stops. If several sources are already active when the
policy starts, the most recently created eligible stream is the initial winner;
stop competing playback before first enabling it.

This is an event-driven application policy, not sample-perfect hardware switching:
there can be a brief handover delay. Failed mute controls are reported as degraded;
do not treat an active service alone as proof of exclusivity. The initial release
still needs physical cross-source testing, particularly with Bluetooth/AirPlay
senders. DSP and synchronized multiroom are separate planned features.

Only per-stream mutes are managed; hardware/master volume is not changed by the
policy. Preexisting user mutes are not claimed. Policy-owned mutes are recorded in
a private runtime file and restored on exit/disable, guarded by PipeWire's server
cookie plus stream serial/id so a restarted server or recycled id cannot unmute
another stream. While the policy owns a stream mute, it controls that mute until
handover. Missing graphs/failed restoration can require manual intervention.

## Local control security

Enabling/disabling this option restarts an active Soloist to add/remove
`--ws 127.0.0.1:0`. Without exclusive mode, the launcher keeps the optional API
disabled. The operating system assigns a local port; discovery stays in Soloist's
data directory. The controller rejects non-loopback endpoints.

Spotify's API has no client authentication or Origin checks. **Other local users
or compromised services on the Pi can access it while exclusive mode is enabled.**
No LAN listener, reverse proxy, router mapping or desktop server is created for
it. This is intended for the project's trusted home receiver, not a multi-tenant
host. The same-user audio/PipeWire session is already trusted. No new runtime
sudo permission is needed for source arbitration.

Playback metadata is consumed locally to extract state; track/queue content is
not logged or persisted by this controller. The runtime file stores source ids
and mute ownership, not credentials. Bluetooth identifiers can appear in that
private file; the desktop receives only a generic Bluetooth source label.
Settings are `~/.config/pi-audio-tray/source-policy.json` (0600), and runtime state
is `/run/user/UID/pi-audio-policy.json` (0600). Inspect failures with
`journalctl --user -u pi-audio-policy`.

## Master volume

The tray's **Master volume…** controls the current default PipeWire output shared
by all sources. It works over normal SSH without installing a new privileged
helper. The slider/mute controls are independent from Spotify/MPD player volumes.
Source policies do not override this master control.

Opening the dialog only reads the level. Changes are asynchronous and slider
requests coalesce; stale replies do not move the slider over newer edits. The
receiver rechecks sink identity and server cookie before each change. A default
output/server change requires a fresh read, rather than applying an old request
to a new device. Only 0–100% is accepted; software amplification is not offered.
An already amplified muted output must be reduced before unmuting through the app.
Changing volume does not automatically clear mute. Existing reboot-volume policy
is not replaced by this control.

The Soloist launcher now uses `--initial-volume 100`; `--cache-size 100` is a
separate cache limit. Its initial volume setting does not set master/hardware gain.
