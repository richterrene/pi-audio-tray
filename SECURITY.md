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
| SSH user → root | Exact helper pair/off commands, a fixed systemd update start, and optional graceful poweroff. These permit pairing, update-triggered interruption and optional poweroff, not arbitrary administrative commands. Root-owned helpers, units and all parent paths must be non-writable by that user. |
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

The fresh-Pi wizard installs the exact pairing/update rules below and, only if
selected, the shutdown rule. It validates them with `visudo` before installation.
It does not remove or narrow any pre-existing sudo grants (including Pi OS defaults).
For manual setup, grant only the exact required commands in a sudoers file validated with
`visudo`. For example, substitute your real Pi username for `AUDIO_USER`:

```sudoers
AUDIO_USER ALL=(root) NOPASSWD: /usr/local/lib/pi-audio-tray/pi-helper pair
AUDIO_USER ALL=(root) NOPASSWD: /usr/local/lib/pi-audio-tray/pi-helper off
AUDIO_USER ALL=(root) NOPASSWD: /usr/bin/systemctl start soloist-update.service
```

Optional shutdown (0.3.2+) needs one additional exact rule:

```sudoers
AUDIO_USER ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block poweroff
```

This explicitly grants the SSH account power to stop the Pi, even without the
GUI confirmation if invoked directly. It does not grant arbitrary systemctl
arguments. The GUI defaults confirmation to No, binds it to the selected
connection, refuses overlap with pairing/mutations, and blocks known active
scheduled updates. This is not an atomic lock against all manual package
operations. No forced shutdown or automatic retry is performed. Successful SSH
acknowledgment means the request was submitted, not proof that poweroff completed;
network loss cannot prove it either. Check the Pi’s activity LED before unplugging.
Reconnecting power is required to start this receiver again.

