import gzip
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

from test_contract import meta, sdist, wheel

from metapreserve import InputError, compare_metadata, parse_metadata
from metapreserve.archives import read_artifact


class ProfileBoundaryTests(unittest.TestCase):
    def test_all_multiple_fields_preserve_sequence(self):
        for field in [
            "Platform",
            "Supported-Platform",
            "Classifier",
            "Requires-Dist",
            "Requires-External",
            "Project-URL",
            "Provides-Extra",
            "Import-Name",
            "Import-Namespace",
            "Provides-Dist",
            "Obsoletes-Dist",
            "License-File",
            "Requires",
            "Provides",
            "Obsoletes",
        ]:
            with self.subTest(field=field):
                a = meta(f"Dynamic: {field}\n{field}: first\n{field}: second\n")
                b = meta(f"{field}: second\n{field}: first\n")
                self.assertEqual(
                    compare_metadata(a, b, built_from=True)["mismatched_fields"],
                    [field.lower()],
                )

    def test_empty_dynamic_declared_field_is_reported(self):
        result = compare_metadata(meta("Dynamic: Summary\n"), meta(), built_from=True)
        self.assertIn("summary", result["unconstrained_fields"])

    def test_unsupported_dynamic_comma_lists(self):
        for field in ["Keywords", "Author-Email", "Maintainer-Email"]:
            with self.subTest(field=field), self.assertRaises(InputError):
                compare_metadata(
                    meta(f"Dynamic: {field}\n{field}: first,second\n"),
                    meta(),
                    built_from=True,
                )

    def test_size_and_bare_cr(self):
        for data in [
            b"a" * 1048577,
            meta("Summary: a\rb\n"),
            meta("Header without colon\n"),
            meta("Description: a\n\nbody"),
        ]:
            with self.subTest(size=len(data)), self.assertRaises(InputError):
                parse_metadata(data)

    def test_field_version_restrictions(self):
        for field, version in [
            ("License-File", "2.3"),
            ("License-Expression", "2.3"),
            ("Import-Name", "2.4"),
            ("Import-Namespace", "2.4"),
        ]:
            with self.subTest(field=field), self.assertRaises(InputError):
                parse_metadata(meta(field + ": x\n", version=version))

    def test_static_field_does_not_become_dynamic_via_wheel(self):
        a = meta("Summary: fixed\n")
        b = meta("Dynamic: Summary\nSummary: changed\n")
        self.assertEqual(
            compare_metadata(a, b, built_from=True)["mismatched_fields"], ["summary"]
        )

    def test_future_metadata_and_identity_errors(self):
        for data in [
            meta(version="2.0"),
            meta(name="bad name"),
            meta(release="not a version"),
            meta("Dynamic: Unknown\n"),
        ]:
            with self.subTest(data=data), self.assertRaises(InputError):
                parse_metadata(data)


class ArchiveBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.w = self.root / "demo-1.0-py3-none-any.whl"
        self.s = self.root / "demo-1.0.tar.gz"

    def tearDown(self):
        self.tmp.cleanup()

    def test_duplicate_entry(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            wheel(self.w, meta(), [("demo-1.0.dist-info/METADATA", meta())])
        with self.assertRaises(InputError):
            read_artifact(self.w, "wheel")

    def test_special_zip_member(self):
        wheel(self.w, meta())
        with zipfile.ZipFile(self.w, "a") as z:
            entry = zipfile.ZipInfo("link")
            entry.create_system = 3
            entry.external_attr = 0o120777 << 16
            z.writestr(entry, b"target")
        with self.assertRaises(InputError):
            read_artifact(self.w, "wheel")

    def test_special_tar_member(self):
        with tarfile.open(self.s, "w:gz") as tar:
            entry = tarfile.TarInfo("demo-1.0/PKG-INFO")
            entry.type = tarfile.SYMTYPE
            entry.linkname = "elsewhere"
            tar.addfile(entry)
        with self.assertRaises(InputError):
            read_artifact(self.s, "sdist")

    def test_wrong_metadata_directory(self):
        with zipfile.ZipFile(self.w, "w") as z:
            z.writestr("other-1.0.dist-info/METADATA", meta())
        with self.assertRaises(InputError):
            read_artifact(self.w, "wheel")

    def test_missing_metadata_and_truncated_archives(self):
        for data in [b"not a zip", b"PK\x03\x04", b""]:
            self.w.write_bytes(data)
            with self.subTest(data=data), self.assertRaises(InputError):
                read_artifact(self.w, "wheel")
        with zipfile.ZipFile(self.w, "w") as z:
            z.writestr("module.py", b"pass")
        with self.assertRaises(InputError):
            read_artifact(self.w, "wheel")
        self.s.write_bytes(gzip.compress(b"not a tar"))
        with self.assertRaises(InputError):
            read_artifact(self.s, "sdist")

    def test_reduced_limits(self):
        wheel(self.w, meta())
        sdist(self.s, meta())
        for name, value, path, kind in [
            ("MAX_ARCHIVE", 10, self.w, "wheel"),
            ("MAX_METADATA", 10, self.w, "wheel"),
            ("MAX_MEMBERS", 0, self.w, "wheel"),
            ("MAX_EXPANDED_TAR", 10, self.s, "sdist"),
            ("MAX_METADATA", 10, self.s, "sdist"),
            ("MAX_MEMBERS", 0, self.s, "sdist"),
        ]:
            with (
                self.subTest(name=name, kind=kind),
                patch("metapreserve.archives." + name, value),
                self.assertRaises(InputError),
            ):
                read_artifact(path, kind)

    def test_cli_all_or_nothing(self):
        sdist(self.s, meta())
        wheel(self.w, meta("Summary: changed\n"))
        bad = self.root / "broken-1.0-py3-none-any.whl"
        bad.write_bytes(b"bad")
        cmd = [
            sys.executable,
            "-m",
            "metapreserve",
            str(self.s),
            str(self.w),
            str(bad),
            "--built-from",
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertEqual(p.returncode, 2)
        self.assertNotIn('"wheels"', p.stdout)

    @unittest.skipUnless(Path("/dev/full").exists(), "requires /dev/full")
    def test_output_failure_exits_two(self):
        sdist(self.s, meta())
        wheel(self.w, meta())
        with open("/dev/full", "wb") as sink:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "metapreserve",
                    str(self.s),
                    str(self.w),
                    "--built-from",
                ],
                stdout=sink,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(result.returncode, 2, result.stderr)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFO")
    def test_fifo_refused_without_blocking(self):
        os.mkfifo(self.w)
        cmd = [
            sys.executable,
            "-c",
            "from metapreserve.archives import read_artifact; read_artifact(__import__('sys').argv[1], 'wheel')",
            str(self.w),
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=5)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("regular file", p.stderr)


if __name__ == "__main__":
    unittest.main()
