"""Web power authorization, one-time intent and the fixed privilege boundary."""
import os
from pathlib import Path
import re
import tempfile
import sys
import unittest
from unittest.mock import Mock, patch

from pi import web_power
import test_web_login as _login_tests
login = _login_tests.login


@unittest.skipUnless(os.name == 'posix', 'Pi web service')
class PowerHTTP(unittest.TestCase):
    request = _login_tests.LoginHTTP.request
    form = _login_tests.LoginHTTP.form
    signed_in = _login_tests.LoginHTTP.signed_in
    tearDown = _login_tests.LoginHTTP.tearDown

    def setUp(self):
        _login_tests.LoginHTTP.setUp(self)
        self.power = Mock()
        self.power.available.return_value = True
        self.power.request.return_value = 'accepted'
        self.server.power = self.power

    def power_form(self, jar):
        status, headers, body = self.request('/power', cookie=jar)
        self.assertEqual(status, 200)
        csrf = re.search('name="csrf" value="([^"]+)"', body).group(1)
        confirm = re.search('name="confirm" value="([^"]+)"', body).group(1)
        form_cookie = next(v.split(';')[0] for k,v in headers if k=='Set-Cookie')
        return {'csrf': csrf, 'confirm': confirm}, jar+'; '+form_cookie

    def test_confirmation_requires_login_get_never_shuts_down_and_post_is_single_use(self):
        self.assertEqual(self.request('/power')[0],303)
        jar = self.signed_in()
        fields, cookie = self.power_form(jar)
        self.power.request.assert_not_called()
        self.assertEqual(self.request('/power','POST',fields,cookie)[0],202)
        self.power.request.assert_called_once()
        self.assertEqual(self.request('/power','POST',fields,cookie)[0],403)
        self.power.request.assert_called_once()

    def test_origin_csrf_nonce_expiry_and_session_binding_cannot_trigger_power(self):
        jar = self.signed_in()
        fields, cookie = self.power_form(jar)
        self.assertEqual(self.request('/power','POST',fields,cookie,origin='https://evil.example')[0],403)
        self.assertEqual(self.request('/power','POST',{**fields,'csrf':'wrong'},cookie)[0],403)
        self.assertEqual(self.request('/power','POST',{**fields,'confirm':'wrong'},cookie)[0],403)
        other = self.store.create(60)
        other_cookie = re.sub(login.SESSION+'=[^;]+',login.SESSION+'='+other,cookie)
        self.assertEqual(self.request('/power','POST',fields,other_cookie)[0],403)
        with patch.object(login.time,'time',return_value=__import__('time').time()+121):
            self.assertEqual(self.request('/power','POST',fields,cookie)[0],403)
        self.power.request.assert_not_called()

    def test_confirmation_tokens_are_bounded_private_and_revoked_with_the_session(self):
        session = self.signed_in().split('=',1)[1]
        for _ in range(140):
            token = self.store.confirmation(session)
        self.assertNotIn(token.encode(),self.store.path.read_bytes())
        with self.store.db() as db:
            self.assertLessEqual(db.execute('SELECT count(*) FROM confirmations').fetchone()[0],128)
        self.store.remove(session)
        self.assertFalse(self.store.consume_confirmation(session,token))
        self.power.request.assert_not_called()

    def test_blocked_shutdown_is_reported_and_still_consumes_confirmation(self):
        fields,cookie=self.power_form(self.signed_in())
        self.power.request.return_value='busy'
        status,_,body=self.request('/power','POST',fields,cookie)
        self.assertEqual(status,503)
        self.assertIn('not scheduled',body)
        self.assertEqual(self.request('/power','POST',fields,cookie)[0],403)
        self.power.request.assert_called_once()


