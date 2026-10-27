import http.client
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
import time
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode
from pi import web_login as login
from pi import web_login_config as config


class SessionSecurity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.password = self.root / 'htpasswd'
        self.password.write_text('music:example-credential-hash')
        self.store = login.Sessions(self.root, self.password)

    def tearDown(self):
        self.temp.cleanup()

    def test_only_hashes_are_persisted_and_restart_retains_session(self):
        token = self.store.create(login.REMEMBER)
        self.assertNotIn(token.encode(), self.store.path.read_bytes())
        self.assertTrue(login.Sessions(self.root, self.password).valid(token))
        if os.name == 'posix':
            self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_expiry_password_rotation_and_revocation_are_enforced(self):
        token = self.store.create(10)
        self.assertTrue(self.store.valid(token))
        with patch.object(login.time, 'time', return_value=time.time() + 11):
            self.assertFalse(self.store.valid(token))
        self.password.write_text('music:changed-hash')
        self.assertFalse(self.store.valid(token))
        one, two = self.store.create(100), self.store.create(100)
        self.store.remove(one)
        self.assertFalse(self.store.valid(one))
        self.assertTrue(self.store.valid(two))
        self.store.remove(two, all_devices=True)
        self.assertFalse(self.store.valid(two))

    def test_invalid_and_ambiguous_cookies_are_refused(self):
        for value in ('', 'bad', '" OR 1=1', 'a' * 10000):
            self.assertFalse(self.store.valid(value))
        self.assertEqual(login.cookies(login.SESSION + '=a; ' + login.SESSION + ' = b'), {})
        self.assertEqual(login.cookies('x' * 9000), {})

    def test_sessions_are_bounded_and_failed_login_limit_resets(self):
        for _ in range(140):
            self.store.create(100)
        with self.store.db() as db:
            self.assertLessEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0], 128)
        for _ in range(10):
            self.store.attempt('example-client', False)
        self.assertFalse(self.store.allowed('example-client'))
        with patch.object(login.time, 'time', return_value=time.time() + 601):
            self.assertTrue(self.store.allowed('example-client'))


