# Setting up a receiver

These instructions target **64-bit Raspberry Pi OS / Debian 13 Trixie** on an
ARM64 Pi. They are intended for an administrator on a trusted home network.
Commands run on the Pi as its normal SSH user, from a copy of this repository.
Review them before execution. Back up your existing audio configuration first.
Do not run the whole session as root: PipeWire and Soloist use user services.

## 1. Install dependencies

```sh
sudo apt update
sudo apt install curl bluez python3-dbus python3-gi pipewire pipewire-pulse wireplumber pipewire-bin libspa-0.2-bluetooth
sudo install -d -m 755 /usr/local/lib/pi-audio-tray
sudo install -o root -g root -m 755 pi_helper.py /usr/local/lib/pi-audio-tray/pi-helper
sudo install -o root -g root -m 755 pi/soloist-launch.py /usr/local/lib/pi-audio-tray/soloist-launch
```

## 2. Download the official client

Generate your own key in the [Spotify Soloist dashboard](https://developer.spotify.com/dashboard/soloist)
and review Spotify's terms there. The application does not accept those terms
on your behalf. Download the appropriate build from Spotify; no Soloist binary
is distributed by this project. For ARM64:

```sh
soloist_download_dir=$(mktemp -d)
curl --fail --location https://soloist-builds.spotifycdn.com/soloist_release_arm64.tar.gz -o "$soloist_download_dir/soloist.tar.gz"
tar -xzf "$soloist_download_dir/soloist.tar.gz" -C "$soloist_download_dir"
sudo install -o root -g root -m 755 "$soloist_download_dir/soloist" /usr/local/bin/soloist
soloist --version
```

## 3. Configure headless audio and boot startup

```sh
mkdir -p ~/.config/systemd/user ~/.config/wireplumber/wireplumber.conf.d
install -d -m 700 ~/.config/soloist
install -m 644 pi/soloist.service ~/.config/systemd/user/soloist.service
install -m 644 pi/50-audio-receiver.conf ~/.config/wireplumber/wireplumber.conf.d/50-audio-receiver.conf
sudo loginctl enable-linger "$(id -un)"
sudo systemctl enable --now bluetooth
sudo rfkill unblock bluetooth
sudo bluetoothctl power on
```

If migrating, stop and disable Raspotify, BlueALSA and bluealsa-aplay first.
Do not run two Bluetooth audio backends on the same adapter:

```sh
sudo systemctl disable --now raspotify bluealsa bluealsa-aplay
```

Skip that command on a fresh installation where those services do not exist.
Then enable the new user services:

```sh
systemctl --user daemon-reload
systemctl --user enable --now pipewire pipewire-pulse wireplumber
systemctl --user enable soloist
wpctl status --name
```

Select the intended **Audio Sink** from that output using `wpctl set-default ID`.
IDs are discovered at runtime; do not copy another machine's number. The tested
headphone sink has `alsa.card_name = "bcm2835 Headphones"` in `wpctl inspect ID`.
Confirm that it is not muted and start with a low volume.

Configure the SSH connection in the desktop app, then use **Settings → Spotify
Soloist → Set / replace API key**. The app writes the private key file and starts
Soloist. Never paste the key into an issue, chat, shell command, or service unit.
The supplied default Spotify Connect device name is **Pi Audio**. Override it
by writing a device name to `~/.config/soloist/device-name` on the Pi.

Verify without printing command-line secrets:

```sh
systemctl --user is-active soloist pipewire wireplumber
soloist ctl status -D ~/.local/state/soloist
```

## 4. Optional daily Soloist updates

```sh
sudo install -o root -g root -m 755 pi/soloist-update.py /usr/local/lib/pi-audio-tray/soloist-update
sudo install -d -m 755 /etc/pi-audio-tray
id -un | sudo tee /etc/pi-audio-tray/soloist-user >/dev/null
sudo install -m 644 pi/soloist-update.service pi/soloist-update.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now soloist-update.timer
sudo systemctl start soloist-update.service
systemctl show soloist-update.service -p Result
```

This updater currently supports **ARM64 only**. It uses HTTPS and validates the
download format/version; it does not verify a separate Spotify signature. Its
privileged download trust is the official Spotify CDN and the local CA store.
Keep the script and `/etc/pi-audio-tray` configuration root-owned.

## 5. Optional system updates and conditional reboots

Review the source origins and schedules first. These examples are specifically
for Trixie. Reboots can interrupt playback and existing SSH sessions.

```sh
sudo apt install unattended-upgrades needrestart
sudo install -m 644 pi/52pi-audio-upgrades /etc/apt/apt.conf.d/52pi-audio-upgrades
sudo install -m 644 pi/needrestart.conf /etc/needrestart/conf.d/pi-audio.conf
sudo install -d /etc/systemd/system/apt-daily.timer.d /etc/systemd/system/apt-daily-upgrade.timer.d
sudo install -m 644 pi/apt-daily-timer.conf /etc/systemd/system/apt-daily.timer.d/pi-audio.conf
sudo install -m 644 pi/apt-upgrade-timer.conf /etc/systemd/system/apt-daily-upgrade.timer.d/pi-audio.conf
sudo install -o root -g root -m 755 pi/reboot-if-needed.py /usr/local/lib/pi-audio-tray/reboot-if-needed
sudo install -m 644 pi/pi-audio-reboot.service pi/pi-audio-reboot.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now apt-daily.timer apt-daily-upgrade.timer pi-audio-reboot.timer
sudo systemctl restart apt-daily.timer apt-daily-upgrade.timer
sudo unattended-upgrade --dry-run --debug
sudo /usr/local/lib/pi-audio-tray/reboot-if-needed --check-only
```

See the main README for exact schedules and limitations. Logs are retained by
systemd and unattended-upgrades. The app shows timer configuration and the last
service result; it is not a guarantee that every package or service is healthy.
