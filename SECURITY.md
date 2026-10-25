# Security and privacy

This is experimental software for a trusted home-network receiver. The desktop
app and Pi helper have not undergone an independent security audit.

## Supported versions and review status

Use 0.3.1 or later, with the latest release and its matching Pi helper/launcher.
The follow-up findings affect 0.3.0 and earlier. Older releases do
not receive backports. This is a best-effort personal project without a security
response SLA. The [dated review](docs/SECURITY_REVIEW_2026-10-25.md) records findings,
fixes, evidence and residual risks; its initial 0.3.0 conclusions are explicitly
corrected by the follow-up. No independent audit or penetration test is claimed.

## Threat model and attack surfaces

| Boundary | Assumptions and controls |
| --- | --- |
| Desktop → Pi | SSH encryption and host-key verification. A new host requires independent fingerprint confirmation; changed keys fail. The selected Pi/SSH account is trusted to supply truthful status. No agent forwarding is requested. |
| Local desktop | The desktop OS, Python/Qt bundle, SSH agent and logged-in user are trusted. Settings contain host/user/key paths but not private key contents. Private directories must have trusted parent directories. Same-user malware can read memory or alter settings. |
| Bluetooth → Pi | Nearby devices and their names are untrusted. Confirm the device and, when shown, the matching passkey. Just Works authorization has no numeric identity proof. BlueZ owns authentication/encryption; the app’s agent accepts callbacks only from its captured unique bus owner. |
| SSH user → root | Exact helper pair/off commands and a fixed systemd update start. These permit pairing and update-triggered interruption, not arbitrary administrative commands. Root-owned helpers, units and all parent paths must be non-writable by that user. |
| Updates → system | APT repositories can deliver root-executed code; trust their signing keys/origins. Soloist trusts Spotify’s HTTPS/CDN and runs as the audio account after structural validation. Third-party audio/client internals are outside this code review. |
| Repository → desktop | GitHub, PyPI packages, Actions runners and maintainers are trusted. Direct Python dependencies and Actions revisions are pinned, but transitive dependencies lack a complete hash-locked set. Package checksums detect transfer changes; they are not independent publisher signatures. |

The tray starts no desktop network server; it connects directly to the configured
SSH endpoint. OpenSSH Host/ProxyJump/ProxyCommand settings are not loaded; set the
actual hostname, port, username and known-host alias in the app. Paramiko uses the
user’s OpenSSH known_hosts and the private app-approved host store. It may use
SSH-agent identities in addition to the selected key. Password entry and agent
forwarding to the Pi are not supported.

The receiver’s SSH, Bluetooth, Spotify and audio-stack network behavior is owned
by those components. This app does not configure a firewall or promise a complete
port inventory. Keep it on a trusted LAN or a separately secured VPN; do not
port-forward the receiver publicly. Radio range is not a security boundary.
Other agents, local administrators and BlueZ policy can override pairing state.

## Credentials

- SSH keys stay on the desktop. Unknown host keys require explicit approval;
  changed keys fail verification. Verify fingerprints independently.
- Soloist keys are entered in a masked dialog and sent through SSH directly to
  the Pi. The Pi directory is mode 700 and the file is mode 600. The desktop app
  does not save a local copy or write a persistent application log.
- Removing a key file is not revocation. Revoke or replace exposed keys in the
  Spotify dashboard. Stored Soloist sessions may also contain credentials.
- The official Soloist executable currently accepts its key via a command-line
  option. Even though the key is absent from the unit file and repository,
  process/status inspection on a default Linux `/proc` mount can expose it to
  other local accounts. Optional `hidepid=2` reduces that exposure; root and
  processes under the audio account can still read it. Treat that account and
  administrators as trusted. The supplied unit disables core dumps. Never publish full process
  listings, `systemctl status soloist`, crash dumps, or unsanitized logs.
- The supplied launcher does not enable Soloist’s optional WebSocket server.
  That server has no authentication, including for other local users. If you
  enable it yourself, local control commands become available, but do not expose
  it to your LAN or the internet.

## Privileged operations

The GUI uses SSH. Installation requires an administrator; pairing needs root
access to the fixed Pi helper. Update checks start a fixed root-owned systemd
service. Do not make helpers or their parent directories writable by ordinary
users. Do not grant passwordless sudo to a script in a writable checkout.

The app does not create sudo rules. If restricting a user after administrative
setup, grant only the exact required commands in a sudoers file validated with
`visudo`. For example, substitute your real Pi username for `AUDIO_USER`:

