# 0.10.1 stable-release readiness

**Current stage: release candidate `0.10.1-rc.1`; not stable.** Feature development
is frozen for this cycle. Only defect fixes, validation, documentation and
packaging changes belong in this cycle. Finding a blocking defect means fixing
it, rebuilding a new candidate and repeating the affected checks.

The current maintainer has an Ubuntu desktop and one working Pi. There is no
spare Pi/card or interactive Mac/Windows test machine available for this cycle.
Do not overwrite the working receiver to manufacture a fresh-install result.
CI and virtual tests are evidence of their stated scope, not substitutes for
missing physical/interactive checks. Stable publication remains withheld until
the required gates below have evidence, or a separately documented release-scope
decision changes the supported platform/feature set.

## Gates and evidence

| Gate | Current evidence / remaining work |
| --- | --- |
| Desktop regressions | Local full suite: 326 tests, 3 existing tool/platform skips. Required PR security/Linux/Windows/both-macOS checks must also pass for the candidate commit. |
| Small-screen setup | Fixed: choices and tools scroll, checkbox labels wrap, identity/progress/Close remain visible. All 19 languages checked at a 640×480 logical work area; focused tests repeated at 100%, 150%, 200% Qt scaling. |
| Ubuntu graphical runtime | Actual X11/XWayland Qt backend: 20 interaction tests plus 6 layout tests pass with isolated settings/mocked receiver calls. This does not certify every native menu/focus/SSH-agent interaction. |
| ARM virtual smoke matrix | Re-run 2026-10-29: 35 tests per image, no failures/errors, 1 skip each; supported ARM64 accepted, mixed ARM64-kernel/armhf-userspace rejected. Real D-Bus harness skipped where dependencies are absent. [Exact results](testing/virtual-images-2026-10-29.json). No full systemd installer or audio test. |
| Existing receiver read-only check | Reference receiver reachable, managed/supported; all component configuration/binaries present. EQ was installed but bypassed at inspection. Service flags are not proof of audible playback, repair or reboot persistence. No settings/services changed. |
| Native package validation | All four manual build/smoke jobs must pass on the candidate source. Verify every downloaded SHA256 and compare merged source tree before release; native signing limitations remain explicit. |
| Fresh physical setup | **Pending:** spare Pi/card, supported clean OS, complete wizard, credentials, audio and reboot validation. Automated fixtures/emulation do not close this gate. |
| Existing repair/upgrade | **Pending:** deliberate backup followed by selected component repair, credential preservation and reboot/audio checks. Read-only inventory does not close this gate. No destructive migration on the sole working receiver. |
| Interactive desktops | **Pending:** the Ubuntu manual checklist and actual Windows 11, macOS 15+ Intel and Apple Silicon checks below. Mac/Windows human testers/hardware are not currently available. |
| Normal-use trial | **Pending:** at least 72 hours of normal use of the installed candidate, including the scenarios below. No passive telemetry or background test service is added. Record start/end and observations; do not count the earlier preview's usage as this candidate's trial. |
| Documentation/security | Compatibility, install/upgrade/recovery and known limits are linked below. Resolve new high/severe security findings and release blockers before promotion. Existing disclosed signing/platform/optional-feature limits must remain visible. |

## Proposed support boundary

For the first stable release, the established receiver target is a Raspberry Pi
3 Model B+ with 1 GiB RAM, Pi OS / Debian 13 ARM64 userspace, enabled Bluetooth,
PipeWire/WirePlumber and built-in 3.5 mm output. The known interactive desktop is
Ubuntu 26.04 x86_64 GNOME with AppIndicator; packages build on Ubuntu 24.04.
Older Linux distributions, every Pi model/DAC and 32-bit receiver setups are not
implicitly covered by a stable tag.

Windows x64 targets Windows 11; macOS packages target 15+ on Intel/Apple Silicon.
They remain preview-quality platform ports until their manual gates pass. Do not
publish a single stable release claiming all platforms are validated merely
because CI is green. A Linux-only stable scope would require a deliberate,
visible documentation/release decision; this candidate does not silently make it.

Fresh setup remains labelled preview until its physical gate passes. AirPlay,
EQ, MPD/UPnP/web and administrative maintenance retain their documented limits.
A stable core does not certify arbitrary UPnP servers, source-built software,
Bluetooth adapters or network environments.

## Manual desktop and normal-use checklist

Run with a candidate package, a private backup of desktop settings and a known
working receiver. Start at a low amplifier volume; do not use live update or
shutdown actions merely to test a button.

