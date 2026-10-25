#!/usr/bin/python3 -I
"""Renew the local HTTPS leaf certificate; all inputs are root-owned."""
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    if os.geteuid() != 0:
        raise SystemExit('Administrator privileges required')
    root = Path('/etc/pi-audio-tray/music/tls')
    cert = root / 'server.pem'
    if subprocess.run(['openssl', 'x509', '-checkend', str(30 * 86400), '-noout', '-in', str(cert)], stdout=subprocess.DEVNULL, check=False).returncode == 0:
        return
    descriptor, target = tempfile.mkstemp(prefix='renew-', dir=root)
    os.close(descriptor)
    try:
        subprocess.run(['openssl', 'x509', '-req', '-in', str(root / 'server.csr'), '-CA', str(root / 'ca.pem'), '-CAkey', str(root / 'ca.key'), '-CAserial', str(root / 'ca.srl'), '-days', '365', '-extfile', str(root / 'extensions'), '-out', target], check=True)
        subprocess.run(['openssl', 'verify', '-CAfile', str(root / 'ca.pem'), target], check=True)
        os.chmod(target, 0o600)
        os.replace(target, cert)
        subprocess.run(['systemctl', 'reload-or-restart', 'pi-audio-web.service'], check=True)
    finally:
        Path(target).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
