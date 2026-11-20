"""Stored ZIP names must not be silently aliased by ZipInfo sanitization."""

import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from metapreserve import InputError
from metapreserve.archives import read_artifact

METADATA = b"Metadata-Version: 2.4\nName: demo\nVersion: 1\nLicense-File: NOTICE\n"


def zip_bytes(kind, affected="metadata", nul_offset=None):
    root = "demo-1.dist-info" if kind == "wheel" else "demo-1"
    metadata = root + ("/METADATA" if kind == "wheel" else "/PKG-INFO")
    license_name = root + ("/licenses/NOTICE" if kind == "wheel" else "/NOTICE")
    names = {"metadata": metadata, "license": license_name, "unrelated": root + "/data"}
    original = names[affected]
    stored = original + "!suffix"
    files = [
        (metadata, METADATA),
        (license_name, b"notice\n"),
        (root + "/data", b"data"),
    ]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files:
            archive.writestr(stored if name == original else name, content)
    data = output.getvalue()
    if nul_offset is None:
        return data
    encoded = stored.encode()
    mutated = encoded[:nul_offset] + b"\x00" + encoded[nul_offset + 1 :]
    # Patch both local header and central directory without using ZipInfo,
    # whose writer itself truncates NUL-containing filenames.
    if data.count(encoded) != 2:
        raise AssertionError("fixture must patch exactly two stored filename copies")
    return data.replace(encoded, mutated)


class ZipStoredNameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def save(self, kind, data, folder=""):
        root = self.root / folder
        root.mkdir(exist_ok=True)
        path = root / ("demo-1-py3-none-any.whl" if kind == "wheel" else "demo-1.zip")
        path.write_bytes(data)
        return path

    def test_truncated_metadata_alias_is_refused(self):
        for kind in ["wheel", "sdist"]:
            root = "demo-1.dist-info" if kind == "wheel" else "demo-1"
            name = root + ("/METADATA" if kind == "wheel" else "/PKG-INFO")
            data = zip_bytes(kind, nul_offset=len(name))
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                info = archive.infolist()[0]
                self.assertEqual(info.filename, name)
                self.assertIn("\x00", info.orig_filename)
                self.assertEqual(archive.read(info), METADATA)
            with (
                self.subTest(kind=kind),
                self.assertRaisesRegex(InputError, "noncanonical"),
            ):
                read_artifact(self.save(kind, data), kind)

    def test_license_and_unrelated_names_are_globally_validated(self):
        for kind in ["wheel", "sdist"]:
            root = "demo-1.dist-info" if kind == "wheel" else "demo-1"
            for affected, name in [
                (
                    "license",
                    root + ("/licenses/NOTICE" if kind == "wheel" else "/NOTICE"),
                ),
                ("unrelated", root + "/data"),
            ]:
                path = self.save(kind, zip_bytes(kind, affected, len(name)))
                for inspect in [False, True]:
                    with self.subTest(kind=kind, affected=affected, inspect=inspect):
                        with self.assertRaisesRegex(InputError, "noncanonical"):
                            read_artifact(path, kind, inspect_licenses=inspect)

    def test_nul_at_every_stored_name_position_is_refused(self):
        for kind in ["wheel", "sdist"]:
            root = "demo-1.dist-info" if kind == "wheel" else "demo-1"
            name = root + ("/METADATA" if kind == "wheel" else "/PKG-INFO") + "!suffix"
            for offset in range(len(name)):
                with self.subTest(kind=kind, offset=offset):
                    with self.assertRaises(InputError):
                        read_artifact(
                            self.save(kind, zip_bytes(kind, nul_offset=offset)), kind
                        )

    def test_valid_stored_names_and_license_receipts_remain_supported(self):
        for kind in ["wheel", "sdist"]:
            # A suffix in an unrelated member is ordinary data, not an alias.
            path = self.save(kind, zip_bytes(kind, affected="unrelated"))
            result = read_artifact(path, kind, inspect_licenses=True)
            self.assertEqual(result.metadata, METADATA)
            self.assertTrue(result.license_files[0]["present"])
            self.assertEqual(result.license_files[0]["bytes"], 7)

    def test_bad_later_wheel_prevents_any_success_report(self):
        source = self.save("sdist", zip_bytes("sdist", affected="unrelated"))
        good = self.save("wheel", zip_bytes("wheel", affected="unrelated"), "good")
        bad = self.save(
            "wheel",
            zip_bytes("wheel", nul_offset=len("demo-1.dist-info/METADATA")),
            "bad",
        )
        for inspect in [False, True]:
            command = [
                sys.executable,
                "-m",
                "metapreserve",
                str(source),
                str(good),
                str(bad),
                "--built-from",
            ]
            if inspect:
                command.append("--license-files")
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Traceback", result.stderr)
            report = json.loads(result.stdout)
            self.assertNotIn("wheels", report)
            self.assertEqual(report["status"], "INVALID")


if __name__ == "__main__":
    unittest.main()
