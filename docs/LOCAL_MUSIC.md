# Local USB music and phone control

Pi Audio Tray 0.5 adds an optional **USB music…** window. It browses folders, adds
tracks/albums to the queue, plays a selected track, and controls play/pause/stop,
previous/next and the local player's software volume. Double-click a folder to
browse or a track to add it and play it. Queue entries can also be double-clicked.
Pages contain at most 100 entries. Use **Rescan USB** after changing the drive's
contents on another computer. Closing the music window stops its detail polling;
minimizing it suppresses requests. Existing background service-health checks keep
their once-per-minute minimum interval. One request already in flight may finish.

The optional mobile-friendly **myMPD** browser player shares the **same MPD queue,
volume and playback state**. The phone controls playback on the Pi; audio still
comes from the Pi's configured output. These features do not download or stream
Bandcamp purchases directly. They play the local files on the attached USB drive.

Pause Spotify/Bluetooth before starting local music, and pause local music before
switching back. PipeWire can mix simultaneous streams; automatic source switching
is not implemented. Local music volume is separate from Spotify Connect volume.

## Compatibility and prerequisites

- Desktop: the app's existing Linux, Windows x64 and macOS requirements apply.
  Music control uses the existing verified SSH connection, not a new desktop
  dependency or an MPD TCP port. The HTTPS address is optional in Settings and
  otherwise defaults to `https://<configured-pi-host>:8443/`.
- Pi: the supplied installer targets **Debian/Raspberry Pi OS 13 (Trixie), FAT32
  USB storage, and an existing working headless PipeWire setup** under a normal
  audio user. It is not a general installer for older releases, ALSA-only hosts,
  NTFS/exFAT, or an existing custom MPD/web-server installation.
- MPD and nginx come from Debian; myMPD comes from its upstream maintainer's
  signed Open Build Service repository. No Spotify API key is needed for USB music.
- A local Wi-Fi/LAN connection is required for the phone. The supplied HTTPS
  listener permits private IPv4 networks and loopback; it has no public IPv6
  listener. Guest Wi-Fi/client isolation can prevent access. No router forwarding,
  cloud tunnel, publicly reachable service or UPnP configuration is created.

## Opt-in Pi setup

Read `pi/install-music.py`, `pi/renew-music-tls.py` and `pi_music.py` before running
an administrator installer. Back up the Pi first. This setup uses dedicated
`pi-audio-*` services and masks the distribution MPD, myMPD and nginx services so
package installation does not start an unprotected default listener. Do not run
it on a host already relying on those distribution services.

Identify the intended USB partition with `lsblk -f`; do not select the SD boot
partition. Copy all three reviewed files into one private staging directory on
the Pi, for example:

```sh
# On the Pi, in your private staging directory:
chmod 700 .
# Place these files here: install-music.py, renew-music-tls.py, pi_music.py
sudo /usr/bin/python3 -I ./install-music.py \
  --user YOUR_AUDIO_USER --uuid YOUR-UUID --hostname audio-pi.local
```

The installer does not format the drive or start playback. It adds a UUID mount
at `/srv/pi-audio-music`, read-only with `nosuid,nodev,noexec`, limited to the audio
owner and music group. It expects a `Music` directory at the drive root. A missing
drive does not prevent boot. Database, queue and cache live on the SD, not the USB.
The initial local-player volume is 30%; raise it gradually when first testing.
A repeated installation also resets this local volume to 30%.

The existing helper installer updates the unprivileged music bridge too. It does
**not** install MPD, mount a drive, set up HTTPS, or grant broad runtime sudo.
Administrator setup stays a separate, explicit action.

## HTTPS and login

Open `https://audio-pi.local:8443/` using your actual Pi hostname. The IP address at
installation time is also in the certificate. If mDNS does not work on the phone,
use that IP, preferably reserved in the router's DHCP configuration. The music
URL can be overridden in the app's Settings; only HTTPS URLs without embedded
credentials are accepted. The app opens it in your normal browser.

A dedicated random **music** web password is generated; it is unrelated to the
Pi SSH password and Soloist key. The initial login is stored in the audio user's
private file `~/.config/pi-audio-tray/music-web-login.txt` (mode 0600). Retrieve it
through the existing trusted SSH connection and put it in your password manager.
It is plaintext in that protected bootstrap file: remove the bootstrap copy from
the Pi and any desktop copy after saving the credential. The server retains only
a salted SHA-512-crypt hash with 10,000 rounds; the password has 192 bits of random
entropy. There is no password in source, releases, screenshots, URLs, or logs.
To change it interactively:

```sh
sudo htpasswd -5 -r 10000 /etc/pi-audio-tray/music/htpasswd music
```

