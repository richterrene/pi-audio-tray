import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.request

import core

ROOT = Path(__file__).resolve().parents[1]


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


updater = load_script('secure_updater', 'pi/soloist-update.py')
launcher = load_script('secure_launcher', 'pi/soloist-launch.py')


class PrivateStorage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'XDG_CONFIG_HOME': self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_insecure_existing_directory_permissions_are_repaired(self):
        root = Path(self.temp.name) / 'pi-audio-tray'
        root.mkdir(mode=0o755)
        core.config_dir()
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def test_config_directory_cannot_be_a_symlink(self):
        target = Path(self.temp.name) / 'target'
        target.mkdir()
        (Path(self.temp.name) / 'pi-audio-tray').symlink_to(target, target_is_directory=True)
        with self.assertRaises(OSError):
            core.config_dir()

    def test_old_predictable_temporary_symlink_cannot_truncate_other_file(self):
        root = core.config_dir()
        target = Path(self.temp.name) / 'unrelated'
        target.write_text('preserve me')
        (root / 'config.json.tmp').symlink_to(target)
        core.save_config(core.DEFAULTS)
        self.assertEqual(target.read_text(), 'preserve me')
        self.assertEqual((root / 'config.json').stat().st_mode & 0o777, 0o600)

    def test_instance_lock_refuses_symlink_without_truncating_target(self):
        root = core.config_dir()
        target = Path(self.temp.name) / 'unrelated'
        target.write_text('preserve me')
        (root / 'instance.lock').symlink_to(target)
        with self.assertRaises(OSError):
            core.instance_lock_file()
        self.assertEqual(target.read_text(), 'preserve me')

    def test_stale_host_key_prompt_cannot_overwrite_an_existing_trusted_key(self):
        import paramiko
        first, second = paramiko.RSAKey.generate(2048), paramiko.RSAKey.generate(2048)
        core.trust_host(core.UnknownHost('audio-pi.example', first))
        with self.assertRaises(ValueError):
            core.trust_host(core.UnknownHost('audio-pi.example', second))
        keys = paramiko.HostKeys(str(core.config_dir() / 'known_hosts'))
        self.assertTrue(keys.check('audio-pi.example', first))


class FakeChannel:
    def __init__(self, output=(), errors=(), never_exit=False):
        self.output, self.errors = list(output), list(errors)
        self.never_exit = never_exit
        self.closed = False

    def settimeout(self, value): pass
    def recv_ready(self): return bool(self.output)
    def recv_stderr_ready(self): return bool(self.errors)
    def recv(self, size): return self.output.pop(0)
    def recv_stderr(self, size): return self.errors.pop(0)
    def exit_status_ready(self): return not self.never_exit and not self.output and not self.errors
    def recv_exit_status(self): return 0
    def close(self): self.closed = True


def client_for(channel):
    client = Mock()
    client.exec_command.return_value = (Mock(), SimpleNamespace(channel=channel), Mock())
    return client


class BoundedSSH(unittest.TestCase):
    def test_stdout_and_stderr_are_drained_together(self):
        channel = FakeChannel(output=[b'hello'], errors=[b'warning'])
        self.assertEqual(core.run(client_for(channel), 'fixed-command'), 'hello')
        self.assertTrue(channel.closed)

    def test_large_server_output_is_rejected_and_channel_closed(self):
        channel = FakeChannel(output=[b'x' * 16384] * 10)
        with self.assertRaisesRegex(RuntimeError, 'output limit'):
            core.run(client_for(channel), 'fixed-command')
        self.assertTrue(channel.closed)

    def test_trickling_or_stalled_server_cannot_extend_absolute_deadline(self):
        channel = FakeChannel(never_exit=True)
        with patch.object(core.time, 'monotonic', side_effect=[0, 0.1, 2]), patch.object(core.time, 'sleep'):
            with self.assertRaises(TimeoutError):
                core.run(client_for(channel), 'fixed-command', timeout=1)
        self.assertTrue(channel.closed)

    def test_pairing_control_does_not_perform_network_io_on_the_gui_thread(self):
        session = core.PairSession(core.DEFAULTS, Mock())
        session.input = Mock()
        session.send(action='approve', token='1')
        session.input.write.assert_not_called()
        self.assertEqual(session.outgoing.get_nowait(), {'action': 'approve', 'token': '1'})

    def test_pairing_control_queue_is_bounded(self):
        session = core.PairSession(core.DEFAULTS, Mock())
        for _ in range(16): session.send(action='stop')
        with self.assertRaises(RuntimeError): session.send(action='stop')

    def test_remote_session_identifier_cannot_spoof_local_session(self):
        callback = Mock()
        session = core.PairSession(core.DEFAULTS, callback)
        session.emit({'event': 'closed', 'session_id': 'spoofed'})
        self.assertEqual(callback.call_args.args[0]['session_id'], session.session_id)


