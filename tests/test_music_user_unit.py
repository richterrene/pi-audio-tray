"""Avoid duplicate package myMPD user daemons without touching other accounts."""
import importlib.util
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch

spec=importlib.util.spec_from_file_location('music_install',Path(__file__).resolve().parents[1]/'pi/install-music.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class UserUnitTests(unittest.TestCase):
    def setUp(self):
        self.user=types.SimpleNamespace(pw_name='audio',pw_uid=1234)

    def test_masks_only_selected_user_and_preserves_isolated_server(self):
        with patch.object(m,'run') as run, patch.object(m.subprocess,'run',return_value=Mock(returncode=3)) as process:
            m.mask_packaged_user_mympd(self.user)
        commands=[call.args for call in run.call_args_list]
        self.assertEqual(commands[-1],('runuser','-u','audio','--','/usr/bin/systemctl','--user','mask','mympd.service'))
        self.assertFalse(any('--global' in command or 'pi-audio-mympd.service' in command for command in commands))
        env=run.call_args.kwargs['env']
        self.assertEqual(env['DBUS_SESSION_BUS_ADDRESS'],'unix:path=/run/user/1234/bus')
        self.assertEqual(process.call_args.args[0][-2:],['reset-failed','mympd.service'])

    def test_existing_active_user_server_requires_manual_integration(self):
        with patch.object(m,'run') as run, patch.object(m.subprocess,'run',return_value=Mock(returncode=0)):
            with self.assertRaisesRegex(RuntimeError,'already active'):
                m.mask_packaged_user_mympd(self.user)
        self.assertFalse(any('mask' in call.args for call in run.call_args_list))

    def test_missing_unit_on_fresh_install_is_masked_without_stop_failure(self):
        with patch.object(m,'run') as run, patch.object(m.subprocess,'run',side_effect=[Mock(returncode=4),Mock(returncode=1)]):
            m.mask_packaged_user_mympd(self.user)
        self.assertNotIn('--now',run.call_args.args)

if __name__=='__main__':unittest.main()
