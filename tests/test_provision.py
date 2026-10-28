"""Setup trust-boundary and plan tests; do not mutate a real Pi."""
import importlib.util
import json
import os
import sys
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication
APP = QApplication.instance() or QApplication([])

from setup_plan import validate_plan
import provision

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def plan(**overrides):
    return dict(music=False, web=False, updates=True, cockpit=False, shutdown=True, privacy=True,
                usb_uuid='', hostname='audio-pi.local', **overrides)


class PlanTests(unittest.TestCase):
    def test_defaults_and_unknown_fields(self):
        self.assertEqual(validate_plan(plan())['hostname'], 'audio-pi.local')
        for changed in ({**plan(), 'extra': True}, {**plan(), 'updates': 1},
                        {**plan(), 'hostname': 'pi; touch /tmp/a'},
                        {**plan(), 'hostname': 'pi..local'},
                        {**plan(), 'hostname': '-pi.local'},
                        {**plan(), 'hostname': 'pi\n.local'},
                        {**plan(), 'usb_uuid': 'FFFF-1234'},
                        {**plan(), 'web': True},
                        {**plan(), 'music': True, 'usb_uuid': '../../etc/passwd'}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_plan(changed)
        value = {**plan(), 'music': True, 'web': True, 'usb_uuid': 'ABCD-1234'}
        self.assertEqual(validate_plan(value), value)

    def test_bundle_is_explicit_and_complete(self):
        self.assertEqual(len(set(provision.PI_FILES + provision.TOP_FILES)), len(provision.PI_FILES + provision.TOP_FILES))
        for name in provision.PI_FILES:
            self.assertTrue((ROOT / 'pi' / name).is_file(), name)
        for name in provision.TOP_FILES:
            self.assertTrue((ROOT / name).is_file(), name)
        self.assertFalse(any('config.json' in n or 'api-key' in n for n in provision.PI_FILES))

    @unittest.skipUnless(sys.platform == 'linux', 'Linux receiver filesystem rules')
    def test_privileged_writer_refuses_symlink_ancestors(self):
        installer = load('receiver', ROOT / 'pi/setup-receiver.py')
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / 'target').mkdir()
            (base / 'link').symlink_to(base / 'target')
            with self.assertRaises(ValueError):
                installer.write(base / 'link/file', 'bad')
            self.assertFalse((base / 'target/file').exists())

    def test_output_validation_before_connect(self):
        for value in ('1;id', '١', '-1', '0', str(2**31), 1):
            with self.subTest(value=value), patch.object(provision, 'connect') as connect, self.assertRaises(ValueError):
                provision.select_output({}, value)
            connect.assert_not_called()

    def test_output_selection_rechecks_stale_id(self):
        with patch.object(provision, 'connect') as connect, patch.object(provision, 'run', return_value='[]') as run:
            with self.assertRaises(ValueError):
                provision.select_output({}, '42')
            self.assertEqual(run.call_count, 1)
            connect.return_value.close.assert_called_once()

    @unittest.skipUnless(sys.platform == 'linux', 'Linux receiver inventory')
    def test_probe_rejects_unsupported_os_and_existing_receiver(self):
        probe = load('probe', ROOT / 'pi/setup-probe.py')
        def read(path):
            if str(path) == '/etc/os-release':
                return 'ID=debian\nVERSION_ID="12"\n'
            return 'Raspberry Pi 3 Model B Plus\0'
        result = types.SimpleNamespace(returncode=1, stdout='')
        with patch.object(probe.Path, 'read_text', read), patch.object(probe.Path, 'exists', return_value=True), patch.object(probe.Path, 'is_dir', return_value=True), patch.object(probe.platform, 'machine', return_value='aarch64'), patch.object(probe.subprocess, 'run', return_value=result), patch.object(probe.pwd, 'getpwuid', return_value=types.SimpleNamespace(pw_uid=1000, pw_name='audio', pw_dir='/home/audio')):
            data = probe.inventory()
        self.assertFalse(data['supported'])
        self.assertIn('Requires Debian / Raspberry Pi OS 13 Trixie', data['reasons'])
        self.assertTrue(data['conflicts'])

    @unittest.skipUnless(sys.platform == 'linux', 'Linux receiver inventory')
    def test_64bit_kernel_with_32bit_userspace_is_rejected(self):
        probe = load('probe_userspace', ROOT / 'pi/setup-probe.py')
        def read(path):
            return 'ID=debian\nVERSION_ID="13"\n' if str(path) == '/etc/os-release' else 'Raspberry Pi 3 Model B\0'
        def run(args, **kwargs):
            if args[0] == '/usr/bin/dpkg':
                return types.SimpleNamespace(returncode=0, stdout='armhf\n')
            return types.SimpleNamespace(returncode=1, stdout='')
        with patch.object(probe.Path, 'read_text', read), patch.object(probe.Path, 'is_dir', return_value=True), patch.object(probe.platform, 'machine', return_value='aarch64'), patch.object(probe.subprocess, 'run', side_effect=run), patch.object(probe.pwd, 'getpwuid', return_value=types.SimpleNamespace(pw_uid=1000, pw_name='audio', pw_dir='/home/audio')), patch.object(probe.struct, 'calcsize', return_value=4):
            # Mark only the device-tree model as present, not existing receiver files.
            with patch.object(probe.Path, 'exists', lambda path: str(path) == '/proc/device-tree/model'):
                result = probe.inventory()
        self.assertFalse(result['supported'])
        self.assertEqual(result['userspace_architecture'], 'armhf')
        self.assertEqual(result['python_bits'], 32)
        self.assertEqual(result['reasons'], ['Requires 64-bit ARM64 userspace, not only a 64-bit kernel'])


