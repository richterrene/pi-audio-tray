"""Safety checks for the optional lab driver; no Docker/VM/image download here."""
import importlib.util
import contextlib
import io
from pathlib import Path
import struct
import os
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('virtual_lab', Path(__file__).resolve().parents[1] / 'tools/virtual/run.py')
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


class VirtualLabSafety(unittest.TestCase):
    def image(self, path, second_start=32, second_count=64):
        header = bytearray(512)
        header[510:] = b'\x55\xaa'
        header[450] = 0x0c
        struct.pack_into('<II', header, 454, 8, 16)
        header[466] = 0x83
        struct.pack_into('<II', header, 470, second_start, second_count)
        with path.open('wb') as stream:
            stream.write(header)
            stream.truncate(128 * 512)

    def test_partition_bounds_and_overlap_are_validated_before_disk_editing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'image'
            self.image(path)
            self.assertEqual(lab.partitions(path), [(0x0c, 8, 16), (0x83, 32, 64)])
            for start, count in ((16, 64), (32, 200), (0, 64), (32, 0)):
                self.image(path, start, count)
                with self.assertRaises(ValueError):
                    lab.partitions(path)

    def test_sparse_copy_rejects_truncated_source(self):
        import io
        with self.assertRaises(ValueError):
            lab.sparse_copy(io.BytesIO(b'abc'), io.BytesIO(), 10)

    @unittest.skipUnless(hasattr(os, 'getuid'), 'Linux/Unix runner UID')
    def test_helper_container_has_no_host_privilege_or_network(self):
        with patch.object(lab, 'command') as run:
            lab.runner(Path('/tmp/example-lab'), ['python3', '/lab/run.py'])
        args = run.call_args.args[0]
        self.assertEqual(args[args.index('--network')+1], 'none')
        self.assertIn('--read-only', args)
        self.assertEqual(args[args.index('--cap-drop')+1], 'ALL')
        self.assertIn('no-new-privileges', args)
        self.assertIn('--user', args)
        self.assertNotIn('--privileged', args)
        self.assertNotIn('--device', args)
        self.assertEqual(args[args.index('-v')+1], '/tmp/example-lab:/lab:rw')

    def test_internal_prepare_cannot_run_on_host(self):
        with contextlib.redirect_stderr(io.StringIO()), \
             patch.object(lab.sys, 'argv', ['run.py', '--prepare', 'arm64-pi3']), \
             patch.object(lab, 'prepare_inside') as prepare, \
             self.assertRaises(SystemExit):
            lab.main()
        prepare.assert_not_called()


if __name__ == '__main__':
    unittest.main()