class ProtocolValidation(unittest.TestCase):
    def status(self, **changes):
        return {'protocol': 1, 'services': {'soloist': 'active'}, 'adapter': {}, 'devices': [], **changes}

    def test_invalid_status_shapes_are_rejected(self):
        for message in ([], self.status(services={'soloist': []}), self.status(devices=[None]),
                        self.status(adapter={'Powered': 'yes'}), self.status(spotify_backend='unexpected')):
            with self.assertRaises(ValueError): core.validate_status(message)

    def test_nonfinite_or_impossible_metrics_are_rejected(self):
        for stats in ({'temperature_c': float('nan')}, {'cpu_percent': 101}, {'load': [1, 2, float('inf')]},
                      {'memory_total': 100, 'memory_used': 101}, {'cpu_count': 0}):
            with self.assertRaises(ValueError): core.validate_status(self.status(stats=stats))

    def test_names_cannot_inject_lines_or_bidi_spoofing(self):
        clean = core.validate_status(self.status(devices=[{'name': 'Phone\n\u202eevil'}]))
        self.assertEqual(clean['devices'][0]['name'], 'Phoneevil')

    def test_valid_stats_are_preserved(self):
        stats = {'cpu_percent': 12.5, 'temperature_c': 45, 'load': [0.5, 0.3, 0.2],
                 'cpu_count': 4, 'memory_total': 100, 'memory_used': 50, 'uptime_seconds': 90}
        self.assertEqual(core.validate_status(self.status(stats=stats))['stats'], stats)

    def test_pairing_messages_cannot_end_the_local_session_or_spoof_identity(self):
        for event in ({'event': 'ended'}, {'event': 'confirm', 'token': [], 'address': ''},
                      {'event': 'ready', 'expires': 'tomorrow'}):
            with self.assertRaises(ValueError): core.validate_pair_event(event)


class UpdaterSecurity(unittest.TestCase):
    def binary(self):
        data = bytearray(128)
        data[:7] = b'\x7fELF\x02\x01\x01'
        data[16:20] = b'\x03\x00\xb7\x00'
        return bytes(data)

    def archive(self, names=('soloist',), link=False, payload=None):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as tar:
            for name in names:
                item = tarfile.TarInfo(name)
                if link:
                    item.type = tarfile.SYMTYPE
                    item.linkname = '/etc/passwd'
                    tar.addfile(item)
                else:
                    body = self.binary() if payload is None else payload
                    item.size = len(body)
                    tar.addfile(item, io.BytesIO(body))
        return output.getvalue()

    def test_valid_client_is_parsed_without_execution(self):
        with patch.object(updater.subprocess, 'run') as execute:
            self.assertEqual(updater.extract_binary(self.archive()), self.binary())
            execute.assert_not_called()

    def test_archive_duplicates_links_traversal_and_wrong_architecture_are_rejected(self):
        cases = [self.archive(('soloist', 'nested/soloist')), self.archive(link=True),
                 self.archive(('../soloist',)), self.archive(payload=b'x' * 128)]
        for archive in cases:
            with self.assertRaises(ValueError): updater.extract_binary(archive)

    def test_decompression_and_member_sizes_are_bounded(self):
        with patch.object(updater, 'MAX_UNPACKED', 1024):
            with self.assertRaises(ValueError):
                updater.extract_binary(self.archive(payload=b'x' * 8192))
        with self.assertRaises(ValueError): updater.BoundedReader(io.BytesIO(b'12345'), 4).read(1000)

    def test_updater_rejects_http_and_off_host_redirects(self):
        redirect = updater.OfficialRedirect()
        request = urllib.request.Request(updater.URL)
        for url in ('http://soloist-builds.spotifycdn.com/file', 'https://example.com/file',
                    'https://soloist-builds.spotifycdn.com.evil.example/file', 'file:///etc/passwd'):
            with self.assertRaises(ValueError): redirect.redirect_request(request, None, 302, '', {}, url)

    def test_installation_never_executes_downloaded_bytes_and_backup_does_not_follow_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / 'soloist'
            victim = Path(temp) / 'unrelated'
            victim.write_bytes(b'preserve me')
            destination.write_bytes(b'old client')
            destination.with_name('soloist.previous').symlink_to(victim)
            with patch.object(updater, 'DEST', destination), patch.object(updater.subprocess, 'run') as execute:
                changed, digest = updater.install_binary(self.binary(), 'different')
                self.assertTrue(changed)
                execute.assert_not_called()
            self.assertEqual(victim.read_bytes(), b'preserve me')
            self.assertEqual(destination.with_name('soloist.previous').read_bytes(), b'old client')

    def test_key_loader_rejects_symlinks_world_readable_files_and_large_files(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'key'
            path.write_text('dummy-test-value')
            path.chmod(0o644)
            with self.assertRaises(ValueError): launcher.load_key(path)
            path.chmod(0o600)
            self.assertEqual(launcher.load_key(path), 'dummy-test-value')
            link = Path(temp) / 'link'
            link.symlink_to(path)
            with self.assertRaises(OSError): launcher.load_key(link)
            path.write_bytes(b'x' * 16385)
            with self.assertRaises(ValueError): launcher.load_key(path)


if __name__ == '__main__':
    unittest.main()
