# Multiple Pi connections

Choose **Manage Pis…** in the status window or tray **Switch Pi** submenu. Add a name for each Pi,
choose **Use selected Pi**, then enter that Pi's SSH settings. The **Active Pi**
selector and tray **Switch Pi** submenu switch among saved connections. The app
monitors and controls one Pi at a time; it does not mix queues or provide a fleet
dashboard. Rename profiles to recognizable names such as Living room and Study.

Each profile stores its own hostname/IP, SSH port, username, key filename,
known-host alias, refresh interval and optional HTTPS music URL. It does not copy
SSH private-key contents, passwords, Soloist keys or web-player credentials.
Language, refresh-interval and web-URL edits do not reset music views or cancel
accepted controls for the same SSH connection. Language and login autostart are preferences for the desktop account, not per-Pi
settings. Receiver compatibility requirements are the same for every profile.

The existing connection is automatically presented as **My Pi** on upgrade.
Reading it does not rewrite any files. The private `profiles.json` file is created
when a profile or connection is saved. It lives beside `config.json` in the app's
private configuration directory. The selected profile is restored on next launch.
Up to 32 named profiles are supported. Removing an inactive profile deletes only
its saved connection, without contacting the Pi or deleting SSH keys/host keys.
Switch away from an active profile before removing it.

## Switching safely

Switching is blocked during Bluetooth pairing, setup, key changes, updates and a
pending shutdown. Once the shutdown has disconnected the Pi, another profile can
be selected. The previous Pi keeps playing: switching does not pause music,
stop services, shut down a Pi, or disable trusted Bluetooth connections.

On a receiver/SSH identity change, existing music windows are hidden and old pending commands/results are
invalidated by a connection generation counter. A command already sent to the
previous Pi can finish there. New operations use the selected profile. Every
new hostname or known-host alias still requires verified SSH host-key approval;
a changed key remains an error. Profiles for different users on the same SSH
host share that host's verification, as SSH normally does. If several Pis have
the same hostname/IP on different networks, give them distinct hostnames or
verified known-host aliases; never erase the warning to make a key mismatch pass.

## Storage and recovery

`profiles.json` is authoritative after migration and uses the existing atomic
private-write/Windows ACL protection. `config.json` is maintained for legacy
tools, but an older app version does not understand multiple profiles. Avoid
running old and new versions against the same configuration. Back up the whole
private app configuration directory when migrating computers; local usernames
and paths in it are private data and must not be committed to Git.

Malformed/linked profile registries are refused and do not silently select a
stale connection. Back up a damaged file before repairing it or restoring a
known-good copy. Do not manually edit either file while the app is running.
