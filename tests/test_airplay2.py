"""Pinned-source integrity, service boundaries and AirPlay 2 runtime controls."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import audio
import pi_audio

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load('airplay_builder_test', 'pi/airplay-build.py')


class Sources(unittest.TestCase):
    def test_bad_checksum_is_rejected_before_unpacking(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError): builder.extract(b'untrusted', 'nqptp', Path(folder))
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_source_archive_links_traversal_and_special_files_are_rejected(self):
        for name, kind in (('../escape', tarfile.REGTYPE), ('nqptp-1/file', tarfile.SYMTYPE),
                           ('/absolute', tarfile.REGTYPE), ('nqptp-1/file', tarfile.FIFOTYPE)):
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode='w:gz') as archive:
                item = tarfile.TarInfo(name)
                item.type = kind
                archive.addfile(item)
            blob = data.getvalue()
            with tempfile.TemporaryDirectory() as folder, patch.dict(builder.SOURCES, {'nqptp': ('1', hashlib.sha256(blob).hexdigest())}):
                with self.assertRaises(ValueError): builder.extract(blob, 'nqptp', Path(folder))
                self.assertEqual(list(Path(folder).iterdir()), [])

    def test_build_refuses_root_before_network_or_execution(self):
        if not hasattr(os, 'geteuid'):
            self.skipTest('POSIX build account')
        with patch.object(builder.os, 'geteuid', return_value=0), patch.object(builder.urllib.request, 'urlopen') as download, self.assertRaises(ValueError):
            builder.build(Path('/unused'))
        download.assert_not_called()

    def test_ui_install_mode_is_strict_and_does_not_put_password_in_plan(self):
        with patch.object(audio.provision, 'administrator_install') as install:
            audio.install({}, 'example-sudo', print, 'airplay2')
        self.assertEqual(install.call_args.args[1], {'mode': 'airplay2'})
        with patch.object(audio.provision, 'administrator_install') as install, self.assertRaises(ValueError):
            audio.install({}, 'example-sudo', print, 'airplay2;id')
        install.assert_not_called()


@unittest.skipIf(sys.platform == 'win32', 'POSIX receiver configuration')
class Receiver(unittest.TestCase):
    def test_modes_keep_independent_private_settings_and_use_native_pipewire(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(pi_audio, 'ROOT', Path(folder).resolve() / 'settings'):
            with patch.object(pi_audio, 'mode', return_value='classic'):
                pi_audio.configure({'name': 'Classic', 'password': 'example-pass'})
            with patch.object(pi_audio, 'mode', return_value='airplay2'):
                pi_audio.configure({'name': 'AirPlay 2', 'password': 'different-pass'})
            self.assertIn('Classic', (pi_audio.ROOT / 'airplay.conf').read_text())
            text = (pi_audio.ROOT / 'airplay2.conf').read_text()
            self.assertIn('service_type = "airplay2";', text)
            self.assertIn('output_backend = "pipewire";', text)
            self.assertEqual((pi_audio.ROOT / 'airplay2.conf').stat().st_mode & 0o777, 0o600)

    def test_enable_starts_timing_before_receiver_and_disable_stops_it(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(pi_audio, 'ROOT', Path(folder)), patch.object(pi_audio, 'mode', return_value='airplay2'), patch.object(pi_audio, 'command') as command, patch.object(pi_audio, 'state', return_value='active'), patch.object(pi_audio, 'status', return_value={}):
            (Path(folder) / 'airplay2.conf').write_text('fixture')
            command.return_value = types.SimpleNamespace(returncode=0, stdout='disabled')
            pi_audio.action('enable')
            self.assertEqual(command.call_args_list[1].args, ('/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'enable', '--now', pi_audio.TIMING))
            self.assertEqual(command.call_args_list[2].args, ('/usr/bin/systemctl', '--user', 'enable', '--now', pi_audio.UNIT))
            command.reset_mock()
            pi_audio.action('disable')
            self.assertEqual(command.call_args_list[-1].args, ('/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'disable', '--now', pi_audio.TIMING))

    def test_timing_failure_does_not_start_receiver(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(pi_audio, 'ROOT', Path(folder)), patch.object(pi_audio, 'mode', return_value='airplay2'), patch.object(pi_audio, 'command') as command, patch.object(pi_audio, 'state', return_value='failed'):
            (Path(folder) / 'airplay2.conf').write_text('fixture')
            command.return_value = types.SimpleNamespace(returncode=1, stdout='disabled')
            with self.assertRaises(RuntimeError): pi_audio.action('enable')
            self.assertFalse(any(call.args[:2] == ('/usr/bin/systemctl', '--user') for call in command.call_args_list))

    def test_timing_unit_has_only_binding_capability_and_shared_shm(self):
        text = (ROOT / 'pi/pi-audio-nqptp.service').read_text()
        self.assertIn('DynamicUser=yes', text)
        self.assertIn('CapabilityBoundingSet=CAP_NET_BIND_SERVICE', text)
        self.assertNotIn('PrivateDevices=yes', text)
        self.assertNotIn('PrivateIPC=yes', text)
        self.assertIn('AF_NETLINK', text)


@unittest.skipUnless(sys.platform == 'linux', 'Linux artifact ownership')
class Artifacts(unittest.TestCase):
    def test_artifact_requires_regular_arm64_executable_and_rejects_symlinks(self):
        install = load('airplay2_artifact_test', 'pi/airplay2-install.py')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'binary'
            data = bytearray(64)
            data[:7] = b'\x7fELF\x02\x01\x01'
            data[16:18] = b'\x03\x00'
            data[18:20] = b'\xb7\x00'
            path.write_bytes(data)
            self.assertEqual(install.read_artifact(path, os.getuid(), binary=True), data)
            data[18:20] = b'\x3e\x00'
            path.write_bytes(data)
            with self.assertRaises(ValueError): install.read_artifact(path, os.getuid(), binary=True)
            link = Path(folder) / 'link'
            link.symlink_to(path)
            with self.assertRaises(OSError): install.read_artifact(link, os.getuid(), binary=True)


if __name__ == '__main__': unittest.main()
