"""File receipts must not accept ZIP entries whose attributes mark a directory."""

import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from metapreserve import InputError
from metapreserve.archives import read_artifact

METADATA = b"Metadata-Version: 2.4\nName: demo\nVersion: 1\n"


class ZipDirectoryMarkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def archive(self, kind, marker, *, license_file=False):
        wheel = kind == "wheel"
        path = self.root / ("demo-1-py3-none-any.whl" if wheel else "demo-1.zip")
        root = "demo-1.dist-info" if wheel else "demo-1"
        metadata_member = root + ("/METADATA" if wheel else "/PKG-INFO")
        selected = root + ("/licenses/NOTICE" if wheel else "/NOTICE")
        metadata = METADATA + (b"License-File: NOTICE\n" if license_file else b"")
        with zipfile.ZipFile(path, "w") as archive:
            if license_file:
                archive.writestr(metadata_member, metadata)
            entry = zipfile.ZipInfo(selected if license_file else metadata_member)
            entry.create_system = marker[0]
            entry.external_attr = marker[1]
            archive.writestr(entry, b"notice\n" if license_file else metadata)
        return path

    def test_metadata_refuses_directory_markers_without_slash(self):
        for kind in ["wheel", "sdist"]:
            for marker in [(3, (stat.S_IFDIR | 0o755) << 16), (0, 0x10), (3, 0x10)]:
                with self.subTest(kind=kind, marker=marker):
                    path = self.archive(kind, marker)
                    with self.assertRaisesRegex(
                        InputError, "invalid or oversized metadata"
                    ):
                        read_artifact(path, kind)

    def test_license_receipts_refuse_directory_markers_without_slash(self):
        for kind in ["wheel", "sdist"]:
            for marker in [(3, (stat.S_IFDIR | 0o755) << 16), (0, 0x10), (3, 0x10)]:
                with self.subTest(kind=kind, marker=marker):
                    path = self.archive(kind, marker, license_file=True)
                    with self.assertRaisesRegex(
                        InputError, "invalid or oversized license-file"
                    ):
                        read_artifact(path, kind, inspect_licenses=True)
                    # Unrequested license inspection still ignores payload contents.
                    self.assertIsNone(read_artifact(path, kind).license_files)

    def test_regular_markers_and_readonly_dos_files_remain_supported(self):
        for kind in ["wheel", "sdist"]:
            for marker in [
                (3, (stat.S_IFREG | 0o644) << 16),
                (0, 0x20),
                (0, 0x21),
                (3, 0o644 << 16),
            ]:
                for license_file in [False, True]:
                    with self.subTest(kind=kind, marker=marker, licenses=license_file):
                        path = self.archive(kind, marker, license_file=license_file)
                        result = read_artifact(
                            path, kind, inspect_licenses=license_file
                        )
                        self.assertIn(b"Name: demo", result.metadata)
                        if license_file:
                            self.assertTrue(result.license_files[0]["present"])

    def test_late_bad_wheel_keeps_cli_all_or_nothing(self):
        source = self.archive("sdist", (0, 0x20))
        bad = self.archive("wheel", (0, 0x10))
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "metapreserve",
                str(source),
                str(bad),
                "--built-from",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("wheels", result.stdout)
