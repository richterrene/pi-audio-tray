#!/usr/bin/env python3
"""Exercise actual NQPTP client isolation using its compiled shared-memory layout.

Use only in a disposable build container. No AirPlay sender or physical outputs
are needed. The upstream 1.2.9 test still hard-codes ABI 10/72 bytes; the pinned
sources use ABI 11, so derive sizes and offsets from their actual C header.
"""
import mmap
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time


def check(work):
    source = work / 'nqptp-1.2.9'
    with tempfile.TemporaryDirectory(prefix='pi-audio-layout-') as folder:
        folder = Path(folder)
        program = folder / 'layout.c'
        program.write_text('#include <stdio.h>\n#include <stddef.h>\n#include "nqptp-shm-structures.h"\nint main(void) { printf("%d %zu %zu %zu", NQPTP_SHM_STRUCTURES_VERSION, sizeof(struct shm_structure), offsetof(struct shm_structure, main), offsetof(struct shm_structure, secondary)); }\n')
        subprocess.run(['gcc', '-I', str(source), str(program), '-o', str(folder / 'layout')], check=True)
        version, size, main, secondary = map(int, subprocess.check_output([str(folder / 'layout')], text=True).split())
    assert version == 11 and size == 88 and main == 8 and secondary == 48, 'Unreviewed shared-memory ABI'
    names = ('/pi-audio-test-room-a', '/pi-audio-test-room-b')
    regions = [Path('/dev/shm') / name[1:] for name in names]
    if any(path.exists() for path in regions):
        raise ValueError('Test regions already exist; use a fresh container')
    process = subprocess.Popen([str(source / 'nqptp')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.6)
        assert process.poll() is None, 'Timing daemon could not start'
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            def send(name, command):
                sock.sendto((name + ' ' + command).encode() + b'\0', ('127.0.0.1', 9000))
                time.sleep(0.25)
            for name in names:
                send(name, 'T 127.0.0.2')
                send(name, 'B')
            for index, path in enumerate(regions):
                with path.open('r+b') as stream, mmap.mmap(stream.fileno(), size) as region:
                    assert struct.unpack_from('<H', region, 0)[0] == version
                    for offset in (main, secondary):
                        struct.pack_into('<Q', region, offset, 1000 + index)
                    region.flush()
            send(names[1], 'T')
            a, b = [path.read_bytes() for path in regions]
            for offset in (main, secondary):
                assert struct.unpack_from('<Q', a, offset)[0] == 1000, 'One room disrupted another'
                assert struct.unpack_from('<Q', b, offset)[0] == 0, 'Stopped room retained its master clock'
            send(names[0], 'E')
        print('Actual NQPTP ABI 11: two-client timing isolation passed.')
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        for path in regions:
            path.unlink(missing_ok=True)


if __name__ == '__main__':
    check(Path(sys.argv[1]).resolve())
