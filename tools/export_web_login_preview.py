#!/usr/bin/env python3
"""Export the separate Pi web-player sign-in HTML for a safe browser screenshot.

Uses the real renderer with a blank password and demo-only form token. No live
receiver, credential file or authentication session is accessed. Serve the output
on loopback and capture it in a browser; never enter credentials in this preview.
"""
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pi.web_login import Handler


class Preview:
    def reply(self, status, body='', headers=()):
        self.body = body


def main():
    directory = Path(tempfile.mkdtemp(prefix='pi-audio-login-preview-'))
    preview = Preview()
    Handler.page(preview)
    (directory / 'login.html').write_text(preview.body)
    print('Browser screenshot HTML:', directory)
    print('Serve this directory on loopback, then capture login.html as a browser screenshot.')


if __name__ == '__main__':
    main()