Local `.local` names cannot use the usual public-domain certificate setup. The
installer creates a dedicated local CA and a one-year server certificate. Until
you trust that CA, the browser will warn about the issuer. Get the **public**
`~/.config/pi-audio-tray/music-ca.crt` via trusted SSH; verify that it matches the
Pi's CA before installing it on a phone/computer. Never copy or distribute any
`.key` file. Device-specific certificate trust is a deliberate user action; the
app does not bypass certificate validation or install a system trust anchor.
On iPhone, open the certificate attachment/file, install the downloaded profile
in Settings, then enable **Pi Audio local CA** under **Settings → General →
About → Certificate Trust Settings → Enable Full Trust for Root Certificates**.
See [Apple’s instructions](https://support.apple.com/102390).
On Android, save the public `.crt` file, then search Settings for **Install a
certificate** and choose **CA certificate** (often under Security & privacy →
More security settings → Encryption & credentials). Select this CA and approve
the device’s confirmation. Menu names vary by vendor/version; use the CA option
for browser HTTPS, not the Wi-Fi/client-certificate option. Reopen the browser
afterwards. [Google’s certificate settings guide](https://support.google.com/pixelphone/answer/2844832)
explains where these settings and removal controls live.
A trusted local CA can issue certificates, so its root-only private key must be
protected. Remove its trust from devices when retiring the Pi.

The root-only TLS renewal timer checks daily and reissues the leaf certificate
when fewer than 30 days remain. The CA lasts ten years and must be replaced
manually before expiry. Renewal retains the original hostname/IP names. If those
change, an administrator must reissue the server certificate and adjust allowed
browser origins. A routine DHCP IP change does not affect the hostname URL.

## Security boundary and maintenance

- MPD listens only on `/run/pi-audio-mpd/socket`, not LAN TCP 6600. Its directory
  is owner/music-group only and an explicit post-start permission step sets the socket to 0660.
- MPD runs as the existing audio user and uses PipeWire. myMPD runs as a separate
  non-login `pi-audio-web` account, with `ProtectHome=yes`, read-only system paths
  and isolated writable state/cache. It cannot read the audio user's Soloist key,
  SSH files or bootstrap login. Music-group membership permits control of MPD and
  reading the read-only library; it is a trusted playback-control boundary.
- myMPD HTTP binds only to `127.0.0.1:8080`. Local processes/accounts on the
  Pi can reach that backend without the nginx login; local Pi users remain
  trusted for playback control. nginx exposes HTTPS 8443 and requires
  login for UI, API, artwork and WebSockets. This is a full-access music account,
  not an administrator login. Do not share it with untrusted people.
- The upstream myMPD PIN protects only some operations; it is **not** used as the
  login boundary. External script execution and its script API are disabled or
  denied. Browsers sending an Origin other than a configured HTTPS address are
  rejected, including WebSocket requests. Requests without Origin still require
  authentication. Credentials are stripped before forwarding to myMPD.
- TLS permits 1.2/1.3. Root owns certificates, CA key, nginx configuration and
  password hash. There is no HTTP port 80 listener or automatic insecure fallback.
  nginx access logging is disabled; error logs may still contain client/network
  and request metadata. Do not publish operational logs without review.
- The SSH bridge accepts one bounded JSON request on stdin, uses only allowlisted
  MPD operations, rejects protocol-control characters, absolute paths, parent
  traversal and URL schemes, bounds replies and timeouts, and never puts music
  metadata into shell commands. It refuses to run as root. No new sudo rule is
  needed for playback.
- The myMPD APT source uses HTTPS, a dedicated `signed-by` keyring, and a key
  fingerprint checked against the upstream installation documentation. APT pinning
  permits only the `mympd` package from that repository; other packages remain
  Debian-provided. The installer adds that specific origin to unattended upgrades.
  Repository/key rotation failures require administrator review, not bypassing
  signatures. Debian still supplies nginx/MPD security updates. This introduces
  trust in the upstream package maintainer and their signing key.
- Cockpit stays separate with its existing loopback/SSH tunnel access. No Soloist
  WebSocket listener is enabled. Music setup does not change Spotify/Bluetooth
  credentials, sink gain, or existing update/reboot policy.

Check service state without printing audio secrets:

```sh
systemctl is-active pi-audio-mpd pi-audio-mympd pi-audio-web
systemctl list-timers pi-audio-music-tls.timer
journalctl -u pi-audio-mpd -u pi-audio-mympd -u pi-audio-web --since today
findmnt /srv/pi-audio-music
```

To disable phone access: `sudo systemctl disable --now pi-audio-web.service`.
To stop all local playback as well, stop `pi-audio-mympd` and `pi-audio-mpd`.
Unmount the USB before removal, or gracefully shut down the Pi. Do not remove a
mounted drive during playback. Keep the existing Bluetooth/Spotify setup intact
when disabling these optional services.

References: [MPD manual](https://mpd.readthedocs.io/en/stable/user.html),
[myMPD upstream installation](https://jcorporation.github.io/myMPD/010-installation/prebuild-packages-debian.html),
[myMPD security](https://jcorporation.github.io/myMPD/090-security.html), and
[myMPD reverse proxy](https://jcorporation.github.io/myMPD/070-additional-topics/behind-a-reverse-proxy.html).
