"""Cached versus fresh checks, exact root operations and reboot-policy safety."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import updates

ROOT = Path(__file__).resolve().parents[1]


def inventory(**changes):
    return {'protocol': 1, 'packages': [{'name': 'example', 'current': '1', 'candidate': '2'}],
            'total': 1, 'truncated': False, 'checked_at': None, 'installed_at': None,
            'reboot_required': None, 'control_installed': True, 'policy_configured': True, **changes}


class Protocol(unittest.TestCase):
    def test_update_inventory_is_bounded_and_rejects_unsafe_names(self):
        self.assertEqual(updates.validate(inventory()), inventory())
        for value in (inventory(total=True), inventory(total=0), inventory(reboot_required=1),
                      inventory(checked_at=-1), inventory(packages=[{'name': '--option', 'current': '1', 'candidate': '2'}]),
                      inventory(packages=[{'name': 'example', 'current': '1', 'candidate': 'x\n'}]), inventory(truncated=True)):
            with self.subTest(value=value), self.assertRaises(ValueError): updates.validate(value)

    def test_read_only_check_uses_stdin_and_always_closes_connection(self):
        client = Mock()
        with patch.object(updates, 'connect', return_value=client), patch.object(updates, 'run', return_value=json.dumps(inventory())) as run:
            updates.check({})
        self.assertEqual(run.call_args.args[1], '/usr/bin/python3 -I - --status')
        client.close.assert_called_once()

    def test_only_two_exact_installed_actions_can_run(self):
        for operation in ('check', 'install'):
            client, channel = Mock(), Mock()
            client.get_transport.return_value.open_session.return_value = channel
            with patch.object(updates, 'connect', return_value=client), patch.object(updates.provision, 'read_progress'):
                updates.operate({}, operation, print)
            channel.exec_command.assert_called_once_with('/usr/bin/sudo -n /usr/local/lib/pi-audio-tray/pi-system-updates ' + operation)
            client.close.assert_called_once()
        with patch.object(updates, 'connect') as connect, self.assertRaises(ValueError): updates.operate({}, 'install;reboot', print)
        connect.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux', 'Linux APT installer fixture')
class AptOperations(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location('system_updates_test', ROOT / 'pi/system-updates.py')
        value = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(value)
        return value

    def test_cached_parser_keeps_held_candidates_and_marks_truncated_list(self):
        module = self.module()
        lines = '\n'.join(f'pkg{i}/stable 2 arm64 [upgradable from: 1]' for i in range(300))
        with patch.object(module.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0, stdout=lines)) as run:
            values, total = module.pending()
        self.assertEqual((len(values), total), (256, 300))
        self.assertEqual(run.call_args.args[0], ['/usr/bin/apt', 'list', '--upgradable'])
        self.assertEqual(run.call_args.kwargs['env']['LC_ALL'], 'C')

    def exercise(self, operation, reboot_config="PI_AUDIO_REBOOT='false'"):
        module = self.module()
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            module.STATE, module.STATUS, module.SETUP_STATE = base / 'state', base / 'state/status.json', base / 'setup'
            owner = '/etc/pi-audio-tray/soloist-user'
            original_read, original_stat, original_file = Path.read_text, Path.stat, Path.is_file
            def read(path, *args, **kwargs):
                if str(path) == owner: return 'audio\n'
                if str(path) == '/etc/os-release': return 'ID=debian\nVERSION_ID="13"\n'
                return original_read(path, *args, **kwargs)
            def info(path, **kwargs):
                return types.SimpleNamespace(st_uid=0, st_mode=0o100644) if str(path) == owner else original_stat(path, **kwargs)
            def is_file(path):
                if str(path) in (owner, '/etc/apt/apt.conf.d/52pi-audio-upgrades'): return True
                if str(path) in ('/usr/sbin/needrestart', '/var/lib/apt/periodic/update-success-stamp'): return False
                return original_file(path)
            commands = []
            def run(args, **kwargs):
                commands.append(args)
                return types.SimpleNamespace(returncode=0, stdout='')
            with patch.object(module.os, 'geteuid', return_value=0), \
                 patch.object(module.pwd, 'getpwuid', return_value=types.SimpleNamespace(pw_uid=1000, pw_name='audio')), \
                 patch.object(Path, 'read_text', read), patch.object(Path, 'stat', info), patch.object(Path, 'is_file', is_file), \
                 patch.object(module, 'directory', side_effect=lambda path, mode: path.mkdir(parents=True, exist_ok=True)), \
                 patch.object(module.subprocess, 'run', side_effect=run), \
                 patch.object(module.subprocess, 'check_output', return_value=reboot_config), patch.object(module, 'emit'):
                try:
                    module.operate(operation)
                finally:
                    self.commands = commands
            return json.loads(module.STATUS.read_text())

    def test_refresh_does_not_install_and_install_never_requests_reboot(self):
        self.exercise('check')
        self.assertFalse(any('unattended-upgrade' in command[0] for command in self.commands))
        result = self.exercise('install')
        self.assertIsInstance(result['installed_at'], int)
        self.assertTrue(any(command == ['/usr/bin/unattended-upgrade', '--verbose'] for command in self.commands))
        self.assertFalse(any(any('reboot' in argument or 'dist-upgrade' in argument for argument in command) for command in self.commands))

    def test_conflicting_auto_reboot_policy_blocks_installation(self):
        with self.assertRaises(ValueError): self.exercise('install', "PI_AUDIO_REBOOT='true'")
        self.assertFalse(any('unattended-upgrade' in command[0] for command in self.commands))


if __name__ == '__main__': unittest.main()
