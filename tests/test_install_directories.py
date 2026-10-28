"""Real directory mode checks under the same private umask as Pi installers."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(sys.platform == 'linux', 'Linux installer directory modes')
class DirectoryModes(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / 'pi/setup-receiver.py'
        spec = importlib.util.spec_from_file_location('directory_installer_test', source)
        self.installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.installer)

    def exercise(self, private):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            target = base / 'public' / 'units' / 'user'
            original_stat = Path.stat
            def root_stat(path, **kwargs):
                result = original_stat(path, **kwargs)
                values = list(result)
                values[4] = 0  # Model root ownership; never require test sudo.
                if path == Path('/tmp'):
                    values[0] &= ~0o022
                return os.stat_result(values)
            previous = os.umask(0o077)
            try:
                with patch.object(Path, 'stat', root_stat):
                    self.installer.safe_dir(target, 0o700 if private else None)
            finally:
                os.umask(previous)
            self.assertEqual(base.stat().st_mode & 0o777, 0o700)
            for directory in (base / 'public', base / 'public/units'):
                self.assertEqual(directory.stat().st_mode & 0o777, 0o755)
            self.assertEqual(target.stat().st_mode & 0o777, 0o700 if private else 0o755)

    def test_public_unit_and_binary_directories_survive_private_umask(self):
        self.exercise(False)

    def test_private_state_target_and_existing_private_ancestors_stay_private(self):
        self.exercise(True)

    def test_repair_existing_public_parents_is_explicit_and_allowlisted(self):
        with patch.object(self.installer, 'safe_dir') as create:
            self.installer.public_dir('/usr/local/lib/systemd/user')
            self.assertEqual([(str(c.args[0]), c.args[1]) for c in create.call_args_list], [
                ('/usr/local/lib/systemd', 0o755), ('/usr/local/lib/systemd/user', 0o755)])
            create.reset_mock()
            for value in ('/var/lib/pi-audio-setup', '/usr/local/lib/systemd/../private'):
                with self.assertRaises(ValueError):
                    self.installer.public_dir(value)
            create.assert_not_called()


if __name__ == '__main__':
    unittest.main()