class TransportTests(unittest.TestCase):
    def test_sudo_password_only_goes_over_stdin_and_cleanup_is_exact(self):
        channel = Mock()
        events = (json.dumps({'step': 'complete', 'message': 'Installed.'}) + '\n').encode()
        channel.recv_ready.side_effect = [True, False, False]
        channel.recv.return_value = events
        channel.recv_stderr_ready.return_value = False
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = 0
        client = Mock()
        client.get_transport.return_value.open_session.return_value = channel
        sftp = client.open_sftp.return_value.__enter__ = Mock(return_value=Mock())
        client.open_sftp.return_value.__exit__ = Mock()
        remote = sftp.return_value
        remote.file.return_value.__enter__ = Mock(return_value=Mock())
        remote.file.return_value.__exit__ = Mock()
        with patch.object(provision, 'connect', return_value=client):
            messages = []
            provision.install({}, plan(), 'example-password-not-a-secret', messages.append)
        self.assertEqual(messages, ['Installed.'])
        self.assertNotIn('example-password', channel.exec_command.call_args.args[0])
        channel.sendall.assert_called_once_with(b'example-password-not-a-secret\n')
        channel.shutdown_write.assert_called_once()
        self.assertEqual(remote.remove.call_count, len(provision.PI_FILES + provision.TOP_FILES))
        for call in remote.remove.call_args_list:
            self.assertRegex(call.args[0], r'^/tmp/pi-audio-setup-[a-f0-9]{32}/[A-Za-z0-9_.-]+$')
        client.close.assert_called_once()


