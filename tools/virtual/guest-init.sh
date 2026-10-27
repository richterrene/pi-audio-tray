#!/bin/sh
# Test-only init for disposable QEMU disks. Never installed by the application.
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev 2>/dev/null || true
mount -t tmpfs tmpfs /run
hostname pi-audio-virtual
id pi-lab >/dev/null 2>&1 || useradd -m -s /bin/sh pi-lab
python3 /opt/pi-audio-lab/guest_checks.py
sync
printf '\nPI_AUDIO_LAB_FINISHED\n'
# QEMU's Pi model cannot always power off. The host stops only this VM after
# receiving the completion marker; no guest poweroff/reboot is needed.
while :; do sleep 60; done
