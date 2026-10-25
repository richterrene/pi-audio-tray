# Application security review — 2026-10-25

Scope: desktop configuration and SSH transport, Qt pairing dialogs, the privileged
BlueZ helper, Soloist key storage/launcher, updater, reboot helper, packaging and
GitHub Actions. This is a maintainer review with regression tests, not an
independent audit or a claim that the application is vulnerability-free.

## Findings and changes in 0.3.0

| Finding | Change |
| --- | --- |
| The updater previously executed the downloaded client’s `--version` as root. A compromised upstream download could therefore execute with updater privileges. | Remove that execution. Bound download/decompression/archive size and member count, validate ARM64 ELF headers, restrict redirects to the official HTTPS host, reject archive links/path traversal, and sandbox the updater service. |
| Soloist’s key argument could be read through a permissive `/proc`; the optional unauthenticated local WebSocket server expanded access. | Keep the key file private, validate ownership/type/size without following links, disable the server in the supplied launcher, and disable core dumps in the user unit. Document optional `hidepid=2`, which still permits root and same-user inspection. |
| SSH replies and pairing messages lacked adequate stream and schema bounds. | Add deadlines, combined stdout/stderr caps, bounded lines and queues, validated status/events, sanitized device text, and plain-text dialogs. Drain both SSH streams to avoid pipe deadlocks. |
| A queued reply/dialog from an old pairing session or connection could affect newer GUI state. | Bind events and dialogs to a local session ID, reject stale job generations, limit outstanding dialogs, and block settings changes during mutations. |
| Bluetooth closure could fail incompletely; service authorization was too broad. | Attempt both closure flags independently, verify closure before reporting success, and allow only audio UUIDs through the agent. |
| Local settings/host-key writes and lock handling could follow unsafe paths or expose partial writes. | Private directories/files, random atomic replacement, no-follow locks, exclusive private SFTP staging, and refusal to replace a different already trusted host key. |
| Maintenance scripts inherited Python/import or command environment; build actions used mutable tags. | Isolated Python entry points, fixed maintenance PATH/absolute privileged commands, tighter unit restrictions, SHA-pinned Actions, and no persisted checkout credentials. |

Status collection runs as the ordinary SSH/audio user. It returns bounded,
validated CPU/temperature/load/memory/disk/uptime snapshots; no process command
lines or credentials are needed for these statistics.

The follow-up review below found that skipping trust after a failed close had
not actually been implemented in 0.3.0. That gap is fixed by the follow-up; the
initial review must not be read as evidence that 0.3.0 had that protection.

## Validation

- 45 unit/regression tests: unsafe paths and permissions, stale trust/dialog/job
  state, bounded SSH output, protocol and statistics schemas, BlueZ authorization
  and cleanup, malformed/oversized/linked archives, redirect policy, key handling,
  updater replacement without execution, and conditional reboot decisions.
- Live Debian 13 ARM64 receiver: normal-user SSH status and stats, service state,
  pairing open/close with explicit verification, hardened updater success,
  Soloist restart, and conditional-reboot check without rebooting.
- On that receiver, optional `hidepid=2` was tested before persistence: the audio
  user retained process access and monitoring, while an unrelated unprivileged
  account could not read its process arguments. Audio services stayed active.
- `pip-audit` on the 14 installed build/runtime distributions reported no known
  vulnerabilities on the review date. This is a point-in-time database check.
- Bandit reviewed 42 findings (6 medium, 36 low). Medium findings are SSH command
  calls with fixed application commands, securely created temporary paths, and
  the AppImage runtime download whose pinned SHA-256 is checked before use. Low
  findings are primarily deliberate subprocess calls with argument arrays and
  cleanup handling. None were treated as proof of safety; relevant behavior is
  covered by tests and the source review above.

Native package build/smoke-test results are recorded in GitHub Actions for the
release. macOS CI does not replace interactive testing on a physical Mac.

## Remaining trust and limitations

- No separate Spotify signature is available to this updater. TLS, the official
  CDN, local CA store, root configuration and Spotify’s proprietary executable
  remain trusted. Stored hashes are consistency checks, not publisher signatures.
- The key remains in Soloist’s arguments because that is the upstream interface.
  Root or a compromised audio account can read it and stored session credentials.
  Removing a key file is not revocation at Spotify.