@unittest.skipUnless(sys.platform == 'linux', 'Linux administrator installer fixtures')
class InstallerStages(unittest.TestCase):
    def exercise(self, selected, *, previous_permissions='', pending_fresh=False, owner='audio', broken_web=False, external_cockpit=False):
        import shutil
        installer = load('receiver_stages', ROOT / 'pi/setup-receiver.py')
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / 'source'
            source.mkdir()
            for name in provision.PI_FILES:
                shutil.copyfile(ROOT / 'pi' / name, source / name)
            for name in provision.TOP_FILES:
                shutil.copyfile(ROOT / name, source / name)
            state = base / 'state'
            outputs, commands = {}, []
            self.last_commands = commands
            maintenance = isinstance(selected, dict) and selected.get('operation') == 'maintain'
            if pending_fresh:
                state.mkdir()
                (state / 'state.json').write_text(json.dumps({'complete': False}))
            installer.SOURCE, installer.STATE = source, state
            original_module = installer.module
            facts = {'supported': True, 'reasons': [], 'architecture': 'aarch64',
                     'model': 'Raspberry Pi 3 Model B Plus', 'userspace_architecture': 'arm64', 'python_bits': 64}
            account = types.SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid(),
                        pw_name='audio', pw_dir=str(base / 'home'))
            def directory(path, mode=None):
                if Path(path).is_relative_to(base):
                    Path(path).mkdir(parents=True, exist_ok=True)
                return Path(path)
            def write(path, text, mode=0o644):
                path = Path(path)
                outputs[str(path)] = (text, mode)
                if path.is_relative_to(state):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text)
            def check_output(args, **kwargs):
                if args[0] == '/usr/sbin/blkid': return 'vfat\n'
                if args[0] == '/usr/bin/lsblk': return 'usb\n' if 'TRAN' in args else '/media/MUSIC\n'
                raise AssertionError(args)
            original_read = Path.read_text
            def read_text(path, *args, **kwargs):
                if str(path) == '/etc/os-release': return 'ID=debian\nVERSION_ID="13"\n'
                if str(path) == '/etc/fstab': return '# fixture\nproc /proc proc defaults 0 0\n'
                if str(path) == '/etc/pi-audio-tray/soloist-user': return owner + '\n'
                if str(path) == '/etc/sudoers.d/pi-audio-tray': return previous_permissions
                return original_read(path, *args, **kwargs)
            original_exists = Path.exists
            def exists(path):
                if str(path).startswith('/dev/disk/by-uuid/'): return True
                if str(path) == '/etc/systemd/system/pi-audio-web.service' and broken_web: return True
                return original_exists(path)
            original_stat, original_copy, original_is_file = Path.stat, shutil.copyfile, Path.is_file
            def is_file(path):
                if str(path) == '/etc/pi-audio-tray/soloist-user' or (previous_permissions and str(path) == '/etc/sudoers.d/pi-audio-tray'):
                    return True
                return original_is_file(path)
            def info(path, **kwargs):
                if str(path) == '/etc/pi-audio-tray/soloist-user' or (previous_permissions and str(path) == '/etc/sudoers.d/pi-audio-tray'):
                    return types.SimpleNamespace(st_uid=0, st_mode=0o100644)
                return original_stat(path, **kwargs)
            def copy(source, destination, **kwargs):
                if Path(source).is_relative_to(base):
                    return original_copy(source, destination, **kwargs)
                Path(destination).write_text('fixture configuration backup')
            def process(args, **kwargs):
                installed = external_cockpit and args[0] == '/usr/bin/dpkg-query'
                return types.SimpleNamespace(returncode=0 if installed else 1, stdout='install ok installed' if installed else '')
            with patch.object(installer.os, 'geteuid', return_value=0), patch.dict(os.environ, {'SUDO_UID': str(os.getuid())}), patch.object(installer.pwd, 'getpwuid', return_value=account), patch.object(installer, 'safe_dir', side_effect=directory), patch.object(installer, 'write', side_effect=write), patch.object(installer, 'run', side_effect=lambda *a, **kw: commands.append(a)), patch.object(installer, 'emit'), patch.object(installer.socket, 'socket'), patch.object(installer, 'module', side_effect=lambda name: types.SimpleNamespace(inventory=lambda: facts) if name == 'setup-probe' else original_module(name)), patch.object(installer.sys, 'argv', ['setup-receiver.py', json.dumps(selected)]), patch.object(installer.subprocess, 'check_output', side_effect=check_output), patch.object(installer.subprocess, 'run', side_effect=process), patch.object(Path, 'read_text', read_text), patch.object(Path, 'exists', exists), patch.object(Path, 'stat', info), patch.object(Path, 'is_file', is_file), patch.object(Path, 'glob', return_value=iter(())), patch.object(shutil, 'copyfile', side_effect=copy):
                try:
                    installer.main()
                finally:
                    installer.LOG.close()
            self.assertTrue(json.loads((state / ('maintenance.json' if maintenance else 'state.json')).read_text())['complete'])
            return outputs, commands

    def test_base_setup_stages_and_narrow_permissions(self):
        files, commands = self.exercise(plan())
        rules, mode = files['/etc/sudoers.d/pi-audio-tray']
        self.assertEqual(mode, 0o440)
        self.assertEqual(len(rules.splitlines()), 4)
        self.assertNotIn('NOPASSWD: ALL', rules)
        self.assertTrue(any(c[:2] == ('visudo', '-cf') for c in commands))
        self.assertIn('hidepid=2', files['/etc/fstab'][0])
        self.assertIn('pairable off', files['/etc/systemd/system/pi-audio-bluetooth-privacy.service'][0])
        self.assertFalse(any('install-music.py' in ' '.join(c) for c in commands))
        self.assertFalse(any('cockpit-ws' in c for c in commands))
        reboot_enable = next(i for i,c in enumerate(commands) if c[:3] == ('systemctl', 'enable', '--now') and 'pi-audio-reboot.timer' in c)
        verify = next(i for i,c in enumerate(commands) if c[:3] == ('systemctl', 'is-active', '--quiet'))
        self.assertGreater(reboot_enable, verify)

    def test_all_components_and_cockpit_listener(self):
        files, commands = self.exercise({**plan(), 'music': True, 'web': True,
                        'cockpit': True, 'usb_uuid': 'ABCD-1234'})
        self.assertIn('ListenStream=127.0.0.1:9090', files['/etc/systemd/system/cockpit.socket.d/pi-audio.conf'][0])
        music = next(c for c in commands if any('install-music.py' in a for a in c))
        self.assertNotIn('--no-web', music)
        self.assertIn('ABCD-1234', music)
        mask = commands.index(('systemctl', 'mask', 'cockpit.socket'))
        install = next(i for i,c in enumerate(commands) if 'cockpit-ws' in c)
        self.assertLess(mask, install)

    def test_mpd_only_omits_web_and_optional_privileges(self):
        files, commands = self.exercise({**plan(), 'music': True, 'usb_uuid': 'ABCD-1234',
                        'web': False, 'shutdown': False, 'updates': False, 'privacy': False})
        music = next(c for c in commands if any('install-music.py' in a for a in c))
        self.assertIn('--no-web', music)
        self.assertNotIn('/etc/fstab', files)
        self.assertNotIn('/etc/apt/apt.conf.d/52pi-audio-upgrades', files)
        self.assertNotIn('poweroff', files['/etc/sudoers.d/pi-audio-tray'][0])


class WizardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_unsupported_inventory_cannot_install_and_window_cannot_close_during_task(self):
        from setup_ui import SetupDialog
        dialog = SetupDialog({'host': 'audio-pi.local', 'username': 'audio'})
        dialog.inventory({'supported': False, 'reasons': ['Requires Debian / Raspberry Pi OS 13 Trixie']})
        self.assertFalse(dialog.checked)
        dialog.worker = object()
        dialog.reject()
        self.assertEqual(dialog.result(), 0)
        dialog.worker = None
        dialog.close()
        dialog.deleteLater()
        self.app.processEvents()

    def test_no_web_without_mpd_and_remote_markup_is_plain(self):
        from setup_ui import SetupDialog
        dialog = SetupDialog({'host': '<b>audio</b>', 'username': 'audio'})
        self.assertFalse(dialog.web.isEnabled())
        dialog.music.setChecked(True)
        self.assertTrue(dialog.web.isEnabled())
        dialog.web.setChecked(True)
        dialog.music.setChecked(False)
        self.assertFalse(dialog.web.isChecked())
        from PySide6.QtCore import Qt
        self.assertEqual(dialog.target.textFormat(), Qt.TextFormat.PlainText)
        dialog.close()
        dialog.deleteLater()
        self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
