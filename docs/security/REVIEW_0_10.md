# Optional web shutdown review — 0.10.0 preview

This is an implementation review and test record, not an independent audit.
The optional component expands an existing music browser session's authority to
request graceful shutdown. The installer and documentation explicitly disclose
that scope. It is not enabled by ordinary desktop updates or fresh-Pi choices.

The web login process retains its normal system UID, no capabilities,
NoNewPrivileges, Unix-only networking, protected home/system and private state.
The root socket-activated broker has one fixed queue target and an exact byte
protocol, checks kernel peer UID, reads only bounded input with timeout, and
uses no shell or HTTP-provided unit/command/path. Its 0600 socket belongs to the
authentication account; other www-data processes are not granted access. Root
roles retain empty capability sets, protected filesystem/device namespaces,
blocked known credential/media paths, bounded tasks/memory/time and no core
dumps. This reduces exposure but is not a security boundary against root or a
compromised trusted broker implementation.

HTTP adds session validation, the existing exact Origin/form/CSRF boundary and
a session-bound, one-use intent nonce (120 seconds). Only nonce/session hashes
are persisted. Nonces are bounded to 128 rows, expired and removed on logout.
GET never queues shutdown. Host/Origin restrictions, HTTPS, cookie attributes,
no-store/CSP and blocked myMPD script endpoints remain. Same-origin script
compromise or a stolen valid session remains a risk because this feature
intentionally gives that session shutdown authority.

The broker and its delayed fixed job both acquire the managed setup lock,
check known update units and acquire shared POSIX package-manager locks that
conflict with normal APT/dpkg exclusive locks. Unknown states fail closed. The
final command explicitly checks inhibitors and has no force/ignore option.
The two-second delay delivers acknowledgement before web services stop; a new
update can veto the later execution. There is no automatic retry or power-on.

The root installer snapshots an exact bounded manifest into private storage,
refuses unowned power files, validates the existing HTTPS hostname/account,
keeps a private public-configuration backup and the setup lock, validates units
and nginx, and preserves credentials/certificate/session data. It never queues
poweroff. Login repairs now restart the login process to actually load changed
code; previous nginx dependency content is retained on rollback. Installation
is not a full system transaction; partial files may require reviewed repair.

Evidence: 300 local tests passed with three existing skips. New tests cover
HTTP authorization and intent replay/expiry/session binding, hashed bounded
nonces, exact broker commands, peer mismatch, root execution guards, known
updates, setup locks and a real competing-process package lock. Shutdown calls
are mocked; CI never shuts down a host. systemd unit verification passed. Static
review adds no high findings; the remaining medium finding in the touched files
is the existing first-migration port-conflict bind probe. The confirmation page
was visually checked with disposable local fixture state, without a real Pi
connection, and its screenshot contains no account/host/credential data.

Live installation must verify the private read-only broker check, HTTPS login
and rejection of an invalid intent. Its disposable test session is removed
without printing a password, cookie or nonce. A successful real shutdown POST
is deliberately left to a user who intends to power off the receiver.
