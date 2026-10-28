# Fixed stereo EQ review — 0.9.0 preview

This is an implementation review and recorded test evidence, not an independent
security audit or a promise of compatibility with every Pi/audio output.

## Scope and trust boundaries

Reviewed the desktop transport/dialog, pure graph generator, normal-user receiver
helper, fixed administrator installer/user unit, component inventory, native
packaging manifests, translation additions and opt-in null-output test harness.
Existing SSH host verification and profile capture remain in use. No new runtime
sudo rule, network listener, executable download or desktop dependency is added.

The installer has a narrow managed Debian/Pi OS 13 ARM64 boundary, verifies both
kernel and userspace architecture, and refuses foreign add-on ownership. It uses
the existing shared setup lock, root-private snapshot/log directory and explicit
public runtime directory modes. It bypasses only its own EQ service on repair;
existing players are not restarted. As with the base installer, an administrator
explicitly authorizes running the bundled reviewed code. This does not protect
against a compromised desktop distributing malicious installer source.

Runtime settings are exact-schema bounded data over SSH stdin. The normal audio
account cannot use these controls to write arbitrary root files, select plugins
or execute supplied commands. Output names are bounded and must match a physical
stereo sink. Private atomic files, nofollow reads, a serializing lock, previous
settings and service/file restoration protect ordinary failure/retry paths.
The account and root can still modify their own PipeWire session directly;
user-private service/configuration is not a same-account sandbox.

Conservative gain attenuation and a final sample clamp are not a dynamic limiter
or speaker protection guarantee. Bypassing an attenuated curve can sound louder.
The UI shows effective preamp, uses explicit Apply, and preserves hardware gain.
No automatic background polling or live slider commands are added.

## Review findings and fixes

- Qt cleanup uses `finished_task`, preserving `QDialog.finished(int)`. Real-thread
  regressions cover the new dialog alongside volume, updates, source policy,
  AirPlay and components. This retains the 0.8.3 lifecycle fix.
- Unsupported/missing targets cannot be enabled; bypass remains available with
  missing hardware or invalid saved settings. Apply failure restores old files
  and service enablement where possible. Repair deliberately leaves EQ bypassed.
- Public install directories use the reviewed explicit-mode helpers, avoiding
  the private-umask traversal defect found in 0.8.1. Private ancestors stay private.
- The live test initially used a pre-EQ sink monitor. Its recorder now connects
  explicitly to its own null sink's monitor, so measurements are post-filter.
  Only this harness's generated node/port IDs are linked; no real output is used.
- Installed PipeWire 1.4 requires the formatted module-array representation used
  by the graph generator. The live harness now uses the identical serializer.
- Runtime capability/active checks include the supported platform/features;
  filtered output descriptions retain a printable nonempty fallback.

## Evidence and remaining limits

- 283 local regression tests passed, with three existing skips, and the GUI
  smoke test passed. Local Unix-socket HTTP tests ran with socket permissions;
  ordinary sandbox restrictions are not application failures.
- All 19 language catalogs cover 376 templates with matching placeholders.
  Translations remain contribution-ready drafts, not professional language QA.
- Exact desktop dependency inventory: no known advisories from pip-audit 2.10.1.
- Bandit 1.9.4 over the expanded runtime/build/tool scope: 0 high, 19 medium,
  140 low findings. The medium findings are pre-existing reviewed SSH, URL,
  temporary-path, network-binding, file-mode and bounded XML-parser boundaries;
  see earlier security records. New EQ runtime/installer findings are low-severity
  subprocess imports/calls with fixed argument vectors, no shell expansion.
  Static counts are evidence to inspect, not a security pass/fail verdict.
- Actual Pi 3 B+ / Debian 13 / PipeWire 1.4.2 null-output tests at 44.1/48 kHz:
  both channels −6.00 dB for the −6 dB/1 kHz band, zero filter xrun counters,
  2.5–3% of one CPU core for DSP, target recreation by stable name, direct
  recovery after filter termination, and unchanged hardware default.
- Physical Flat EQ installation passed on the reference receiver. Installed
  helper/module/unit bytes matched the reviewed source and were root-owned;
  startup enablement, filter restart, settled routing without duplicate/direct
  playback or feedback, active source policy, and unchanged master output/gain
  were verified. An initial immediate post-restart graph assertion ran before
  links settled; the bounded link-readiness check passed. Startup enablement is not a claim
  that a physical reboot was tested. Other DAC hotplug behavior, long-running
  CPU/latency, multichannel outputs and room correction are outside this evidence.

No media metadata, account credentials, local hostnames, private keys or user
settings are included in screenshots/test fixtures. The isolated harness emits
only synthetic-node diagnostics. Current-source native desktop packaging checks
must pass before release; local Linux checks do not establish macOS/Windows
interactive behavior. See [operation/recovery](../EQUALIZER.md) and
[the security model](../../SECURITY.md).

## 0.9.1 navigation and credential separation

Everyday Equalizer controls now sit beside Master volume in the tray menu.
Administrator installation/repair is a separate dialog reached through Pi
components; the playback dialog no longer constructs a password field or offers
installation actions. The same fixed installer, verified SSH transport, explicit
confirmation and credential clearing remain. No receiver helper, startup setting,
EQ curve or runtime privilege changes are needed for this desktop update. Real
Qt worker/close regressions include both dialogs, and the compact-menu limit is
retained. Screenshots and all language catalogs follow the new navigation.

## 0.9.2 EQ/source-policy interaction

A physical Spotify reproduction showed that enabling Flat EQ could wake an
existing policy-muted AirPlay stream in the audio graph. The arbiter mistook the
rewiring for new playback, selected AirPlay and paused Spotify. The EQ service
had not failed. Earlier synthetic EQ tests did not run this concurrent-source
scenario and therefore missed the interaction.

The normal-user policy helper now observes the existing private DSP lock (with
nofollow/nonblocking opens, owner/mode/regular-file checks) and changes in the two
fixed filter-node identities. It suppresses graph-only source promotion during
rewiring and a bounded two-second settling period; explicit observed Spotify or
BlueZ play events remain effective. It keeps mute enforcement and ownership
checks active. Same-account users already control this audio session; the guard
is coordination, not a security boundary. No extra runtime privilege or port is
added. Regressions cover the Spotify/AirPlay rewire sequence, explicit takeover,
lock release, filter restart/removal and expiry. Full suite: 285 tests passed
with three existing skips. Focused static review found no high/medium findings.
