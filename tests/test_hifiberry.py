"""Vendor receivers use SSH monitoring without replacing vendor configuration."""
import json
import unittest
from unittest.mock import MagicMock, Mock, patch

import core
import provision


class HiFiBerryReceiver(unittest.TestCase):
    def test_poll_uses_bundled_unprivileged_probe_without_installed_helper(self):
        status = {'protocol': 1, 'receiver': 'hifiberry', 'services': {'soloist': 'active'},
                  'adapter': {}, 'devices': [], 'spotify_backend': 'soloist'}
        client = Mock()
        with patch.object(core, 'connect', return_value=client), patch.object(core, 'run',
                side_effect=['hifiberry\n', json.dumps(status)]) as run:
            result = core.poll({}, detailed=False)
        self.assertEqual(result['receiver'], 'hifiberry')
        self.assertFalse(result['_detailed'])
        self.assertEqual(run.call_args.args[1], '/usr/bin/python3 -I - health')
        self.assertIn('def collect_stats', run.call_args.kwargs['input_data'])
        client.close.assert_called_once()

    def test_vendor_receiver_cannot_be_overwritten_by_helper_or_admin_installer(self):
        for operation in (lambda: core.install_helper({}), lambda: provision.administrator_install(
                {}, {}, '', lambda _: None, 'setup-receiver.py', (), ())):
            client = MagicMock()
            with patch.object(core, 'connect', return_value=client), patch.object(provision, 'connect', return_value=client), \
                    patch.object(core, 'run', return_value='hifiberry\n'), self.assertRaisesRegex(RuntimeError, 'HiFiBerry'):
                operation()
            client.open_sftp.return_value.__enter__.return_value.mkdir.assert_not_called()
            client.open_sftp.return_value.__enter__.return_value.file.assert_not_called()
            client.close.assert_called_once()

    def test_unknown_receiver_type_is_rejected(self):
        with self.assertRaises(ValueError):
            core.validate_status({'protocol': 1, 'services': {}, 'adapter': {}, 'devices': [], 'receiver': 'unexpected'})

    def test_soloist_update_uses_vendor_user_unit(self):
        client = Mock()
        with patch.object(core, 'connect', return_value=client), patch.object(core, 'run', return_value='hifiberry\n') as run:
            core.update_soloist({})
        self.assertEqual(run.call_args.args[1], 'systemctl --user start soloist-update.service')


if __name__ == '__main__':
    unittest.main()