@unittest.skipUnless(os.name == 'posix', 'Login service runs on the Linux Pi')
class LoginHTTP(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pi-login-')
        root = Path(self.temp.name)
        password = root / 'htpasswd'
        password.write_text('music:example-credential-hash')
        self.store = login.Sessions(root, password)
        self.path = str(root / 'socket')
        self.server = login.Server(self.path, self.store, ['https://pi.local'])
        self.errors = []
        self.server.handle_error = lambda *args: self.errors.append(str(sys.exception()))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def request(self, path, method='GET', fields=None, cookie='', origin='https://pi.local'):
        connection = http.client.HTTPConnection('localhost', timeout=5)
        connection.sock = socket.socket(socket.AF_UNIX)
        connection.sock.connect(self.path)
        headers = {'Cookie': cookie, 'Origin': origin}
        body = None if fields is None else urlencode(fields)
        if body is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        data = response.read().decode()
        result = (response.status, response.getheaders(), data)
        connection.close()
        return result

    def form(self):
        status, headers, body = self.request('/login')
        self.assertEqual(status, 200)
        csrf = re.search('name="csrf" value="([^"]+)"', body).group(1)
        jar = next(v.split(';')[0] for k, v in headers if k == 'Set-Cookie')
        return csrf, jar

    def signed_in(self):
        csrf, jar = self.form()
        with patch.object(login.subprocess, 'run', return_value=Mock(returncode=0)) as verify:
            status, headers, _ = self.request('/login', 'POST', {'username': 'music', 'password': 'example-only-test-password', 'csrf': csrf, 'remember': 'yes'}, jar)
        self.assertEqual(status, 303)
        self.assertNotIn('example-only-test-password', ' '.join(verify.call_args.args[0]))
        self.assertEqual(verify.call_args.kwargs['input'], 'example-only-test-password\n')
        value = next(v for k, v in headers if k == 'Set-Cookie' and v.startswith(login.SESSION + '='))
        for flag in ('Secure', 'HttpOnly', 'SameSite=Lax', 'Path=/', 'Max-Age=' + str(login.REMEMBER)):
            self.assertIn(flag, value)
        self.assertFalse(any(k.lower() == 'www-authenticate' for k, _ in headers))
        return value.split(';')[0]

    def test_login_persists_without_http_auth_challenges_and_account_has_logout(self):
        jar = self.signed_in()
        self.assertEqual(self.request('/check', cookie=jar)[0], 204)
        self.assertEqual(self.request('/check')[0], 401)
        status, _, body = self.request('/account', cookie=jar)
        self.assertEqual(status, 200)
        self.assertIn('Sign out all browsers', body)

    def test_cross_origin_and_missing_or_mismatched_csrf_never_verify_password(self):
        csrf, jar = self.form()
        with patch.object(login.subprocess, 'run') as verify:
            for token, cookie, origin in ((csrf, jar, 'https://evil.invalid'), ('', jar, 'https://pi.local'), (csrf, '', 'https://pi.local')):
                status, _, _ = self.request('/login', 'POST', {'password': 'test', 'csrf': token}, cookie, origin)
                self.assertEqual(status, 403)
            verify.assert_not_called()

    def test_failed_credentials_and_newline_passwords_fail_closed(self):
        csrf, jar = self.form()
        with patch.object(login.subprocess, 'run', return_value=Mock(returncode=3)):
            self.assertEqual(self.request('/login', 'POST', {'username': 'music', 'password': 'wrong', 'csrf': csrf}, jar)[0], 401)
        with patch.object(login.subprocess, 'run') as verify:
            self.assertEqual(self.request('/login', 'POST', {'password': 'bad\npassword', 'csrf': csrf}, jar)[0], 400)
            verify.assert_not_called()

    def test_logout_revokes_session_and_requires_csrf(self):
        jar = self.signed_in()
        self.assertEqual(self.request('/logout', 'POST', {'csrf': 'bad'}, jar)[0], 403)
        self.assertEqual(self.request('/check', cookie=jar)[0], 204)
        _, headers, body = self.request('/account', cookie=jar)
        form_cookie = next(v.split(';')[0] for k, v in headers if k == 'Set-Cookie')
        csrf = re.search('name="csrf" value="([^"]+)"', body).group(1)
        self.assertEqual(self.request('/logout', 'POST', {'csrf': csrf, 'all': '0'}, jar + '; ' + form_cookie)[0], 303)
        self.assertEqual(self.request('/check', cookie=jar)[0], 401)

    def test_nonremembered_login_has_session_cookie_but_bounded_server_lifetime(self):
        csrf, jar = self.form()
        with patch.object(login.subprocess, 'run', return_value=Mock(returncode=0)):
            status, headers, _ = self.request('/login', 'POST', {'username': 'music', 'password': 'test', 'csrf': csrf}, jar)
        value = next(v for k, v in headers if k == 'Set-Cookie' and v.startswith(login.SESSION + '='))
        self.assertEqual(status, 303)
        self.assertNotIn('Max-Age', value)
        with self.store.db() as db:
            expiry = db.execute('SELECT expires FROM sessions').fetchone()[0]
        self.assertLessEqual(expiry - time.time(), 12 * 3600)

    @unittest.skipUnless(Path('/usr/bin/htpasswd').exists(), 'Apache verifier is installed on the Pi/security CI')
    def test_real_apache_hash_verification_uses_stdin_and_preserves_hash_file(self):
        password = 'disposable-test-password-only'
        # Public disposable fixture, not a real credential. Ubuntu 24.04's older
        # verifier reads this format but lacks Debian 13's -5/-r generation flags.
        hashed = 'music:$6$rounds=10000$x4Ymx4HwE0Z27Rji$URKjFaAlYED/IqQcX98OTeCXK/8ZrNhSt75Gocov5dcxIwingn.NVtQ4EcKFI.nZjisRMchZ5hMJFhVreZhnn1\n'
        self.store.password_file.write_text(hashed)
        csrf, jar = self.form()
        status, _, _ = self.request('/login', 'POST', {'username': 'music', 'password': password, 'csrf': csrf, 'remember': 'yes'}, jar)
        self.assertEqual(status, 303)
        self.assertEqual(self.store.password_file.read_text(), hashed)


class WebConfiguration(unittest.TestCase):
    def test_untrusted_host_and_address_values_cannot_inject_nginx_config(self):
        for hostname in ('pi.local;return 200;', 'x\nserver', '"x"', 'x/../y'):
            with self.assertRaises(ValueError):
                config.nginx_config(hostname, [])
        text = config.nginx_config('pi.local', ['192.168.1.2', '8.8.8.8', 'fe80::1'])
        self.assertNotIn('8.8.8.8', text)
        self.assertNotIn('fe80::1', text)
        self.assertIn('auth_request /_session_check', text)
        self.assertIn('listen 80;', text)
        self.assertIn('listen 443 ssl;', text)
        self.assertIn('listen 8443 ssl;', text)
        self.assertNotIn('auth_basic', text)
        self.assertIn('proxy_set_header Cookie "";', text)