1. Extract/install the correct package, verify its SHA256 and open it. Confirm
   version, single-instance behavior, tray/menu visibility and status window.
   Follow the platform signing instructions; do not disable OS protection globally.
2. Check Close, window X, Escape, minimize, reopen from tray and Quit. Exercise
   Settings, profiles, music/network, volume, EQ, policy and components. Closing
   a busy administrator operation must remain blocked; queued master-volume
   writes must drain before closing. Confirm dialog focus returns sensibly.
3. Change a presentation preference; cancel a connection edit; correct invalid
   fields and save. Restart and verify settings. Toggle login autostart, log out
   and in once, then restore the preference. Quit must not disable autostart.
4. Exercise a real SSH key and, if used, a passphrase-protected key via the native
   agent. Confirm unknown-host verification and rejection of a changed key on a
   separate test profile/fixture; never replace the working receiver's trusted key.
5. Temporarily use an unreachable test profile, return to the real profile and
   confirm recovery. Switch while a read is pending. No previous-Pi state or
   queued command should reach the new target. Do not interrupt the receiver's
   network configuration to simulate a desktop connection failure.
6. During normal listening, test Spotify, Bluetooth, MPD, AirPlay and exclusive
   handover for the sources you use. Check master volume/mute and EQ Apply/Bypass
   without surprise stops. USB/UPnP library, queue, Enter/double-click and Add to
   queue should remain responsive during refresh; no accidental Previous/volume
   write or stale-server track selection should occur.
7. Once during the trial, gracefully reboot the Pi at a convenient time (outside
   updates/setup). Confirm audio settings, service startup, disabled pairing and
   desktop reconnection. Separately verify confirmed shutdown only when actually
   intending to power it off; reconnection requires physical power.
8. Use the candidate normally for at least 72 hours, including desktop restart
   and hidden-tray polling. Record start/end, app/OS/board versions, operations
   exercised, failures and recovery. Do not attach keys, passwords, cookies,
   full settings or private device/library names to a public report.

The maintainer can collect Ubuntu/receiver usage evidence now. Mac and Windows
reports need actual users on those platforms. Fixes affecting lifecycle, auth,
packaging or persistence require repeating those checks on the new candidate.

## Physical install/repair checklist

On spare storage/hardware: flash the supported 64-bit image with networking and
SSH key access; verify fingerprint; run [fresh setup](NEW_PI_SETUP.md); test base
services and chosen options; set independent credentials; verify USB is read-only,
network exposure is as documented and pairing starts disabled. Reboot and verify
playback, selected output, volume, certificate/login continuity and update timers.

On a backed-up managed test receiver: record current component state and credential
fingerprints privately; run [selected repair](PI_COMPONENTS.md), repeat it, and
check unselected features/credentials/media are retained. Test a controlled
interruption/resume only on the disposable receiver, not the sole working system.
A configuration snapshot is not a complete rollback image.

## Candidate installation and rollback

Desktop upgrade does not deploy receiver helpers automatically. Before upgrading,
finish any Pi operation, disable temporary pairing and Quit the old tray app.
Back up its private settings directory, preserving permissions/ACLs:

- Linux: `~/.config/pi-audio-tray` (or the configured XDG location).
- macOS: `~/Library/Application Support/Pi Audio Tray`.
- Windows: `%APPDATA%\Pi Audio Tray`.

Keep the previous package. Install/extract the new package into a permanent user
location and launch it; verify version, selected profile, trust and autostart.
The current candidate changes no settings schema. To roll back, Quit it, reopen
the previous package and repair your launcher/autostart registration if needed.
Restore the private settings backup only if it is needed; keep its access controls
and never approve a changed host key simply to make recovery work. Receiver
configuration rollback is a separate administrator task; see
[repair backups and partial failures](PI_COMPONENTS.md#credentials-backups-and-partial-failure).

## Stable promotion

A maintainer must review the gate evidence and normal-use log, resolve blockers,
update the supported-scope/known-limit notes and remove the candidate suffix in
`version.py`. Run the full checks and four native package jobs for that exact
commit, merge through the protected PR and compare source trees. Publish
`v0.10.1` as a non-prerelease only after verifying the eight package/hash assets.
Do not just relabel a different binary or an unvalidated RC as stable. Keep old
previews available for rollback and include compatibility/security limitations
in the release notes.

Current candidate filenames/UI retain `-rc.1`; macOS's numeric bundle metadata
uses `0.10.1`. Neither field nor a successful build changes this readiness status.
