import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('reboot_check',
    Path(__file__).resolve().parents[1] / 'pi/reboot-if-needed.py')
reboot_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reboot_check)


class ConditionalReboot(unittest.TestCase):
    def check(self, kernel, check_only=False, busy=False):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            if args[0].endswith('needrestart'):
                return SimpleNamespace(stdout=f'NEEDRESTART-KSTA: {kernel}\n', returncode=0)
            return SimpleNamespace(returncode=0 if busy else 3)
        with patch.object(reboot_check.Path, 'exists', return_value=False), \
             patch.object(reboot_check.subprocess, 'run', side_effect=run), \
             patch.object(reboot_check.sys, 'argv', ['check'] + (['--check-only'] if check_only else [])):
            reboot_check.main()
        return calls

    def test_dry_run_never_reboots_even_if_kernel_is_outdated(self):
        self.assertEqual(len(self.check('2', check_only=True)), 1)

    def test_current_or_unknown_kernel_does_not_trigger_reboot(self):
        for kernel in ('0', '1'):
            self.assertEqual(len(self.check(kernel)), 1)

    def test_running_update_prevents_reboot(self):
        self.assertEqual(len(self.check('3', busy=True)), 2)

    def test_required_reboot_is_requested_only_when_updates_are_idle(self):
        self.assertEqual(self.check('2')[-1], ['/usr/bin/systemctl', 'reboot'])


if __name__ == '__main__':
    unittest.main()
