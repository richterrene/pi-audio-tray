"""Audio add-on trust boundaries and desktop lifecycle; no Pi/network required."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import audio
import pi_audio
import provision

ROOT = Path(__file__).resolve().parents[1]


def settings(**changes):
    return {'name': 'Test Audio', 'password': 'Example-only-123', **changes}


def snapshot(**changes):
    return {'protocol': 1, 'installed': True, 'configured': True, 'name': 'Test Audio',
            'state': 'inactive', 'enabled': False, 'pipewire': 'active', 'version': '4.3.7-pa-', 'mode': 'classic', 'timing': 'unknown', **changes}


class AudioProtocol(unittest.TestCase):
    def test_rejects_settings_injection_and_excessive_requests(self):
        for changed in ({'extra': 1}, {'name': 'x\noutput_backend = "pipe"'},
                        {'name': 'x;id'}, {'name': ''}, {'name': 'x' * 51},
                        {'password': 'short'}, {'password': 'x' * 129},
                        {'password': 'embedded space'}, {'password': 'unicode-\u202e'},
                        {'password': 123}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                pi_audio.validate_settings(settings(**changed))

    def test_libconfig_quotes_password_as_data(self):
        value = settings(password='quotes";output_backend="pipe\\end')
        text = pi_audio.configuration(value)
        self.assertIn('password = ' + json.dumps(value['password']) + ';', text)
        self.assertIn('output_backend = "pa";', text)
        self.assertNotIn('\n output_backend = "pipe"', text)

    @unittest.skipIf(sys.platform == 'win32', 'POSIX receiver configuration')
    def test_status_returns_receiver_name_without_password(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(pi_audio, 'ROOT', Path(folder).resolve() / 'config'), patch.object(pi_audio, 'state', return_value='inactive'), patch.object(pi_audio, 'command', return_value=types.SimpleNamespace(stdout='')):
            pi_audio.configure(settings())
            value = pi_audio.status()
            self.assertEqual(value['name'], 'Test Audio')
            self.assertNotIn(settings()['password'], json.dumps(value))

    @unittest.skipIf(sys.platform == 'win32', 'POSIX receiver private files')
    def test_private_config_atomic_replacement_and_symlink_refusal(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(pi_audio, 'ROOT', Path(folder).resolve() / 'config'):
            pi_audio.configure(settings())
            self.assertEqual(pi_audio.ROOT.stat().st_mode & 0o777, 0o700)
            config = pi_audio.ROOT / 'airplay.conf'
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            victim = Path(folder) / 'victim'
            victim.write_text('unchanged')
            config.unlink()
            config.symlink_to(victim)
            with self.assertRaises(ValueError):
                pi_audio.configure(settings())
            self.assertEqual(victim.read_text(), 'unchanged')

    def test_invalid_config_does_not_touch_filesystem(self):
        with patch.object(pi_audio.ROOT.__class__, 'mkdir') as mkdir, self.assertRaises(ValueError):
            pi_audio.configure(settings(password='bad'))
        mkdir.assert_not_called()

    def test_wire_validation_rejects_malformed_status(self):
        self.assertEqual(audio.validate_status(snapshot()), snapshot())
        for changed in ({'protocol': True}, {'installed': 1}, {'state': []},
                        {'pipewire': 'active;id'}, {'version': 'x\n'}, {'version': 'x' * 257},
                        {'unknown': True}, {'name': '<b>Untrusted</b>'}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                audio.validate_status(snapshot(**changed))

    def test_credentials_use_stdin_and_never_the_command(self):
        client = Mock()
        with patch.object(audio, 'connect', return_value=client), patch.object(audio, 'run', return_value=json.dumps(snapshot())) as run:
            audio.request({}, 'configure', settings())
        command = run.call_args.args[1]
        self.assertEqual(command, '/usr/local/lib/pi-audio-tray/pi-audio configure')
        self.assertNotIn(settings()['password'], command)
        self.assertEqual(json.loads(run.call_args.kwargs['input_data']), settings())
        client.close.assert_called_once()

    def test_request_validation_happens_before_ssh(self):
        for action, data in (('status;id', None), ('enable', settings()), ('configure', settings(password='bad'))):
            with patch.object(audio, 'connect') as connect, self.assertRaises(ValueError):
                audio.request({}, action, data)
            connect.assert_not_called()

    def test_transport_failure_still_closes_client(self):
        client = Mock()
        with patch.object(audio, 'connect', return_value=client), patch.object(audio, 'run', side_effect=TimeoutError), self.assertRaises(TimeoutError):
            audio.request({}, 'status')
        client.close.assert_called_once()

    def test_fixed_service_actions_and_no_sudo(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(pi_audio, 'ROOT', Path(folder)), patch.object(pi_audio, 'command') as command, patch.object(pi_audio, 'status', return_value=snapshot()):
            command.return_value = types.SimpleNamespace(returncode=0)
            with self.assertRaises(ValueError):
                pi_audio.action('enable')
            command.assert_not_called()
            (Path(folder) / 'airplay.conf').write_text('private settings')
            pi_audio.action('enable')
            command.assert_called_once_with('/usr/bin/systemctl', '--user', 'enable', '--now', pi_audio.UNIT)
            command.reset_mock()
            pi_audio.action('disable')
            command.assert_called_once_with('/usr/bin/systemctl', '--user', 'disable', '--now', pi_audio.UNIT)

    def test_saving_does_not_enable_listener(self):
        for active in ('active', 'inactive'):
            with patch.object(pi_audio, 'configure'), patch.object(pi_audio, 'state', return_value=active), patch.object(pi_audio, 'command') as command, patch.object(pi_audio, 'status', return_value=snapshot()):
                command.return_value = types.SimpleNamespace(returncode=0)
                pi_audio.action('configure', settings())
                if active == 'active':
                    command.assert_called_once_with('/usr/bin/systemctl', '--user', 'restart', pi_audio.UNIT)
                else:
                    command.assert_not_called()

    def test_bundle_and_installer_plan_contain_no_credentials(self):
        for name in audio.PI_FILES:
            self.assertTrue((ROOT / 'pi' / name).is_file())
        for name in audio.TOP_FILES:
            self.assertTrue((ROOT / name).is_file())
        with patch.object(provision, 'administrator_install') as install:
            audio.install({}, 'sudo-example', print)
        self.assertEqual(install.call_args.args[1], {'mode': 'classic'})
        self.assertEqual(install.call_args.args[4:], ('install-airplay.py', audio.PI_FILES, audio.TOP_FILES))


@unittest.skipUnless(sys.platform == 'linux', 'Linux administrator installer fixtures')
class AirPlayInstaller(unittest.TestCase):
    def exercise(self, *, existing=False, arch='arm64', version='4.3.7-ALSA-pa-dummy-'):
        spec = importlib.util.spec_from_file_location('airplay_install', ROOT / 'pi/install-airplay.py')
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / 'source'
            source.mkdir()
            for name in audio.PI_FILES:
                shutil.copyfile(ROOT / 'pi' / name, source / name)

            for name in audio.TOP_FILES:
                shutil.copyfile(ROOT / name, source / name)
            installer.__file__ = str(source / 'install-airplay.py')
            installer.STATE = base / 'state'
            installer.SETUP_STATE = base / 'setup-state'
            commands, outputs = [], {}
            receiver = Mock()
            receiver.safe_dir.side_effect = lambda path, *args: Path(path).mkdir(parents=True, exist_ok=True) if Path(path).is_relative_to(base) else None
            receiver.run.side_effect = lambda *args, **kwargs: commands.append(args)
            def write(path, text, mode=0o644):
                outputs[str(path)] = (text, mode)
                if Path(path).is_relative_to(installer.STATE):
                    Path(path).write_text(text)
            receiver.write.side_effect = write
            original_stat, original_read, original_is_file = Path.stat, Path.read_text, Path.is_file
            marker = '/etc/pi-audio-tray/soloist-user'
            def info(path, **kwargs):
                if str(path) == marker:
                    return types.SimpleNamespace(st_uid=0, st_mode=0o100644)
                result = original_stat(path, **kwargs)
                if stat.S_ISDIR(result.st_mode):
                    values = list(result)
                    values[4] = 0
                    # Temporary test parents are not root-owned in CI.
                    values[0] &= ~0o022
                    return os.stat_result(values)
                return result
            def read(path, *args, **kwargs):
                if str(path) == '/etc/os-release': return 'ID=debian\nVERSION_ID="13"\n'
                if str(path) == '/proc/device-tree/model': return 'Raspberry Pi 3 Model B+\0'
                if str(path) == marker: return 'audio\n'
                return original_read(path, *args, **kwargs)
            def is_file(path):
                return True if str(path) == marker else original_is_file(path)
            original_exists = Path.exists
            def exists(path):
                if str(path) == '/usr/bin/shairport-sync': return existing
                if str(path) in ('/etc/shairport-sync.conf', '/usr/local/lib/systemd/user/pi-audio-airplay.service'): return False
                return original_exists(path)
            with patch.object(installer.os, 'geteuid', return_value=0), \
                 patch.dict(os.environ, {'SUDO_UID': str(os.getuid())}), \
                 patch.object(installer.pwd, 'getpwuid', return_value=types.SimpleNamespace(pw_name='audio')), \
                 patch.object(installer.sys, 'argv', ['install-airplay.py', '{}']), \
                 patch.object(installer.subprocess, 'check_output', side_effect=lambda args, **kw: arch if 'dpkg' in args[0] else version), \
                 patch.object(Path, 'stat', info), patch.object(Path, 'read_text', read), \
                 patch.object(Path, 'is_file', is_file), patch.object(Path, 'exists', exists), \
                 patch.object(installer.importlib.util, 'module_from_spec', return_value=receiver), \
                 patch('importlib.machinery.SourceFileLoader.exec_module'):
                installer.main()
            return commands, outputs

    def test_masks_system_receiver_before_package_install_and_leaves_user_unit_disabled(self):
        commands, outputs = self.exercise()
        mask = commands.index(('/usr/bin/systemctl', 'mask', 'shairport-sync.service'))
        package = next(i for i, command in enumerate(commands) if command[:2] == ('/usr/bin/apt-get', 'install'))
        self.assertLess(mask, package)
        self.assertIn('/usr/local/lib/pi-audio-tray/pi-audio', outputs)
        self.assertIn('/usr/local/lib/systemd/user/pi-audio-airplay.service', outputs)
        self.assertFalse(any('enable' in command or 'restart' in command for command in commands))
        self.assertFalse(any('/etc/sudoers' in path or path.startswith('/home/') for path in outputs))

    def test_existing_installation_and_unsupported_platform_or_build_are_refused(self):
        for changes in ({'existing': True}, {'arch': 'armhf'}, {'version': '4.3.7-ALSA-'}, {'version': '5.0-AirPlay2-pa-'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.exercise(**changes)


class AudioDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_no_polling_and_disabled_controls_until_fresh_status(self):
        from PySide6.QtCore import QTimer
        from audio_ui import AudioDialog
        dialog = AudioDialog({})
        self.assertFalse(dialog.save_button.isEnabled())
        self.assertFalse(dialog.enable_button.isEnabled())
        self.assertEqual(dialog.findChildren(QTimer), [])
        dialog.show_status(snapshot(configured=False))
        dialog.controls()
        self.assertTrue(dialog.save_button.isEnabled())
        self.assertFalse(dialog.enable_button.isEnabled())
        dialog.show_status(snapshot())
        dialog.controls()
        self.assertTrue(dialog.enable_button.isEnabled())
        dialog.deleteLater()

    def test_busy_installer_cannot_be_closed_and_clears_password_on_close(self):
        from audio_ui import AudioDialog
        dialog = AudioDialog({})
        dialog.password.setText('test-example')
        dialog.worker = Mock()
        dialog.reject()
        self.assertEqual(dialog.password.text(), 'test-example')
        dialog.worker = None
        dialog.reject()
        self.assertEqual(dialog.password.text(), '')
        dialog.deleteLater()

    def test_cancel_install_clears_sudo_password_without_connection(self):
        from audio_ui import AudioDialog
        dialog = AudioDialog({})
        dialog.admin.setText('sudo-example')
        with patch.object(dialog, 'confirm', return_value=False), patch.object(audio, 'install') as install:
            dialog.install()
        install.assert_not_called()
        self.assertEqual(dialog.admin.text(), '')
        dialog.deleteLater()

    def test_refresh_preserves_unsaved_receiver_name(self):
        from audio_ui import AudioDialog
        dialog = AudioDialog({})
        dialog.show_status(snapshot())
        self.assertEqual(dialog.name.text(), 'Test Audio')
        dialog.name.setText('Unsaved name')
        dialog.name.setModified(True)
        dialog.show_status(snapshot())
        self.assertEqual(dialog.name.text(), 'Unsaved name')
        dialog.deleteLater()


if __name__ == '__main__':
    unittest.main()
