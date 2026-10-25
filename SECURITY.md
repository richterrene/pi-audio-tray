# Security and privacy

This is experimental software for a trusted home-network receiver. The desktop
app and Pi helper have not undergone an independent security audit.

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

## Reporting

Never attach credentials, private keys, personalized config files, or unredacted
diagnostics to public issues. For a suspected credential exposure, revoke the
credential first. Use GitHub private vulnerability reporting if enabled; do not
post a working security exploit containing personal data in a public issue.
