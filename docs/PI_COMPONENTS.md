# Inspecting, adding and repairing receiver components

Use **Maintenance → Check / repair Pi components…**, or the matching button in
the fresh-Pi wizard. The target SSH account/host is always displayed. Select a Pi
profile before opening it; the dialog captures that connection and does not switch
targets mid-operation. Repeat the check for each named receiver as needed.

Checks are on demand, without a background timer. They inspect installed packages
or binaries, expected configuration files and service states separately. Hover
over a row for versions, units and limitations. A running service does not prove
that Spotify authentication, a speaker or a Bluetooth sender works. Private keys,
passwords and certificate material are not read by the inventory; private
prerequisites are checked by the administrator installer before a repair.

## Fresh setup versus maintenance

The original wizard remains **fresh installation only**. It refuses existing
receiver/web configurations and competing packages. An interrupted fresh install
must be resumed with its original plan/account; a completed wizard is not a
desired-state checklist. Unchecking a choice never uninstalls that feature.

The component dialog adds a separate, additive maintenance operation for a
supported **Debian/Pi OS 13 ARM64 receiver already managed by Pi Audio Tray**.
The root-owned audio-account marker must match the SSH account. It cannot adopt
an arbitrary Volumio/moOde/third-party audio stack. Unsupported platforms remain
inspectable but cannot be repaired with this installer.

Select the components to install/reinstall, enter the Pi sudo password in the
masked field, and review the concrete target and plan. Root checks are repeated
after authentication; displayed facts can become stale. Credentials are sent
only over verified SSH stdin, not saved in desktop settings.

Spotify, Bluetooth and helpers share the **core receiver** repair. Selecting one
selects all three visibly. Selecting the HTTPS player also selects MPD. Unselected
features are not removed/reconfigured, although shared dependencies may be updated
and affected services may restart. Stop playback first. Package repair uses
`--reinstall --no-remove`, not purge/autoremove or a distribution upgrade.

An existing unrelated Cockpit setup is not overwritten. The current and legacy
Pi Audio loopback drop-ins are recognized; general Cockpit exposure is not a
substitute for the loopback configuration. Existing app MPD must belong to the
selected audio account and use the same USB UUID. No media is formatted or copied.

## Credentials, backups and partial failure

Soloist API keys/device names, Bluetooth bonds, music data, MPD databases/queues
and the existing web login/CA are retained. Selected app configuration can be
rewritten from the reviewed templates. Missing credentials/CA material in an
existing web deployment cause refusal instead of silently generating replacements.
Existing web hostname identity is checked against the certificate. A changed
hostname or lost CA needs a separate deliberate migration/recovery.

Root-managed configuration snapshots are retained under
`/var/lib/pi-audio-setup/configuration-backup-*` (root-private). User unit/audio
configuration backups are created as the audio account under
`~/.local/state/pi-audio-tray/setup-backups/`. These are configuration snapshots,
not complete Pi backups or automatic rollback images. Administrators should
review retention and protect backups as sensitive data.

Maintenance shares the installation lock with AirPlay and on-demand system
updates. Detailed output stays in `/var/lib/pi-audio-setup/install.log`; progress
contains fixed stage labels. State is in `maintenance.json`. Completed repairs
can be repeated. After a failed maintenance operation, a changed **additive** plan
can be submitted; the previous state is archived privately. Fresh-install retry
rules remain stricter. A network timeout does not prove that the remote installer
has stopped: inspect its log/locks before retrying.

## System updates on demand

**Maintenance → System updates…** lists candidates from cached APT indexes,
including held candidates. The UI identifies the last known repository refresh;
it does not claim a cached list is current. At most 256 package rows are shown,
with the total and truncation stated explicitly.

Install/repair **Automatic updates** once to deploy two exact noninteractive
sudo operations for the SSH audio account:

```
/usr/local/lib/pi-audio-tray/pi-system-updates check
/usr/local/lib/pi-audio-tray/pi-system-updates install
```

The first refreshes signed repository indexes; the second refreshes then runs
`unattended-upgrade` using this Pi's configured allowed origins/holds. Neither
accepts arbitrary package names or commands. Held, phased or excluded packages
may remain afterward. Existing unattended policy can include obsolete-kernel
cleanup; this is not a feature uninstaller. Soloist's updater and the pinned
AirPlay 2 source build are separate from APT.

No immediate reboot is requested. Installation is refused if APT's automatic
reboot option is enabled; the existing conditional 05:30 timer is preserved.
Services may restart. Reboot-needed metadata is invalidated across a later boot.
Diagnostics remain private in `/var/lib/pi-audio-updates/private/update.log`;
timestamps/reboot metadata in the public status file contain no credentials.

The package repositories/signing keys remain trusted to deliver root-executed
code. Root update privileges permit software changes and service interruption;
they are not an account sandbox. Partial dpkg/APT failures can require manual
administrator recovery; the app never silently runs a purge or forced reboot.

## Optional equalizer

The component inventory includes the fixed EQ helper/module and user-service
state. Installation uses the separate **Install / repair equalizer…** button rather
than the package-repair checklist. Everyday curve changes use **Equalizer…**
beside **Master volume…** in the tray menu. This add-on requires a managed
PipeWire receiver; it leaves EQ bypassed on install/repair and preserves saved
preferences. Disabled/bypassed is a valid state, not a broken player.
See [equalizer setup and recovery](EQUALIZER.md).
