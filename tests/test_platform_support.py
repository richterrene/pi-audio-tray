import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import core
from platform_support import lock_instance, open_instance_lock, secure_directory, windows_descriptor, windows_user_sid, windows_allowed_sids


class DesktopStorage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'XDG_CONFIG_HOME': self.temp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_second_process_cannot_take_the_instance_lock(self):
        path = core.config_dir() / 'instance.lock'
        first = open_instance_lock(path)
        lock_instance(first)
        code = '''import sys
from pathlib import Path
from platform_support import open_instance_lock,lock_instance
f=open_instance_lock(Path(sys.argv[1]))
try:
 lock_instance(f)
except BlockingIOError:
 sys.exit(23)
sys.exit(1)
'''
        try:
            result = subprocess.run([sys.executable, '-c', code, str(path)], cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 23, result.stderr.decode(errors='replace'))
        finally:
            first.close()
        with open_instance_lock(path) as second:
            lock_instance(second)

    def test_windows_uses_per_user_appdata_without_xdg_override(self):
        with patch.dict(os.environ, {'APPDATA': self.temp.name}, clear=True), patch.object(core.sys, 'platform', 'win32'):
            self.assertEqual(core.config_dir(), Path(self.temp.name) / 'Pi Audio Tray')

    @unittest.skipUnless(os.name == 'nt', 'Requires native Windows security descriptors')
    def test_windows_directory_and_files_have_private_protected_dacl(self):
        root = core.config_dir()
        core.save_config(core.DEFAULTS)
        sid = windows_user_sid()
        for path in (root, root / 'config.json'):
            descriptor = windows_descriptor(path)
            self.assertIn('D:P', descriptor)
            self.assertCountEqual(windows_allowed_sids(path), [sid, 'S-1-5-18'])

    @unittest.skipUnless(os.name == 'nt', 'Requires native Windows reparse points')
    def test_windows_junction_configuration_is_rejected(self):
        target = Path(self.temp.name) / 'target'
        target.mkdir()
        junction = Path(self.temp.name) / 'pi-audio-tray'
        result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(junction), str(target)], capture_output=True)
        self.assertEqual(result.returncode, 0)
        try:
            with self.assertRaises(OSError): core.config_dir()
        finally:
            os.rmdir(junction)


if __name__ == '__main__': unittest.main()