```sudoers
AUDIO_USER ALL=(root) NOPASSWD: /usr/local/lib/pi-audio-tray/pi-helper pair
AUDIO_USER ALL=(root) NOPASSWD: /usr/local/lib/pi-audio-tray/pi-helper off
AUDIO_USER ALL=(root) NOPASSWD: /usr/bin/systemctl start soloist-update.service
```

Read-only monitoring and Soloist key storage use the normal SSH user's rights.
The GUI helper installer needs broader administrative rights during setup and
will fail under the minimal runtime rules above.

The narrow rules do not make an untrusted SSH account safe: it can repeatedly
open pairing windows or trigger update checks, consume resources, and interrupt
playback. Rate limiting and isolation of a hostile account are not provided.
Use a dedicated audio account and avoid unrelated services/secrets under it.

## Updates and trust boundaries

The root updater limits downloads, decompression, archive entries, redirects,
and execution time. It validates an ARM64 ELF header and installs atomically;
it never executes downloaded code as root. Its service restricts filesystem
writes and resources. Downloads still trust Spotify’s HTTPS CDN and the local
CA store: a stored SHA-256 detects local consistency, not vendor authenticity.
No independent vendor signature is verified. Soloist runs as the audio user,
which can access that user’s credentials and audio devices.

Pairing requests require explicit confirmation. Only Bluetooth audio service
UUIDs are accepted by the pairing agent. Approvals belong to one pairing session;
old dialogs cannot authorize requests in a later session. Closing attempts both
pairable and discoverable flags and reports failure if closure cannot be verified.
A trusted BlueZ device is trusted globally by BlueZ, not exclusively by this app’s
agent: keep other Bluetooth services and pairing agents disabled if unnecessary.

SSH replies are bounded by size/time and validated before display; text dialogs
use plain text. The desktop config directory is private, and sensitive writes
are atomic. These controls do not protect against a compromised desktop user or
SSH/audio account. Do not expose this experimental receiver directly to the internet.

The [2026-10-25 review](docs/SECURITY_REVIEW_2026-10-25.md) records scope,
validation and remaining limitations; it is not an independent audit.

## Deployment and recovery

- Follow [Pi setup and upgrades](docs/PI_SETUP.md). Upgrade root helpers, user
  launcher/units and desktop packages as applicable; the GUI installer replaces
  only the status/pairing helper. Do not run downloaded clients or the status
  probe as root. Check ownership along every installed path with `namei -l`.
- Verify a new SSH fingerprint independently, for example from the Pi console
  using `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`. Confirm the key type
  matches the prompt. For legitimate rotation, verify first, back up the relevant
  known_hosts file, then remove only the old host entry. Never bypass verification
  with automatic acceptance or delete a changed key before investigating it.
- A pairing window is three minutes, with BlueZ timeouts as a fallback. Normal
  shutdown, channel loss, and signals attempt closure. SIGKILL, daemon failure,
  stalled D-Bus/output or other agents can prevent cleanup; reconnect and verify
  both Pairable and Discoverable are false. Disabling pairing retains trusted
  devices/connections; remove unwanted trust using the Pi’s Bluetooth tools.
- Updater download/archive bounds are 64 MiB compressed, 192 MiB expanded, 128 MiB
  executable and 64 entries. Its systemd unit enforces an overall timeout and
  memory/task limits. These do not verify publisher identity or prevent a
  trusted CDN from serving a structurally valid malicious/older client.
- Updates can interrupt playback; there is no health-checked automatic rollback.
  `soloist.previous` preserves only the previous executable, not session/config
  state. If restart fails, inspect sanitized logs and explicitly restart/restore
  a trusted version as administrator. Do not assume a successful download means
  the running process was updated. Service results report failures.
- Daily Soloist checks run at 04:00–06:00 with catch-up; the optional 05:30 reboot
  skips known running scheduled updates and is not caught up during daytime.
  This check is not a system-wide package-manager lock and cannot eliminate a
  race with a manually started update. Maintain valid system time and review
  configured APT origins. See README for the complete schedule.
- The supplied units disable the optional unauthenticated WebSocket server and
  Soloist core dumps. Desktop process memory, swap, clipboard managers, terminal
  capture, system logs, backups and third-party crash collection can still retain
  sensitive data. Python does not provide guaranteed secret-memory erasure.
