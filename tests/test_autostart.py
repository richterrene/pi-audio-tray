"""Autostart registration uses only temporary files or a mocked Windows key."""
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import platform_support as desktop


class Autostart(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.executable = self.root / 'Pi Audio Tray.AppImage'
        self.executable.touch()
        self.environment = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.root), 'XDG_CONFIG_DIRS': '', 'APPIMAGE': str(self.executable)})
        self.environment.start()
        self.platform = patch.object(desktop.sys, 'platform', 'linux')
        self.platform.start()

    def tearDown(self):
        self.platform.stop()
        self.environment.stop()
        self.temp.cleanup()

    def test_linux_roundtrip_and_disable_preserves_unrelated_files(self):
        self.assertFalse(desktop.autostart_enabled())
        desktop.set_autostart(True)
        path = desktop.autostart_path()
        unrelated = path.with_name('another-app.desktop')
        unrelated.write_text('untouched')
        self.assertTrue(desktop.autostart_enabled())
        entry = path.read_text()
        self.assertIn('--autostart', entry)
        self.assertNotIn('--show', entry)
        self.assertNotIn('/.mount_', entry)
        desktop.set_autostart(False)
        desktop.set_autostart(False)
        self.assertFalse(desktop.autostart_enabled())
        self.assertEqual(unrelated.read_text(), 'untouched')

    def test_hidden_entry_remains_disabled(self):
        desktop.set_autostart(True)
        with desktop.autostart_path().open('a') as output:
            output.write('Hidden=true\n')
        self.assertFalse(desktop.autostart_enabled())

    def test_disabling_masks_a_system_entry_instead_of_exposing_it(self):
        system = self.root / 'system/autostart/pi-audio-tray.desktop'
        system.parent.mkdir(parents=True)
        system.write_text('[Desktop Entry]\nType=Application\nExec=/example/app\n')
        with patch.dict(os.environ, {'XDG_CONFIG_DIRS': str(system.parent.parent)}):
            self.assertTrue(desktop.autostart_enabled())
            desktop.set_autostart(False)
            self.assertFalse(desktop.autostart_enabled())
            self.assertIn('Hidden=true', desktop.autostart_path().read_text())
            self.assertTrue(system.exists())
            desktop.set_autostart(True)
            self.assertTrue(desktop.autostart_enabled())

    @unittest.skipUnless(shutil.which('desktop-file-validate'), 'Requires freedesktop desktop validator')
    def test_generated_entry_passes_system_desktop_validator(self):
        desktop.set_autostart(True)
        result = subprocess.run(['desktop-file-validate', str(desktop.autostart_path())], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_nonfile_and_invalid_plist_fail_without_reading_or_crashing(self):
        path = desktop.autostart_path()
        path.mkdir(parents=True)
        with self.assertRaises(ValueError):
            desktop.autostart_enabled()
        with patch.object(desktop.sys, 'platform', 'darwin'), patch.object(Path, 'home', return_value=self.root):
            path = desktop.autostart_path()
            path.parent.mkdir(parents=True)
            path.write_bytes(plistlib.dumps(['not a dictionary']))
            with self.assertRaises(ValueError):
                desktop.autostart_enabled()

    def test_reenable_points_to_current_version(self):
        desktop.set_autostart(True)
        other = self.root / 'new-version.AppImage'
        other.touch()
        with patch.dict(os.environ, {'APPIMAGE': str(other)}):
            desktop.set_autostart(True)
        self.assertIn('new-version.AppImage', desktop.autostart_path().read_text())

    def test_command_uses_outer_appimage_instead_of_frozen_mount(self):
        with patch.object(desktop.sys, 'frozen', True, create=True), patch.object(desktop.sys, 'executable', '/tmp/.mount_example/usr/bin/tray'):
            self.assertEqual(desktop.autostart_command(), [str(self.executable), '--autostart'])

    def test_missing_relative_or_newline_path_is_rejected(self):
        for value in ('relative.AppImage', str(self.root / 'missing'), str(self.executable) + '\nExec=bad'):
            with self.subTest(value=value), patch.dict(os.environ, {'APPIMAGE': value}):
                with self.assertRaises(ValueError):
                    desktop.set_autostart(True)
        self.assertFalse(desktop.autostart_enabled())

    def test_desktop_escaping_has_no_shell_and_doubles_field_codes(self):
        self.assertEqual(desktop._desktop_argument('/path/a%f $x`y"z\\q'),
                         '"/path/a%%f \\\\$x\\\\`y\\\\"z\\\\\\\\q"')

    @unittest.skipIf(os.name == 'nt', 'Symlink creation requires special Windows privilege')
    def test_symlink_entry_cannot_overwrite_or_remove_its_target(self):
        path = desktop.autostart_path()
        path.parent.mkdir()
        target = self.root / 'target'
        target.write_text('keep')
        path.symlink_to(target)
        for operation in (lambda: desktop.autostart_enabled(), lambda: desktop.set_autostart(True), lambda: desktop.set_autostart(False)):
            with self.assertRaises(OSError):
                operation()
        self.assertEqual(target.read_text(), 'keep')

    def test_macos_plist_roundtrip_without_loading_or_stopping_current_app(self):
        with patch.object(desktop.sys, 'platform', 'darwin'), patch.object(Path, 'home', return_value=self.root), \
             patch.object(desktop, 'autostart_command', return_value=[str(self.executable), '--autostart']):
            desktop.set_autostart(True)
            data = plistlib.loads(desktop.autostart_path().read_bytes())
            self.assertEqual(data['ProgramArguments'], [str(self.executable), '--autostart'])
            self.assertTrue(data['RunAtLoad'])
            self.assertNotIn('KeepAlive', data)
            self.assertTrue(desktop.autostart_enabled())
            desktop.set_autostart(False)
            self.assertFalse(desktop.autostart_enabled())

    def test_windows_registry_roundtrip_is_per_user_and_removes_only_own_value(self):
        values = {}
        registry = types.SimpleNamespace(HKEY_CURRENT_USER=1, KEY_SET_VALUE=2, REG_SZ=3)
        key = Mock()
        key.__enter__ = Mock(return_value=key)
        key.__exit__ = Mock(return_value=False)
        registry.OpenKey = Mock(return_value=key)
        registry.CreateKeyEx = Mock(return_value=key)
        def query(_key, name):
            if name not in values:
                raise FileNotFoundError(name)
            return values[name], 3
        registry.QueryValueEx = Mock(side_effect=query)
        registry.SetValueEx = Mock(side_effect=lambda _k, name, _r, _t, value: values.update({name: value}))
        registry.DeleteValue = Mock(side_effect=lambda _k, name: values.pop(name))
        with patch.object(desktop.sys, 'platform', 'win32'), patch.dict('sys.modules', {'winreg': registry}), \
             patch.object(desktop, 'autostart_command', return_value=['C:\\Apps\\Pi Audio Tray.exe', '--autostart']):
            self.assertFalse(desktop.autostart_enabled())
            desktop.set_autostart(True)
            self.assertTrue(desktop.autostart_enabled())
            self.assertEqual(values[desktop.RUN_NAME], '"C:\\Apps\\Pi Audio Tray.exe" --autostart')
            desktop.set_autostart(False)
            self.assertFalse(desktop.autostart_enabled())
        registry.CreateKeyEx.assert_called_once_with(1, desktop.RUN_KEY, 0, 2)
        registry.DeleteValue.assert_called_once_with(key, desktop.RUN_NAME)


if __name__ == '__main__':
    unittest.main()
