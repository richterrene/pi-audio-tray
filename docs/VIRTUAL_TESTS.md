# Local virtual Pi image tests

We can boot official Pi OS images and test the receiver software without a Pi.
This optional **Linux + Docker + QEMU** lab currently runs a small image smoke /
regression matrix. It is not a complete wizard installation or hardware test.
The harness and image cache are separate from the desktop application's runtime.

## What ran on 2026-10-27

The images are official **2026-10-06 Pi OS Lite Trixie** downloads, each pinned to
its published SHA-256 digest. They run their own kernel, ARM userspace and Python
3.13.5 on QEMU 10.0.13's `raspi3b` model, with 1 GiB guest RAM. They do not use
an x86 container as a substitute for ARM execution.

| Image / kernel | Setup probe after fix | Pi-side test result |
| --- | --- | --- |
| ARM64 image, ARM64 kernel | Accepted | 35 run, no failures/errors, 1 skipped |
| armhf image, ARM64 kernel | Rejected as unsupported | 35 run, no failures/errors, 1 skipped |

The mixed image **was incorrectly accepted before the fix**. These tests
reproduced the real 0.7.0 bug; 0.7.1 now checks `dpkg --print-architecture` and
Python pointer size as well as kernel architecture. The administrator installer
also rechecks userspace architecture when resuming an incomplete installation.
The desktop suite separately passes **142 tests**, with 3 existing platform/tool
skips locally. The [machine-readable report](testing/virtual-images-2026-10-27.json)
records the before/after results and exact image hashes.

The guest runs the normal-user compatibility probe plus the existing
`test_pi_helper`, `test_maintenance` and `test_web_login` suites. Web-login tests
exercise real Unix sockets, sessions and CSRF behavior in the ARM image; other
unit tests retain their existing mocks. Apache hash verification is skipped
because these Lite images lack the verifier. The separate real D-Bus agent
check is also skipped when the image lacks dbus-python/GLib or dbus-run-session;
it is not silently counted as passed.

A native ARMv7 Pi 2 boot was attempted but did not reach the guest test completion
marker. It is **excluded from the passing/default matrix**. Starting a kernel is
not a passing test, and these results make no Pi 2 compatibility claim.

## Run it locally

Requirements: Linux, Python 3.12+, an existing working Docker daemon/account,
roughly **15–20 GB free cache space**, and internet access to build the runner and
download the images. No host QEMU installation, sudo, binfmt registration or
physical SD card is required. Docker daemon access remains an administrative
trust boundary; the containers themselves run as your normal UID.

From the repository:

```sh
python3 tools/virtual/run.py --build
```

The first run builds the small tooling container and downloads about 1.1 GB of
compressed images. Later runs reuse the verified downloads and tooling image:

```sh
python3 tools/virtual/run.py
python3 tools/virtual/run.py --cases armhf-pi3-mixed
```

The default cache is **`~/.cache/pi-audio-virtual-tests`**. A different dedicated
cache can be selected with `--cache /path/to/empty-folder`; existing nonempty
folders without the lab marker are refused. Keep transient disk images outside
source control and normal backups. The optional `.virtual-lab/` repository cache
is ignored by Git, but a cache under your home cache directory is preferable.
Do not run the script as root.

Each case gets a pristine-image-derived disk plus a disposable QCOW2 write
layer. Only the test's random, labelled container is stopped on completion or
timeout. Failures return a nonzero exit code while retaining diagnostics:

- `results.json`: matrix summary.
- `arm64-pi3/result.json` and `armhf-pi3-mixed/result.json`: guest results.
- Each case's `serial.log`: kernel and test output for diagnosis.

The default per-guest boot/test timeout is 600 seconds; `--timeout` accepts
30–3600 seconds. It excludes initial downloads/preparation. Rerunning a case
replaces its previous disposable disk/write layer and results. Save a report
elsewhere before rerunning if you need its history. Do not run multiple harness
instances against the same cache at once. Stop the run and wait for its VM to
exit before manually removing a cache. Docker build/image layers are a separate
cache; this tool never prunes unrelated Docker data.

## Isolation and image changes

The runner uses a pinned official Debian base image and distro QEMU packages;
APT versions are resolved at build time, so a later build need not be bit-for-bit
identical. Runtime containers have a read-only root, all capabilities dropped,
no-new-privileges, normal-user ownership, memory/CPU/PID limits for QEMU, and
**no network, published ports, host device access or privileged mode**. Only the
dedicated lab cache is mounted writable. Neither the repository, home directory,
Docker socket, SSH keys nor physical Pi is mounted into the VM runner. As with
any emulator/container, this is not a guarantee against implementation flaws;
use trusted tooling and the pinned official images.

Images and tools are downloaded before network-isolated guest execution. Image
hashes, partition types/bounds and injected payload files are checked. Disk edits
use userspace FAT/ext4 tools, not loop mounts or host block devices. The source
payload is an explicit list of Pi scripts and tests; settings, `.git`, personal
credentials and SD-card backups are never copied. The synthetic `pi-lab` account
has no configured password, SSH keys or exposed login listener. The runtime user
is a test fixture, not a copied account from the working Pi.

To boot without real Pi firmware, the lab adjusts **only the disposable device
tree**: serial-console routing, the emulated SDHCI bus/PIO path, and disabling
onboard Bluetooth/watchdog nodes that cannot serve these tests. It injects a
**test-only init script instead of booting normal systemd**. It does not install
these changes on a real receiver. Source image downloads stay unchanged; the
prepared base contains test files, and runtime writes go into QCOW2 layers.

## What this does not prove

- Complete apt/bootstrap success, systemd service activation, the tray's SSH
  setup flow, interruption/resume across a full install, or boot persistence.
- Successful Spotify authentication, Connect discovery or audible playback.
- Real Bluetooth radio pairing, codec behavior, latency or speaker/headphone output.
- GPIO DAC/HAT drivers, analogue output, HDMI/EDID behavior, USB power, Wi-Fi,
  temperature accuracy, thermal stability or real-board performance.
- Every Raspberry Pi model, older OS releases or other filesystem combinations.

[QEMU documents incomplete Pi hardware emulation](https://www.qemu.org/docs/master/system/arm/raspi.html),
including missing PWM and some Pi 4 peripherals. Its
[generic ARM `virt` machine](https://www.qemu.org/docs/master/system/arm/virt.html)
is useful for future full-system Linux tests, but does not reproduce Pi hardware.

Use these tests alongside host/CI checks and a real blank-Pi installation / audio
check before promoting the setup wizard out of preview. ARM64 passing here means
the software checks ran successfully on these virtual image configurations;
it is not universal Pi compatibility.