- Backups of `.config/soloist`, systemd state/session directories or your home
  may contain credentials. Apply private permissions and appropriate encryption;
  exclude them from Git and shared cloud folders. The app has no telemetry or
  persistent application log; this does not govern Spotify or OS logging.
- Check release SHA-256 files and source/CI provenance. The Linux AppImage has no
  independent release signature; macOS uses ad-hoc signing and is not notarized.
  The tray itself has no automatic desktop update mechanism; install new releases
  manually. macOS signing and App Store limitations are in [MACOS.md](docs/MACOS.md).

For a suspected exposure, stop the affected service, revoke/rotate the Soloist
key in Spotify, revoke affected sessions as supported by Spotify, and replace any
exposed SSH key and its authorized_keys entry. Removing a file does not revoke a
credential or securely erase flash storage/backups. Preserve only redacted
incident evidence. A compromised audio/desktop account requires remediation of
that account/host, not just replacement of the visible key file.

## Reproducing checks

Use a disposable virtual environment and a reviewed checkout:

```sh
python -m unittest discover -s tests -v
python tools/dependency_inventory.py > /tmp/pi-audio-dependencies.txt
# Install these in a separate audit environment, not in the inventoried one:
# pip-audit==2.10.1 bandit==1.9.4
pip-audit --disable-pip --no-deps -r /tmp/pi-audio-dependencies.txt -f json
bandit -r app.py core.py pi_helper.py pi packaging_support.py build_appimage.py build_macos.py
# Linux, requires native python3-dbus/python3-gi and dbus:
dbus-run-session -- /usr/bin/python3 -I tools/check_dbus_agent.py
# Gitleaks 8.30.1; verify its official archive checksum before installing:
gitleaks git --redact --log-opts=--all .
```

The PR/main/manual [security workflow](.github/workflows/security-checks.yml)
fails on regression/D-Bus test failures and known Python dependency advisories.
It uploads exact dependency and static-analysis reports. Bandit is report-only
because deliberate system/SSH calls generate reviewed findings; new findings
still require human review. GitHub dependency alerts, secret scanning and secret
push protection are enabled. Generic/non-provider token scanning and validity
checks are not enabled; no automated check covers every Soloist token or PII.
Automatic Dependabot remediation PRs and automatic merges are not enabled.
Scanners can miss unknown vulnerabilities and native bundled-library issues.
Review Python/Qt/AppImage runtime/OS security advisories separately when updating.

## Static-analysis disposition

Bandit 1.9.4 review of production/build sources on 2026-10-25: 42 results,
6 medium and 36 low. No global rule exclusions are used.

| Rule | Count | Disposition |
| --- | ---: | --- |
| B601 | 2 | Paramiko command execution is intentional. Commands are fixed; only the generated installer temp path is shell-quoted into one command. Connection values and API keys are not shell-interpolated. |
| B108 | 3 | Build temp dirs use mkdtemp (private/random); remote staging uses UUID plus exclusive SFTP creation and mode 600. Temp path predictability is not relied upon for access control. |
| B310 | 1 | AppImage runtime URL is fixed; downloaded/cached bytes must match a pinned SHA-256 before packaging. HTTPS alone is not the runtime integrity control. |
| B603 / B607 | 18 / 9 | Intentional subprocess argument arrays, no shell=True. Pi helper PATH is reset to system directories; privileged maintenance uses absolute paths. Build tools/PATH are trusted inputs on the build host. |
| B404 | 5 | subprocess imports required by those operations; not a vulnerability by themselves. |
| B606 | 1 | Launcher execv uses an absolute Soloist path and a separate argument per value. Remaining key-in-argv exposure is documented above. |
| B110 | 2 | Cleanup suppressions: stale SFTP temp removal and a failed queued stop; the stop path also attempts privileged off separately. Root cleanup has additional narrowly scoped D-Bus/I/O handling reviewed manually. |
| B101 | 1 | Assertion in packaged self-test checks icon construction; no production authorization depends on it. |

## Reporting

Never attach credentials, private keys, personalized config files, or unredacted
diagnostics to public issues. For a suspected credential exposure, revoke the
credential first. Use the enabled [GitHub private vulnerability reporting](https://github.com/richterrene/pi-audio-tray/security/advisories/new).
Include affected version/commit, preconditions, impact and redacted reproduction
steps. Do not post credentials or personal data in a public issue. There is no
guaranteed response time; urgent credential revocation should not wait for a reply.