@unittest.skipUnless(sys.platform=='linux','Linux root broker')
class Broker(unittest.TestCase):
    def test_only_exact_commands_and_authentication_uid_can_queue_one_fixed_service(self):
        from contextlib import nullcontext
        with patch.object(web_power,'guard',return_value=nullcontext()),patch.object(web_power.subprocess,'run') as run:
            for data,uid in ((b'poweroff\n',999),(b'poweroff;id\n',200),(b'poweroff\nextra',200),(b'force\n',200)):
                self.assertEqual(web_power.dispatch(data,uid,200),'denied')
            run.assert_not_called()
            self.assertEqual(web_power.dispatch(b'check\n',200,200),'ready')
            run.assert_not_called()
            self.assertEqual(web_power.dispatch(b'poweroff\n',200,200),'accepted')
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0],web_power.QUEUE)
            self.assertNotIn('shell',run.call_args.kwargs)

    def test_update_or_setup_failure_never_queues_shutdown(self):
        with patch.object(web_power,'guard',side_effect=BlockingIOError),patch.object(web_power.subprocess,'run') as run:
            self.assertEqual(web_power.dispatch(b'poweroff\n',200,200),'busy')
            run.assert_not_called()

    def test_guard_checks_update_states_and_refuses_setup_and_package_locks(self):
        import fcntl
        import stat
        import subprocess
        import sys
        import types
        safe_path = types.SimpleNamespace(st_uid=0,st_mode=stat.S_IFDIR|0o755)
        safe_file = types.SimpleNamespace(st_uid=0,st_mode=stat.S_IFREG|0o600)
        with tempfile.TemporaryDirectory() as folder:
            lock = Path(folder)/'setup';lock.touch(mode=0o600)
            apt = Path(folder)/'apt';apt.touch(mode=0o600)
            with patch.object(web_power,'LOCK',lock),patch.object(web_power,'APT_LOCKS',(str(apt),)),patch.object(Path,'lstat',return_value=safe_path),patch.object(web_power.os,'fstat',return_value=safe_file),patch.object(web_power.subprocess,'run') as run:
                run.return_value=Mock(stdout='inactive\nfailed\ninactive\n')
                with web_power.guard(): pass
                self.assertEqual(tuple(run.call_args.args[0][-3:]),web_power.UPDATES)
                for state in ('','inactive\n','active\ninactive\ninactive\n'):
                    run.return_value.stdout=state
                    with self.assertRaises(BlockingIOError):
                        with web_power.guard():pass
                run.reset_mock()
                with lock.open('rb') as held:
                    fcntl.flock(held,fcntl.LOCK_EX)
                    with self.assertRaises(BlockingIOError):
                        with web_power.guard():pass
                run.assert_not_called()
                code="import fcntl,sys; f=open(sys.argv[1],'r+'); fcntl.lockf(f,fcntl.LOCK_EX); print('locked',flush=True); sys.stdin.read(1)"
                child=subprocess.Popen([sys.executable,'-I','-c',code,str(apt)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
                try:
                    self.assertEqual(child.stdout.readline().strip(),'locked')
                    with self.assertRaises(BlockingIOError):
                        with web_power.guard():pass
                    run.assert_not_called()
                finally:
                    child.communicate('x',timeout=3)

    def test_actual_execution_requires_root_and_rechecks_guards(self):
        with patch.object(web_power.os,'geteuid',return_value=1000),patch.object(web_power.sys,'argv',['power','--execute']),patch.object(web_power.subprocess,'run') as run:
            with self.assertRaises(ValueError):web_power.main()
            run.assert_not_called()
        with patch.object(web_power.os,'geteuid',return_value=0),patch.object(web_power.sys,'argv',['power','--execute']),patch.object(web_power,'guard',side_effect=BlockingIOError),patch.object(web_power.subprocess,'run') as run:
            with self.assertRaises(BlockingIOError):web_power.main()
            run.assert_not_called()

    def test_installer_refuses_nonroot_or_foreign_power_files_before_deployment(self):
        import importlib.util
        root=Path(__file__).resolve().parents[1]
        spec=importlib.util.spec_from_file_location('power_install_test',root/'pi/install-web-power.py')
        installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
        for name in installer.FILES:self.assertTrue((root/'pi'/name).is_file())
        with patch.object(installer.os,'geteuid',return_value=1000),patch.object(installer,'run') as run:
            with self.assertRaises(ValueError):installer.main()
            run.assert_not_called()
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder)/'state';foreign=Path(folder)/'foreign';foreign.write_text('untouched')
            previous = os.umask(0o077)
            try:
                with patch.dict(os.environ),patch.object(installer.os,'geteuid',return_value=0),patch.object(installer,'trusted'),patch.object(installer,'STATE',state),patch.object(installer,'TARGETS',(str(foreign),)),patch.object(sys,'argv',['install','--hostname','pi.local']),patch.object(installer,'run') as run:
                    with self.assertRaises(ValueError):installer.main()
                    run.assert_not_called()
                    self.assertEqual(foreign.read_text(),'untouched')
            finally:
                os.umask(previous)

    def test_units_keep_login_unprivileged_and_broker_socket_private(self):
        root=Path(__file__).resolve().parents[1]
        socket=(root/'pi/pi-audio-power.socket').read_text()
        self.assertIn('--check-inhibitors=yes',web_power.COMMAND)
        self.assertIn('SocketMode=0600',socket)
        self.assertIn('SocketUser=pi-audio-auth',socket)
        self.assertIn('Accept=yes',socket)
        for name in ('pi-audio-power@.service','pi-audio-web-poweroff.service'):
            text=(root/'pi'/name).read_text()
            for setting in ('NoNewPrivileges=yes','CapabilityBoundingSet=','RestrictAddressFamilies=AF_UNIX','ProtectSystem=strict'):
                self.assertIn(setting,text)
        from pi.web_login_config import LOGIN_UNIT
        self.assertIn('User=pi-audio-auth',LOGIN_UNIT)
        self.assertIn('NoNewPrivileges=yes',LOGIN_UNIT)
