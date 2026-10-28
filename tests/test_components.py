"""Per-Pi inventory validation and additive maintenance regressions."""
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import components
import provision
from setup_plan import validate_maintenance
import test_provision


def request(*selected, **changes):
    options = {key: key in selected or (key == 'music' and 'web' in selected)
               for key in ('music', 'web', 'updates', 'cockpit', 'shutdown', 'privacy')}
    options.update(hostname='audio-pi.local', usb_uuid='ABCD-1234' if options['music'] else '')
    options.update(changes)
    return {'operation': 'maintain', 'components': list(selected), 'options': options}


def inventory(**changes):
    rows = [{'id': name, 'action': components.MAPPING[name], 'installed': True,
             'configured': True, 'running': True, 'managed': True, 'details': 'fixture'} for name in components.IDS]
    return {'protocol': 1, 'managed': True, 'supported': True, 'rows': rows,
            'usb_uuid': '', 'hostname': 'audio-pi.local', 'model': 'Raspberry Pi fixture',
            'architecture': 'arm64', 'os': 'Debian 13', 'disks': {'blockdevices': []}, **changes}


class Plans(unittest.TestCase):
    def test_only_additive_known_actions_and_explicit_dependencies(self):
        self.assertEqual(validate_maintenance(request('updates'))['components'], ['updates'])
        self.assertTrue(validate_maintenance(request('web'))['options']['music'])
        for value in (request(), request('uninstall'), request('updates', 'updates'),
                      {**request('updates'), 'operation': 'remove'},
                      request('web', music=False), request('updates', privacy=True),
                      {**request('updates'), 'unexpected': True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_maintenance(value)

    def test_inventory_rejects_duplicate_ids_bad_mappings_and_unbounded_disks(self):
        self.assertEqual(components.validate(inventory()), inventory())
        bad = []
        v = inventory(); v['rows'][0]['action'] = 'shutdown'; bad.append(v)
        v = inventory(); v['rows'][0]['installed'] = 1; bad.append(v)
        v = inventory(); v['rows'][0]['details'] = 'x\nsecret'; bad.append(v)
        v = inventory(); v['rows'][0]['id'] = v['rows'][1]['id']; bad.append(v)
        v = inventory(); v['disks']['blockdevices'] = [{}] * 257; bad.append(v)
        v = inventory(); v['disks']['blockdevices'] = [{'children': 'not a list'}]; bad.append(v)
        for v in bad:
            with self.subTest(value=v), self.assertRaises(ValueError): components.validate(v)

    def test_inventory_is_read_only_source_over_stdin_and_connection_closes(self):
        client = Mock()
        with patch.object(components, 'connect', return_value=client), patch.object(components, 'run', return_value=json.dumps(inventory())) as run:
            components.check({})
        self.assertEqual(run.call_args.args[1], '/usr/bin/python3 -I -')
        self.assertIn('Never read keys or passwords', run.call_args.kwargs['input_data'])
        client.close.assert_called_once()

    def test_bad_maintenance_plan_never_connects(self):
        with patch.object(provision, 'administrator_install') as install, self.assertRaises(ValueError):
            components.maintain({}, request('remove'), 'example', print)
        install.assert_not_called()


@unittest.skipUnless(os.sys.platform == 'linux', 'Linux administrator fixtures')
class MaintenanceStages(unittest.TestCase):
    exercise = test_provision.InstallerStages.exercise

    def test_optional_repair_does_not_reinstall_core_or_unselected_services(self):
        files, commands = self.exercise(request('updates'))
        self.assertIn('/etc/apt/apt.conf.d/52pi-audio-upgrades', files)
        self.assertNotIn('/etc/sudoers.d/pi-audio-tray', files)
        self.assertNotIn('/etc/fstab', files)
        self.assertNotIn('/etc/pi-audio-tray/soloist-user', files)
        self.assertFalse(any('cockpit-ws' in command or 'bluez' in command for command in commands))
        self.assertFalse(any('install-music.py' in ' '.join(command) for command in commands))

    def test_core_repair_preserves_existing_shutdown_and_other_sudo_rules(self):
        old = 'audio ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block poweroff\n'
        files, commands = self.exercise(request('receiver'), previous_permissions=old)
        rules, mode = files['/etc/sudoers.d/pi-audio-tray']
        self.assertIn(old.strip(), rules)
        self.assertEqual(rules.count(old.strip()), 1)
        self.assertEqual(mode, 0o440)
        self.assertTrue(any('restart' in command and 'pipewire.service' in command for command in commands))
        self.assertFalse(any('api-key' in filename for filename in files))

    def test_mpd_only_repair_leaves_web_configuration_and_login_alone(self):
        files, commands = self.exercise(request('music'))
        music = next(command for command in commands if any('install-music.py' in value for value in command))
        self.assertIn('--no-web', music)
        self.assertNotIn('/etc/pi-audio-tray/music/nginx.conf', files)
        self.assertFalse(any('pi-audio-web.service' in command for command in commands))

    def test_pending_fresh_install_wrong_owner_and_missing_web_secrets_fail_before_packages(self):
        for value, changes in ((request('updates'), {'pending_fresh': True}),
                               (request('updates'), {'owner': 'another'}),
                               (request('web'), {'broken_web': True}),
                               (request('cockpit'), {'external_cockpit': True})):
            with self.subTest(value=value, changes=changes), self.assertRaises(ValueError):
                self.exercise(value, **changes)
            self.assertFalse(any('apt-get' in command[0] for command in self.last_commands))


class Dialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_profiles_are_captured_and_repair_is_opt_in_without_periodic_polling(self):
        from component_ui import ComponentsDialog
        from PySide6.QtCore import QTimer, Qt
        config = {'host': 'audio-pi.local', 'username': 'audio'}
        dialog = ComponentsDialog(config)
        config['host'] = 'different-pi.local'
        self.assertEqual(dialog.config['host'], 'audio-pi.local')
        self.assertEqual(dialog.findChildren(QTimer), [])
        dialog.show_inventory(inventory())
        dialog.controls()
        self.assertFalse(dialog.repair_button.isEnabled())
        dialog.table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        dialog.table.item(1, 0).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(dialog.selected(), ['receiver'])
        self.assertTrue(dialog.repair_button.isEnabled())
        dialog.deleteLater()

    def test_unmanaged_receiver_and_foreign_component_are_not_selectable(self):
        from component_ui import ComponentsDialog
        from PySide6.QtCore import Qt
        dialog = ComponentsDialog({'host': 'audio-pi.local', 'username': 'audio'})
        value = inventory(managed=False)
        dialog.show_inventory(value)
        for index in range(len(value['rows'])):
            self.assertFalse(dialog.table.item(index, 0).flags() & Qt.ItemFlag.ItemIsEnabled)
        dialog.worker = Mock()
        dialog.reject()
        self.assertEqual(dialog.result(), 0)
        dialog.worker = None
        dialog.reject()
        dialog.deleteLater()


if __name__ == '__main__': unittest.main()
