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
  privileged process/status inspection on the Pi can expose it. Treat the Pi
  and all accounts with access to it as trusted. Never publish full process
  listings, `systemctl status soloist`, crash dumps, or unsanitized logs.
- The optional Soloist WebSocket server binds to loopback only. Its protocol
  does not provide authentication. Do not expose it to your LAN or the internet.

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

## Reporting

Never attach credentials, private keys, personalized config files, or unredacted
diagnostics to public issues. For a suspected credential exposure, revoke the
credential first. Use GitHub private vulnerability reporting if enabled; do not
post a working security exploit containing personal data in a public issue.