- BlueZ’s Trusted property is global. A device explicitly approved here may be
  trusted by other installed Bluetooth services; the agent’s UUID allowlist does
  not impose a system-wide Bluetooth policy.
- Closing pairing does not disconnect existing trusted devices. A compromised
  Pi or SSH account can return false status. No remote attestation is provided.
- Linux desktop graphics libraries and SSH agents remain host dependencies.
  macOS packages are ad-hoc signed, not Developer ID signed or notarized.

See [SECURITY.md](../SECURITY.md) for deployment rules and reporting, and
[Pi setup](PI_SETUP.md#upgrading-an-existing-installation) for upgrade steps.


## Follow-up review — fixes in 0.3.1 (same date)

The second pass reviewed the same code and deployment files, with particular
attention to caller identity, cleanup failures, concurrent GUI actions, and
whether the earlier documentation matched the implementation.

| ID | Finding and preconditions | Resolution |
| --- | --- | --- |
| F-01 | Agent callbacks did not check the caller’s bus identity. A local process allowed by system-bus policy to reach the exported object could forge prompts/display events or call Cancel/Release. No remote-code-execution or root-escalation path was demonstrated. | Every exported Agent1 method receives the bus-supplied sender and checks it against BlueZ’s unique owner captured when registering. Unrelated senders are rejected before side effects. A replacement BlueZ process cannot inherit the old owner’s authority. |
| F-02 | Cleanup still trusted approved/paired devices after close_pairing failed, contrary to the initial review’s wording. Pending requests also survived a stop, and approvals were not rechecked against adapter flags. | Skip new trust when closure cannot be verified; cancel pending callbacks during stop/cleanup; refuse approvals when pairing/discovery is off. Existing trusted devices are not untrusted automatically. |
| F-03 | Different GUI mutation jobs could overlap, and settings-save validation errors were not caught. | Serialize mutations, retain an emergency pairing-off path, and report invalid settings without changing the saved connection. |
| F-04 | Desktop key validation counted characters while the launcher enforced bytes; NUL/control characters could reach exec validation. | Apply a UTF-8 byte bound reserving the trailing newline and reject nonprintable/whitespace characters before connecting; apply matching printable-key checks in the launcher. |
| D-01 | Repository vulnerability reporting, dependency alerts and secret scanning protections were disabled. The original review also lacked a full-history secret scan and repeatable CI audit. | Enable private reporting, dependency alerts, secret scanning and secret push protection; scan reachable history with Gitleaks; add PR/main/manual security checks with exact dependency inventories and a real D-Bus integration test. |

Follow-up evidence:

- **54** unit/regression tests passed locally, including all nine exported Agent1
  methods rejecting missing/foreign senders, cancellation/late approval, closure
  failure, overlapping jobs and key byte/control bounds.
- `tools/check_dbus_agent.py` passed using real dbus-python connections on an
  isolated private bus. Foreign callers could not display codes, request
  authorization, cancel or release; the simulated BlueZ owner could call its
  agent. No Bluetooth hardware or credentials were used. The mechanism follows
  the upstream [sender_keyword documentation](https://dbus.freedesktop.org/doc/dbus-python/dbus.service.html#dbus.service.method).
- Gitleaks **8.30.1**, official archive checked against its published checksum:
  all **8** commits reachable from local refs at review start scanned with
  `git --log-opts=--all --redact`, no detected secrets. A separate working-directory
  scan also found none; large ignored AppImages were skipped. This is not proof
  that arbitrary proprietary tokens, personal data or all release binaries are absent.
- pip-audit **2.10.1**, exact installed inventory with `--no-deps --disable-pip`:
  **14** distributions, no known advisories. [Raw sanitized result](security/dependency-audit-2026-10-25.json).
  Unlike resolver-based scanning, this explicitly includes installed packaging
  and setuptools versions. Native dependencies/Qt/Python and other OS packages
  are outside that advisory result.
- Bandit **1.9.4**: 42 findings, 6 medium/36 low, individually reviewed. See the
  rule-by-rule disposition in [SECURITY.md](../SECURITY.md#static-analysis-disposition).
- GitHub settings were verified after enabling them. Dependency and secret alert
  queries returned no alerts at that point; GitHub’s background analysis may lag.

Source changes require upgrading the desktop and Pi helper/launcher; repository
settings take effect immediately. A green source test does not prove that an
older installed package or helper contains these fixes. CI runs for the follow-up
record native build/test results separately. See the documented upgrade procedure.
