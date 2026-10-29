#!/usr/bin/python3 -I
"""Private Unix-socket login service for nginx auth_request. No media access."""
import hashlib
from contextlib import contextmanager
import hmac
import html
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import re
import secrets
import socket
import socketserver
import sqlite3
import subprocess
import threading
import time
from urllib.parse import parse_qs

SESSION = '__Host-pi_audio_session'
CSRF = '__Host-pi_audio_form'
REMEMBER = 90 * 86400
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}')


class Sessions:
    def __init__(self, directory, password_file):
        self.path = Path(directory) / 'sessions.sqlite3'
        self.password_file = Path(password_file)
        if self.path.is_symlink():
            raise ValueError('Refusing symbolic-link session database')
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions (hash TEXT PRIMARY KEY, expires INTEGER, credential TEXT, created INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS attempts (ip TEXT PRIMARY KEY, count INTEGER, since INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS confirmations (hash TEXT PRIMARY KEY, session TEXT, expires INTEGER)')
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=3)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def credential(self):
        return hashlib.sha256(self.password_file.read_bytes()).hexdigest()

    def valid(self, token):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            return False
        with self.db() as db:
            row = db.execute('SELECT expires, credential FROM sessions WHERE hash=?', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return bool(row and row[0] > time.time() and hmac.compare_digest(row[1], self.credential()))

    def create(self, lifetime, credential=None):
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE expires <= ?', (now,))
            db.execute('DELETE FROM sessions WHERE hash IN (SELECT hash FROM sessions ORDER BY created DESC LIMIT -1 OFFSET 127)')
            db.execute('INSERT INTO sessions VALUES (?, ?, ?, ?)',
                       (hashlib.sha256(token.encode()).hexdigest(), now + lifetime, credential or self.credential(), now))
        return token

    def remove(self, token, all_devices=False):
        with self.db() as db:
            if all_devices:
                db.execute('DELETE FROM sessions')
                db.execute('DELETE FROM confirmations')
            else:
                db.execute('DELETE FROM sessions WHERE hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
                db.execute('DELETE FROM confirmations WHERE session=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def confirmation(self, session):
        if not self.valid(session):
            raise ValueError('Authenticated session required')
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('DELETE FROM confirmations WHERE expires <= ?', (time.time(),))
            db.execute('DELETE FROM confirmations WHERE hash IN (SELECT hash FROM confirmations ORDER BY expires DESC LIMIT -1 OFFSET 127)')
            db.execute('INSERT INTO confirmations VALUES (?,?,?)',
                (hashlib.sha256(token.encode()).hexdigest(), hashlib.sha256(session.encode()).hexdigest(), int(time.time())+120))
        return token

    def consume_confirmation(self, session, token):
        if not isinstance(token, str) or not TOKEN.fullmatch(token) or not self.valid(session):
            return False
        with self.db() as db:
            result = db.execute('DELETE FROM confirmations WHERE hash=? AND session=? AND expires>?',
                (hashlib.sha256(token.encode()).hexdigest(), hashlib.sha256(session.encode()).hexdigest(), time.time()))
            return result.rowcount == 1

    def allowed(self, ip):
        now = int(time.time())
        with self.db() as db:
            db.execute('DELETE FROM attempts WHERE since < ?', (now - 600,))
            row = db.execute('SELECT count FROM attempts WHERE ip=?', (ip,)).fetchone()
        return not row or row[0] < 10

    def attempt(self, ip, success):
        with self.db() as db:
            if success:
                db.execute('DELETE FROM attempts WHERE ip=?', (ip,))
            else:
                db.execute('DELETE FROM attempts WHERE since < ?', (int(time.time()) - 600,))
                if db.execute('SELECT count(*) FROM attempts').fetchone()[0] >= 1024:
                    return  # nginx also bounds request rates before this service.
                db.execute('INSERT INTO attempts VALUES (?,1,?) ON CONFLICT(ip) DO UPDATE SET count=count+1', (ip, int(time.time())))


def cookies(header):
    if len(header) > 8192:
        return {}
    try:
        parsed = SimpleCookie()
        parsed.load(header)
        # Do not accept ambiguous repeated session/form cookie names.
        for name in (SESSION, CSRF):
            if sum(part.partition('=')[0].strip() == name for part in header.split(';')) > 1:
                return {}
        return {name: value.value for name, value in parsed.items()}
    except CookieError:
        return {}


def cookie(name, value, age=None):
    # Lax permits opening the player from a bookmark/tray/external link. Form
    # tokens remain Strict; unsafe requests also require a trusted Origin.
    same_site = 'Lax' if name == SESSION else 'Strict'
    result = f'{name}={value}; Path=/; Secure; HttpOnly; SameSite={same_site}'
    if age is not None:
        result += f'; Max-Age={age}'
    return result


class PowerClient:
    path = Path('/run/pi-audio-power/control.sock')

    def available(self):
        try:
            info = self.path.lstat()
            import stat
            return stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077
        except OSError:
            return False

    def request(self):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(8)
                connection.connect(str(self.path))
                connection.sendall(b'poweroff\n')
                connection.shutdown(socket.SHUT_WR)
                raw = b''
                while len(raw) <= 256 and not raw.endswith(b'\n'):
                    part = connection.recv(257-len(raw))
                    if not part: break
                    raw += part
            if len(raw) > 256:
                return 'unavailable'
            value = json.loads(raw)
            if isinstance(value, dict) and set(value) == {'status'} and value['status'] in ('accepted', 'busy', 'denied'):
                return value['status']
        except (OSError, ValueError):
            pass
        return 'unavailable'


class Handler(BaseHTTPRequestHandler):
    server_version = 'PiAudioLogin'

    def log_message(self, *args):
        pass  # Never log passwords, cookies, forms, or query strings.

    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def reply(self, status, body='', headers=()):
        data = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        if status != 204:
            self.send_header('Content-Length', str(len(data)))
        self.send_header('Connection', 'close')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        # no-referrer can turn a browser's form POST Origin into "null", which
        # our CSRF boundary correctly refuses. Preserve same-origin form origins.
        self.send_header('Referrer-Policy', 'same-origin')
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if data:
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass  # A browser may abandon a response; never log its contents.
        self.close_connection = True

    def redirect(self, location, headers=()):
        self.reply(303, headers=[('Location', location), *headers])

    def page(self, message='', account=False, status=200, power=False, requested=False):
        csrf = secrets.token_urlsafe(32)
        hidden = '<input type="hidden" name="csrf" value="' + csrf + '">'
        if power:
            if requested:
                content = '<h2>Shutdown requested</h2><p>The Pi will shut down shortly if update and setup checks remain clear. Audio will stop. To start it again, reconnect its power.</p>'
            elif self.server.power and self.server.power.available():
                session = cookies(self.headers.get('Cookie', '')).get(SESSION, '')
                try:
                    confirmation = self.server.sessions.confirmation(session)
                except ValueError:
                    self.redirect('/login')
                    return
                content = ('<h2>Shut down this Pi?</h2><p>This stops all audio and powers off the Pi. To start it again, reconnect its power.</p>'
                           '<form method="post" action="/power">' + hidden +
                           '<input type="hidden" name="confirm" value="' + confirmation + '">'
                           '<button type="submit" class="danger">Shut down Pi</button></form>')
            else:
                content = '<h2>Shutdown unavailable</h2><p>Install the optional web shutdown component first.</p>'
            content += '<p><a href="/">Back to music player</a> · <a href="/account">Account</a></p>'
        elif account:
            content = ('<p>This browser is signed in to your music player.</p><p><a href="/">Open music player</a></p>'
                       '<form method="post" action="/logout">' + hidden +
                       '<button name="all" value="0">Sign out this browser</button> '
                       '<button name="all" value="1">Sign out all browsers</button></form>')
            if self.server.power and self.server.power.available():
                content += '<p><a href="/power">Shut down Pi…</a></p>'
        else:
            content = ('<p>Sign in once to control music on your Pi.</p><form method="post" action="/login">' + hidden +
                       '<label>Username<input name="username" value="music" autocomplete="username" required></label>'
                       '<label>Password<input type="password" name="password" autocomplete="current-password" required maxlength="1024"></label>'
                       '<label class="remember"><input type="checkbox" name="remember" value="yes" checked> Remember this browser for 90 days</label>'
                       '<button type="submit">Open music player</button></form>')
        body = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                '<title>Pi Audio</title><style>body{font:16px system-ui;margin:0;background:#101c29;color:#edf6fa;display:grid;min-height:100vh;place-items:center}'
                'main{max-width:390px;margin:20px;padding:32px;background:#1b2c3b;border-radius:18px}h1{margin:0}label{display:block;margin:18px 0}'
                'input:not([type=checkbox]){box-sizing:border-box;width:100%;padding:12px;border:1px solid #8ba2b4;border-radius:8px;margin-top:6px;background:#10202e;color:white;font:inherit}'
                'button{background:#087f68;color:white;border:0;border-radius:8px;padding:12px 16px;font:inherit;margin:5px 0;cursor:pointer}button.danger{background:#b23c49}a{color:#7ce7c5}.remember{font-size:14px}.error{color:#ffb5b5}</style>'
                '<main><h1>Pi Audio</h1><p class="error" role="alert">' + html.escape(message) + '</p>' + content + '</main></html>')
        self.reply(status, body, [('Set-Cookie', cookie(CSRF, csrf, 600))])

    def do_GET(self):
        if self.path not in ('/check', '/login', '/account', '/power'):
            self.reply(404)
            return
        session = cookies(self.headers.get('Cookie', '')).get(SESSION, '')
        valid = self.server.sessions.valid(session)
        if self.path == '/check':
            self.reply(204 if valid else 401)
        elif self.path in ('/account', '/power') and not valid:
            self.redirect('/login')
        elif self.path == '/power':
            self.page(power=True, status=200 if self.server.power and self.server.power.available() else 503)
        else:
            self.page(account=valid)

    def do_POST(self):
        if self.path not in ('/login', '/logout', '/power'):
            self.reply(404)
            return
        if self.headers.get('Origin') not in self.server.origins:
            self.reply(403, 'Reload the sign-in page and try again.')
            return
        if self.headers.get('Transfer-Encoding') or self.headers.get_content_type() != 'application/x-www-form-urlencoded':
            self.reply(400)
            return
        try:
            size = int(self.headers.get('Content-Length', '-1'))
            if not 0 < size <= 4096:
                raise ValueError()
            fields = parse_qs(self.rfile.read(size).decode('utf-8'), keep_blank_values=True, strict_parsing=True, max_num_fields=5)
            if any(len(value) != 1 for value in fields.values()):
                raise ValueError()
            fields = {key: value[0] for key, value in fields.items()}
        except (ValueError, UnicodeError):
            self.reply(400)
            return
        jar = cookies(self.headers.get('Cookie', ''))
        csrf = fields.get('csrf', '')
        cookie_csrf = jar.get(CSRF, '')
        if not TOKEN.fullmatch(csrf) or not TOKEN.fullmatch(cookie_csrf) or not hmac.compare_digest(csrf, cookie_csrf):
            self.reply(403, 'Reload the sign-in page and try again.')
            return
        current = jar.get(SESSION, '')
        if self.path == '/power':
            if not self.server.sessions.valid(current):
                self.redirect('/login')
                return
            if set(fields) != {'csrf', 'confirm'} or not self.server.sessions.consume_confirmation(current, fields.get('confirm', '')):
                self.reply(403, 'Reload the shutdown page and confirm again.')
                return
            result = self.server.power.request() if self.server.power else 'unavailable'
            if result == 'accepted':
                self.page(power=True, requested=True, status=202)
            else:
                self.page('Shutdown was not scheduled. Updates or setup may be running, or the shutdown helper is unavailable.', power=True, status=503)
            return
        if self.path == '/logout':
            if not self.server.sessions.valid(current):
                self.redirect('/login')
                return
            self.server.sessions.remove(current, fields.get('all') == '1')
            self.redirect('/login', [('Set-Cookie', cookie(SESSION, '', 0))])
            return
        ip = self.headers.get('X-Real-IP', 'local')[:64]
        if not self.server.sessions.allowed(ip):
            self.reply(429, 'Too many attempts. Please wait ten minutes before trying again.', [('Retry-After', '600')])
            return
        password = fields.get('password', '')
        if not password or len(password) > 1024 or any(c in password for c in '\r\n\x00'):
            self.reply(400)
            return
        try:
            credential = self.server.sessions.credential()
            # -i reads stdin: never expose the password through argv/env/logs.
            check = subprocess.run(['/usr/bin/htpasswd', '-vi', str(self.server.sessions.password_file), 'music'],
                                   input=password + '\n', text=True, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=3, check=False)
            valid = check.returncode == 0 and fields.get('username') == 'music'
        except (OSError, subprocess.TimeoutExpired):
            self.reply(503, 'Sign-in temporarily unavailable.')
            return
        self.server.sessions.attempt(ip, valid)
        if not valid:
            self.page('Username or password is incorrect.', status=401)
            return
        remember = fields.get('remember') == 'yes'
        token = self.server.sessions.create(REMEMBER if remember else 12 * 3600, credential)
        if TOKEN.fullmatch(current):
            self.server.sessions.remove(current)
        self.redirect('/', [('Set-Cookie', cookie(SESSION, token, REMEMBER if remember else None)),
                            ('Set-Cookie', cookie(CSRF, '', 0))])


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, path, sessions, origins, power=None):
        self.sessions, self.origins = sessions, set(origins)
        self.power = power
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(path, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, *args):
        try:
            super().process_request_thread(*args)
        finally:
            self.slots.release()

    def handle_error(self, *args):
        print('Login request failed.', flush=True)  # No request details or tokens.


def main():
    os.umask(0o077)
    config = json.loads(Path('/etc/pi-audio-tray/music/login.json').read_text())
    sessions = Sessions('/var/lib/pi-audio-login', '/etc/pi-audio-tray/music/htpasswd')
    sessions.credential()  # Fail startup if the protected verifier file is unreadable.
    path = Path('/run/pi-audio-login/auth.sock')
    path.unlink(missing_ok=True)
    with Server(str(path), sessions, config['origins'], power=PowerClient()) as server:
        path.chmod(0o660)
        notify = os.environ.get('NOTIFY_SOCKET')
        if notify:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as ready:
                ready.sendto(b'READY=1', '\0' + notify[1:] if notify.startswith('@') else notify)
        server.serve_forever()


if __name__ == '__main__':
    main()
