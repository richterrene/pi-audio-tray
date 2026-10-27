# Browser login review · 27 October 2026

Scope: optional migration from nginx HTTP Basic Auth to persistent browser
sessions, and standard HTTP/HTTPS entry ports. This is a maintainer review, not
an independent security audit. The existing player, TLS CA, password, audio
services and trusted-local-user assumptions remain in use.

## Boundary and controls

- nginx owns TLS, private IPv4 access, host/origin checks and authentication of
  player/UI/API/artwork/WebSocket handshakes. Port 80 redirects GET/HEAD navigation
  to HTTPS 443; POST is refused there. HTTPS 8443 stays compatible.
- A dedicated non-login account without home/music access serves only a private
  Unix socket. Systemd waits for explicit readiness after the socket and protected
  verifier file are available. No new package or auth TCP listener is needed.
- Session cookies are host-only, Secure, HttpOnly, SameSite=Lax and Path=/. Lax
  permits normal tray/bookmark/link navigation; unsafe browser requests also pass
  an exact HTTPS Origin check. Sign-in/logout additionally require a random
  SameSite=Strict form cookie matching a hidden token. No CORS allowance.
- Only hashes of random 256-bit session tokens are stored in private SQLite,
  with bounded count, expiry and credential fingerprint. Remembered lifetime is
  90 days; otherwise the browser cookie is nonpersistent and server lifetime is
  12 hours. Password rotation invalidates old sessions. The fingerprint is captured
  before verification to avoid accepting an old password under a newly rotated hash.
- Apache's installed verifier reads the password from stdin. Shells, password
  argv/environment, localStorage, URL tokens and credential access logs are absent.
  Forms, request waits/threads and attempt/session storage are bounded. Per-address
  failures and nginx login-route rates are limited.
- A network-only worker replaces myMPD's cache-first worker, clearing myMPD caches
  without touching other app caches. Player and login responses use no-store.
  Existing Basic Auth config is backed up; the installer validates syntax and
  probes the live login over verified TLS, attempting rollback if activation fails.

## Validation

The new tests cover persistent hashed sessions, expiry, revocation, password
rotation, cookie ambiguity/flags, storage limits, CSRF/origin rejection, failed and
malformed credentials, logout, browser-vs-server lifetime and config injection.
Eleven tests passed on the Pi's ARM64 Python with the real Apache verifier and
disposable credentials; no original credential file was changed by testing.
The full desktop test suite also passes (platform/verifier availability determines
skips). Live deployment checks passed: HTTP redirect/POST refusal, HTTPS form sign-in
with the unchanged credential, secure persistent cookie flags, player access on
443 and 8443, unauthenticated API rejection, allowed/denied WebSocket handshakes,
cross-origin/script-route rejection, private auth-subrequest isolation, CSRF and
logout revocation. The deployed auth-code checksum matched the reviewed source.
Audio services remained active. Browser visual testing is recorded separately
from these protocol checks; no independent penetration test is claimed.

## Residual risks

Existing WebSockets are authenticated at handshake, not every frame. Remote
notifications may continue after revocation/expiry until the connection closes;
new HTTP controls and handshakes are denied. Browser memory/history is not erased
by logout. Local Pi users can still reach loopback myMPD directly. OS, nginx,
auth-account or browser compromise and hostile-LAN denial of service are outside
this boundary. The first HTTP redirect cannot replace a trusted HTTPS bookmark
against an active network attacker. No independent audit is claimed.

See [Security and privacy](../SECURITY.md#persistent-browser-login-and-standard-web-ports)
and [deployment/rollback](LOCAL_MUSIC.md#upgrade-an-existing-web-player-to-remembered-browser-login).
