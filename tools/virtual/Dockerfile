FROM debian:trixie-slim@sha256:62bd423aeee912639ca8ee681c2b762cb3e25137e0a92b20be455898a9f034ad
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl xz-utils qemu-system-arm qemu-utils \
    e2fsprogs mtools device-tree-compiler openssh-client python3 iproute2 \
    && rm -rf /var/lib/apt/lists/*
# The runner is unprivileged. Guest administrator actions stay inside QEMU.
USER 1000:1000
WORKDIR /lab
ENTRYPOINT []
