# Fresh-Pi setup review — 2026-10-27

Scope: 0.7 preview desktop wizard, SSH provisioning transport, explicit bundled
manifest, fresh receiver installer, MPD-only option and packaging. This is an
internal engineering review, not an independent security certification.

## Boundaries and mitigations

- Existing strict SSH host-key checking is reused for probing, provisioning,
  output selection and API-key storage. No auto-accept or changed-key replacement.
- One masked sudo password goes to `sudo -S` stdin without a PTY. It is never
  interpolated into a command, environment, file or progress line. Newlines are
  rejected. Field cleared before review; worker callable released after execution.
  Python/Qt cannot guarantee immediate erasure of all memory copies.
- Explicit file manifest excludes checkout metadata, user config and secrets.
  Random staging directory 700, files 600, bounded SFTP timeouts. Cleanup uses
  exact filenames, not recursive deletion. Disconnect cleanup is best-effort.
- Administrator execution necessarily trusts the shipped code, desktop and SSH
  account staging it. A root-private bundle snapshot is used for subsequent
  stages. This is not safe delegation to an otherwise hostile local account.
- Root rechecks Debian/RPi OS 13, ARM64 Raspberry Pi and normal account, refuses
  existing audio/web setup and user audio overrides, validates the strict plan,
  checks selected USB transport/filesystem/root/boot exclusions and service ports.
- Root-owned state + lock prevents concurrent setup and changed-plan retry.
  Interrupted stages may rerun; completed installations cannot be overwritten.
  Installation is not transactional. Timeouts/disconnects require administrator
  inspection before retry; packages and mounts are never silently rolled back.
- Scheduled system maintenance/reboot timers are armed only after selected
  components pass service verification, so initial setup cannot schedule its
  own reboot partway through package installation.
- Installed runtime sudo grants are exact commands, validated with `visudo`.
  No installation permission is granted. Pre-existing sudo rules remain intact.
- Bluetooth startup disables pairing/discovery; user-approved temporary pairing
  remains governed by the existing reviewed helper. No broad pairing agent is
  left running. Soloist remains a user service with WebSocket disabled.
- Optional `hidepid=2` persists process privacy, with a private fstab backup.
  It protects against other unprivileged users, not root or the audio account.
- MPD-only mode installs no web packages/listeners. The full web choice reuses
  existing cookie/CSRF/session isolation and private-IPv4 HTTPS design. No CA
  private key or prior receiver credentials are copied to clients.
- Cockpit's socket is masked before package installation, configured for
  127.0.0.1 only, then enabled. No public management listener or desktop tunnel
  is automatically opened. Cockpit authentication is separately required.
- Soloist updater still restricts HTTPS host/redirects and archive/ELF sizes.
  Trust remains the official CDN/local CA store; no separate vendor signature
  is checked. myMPD uses the existing pinned repository signing fingerprint.
- Audio output discovery filters the PipeWire graph on the Pi to avoid sending
  unrelated process metadata. IDs are numeric, bounded and rechecked before use;
  node replacement between check and command remains possible. Hardware/master
  volume is unchanged; player defaults are 100%, so first playback needs care.
- GUI uses plain-text inventory/progress, snapshots the configured connection,
  pauses monitoring and prevents close while its task runs. This cannot prevent
  forced termination, power loss or network interruption.

## Static analysis triage

The first CI dependency audit reports **zero known advisories**. Bandit flags the
remote `/tmp` path and the local `NamedTemporaryFile(dir='/tmp')` as medium B108:
the former uses an unpredictable 128-bit name, exclusive SFTP mkdir and mode
700; the latter is exclusively created with mode 600, then handed only to the
trusted audio account and removed. Neither reuses a predictable world-writable
file. B110 is deliberate best-effort cleanup after SSH loss. Subprocess B404/B603
findings refer to fixed argument arrays, bounded user/UUID/ID values and no shell;
the administrator PATH is fixed, and inventory commands use absolute paths.
No `shell=True` or unvalidated command construction is introduced.

## Validation and known limits

137 automated tests pass locally (3 established platform/tool-dependent skips).
New coverage includes schema injection/type checks, manifest completeness,
symlink refusal, wrong-platform/existing-service rejection, stdin-only sudo
transport and exact cleanup, stale output IDs, wizard restrictions, and staged
installer fixtures for base/all-options/MPD-only. Fixture installer commands are
recorded rather than executed; they are not real apt/systemd integration tests.
Packaging self-tests instantiate the wizard and check every required payload file.

Read-only checks on the existing Pi 3B+ correctly refuse a fresh installation and
successfully discover the current audio sink. Initial full `pw-dump` transfer
exceeded the old SSH output cap; filtering it on the Pi fixes that failure.

No spare blank Pi was provisioned. Actual first-boot installation, physical
Bluetooth/Spotify/MPD playback, power-cycle persistence, and hardware-specific
routing remain required before promoting the wizard out of preview. The existing
working receiver was not re-provisioned. Windows CI now explicitly skips the Pi-only Unix-socket web-service module
before import (Windows lacks `socketserver.UnixStreamServer`); Linux/macOS still
exercise it. Native macOS/Windows package builds are
separate from an interactive first-install test on those platforms.

The new-Pi guide documents prerequisites, trust, generated credentials,
certificates, open ports, sudo permissions, process privacy, timezones,
non-transactional failure/retry and manual recovery. This wizard does not perform
SD imaging, network onboarding, legacy backend migration or filesystem formatting.

## Follow-up: virtual image regression (0.7.1)

The [local virtual lab](VIRTUAL_TESTS.md) subsequently ran official ARM64 and
armhf Pi OS images on QEMU's Pi 3 model. It reproduced 0.7.0's mixed-bitness
acceptance bug. The probe and administrator resume check now require ARM64
userspace and a 64-bit Python interpreter. Both image expectations pass after
that change, with 35 Pi-side tests per image (one dependency skip each). The
host suite now has 142 tests, with the same 3 local platform/tool skips.

The lab uses a test-only init, not normal systemd, and does not run the complete
installer or physical audio/radio tests. Its security boundaries, skip reasons,
excluded Pi 2 boot attempt and exact measured results are documented in the guide.
This adds ARM image regression evidence without lifting the setup preview label.
