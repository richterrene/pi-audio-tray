# Receiver maintenance and audio controls review (0.8 preview)

Reviewed 2026-10-28. This is a source, protocol and regression review, not an
independent penetration test or a claim of hardware playback certification.

## Scope and boundaries

- Component inventory reads package/configuration presence and service states;
  private keys, passwords and certificates are not returned to the desktop.
- Fresh installation and additive maintenance are separate plans. Maintenance
  requires the existing root-owned audio-account marker, supported Debian 13
  ARM64 Pi and explicit component selection. Unselected features are not removed.
  Core repairs can restart shared audio services. Existing web identity and USB
  UUID checks prevent accidental replacement; configuration snapshots do not
  constitute transactional rollback. See [component maintenance](../PI_COMPONENTS.md).
- On-demand updates use two exact root-owned sudo commands, the existing allowed
  origins/holds and a shared installation lock. Arbitrary packages/commands are
  not accepted. Automatic unattended reboot configuration is refused before
  installation; the conditional reboot schedule remains separate.
- Master volume needs normal-user SSH only. Requests validate output id, stream
  serial and server cookie, prohibit amplification and keep mute independent.
  Drag requests coalesce; opening the dialog never writes volume.
- Exclusive mode is opt-in, with mixing as the default. It mutes losing recognized
  streams before attempting pause, preserves preexisting mutes and guards owned
  mute restoration against server/id reuse. Soloist's unauthenticated API is
  enabled on loopback only while opted in; other local Pi users remain trusted.
  See [source policy](../SOURCE_POLICY.md), including advancing muted senders and
  the remaining need for physical cross-source tests.
- AirPlay 2 builds checksum-pinned source as a dedicated unprivileged account.
  Root validates and copies ARM64 ELF artifacts without running upstream build
  recipes as root. NQPTP receives only the capability needed for low UDP ports.
  The upstream shared-memory/local-control boundary is unsuitable for untrusted
  multi-user hosts. APT does not update this pinned source build; explicitly
  reinstall from a newer reviewed app. See [audio roadmap](../AUDIO_ROADMAP.md).

## Evidence and limits

The final local run completed 256 tests with three environment-dependent skips
and zero outbound connection attempts. All 350 desktop templates are present
with valid placeholders in the 19 language catalogs. The packaged UI self-test
was also exercised from source.

Local regression coverage includes old protocol compatibility, per-profile
inventory, additive repair selection, preservation/refusal paths, exact privileged
commands, stale volume identity, GUI request coalescing, source handover and
mute ownership, cookie/id reuse, oversized playback events and private settings.
Screenshot generation exercises the real dialogs with fictional data and SSH
blocked. A policy-status formatting collision found this way has a regression.

The pinned sources compile in Debian 13 as an unprivileged user. The local timing
harness checks the ABI derived from the actual pinned C header and client
isolation; the upstream stale ABI assertion is not counted as a passing test.
The native ARM64 source-build and timing harness passed in CI. Desktop CI
provides separate package evidence. Cross-platform CI also exposed fixture
isolation and early-HTTP-rejection races; the tests now avoid host sudo paths
and still assert the actual HTTP rejection after a client broken pipe.
Read-only checks on the existing Pi confirm inventory, update listing and master
output parsing; no receiver upgrade or playback change was performed by these
checks. This does not validate a complete physical fresh installation or every
Bluetooth/AirPlay sender and DAC combination.

The local Bandit run found no high-severity findings and nine medium heuristics.
Each was inspected: secure temporary staging, fixed checksum-pinned download URLs,
all-interface port-conflict probes, intentionally public executable permissions,
and the preexisting web listener/download boundaries. Findings are not suppressed
as proof of safety. The full scan and exact dependency advisory audit are retained
as CI security artifacts. The exact local dependency inventory also had no
known advisories in pip-audit on the review date; results are point-in-time,
not guarantees.

## Physical installation follow-up (0.8.1)

The first Pi deployment exposed a permissions defect missed by mocked installer
fixtures: umask 0077 made newly created public binary/unit directories 0700.
Compilation succeeded, but the normal audio account could not traverse them.
Activation stopped; the orchestration restored the previous master output.

Directory creation now applies explicit modes to each newly created directory,
while retaining existing private ancestors and explicit private targets. A narrow
public-directory repair handles the existing affected unit/binary prefixes. It
rejects traversal, symlinks, unrelated private paths and non-root/writable paths.
Real filesystem regressions exercise the private installer umask and distinguish
public runtime directories from private state, in addition to mocked root stages.
The source build is unchanged; this correction does not make private credentials
or configuration public. Cross-source playback still requires separate tests.
