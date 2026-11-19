"""Declared license paths are content receipts, not legal conclusions."""

import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from metapreserve import InputError
from metapreserve.archives import read_artifact
from metapreserve.licensefiles import compare_license_files


def metadata(paths, version="2.4"):
    return (
        f"Metadata-Version: {version}\nName: demo\nVersion: 1\n"
        + "".join("License-File: " + path + "\n" for path in paths)
    ).encode()


def artifact(root, kind, declarations, contents, *, fmt="tar", version="2.4"):
    top = "demo-1.dist-info" if kind == "wheel" else "demo-1"
    member = top + ("/METADATA" if kind == "wheel" else "/PKG-INFO")
    prefix = top + ("/licenses/" if kind == "wheel" else "/")
    files = [(member, metadata(declarations, version))]
    files += [(prefix + path, data) for path, data in contents.items()]
    filename = (
        "demo-1-py3-none-any.whl"
        if kind == "wheel"
        else "demo-1." + ("zip" if fmt == "zip" else "tar.gz")
    )
    path = root / filename
    if filename.endswith((".whl", ".zip")):
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in files:
                archive.writestr(name, data)
    else:
        with tarfile.open(path, "w:gz") as archive:
            for name, data in files:
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
    return path


class LicenseFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def pair(self, source, wheel, *, left=None, right=None, fmt="tar"):
        a = artifact(
            self.root, "sdist", list(source) if left is None else left, source, fmt=fmt
        )
        b = artifact(self.root, "wheel", list(wheel) if right is None else right, wheel)
        return read_artifact(a, "sdist", inspect_licenses=True), read_artifact(
            b, "wheel", inspect_licenses=True
        )

    def test_tar_and_zip_nested_receipts(self):
        for fmt in ["tar", "zip"]:
            with self.subTest(fmt=fmt):
                a, b = self.pair(
                    {"LICENSE": b"MIT\n", "legal/NOTICE": b"notice\xc3\xa9"},
                    {"LICENSE": b"MIT\n", "legal/NOTICE": b"notice\xc3\xa9"},
                    fmt=fmt,
                )
                report = compare_license_files(a, b)
                self.assertEqual(report["status"], "PRESERVED_IN_PROFILE")
                self.assertEqual(
                    [x["status"] for x in report["checks"]], ["PRESERVED", "PRESERVED"]
                )
                self.assertEqual(a.license_files[1]["member"], "demo-1/legal/NOTICE")
                self.assertEqual(
                    b.license_files[1]["member"],
                    "demo-1.dist-info/licenses/legal/NOTICE",
                )
                self.assertEqual(
                    a.license_files[1]["sha256"], b.license_files[1]["sha256"]
                )
                self.assertEqual(a.license_files[0]["bytes"], 4)

    def test_changed_bytes_including_line_endings(self):
        for content in [b"mit\n", b"MIT\r\n", b"MIT", b"MIT\n ", b""]:
            with self.subTest(content=content):
                a, b = self.pair({"LICENSE": b"MIT\n"}, {"LICENSE": content})
                self.assertEqual(
                    compare_license_files(a, b)["checks"],
                    [{"path": "LICENSE", "status": "CHANGED_BYTES"}],
                )

    def test_missing_source_file_is_not_a_pass(self):
        a, b = self.pair({}, {"LICENSE": b"MIT"}, left=["LICENSE"])
        result = compare_license_files(a, b)
        self.assertEqual(result["status"], "MISMATCH")
        self.assertEqual(result["checks"][0]["status"], "MISSING_SOURCE_FILE")
        self.assertFalse(result["source_files"][0]["present"])

    def test_missing_wheel_declaration_even_if_file_exists(self):
        a, b = self.pair({"LICENSE": b"MIT"}, {"LICENSE": b"MIT"}, right=[])
        self.assertEqual(
            compare_license_files(a, b)["checks"][0]["status"],
            "MISSING_WHEEL_DECLARATION",
        )

    def test_missing_wheel_file(self):
        a, b = self.pair({"LICENSE": b"MIT"}, {}, right=["LICENSE"])
        self.assertEqual(
            compare_license_files(a, b)["checks"][0]["status"], "MISSING_WHEEL_FILE"
        )

    def test_additional_wheel_notices_must_exist(self):
        for exists in [False, True]:
            with self.subTest(exists=exists):
                a, b = self.pair(
                    {}, {"NOTICE": b"vendor"} if exists else {}, right=["NOTICE"]
                )
                result = compare_license_files(a, b)
                self.assertEqual(
                    result["status"], "PRESERVED_IN_PROFILE" if exists else "MISMATCH"
                )
                self.assertEqual(
                    result["checks"][0]["status"],
                    "ADDED_WHEEL_FILE" if exists else "MISSING_WHEEL_FILE",
                )

    def test_no_declarations_is_empty_audit_not_legal_clearance(self):
        a, b = self.pair({}, {})
        result = compare_license_files(a, b)
        self.assertEqual(result["checks"], [])
        self.assertIn("legal compliance", result["not_established"])

    def test_empty_license_file_is_present(self):
        a, b = self.pair({"LICENSE": b""}, {"LICENSE": b""})
        self.assertEqual(
            compare_license_files(a, b)["checks"][0]["status"], "PRESERVED"
        )
        self.assertTrue(a.license_files[0]["present"])

    def test_unsupported_paths_on_both_artifact_kinds(self):
        for kind in ["sdist", "wheel"]:
            for name in [
                "",
                "/LICENSE",
                "../LICENSE",
                "legal/../LICENSE",
                "./LICENSE",
                "legal//LICENSE",
                "LICENSE/",
                "C:LICENSE",
                "legal\\LICENSE",
            ]:
                with self.subTest(kind=kind, name=name):
                    p = artifact(self.root, kind, [name], {})
                    with self.assertRaisesRegex(InputError, "path"):
                        read_artifact(p, kind, inspect_licenses=True)
                    self.assertIsNone(read_artifact(p, kind).license_files)

    def test_duplicate_declarations_refused(self):
        for kind in ["sdist", "wheel"]:
            with self.subTest(kind=kind):
                p = artifact(self.root, kind, ["LICENSE", "LICENSE"], {})
                with self.assertRaisesRegex(InputError, "duplicate"):
                    read_artifact(p, kind, inspect_licenses=True)

    def test_old_metadata_explicitly_unsupported(self):
        for kind in ["sdist", "wheel"]:
            p = artifact(self.root, kind, [], {}, version="2.3")
            with self.subTest(kind=kind), self.assertRaisesRegex(InputError, "2.4"):
                read_artifact(p, kind, inspect_licenses=True)
            self.assertIsNone(read_artifact(p, kind).license_files)

    def test_utf8_validation_only_on_requested_declared_files(self):
        for kind in ["sdist", "wheel"]:
            for fmt in ["tar", "zip"]:
                with self.subTest(kind=kind, fmt=fmt):
                    p = artifact(
                        self.root, kind, ["LICENSE"], {"LICENSE": b"\xff"}, fmt=fmt
                    )
                    with self.assertRaisesRegex(InputError, "UTF-8"):
                        read_artifact(p, kind, inspect_licenses=True)
                    self.assertIsNone(read_artifact(p, kind).license_files)
                    p = artifact(self.root, kind, [], {"LICENSE": b"\xff"}, fmt=fmt)
                    self.assertEqual(
                        read_artifact(p, kind, inspect_licenses=True).license_files, []
                    )

    def test_file_count_limit(self):
        for kind in ["sdist", "wheel"]:
            p = artifact(self.root, kind, [f"L{x}" for x in range(65)], {})
            with self.subTest(kind=kind), self.assertRaisesRegex(InputError, "count"):
                read_artifact(p, kind, inspect_licenses=True)

    def test_per_file_and_total_caps(self):
        for kind in ["sdist", "wheel"]:
            for fmt in ["tar", "zip"]:
                p = artifact(
                    self.root, kind, ["LICENSE"], {"LICENSE": b"a" * 1048577}, fmt=fmt
                )
                with (
                    self.subTest(kind=kind, fmt=fmt),
                    self.assertRaisesRegex(InputError, "oversized"),
                ):
                    read_artifact(p, kind, inspect_licenses=True)
                p = artifact(
                    self.root, kind, ["A", "B"], {"A": b"ab", "B": b"cd"}, fmt=fmt
                )
                with patch("metapreserve.archives.MAX_LICENSE_TOTAL", 3):
                    with self.assertRaisesRegex(InputError, "total"):
                        read_artifact(p, kind, inspect_licenses=True)

    def test_actual_byte_and_count_boundaries(self):
        for kind in ["sdist", "wheel"]:
            for fmt in ["tar", "zip"]:
                with self.subTest(kind=kind, fmt=fmt):
                    contents = {f"L{x}": b"a" * 1048576 for x in range(8)}
                    p = artifact(self.root, kind, list(contents), contents, fmt=fmt)
                    receipts = read_artifact(
                        p, kind, inspect_licenses=True
                    ).license_files
                    self.assertEqual(sum(x["bytes"] for x in receipts), 8 * 1048576)
                    contents["extra"] = b"a"
                    p = artifact(self.root, kind, list(contents), contents, fmt=fmt)
                    with self.assertRaisesRegex(InputError, "total"):
                        read_artifact(p, kind, inspect_licenses=True)
            p = artifact(self.root, kind, [f"L{x}" for x in range(64)], {})
            self.assertEqual(
                len(read_artifact(p, kind, inspect_licenses=True).license_files), 64
            )

    def test_archive_snapshot_is_shared_with_license_receipts(self):
        import hashlib

        from metapreserve import archives

        p = artifact(self.root, "wheel", ["LICENSE"], {"LICENSE": b"original"})
        original = p.read_bytes()
        reader = archives._zip_metadata

        def changed_on_disk(data, kind, inspect_licenses=False):
            p.write_bytes(b"file changed after snapshot")
            return reader(data, kind, inspect_licenses)

        with patch("metapreserve.archives._zip_metadata", side_effect=changed_on_disk):
            receipt = read_artifact(p, "wheel", inspect_licenses=True)
        self.assertEqual(
            receipt.receipt["sha256"], hashlib.sha256(original).hexdigest()
        )
        self.assertEqual(
            receipt.license_files[0]["sha256"], hashlib.sha256(b"original").hexdigest()
        )
        self.assertEqual(p.read_bytes(), b"file changed after snapshot")

    def test_wrong_wheel_location_does_not_satisfy_declaration(self):
        p = artifact(self.root, "wheel", ["LICENSE"], {})
        with zipfile.ZipFile(p, "a") as archive:
            archive.writestr("demo-1.dist-info/LICENSE", b"MIT")
            archive.writestr("LICENSE", b"MIT")
        receipt = read_artifact(p, "wheel", inspect_licenses=True)
        self.assertFalse(receipt.license_files[0]["present"])
        self.assertEqual(
            receipt.license_files[0]["member"], "demo-1.dist-info/licenses/LICENSE"
        )

    def test_missing_receipts_cannot_be_compared(self):
        a = read_artifact(artifact(self.root, "sdist", [], {}), "sdist")
        with self.assertRaisesRegex(InputError, "not requested"):
            compare_license_files(a, a)

    def test_cli_opt_in_mismatch_and_atomicity(self):
        a = artifact(self.root, "sdist", ["LICENSE"], {"LICENSE": b"MIT"})
        b = artifact(self.root, "wheel", ["LICENSE"], {"LICENSE": b"changed"})

        def cli(*extra):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "metapreserve",
                    str(a),
                    str(b),
                    *extra,
                    "--built-from",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.stderr, "")
            return result.returncode, json.loads(result.stdout)

        code, result = cli()
        self.assertEqual(code, 0)
        self.assertNotIn("license_files", result["wheels"][0])
        code, result = cli("--license-files")
        self.assertEqual((code, result["status"]), (1, "MISMATCH"))
        self.assertEqual(
            result["wheels"][0]["license_files"]["checks"][0]["status"], "CHANGED_BYTES"
        )
        invalid = self.root / "invalid"
        invalid.mkdir()
        bad = artifact(invalid, "wheel", ["../LICENSE"], {})
        code, result = cli(str(bad), "--license-files")
        self.assertEqual((code, result["status"]), (2, "INVALID"))
        self.assertNotIn("wheels", result)
