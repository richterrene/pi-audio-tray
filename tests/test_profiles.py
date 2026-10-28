import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import core
import profiles


class PiProfiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {'XDG_CONFIG_HOME': self.temp.name})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def test_existing_configuration_migrates_without_changing_connection_or_host_keys(self):
        root = core.config_dir()
        config = {**core.DEFAULTS, 'host': 'audio-pi.example', 'username': 'audio'}
        core.atomic_private_write(root / 'config.json', json.dumps(config).encode())
        (root / 'known_hosts').write_text('existing host verification data')
        self.assertFalse((root / 'profiles.json').exists())
        self.assertEqual(core.load_config(), config)
        identifier = profiles.add('Studio')
        self.assertEqual(core.load_config(), config)
        self.assertEqual(profiles.load()['profiles'][0]['connection']['host'], config['host'])
        self.assertEqual((root / 'known_hosts').read_text(), 'existing host verification data')
        self.assertNotEqual(identifier, 'default')

    def test_connections_stay_separate_and_language_is_global(self):
        first = {**core.DEFAULTS, 'host': 'first-pi.example', 'username': 'first', 'key_file': '~/.ssh/first'}
        core.save_config(first)
        second_id = profiles.add('Second')
        second = profiles.activate(second_id)
        self.assertEqual(second['host'], '')
        second.update(host='second-pi.example', username='second', key_file='~/.ssh/second', language='de')
        core.save_config(second)
        profiles.activate('default')
        self.assertEqual(core.load_config(), {**first, 'language': 'de'})
        self.assertEqual(core.load_legacy_config(), core.load_config())
        profiles.activate(second_id)
        self.assertEqual(core.load_config(), second)
        if os.name != 'nt':
            self.assertEqual((core.config_dir() / 'profiles.json').stat().st_mode & 0o777, 0o600)

    def test_rename_and_remove_do_not_remove_key_files_or_active_profile(self):
        core.save_config(core.DEFAULTS)
        identifier = profiles.add('Second')
        profiles.rename(identifier, 'Office')
        self.assertIn('Office', [row['name'] for row in profiles.load()['profiles']])
        with self.assertRaises(ValueError): profiles.remove('default')
        profiles.remove(identifier)
        self.assertEqual(len(profiles.load()['profiles']), 1)

    def test_invalid_duplicate_and_control_character_names_do_not_change_file(self):
        core.save_config(core.DEFAULTS)
        before = (core.config_dir() / 'profiles.json').read_bytes()
        for name in ('', ' ', 'My Pi', 'MY PI', 'Bad\nName', 'Bidi\u202eName', 'x' * 81):
            with self.subTest(name=name), self.assertRaises(ValueError): profiles.add(name)
            self.assertEqual((core.config_dir() / 'profiles.json').read_bytes(), before)

    def test_unknown_profile_and_corrupt_registry_fail_without_overwriting(self):
        core.save_config(core.DEFAULTS)
        with self.assertRaises(ValueError): profiles.activate('untrusted')
        path = core.config_dir() / 'profiles.json'
        path.write_text('{broken')
        with self.assertRaises(ValueError): core.save_config(core.DEFAULTS)
        self.assertEqual(path.read_text(), '{broken')
        self.assertEqual(core.load_config(), core.DEFAULTS)

    def test_failed_activation_never_changes_authoritative_active_pi(self):
        core.save_config({**core.DEFAULTS, 'host': 'first-pi.example', 'username': 'first'})
        identifier = profiles.add('Second')
        with patch('profiles.save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): profiles.activate(identifier)
        self.assertEqual(profiles.load()['active'], 'default')
        self.assertEqual(core.load_config()['host'], 'first-pi.example')

    @unittest.skipIf(os.name == 'nt', 'Symlink creation needs Windows privileges')
    def test_linked_registry_is_rejected(self):
        target = Path(self.temp.name) / 'outside'
        target.write_text('untouched')
        (core.config_dir() / 'profiles.json').symlink_to(target)
        with self.assertRaises((OSError, ValueError)): profiles.load()
        with self.assertRaises((OSError, ValueError)): profiles.add('Second')
        self.assertEqual(target.read_text(), 'untouched')


if __name__ == '__main__': unittest.main()
