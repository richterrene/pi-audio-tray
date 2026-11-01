# Bluetooth sources and outputs

Open **Bluetooth → Bluetooth devices…** in the tray menu, or use the
**Bluetooth devices…** button in the status window, for the selected Pi. This is an
unreleased development feature, separate from the 0.10.1 stable candidate.
The first implementation manages the first BlueZ adapter (ordered by object
path); it is not a multi-adapter router. The selected adapter address is checked
on every device operation, so replacing it requires a fresh inventory.

## Installation and pairing

For an existing managed receiver, choose **Install / repair Bluetooth
management…** and enter the Pi's sudo password in the masked dialog. It deploys
reviewed files and a normal-user service, enables both A2DP directions and
restarts WirePlumber. Existing pairings stay in BlueZ, but audio connections may
be interrupted during installation. No new passwordless sudo rule is added. The user service retains an empty
capability bounding set in a private user namespace; the kernel must permit
unprivileged user namespaces. D-Bus service readiness is required before the
installer reports success.
Fresh/repair receiver setup includes the same helper and A2DP roles.

* **Audio source:** a phone or computer sends sound to the Pi. Choose **Pair
  audio source…**, select the Pi on the sender and approve the pairing request.
* **Audio output:** put headphones/speakers in pairing mode, choose **Find
  devices · 20 s**, select the device, then **Pair headphones / speakers…**.
  Approve any confirmation dialog. Legacy PIN/passkey-entry devices remain
  unsupported by the existing agent. Source/output types come from advertised
  A2DP UUIDs; newly discovered devices may initially have an unknown type, and
  dual-role devices are listed as both.

The existing approved pairing window lasts at most three minutes. Closing the
Bluetooth dialog stops its pairing session. Discovery is bounded to 20 seconds
and never makes the Pi discoverable by itself. Inventory polling runs only while
the dialog is visible and not minimized. Device names are plain, sanitized text.

Pairing and connecting do not explicitly change the default audio output. After
connecting headphones, select their output and click **Use audio output**. This
sets the PipeWire/Pulse default and moves existing compatible streams. An enabled
managed equalizer is retargeted with its curve preserved. A failed stream move is
reported; restarting that player may be necessary. Disconnected sinks disappear
and WirePlumber chooses an available fallback. Volume belongs to each output;
check headphone volume before starting playback. Browser launchers that explicitly
pin HDMI may select HDMI again on their next launch.

## Automatic connections

Preferences live privately on each Pi/audio account, keyed by adapter and device
address. The tray profile stores no Bluetooth keys; pairing keys stay in BlueZ.

* **Automatic on:** the normal-user manager allows reconnection and attempts it
  with exponential backoff (up to five minutes) while the paired device is
  available. A phone or headset can still refuse the connection or be busy with
  another host. This is a preference, not a guarantee of reachability.
* **Automatic off:** the device is blocked while disconnected. **Connect** opens
  a manual connection; after it disconnects, the manager blocks it again.
  Turning the preference off preserves an existing connection.
* **Disconnect:** blocks further connections for the current manager session,
  including peer-initiated reconnects. **Connect**, changing the preference, or
  restarting the manager clears this temporary pause. The saved automatic
  preference itself is retained.

Before the first explicit preference/connect/disconnect operation, existing
paired devices retain their BlueZ Trusted/Blocked behavior. Blocked is device-wide and also affects non-audio profiles of that device.
Other Bluetooth managers can change global BlueZ properties; for managed devices this service
reapplies its saved policy. An enforcement failure is shown as degraded rather
than claiming the preference is guaranteed. Do not run competing policy managers
for the same adapter. The manager must run for enforcement; disabling its service
leaves the last BlueZ Blocked/Trusted values in place.

## Boundaries and validation

The controller uses verified SSH, fixed helper commands and bounded JSON on
stdin. BlueZ control is through the normal audio account's system D-Bus access;
the command service is on that account's session bus, not a TCP port. Other
processes running as the same Unix account can request these controls. There is
no new root runtime API, web endpoint, arbitrary shell command, address-to-path
interpolation or saved administrator password. Root deployment uses the existing
managed-owner/platform checks and setup lock. Installing on an unmanaged receiver
requires the core setup first.

The source policy ignores outgoing A2DP source transports, preventing headphone
playback from falsely competing with Spotify/MPD/AirPlay as an incoming source.
Tests cover protocol rejection, preferences, manual/automatic connection behavior,
changed adapters, pairing gates, UI lifecycle, transport direction and packaging
manifests. Synthetic tests do not establish real headphone codec/range behavior.
The new UI copy is provided in English and German; other existing language
catalogs currently use explicit English fallbacks for these additions.
