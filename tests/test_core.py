import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import paramiko
from core import CheckHostKey, UnknownHost, load_config, save_config, trust_host, validate_config


class PrivacyAndHostVerification(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'XDG_CONFIG_HOME': self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_configuration_is_private_and_separate_from_source(self):
        config = load_config()
        self.assertEqual(config['host'], '')
        config.update(host='audio-pi.example', username='audio', key_file='~/.ssh/example-key')
        save_config(config)
        path = Path(self.temp.name) / 'pi-audio-tray/config.json'
        if os.name != 'nt':
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(load_config(), config)
        self.assertNotIn('password', path.read_text())

    def test_invalid_connection_settings_are_rejected(self):
        for changes in ({'port': 0}, {'poll_seconds': 1}, {'host': 'pi\nother'}):
            with self.assertRaises(ValueError):
                validate_config({**load_config(), **changes})

    def test_macos_uses_application_support_without_an_xdg_override(self):
        import core
        with patch.dict(os.environ, {}, clear=True), patch.object(core.sys, 'platform', 'darwin'), \
             patch.object(core.Path, 'home', return_value=Path(self.temp.name)):
            self.assertEqual(core.config_dir(), Path(self.temp.name) / 'Library/Application Support/Pi Audio Tray')

    def test_unknown_host_requires_explicit_approval(self):
        key = paramiko.RSAKey.generate(2048)
        with self.assertRaises(UnknownHost) as raised:
            CheckHostKey().missing_host_key(None, 'audio-pi.example', key)
        path = Path(self.temp.name) / 'pi-audio-tray/known_hosts'
        self.assertFalse(path.exists())
        self.assertTrue(raised.exception.fingerprint.startswith('SHA256:'))
        trust_host(raised.exception)
        hosts = paramiko.HostKeys(str(path))
        self.assertTrue(hosts.check('audio-pi.example', key))
        self.assertFalse(hosts.check('audio-pi.example', paramiko.RSAKey.generate(2048)))
        if os.name != 'nt':
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