Read-only monitoring and Soloist key storage use the normal SSH user's rights.
The legacy GUI helper updater needs broader administrative rights and fails
under the minimal runtime rules above. The fresh-Pi wizard instead accepts a
one-time masked sudo password over verified SSH; it does not save that password
or grant installation privileges permanently. See [setup security and recovery](docs/NEW_PI_SETUP.md#security-and-recovery) and the [setup review](docs/SETUP_REVIEW_2026-10-27.md).

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

## Windows private storage (0.4.0+)

Windows stores settings in the current user's AppData profile. Native security
APIs replace Unix ownership/mode operations: protected DACLs grant the current
user and LocalSystem access, with no Everyone/Users/Authenticated Users grants.
The ownership check accepts the current user or privileged Administrators owner;
administrators and same-user malware remain trusted. NTFS/private profile parent
paths are required. Unsupported ACLs fail closed. Managed links/reparse points
are refused; atomic temporary files are restricted before data is written.
Windows byte-range locking provides single-instance behavior. Native CI covers
ACLs and junction rejection as well as lock exclusion by a second process.

Windows artifacts are unsigned portable ZIPs; SHA-256 files do not independently
authenticate the publisher. There is no Windows installer, elevation request,
automatic startup registration or local server. The packaged self-test uses only
private temporary data. CI is not a substitute for interactive testing on a user's
Windows 11 computer. [Windows details](docs/WINDOWS.md).

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
- Check release SHA-256 files and source/CI provenance. The Linux AppImage and Windows ZIP have no
  independent release signature; macOS uses ad-hoc signing and is not notarized.
  The tray itself has no automatic desktop update mechanism; install new releases
  manually. macOS signing and App Store limitations are in [MACOS.md](docs/MACOS.md).

For a suspected exposure, stop the affected service, revoke/rotate the Soloist
key in Spotify, revoke affected sessions as supported by Spotify, and replace any
exposed SSH key and its authorized_keys entry. Removing a file does not revoke a
credential or securely erase flash storage/backups. Preserve only redacted
incident evidence. A compromised audio/desktop account requires remediation of
that account/host, not just replacement of the visible key file.

Idle background monitoring uses a normal-user `health` helper action. It reads
service/adapter/update health without CPU sampling, returning device aliases, key-file checks
or execution of the proprietary client. Opening a detail view uses full status.
This changes polling cost, not the host/account trust boundary or sudo permissions.

The Pi CPU sampler stores only its latest counters, monotonic timestamp and
boot identifier in `~/.cache/pi-audio-tray/cpu.json` (private directory/file).
The file is bounded and validated; reads reject links/non-regular files and
replacement is atomic. Reboot, stale/reset counters or unavailable cache cause
a fresh one-second sample. Concurrent status clients may shorten the displayed
averaging window; no process names, command lines or credentials are stored.

## Reproducing checks

Use a disposable virtual environment and a reviewed checkout:

```sh
python -m unittest discover -s tests -v
python tools/dependency_inventory.py > /tmp/pi-audio-dependencies.txt
# Install these in a separate audit environment, not in the inventoried one:
# pip-audit==2.10.1 bandit==1.9.4
pip-audit --disable-pip --no-deps -r /tmp/pi-audio-dependencies.txt -f json
bandit -r app.py core.py platform_support.py music_ui.py pi_music.py pi_helper.py pi packaging_support.py build_appimage.py build_macos.py build_windows.py
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


The Windows port adds native DACL/locking code and a Windows build script to the
review scope. DLLs are loaded from System32, SDDL is generated from the process
user SID (never caller-supplied ACL text), and ownership/reparse checks precede
ACL changes. Packaging subprocesses use argument lists and trusted build paths.
Pi-specific POSIX permission/cache tests run on Linux rather than being treated
as Windows coverage. Platform-specific pass/skip counts are retained in CI logs.
The older 42-finding Bandit tally above describes the dated 0.3.x review; newer
CI artifacts contain findings for the expanded source set.


## Optional local music and HTTPS boundary (0.5.0)

The optional MPD/myMPD integration adds a separate web attack surface. Read
[LOCAL_MUSIC.md](docs/LOCAL_MUSIC.md#security-boundary-and-maintenance) for account
isolation, socket/group permissions, the read-only USB mount, bounded SSH music
requests, HTTPS login/origin checks, root-owned TLS renewal, bootstrap credential
handling and third-party APT repository trust. A myMPD PIN is not authentication.
No internet access, port forwarding or automatic CA trust installation is needed.
The music HTTPS listener is private-IPv4-only by access policy; trusted music users
can control the shared queue. The initial random credential is a protected
plaintext bootstrap file until the user removes it.


### Local music review evidence (0.5.0)

The expanded production/build scan reports 71 findings (64 low, 7 medium), all
reviewed rather than suppressed. The additional medium B310 is the installer's
fixed upstream HTTPS signing-key URL: its expected fingerprint is verified before
APT accepts the dedicated source. No arbitrary URL is accepted there. New low
subprocess findings use explicit argument arrays for administrator installation
and certificate renewal; shell execution is not used. The runtime MPD bridge uses
a Unix socket and bounded protocol quoting, with separate tests for traversal,
control characters, operation allowlists, numeric validation, pagination, hidden
window polling, stale results and plain-text metadata. Source secret scanning
found no leaks. Live checks verify login-required UI/API, valid CA+hostname TLS,
cross-origin/script rejection, backend loopback binding, account/key isolation,
read-only mount and 0660 socket, and muted MPD playback through PipeWire. These
checks do not constitute a penetration test or an audit of upstream myMPD/nginx/MPD.


## Music refresh interaction (0.5.1)

Music requests remain serial over the existing verified SSH transport. Background
polls are skipped while work is pending; user commands have a bounded queue
(maximum 32 pending requests), and redundant navigation/volume requests are
coalesced. Captured connection settings and generation are checked before
dispatch and before applying results, preventing queued actions from reaching a
different configured Pi. Closing the music window discards unsent requests; an
already running request can finish. No helper permissions, protocol operations,
credentials, dependencies or Pi services change. The expanded 95-test suite
covers interactive background refreshes, retained input, stale generation/page
results, hidden polling and stable list selection/scrolling.

## UPnP network music

The [dated UPnP review](docs/UPNP_REVIEW_2026-10-26.md) records validation,
reviewed static findings and remaining trust boundaries.

Version 0.6 adds opt-in SSDP discovery and ContentDirectory browsing **on the Pi**.
No desktop listener or new Pi listener is opened, and no router port mapping is
requested. Discovery is on demand, has a bounded search window/candidate count,
and does not automatically enable servers. Only explicitly enabled, recently
discovered sources may supply tracks. Private RFC1918 IPv4 addresses only;
loopback, link-local/cloud metadata, multicast and public addresses are rejected.
Discovery location, control and audio URLs must use the responding server's same
numeric address. Hostname URLs, credentials, fragments, control characters and
cross-host references are refused; XML HTTP requests ignore proxy settings and
reject redirects. Response/XML size, page size, identifiers and socket waits are
bounded. XML DTDs/entities are rejected, SOAP arguments are XML-escaped, and MPD
arguments are quoted, never interpolated into a shell. UI text is plain/sanitized.

Sources and discovered candidates are stored privately under the audio user's
`~/.local/state/pi-audio-network/` (700; JSON 600, atomically replaced under a file
lock). URLs/credentials cannot be supplied by desktop enqueue requests: the Pi
resolves the selected object through its enabled server and accepts audio items
only. Flat albums are bounded to 100 tracks; nested or oversized albums are
rejected before queue changes. If an MPD metadata/add operation fails, rollback
attempts to delete only newly added IDs, never clearing the existing queue; a
connection loss can prevent complete rollback. Requests capture the selected SSH
connection and generation; stale replies and unsent requests for old connections
are discarded. Closing stops unsent work; already-dispatched actions may finish.

UPnP is **unauthenticated**: a LAN peer can spoof discovery/UDNs, and media servers
can log requests and supply malicious/misleading content. Only enable a server
you trust. These controls do not make hostile LAN devices safe. HTTP audio may be
unencrypted; the browser player's HTTPS protects phone control, not the Pi's
media-server connection. After enqueue, MPD's own HTTP/decoder implementation
handles stream requests and may follow redirects; its transport/decoder and the
selected server are inside the trust boundary. Numeric-address pinning during
browsing does not sandbox MPD's later HTTP requests. Removing a source does not
revoke already queued or saved stream URLs. No automatic background discovery,
network library indexing, share passwords, UPnP subscriptions or remote media-server
control actions are implemented. Compatibility limits are documented in
[Local and network music](docs/LOCAL_MUSIC.md#upnp--dlna-music-servers-06).

## Persistent browser login and standard web ports

The [dated web-login review](docs/WEB_LOGIN_REVIEW_2026-10-27.md) records the
implementation, validation and remaining boundaries.

The optional web-login migration replaces HTTP Basic Auth with a form and a
90-day remembered session (or a nonpersistent cookie with a 12-hour server
limit). Cookies use the `__Host-` prefix, Secure, HttpOnly, SameSite=Lax and
Path=/ without Domain. Tokens contain 256 random bits; SQLite stores only SHA-256
hashes, expiry and the password-hash fingerprint. Changing the hash invalidates
old sessions; logout deletes this/all stored sessions. Session count is bounded
to 128. Private state survives service/Pi restarts. No credential/token appears
in URLs, argv, localStorage or application access logs. The existing high-entropy
password and SHA-512-crypt hash remain in use; verification uses the installed
Apache verifier with password on stdin. A new session records the hash fingerprint
from before verification so concurrent password rotation cannot bless an old
password with the new credential fingerprint.

nginx authenticates UI, API, artwork and WebSocket handshakes with auth_request;
the verifier listens only on a group-restricted Unix socket and runs as a separate
non-login account without audio-group membership, home access or capabilities.
Password/config files remain root-owned. Browser password submissions require an
allowlisted HTTPS Origin and a matching random, host-only secure form cookie and
hidden field. State-changing myMPD requests retain the existing Origin check and
Lax cookie boundary (cross-site POSTs do not carry it). nginx limits login routes, and the verifier limits failed
attempts per address (ten/ten minutes), form size, request time, processes/threads
and stored attempt entries. These are bounded home-network protections, not a
complete hostile-LAN denial-of-service defense. No CORS allowance is added.

Port 80 accepts navigation to allowlisted hostnames/IPs and redirects to HTTPS
443; it does not serve a login form or accept POSTs. Private IPv4 access rules
remain; no public IPv6 listener is added. HTTPS 8443 remains compatible. TLS and
CA trust are unchanged. A one-day HSTS policy is used after a valid HTTPS visit;
bookmark HTTPS for protection on first contact as an initial HTTP redirect can
be interfered with by an active network attacker. Never forward these ports
publicly. nginx strips Authorization and Cookie before forwarding to myMPD.

The network-only worker removes myMPD caches, and player/login responses use
no-store. Browser history/in-memory content is not a secure data eraser. Logout
revokes future authenticated requests, not an existing upgraded WebSocket;
notifications can continue until its connection closes. The optional myMPD PIN
and trusted-local-user loopback bypass are separate, unchanged boundaries. Cookie
theft by a compromised browser/OS or root remains outside this protection.
See [deployment, expiry and rollback](docs/LOCAL_MUSIC.md#upgrade-an-existing-web-player-to-remembered-browser-login).
