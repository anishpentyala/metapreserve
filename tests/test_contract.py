import io
import itertools
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from metapreserve import InputError, compare_metadata, parse_metadata
from metapreserve.archives import read_artifact


def meta(extra="", version="2.6", name="demo", release="1.0"):
    return (
        f"Metadata-Version: {version}\nName: {name}\nVersion: {release}\n" + extra
    ).encode()


def wheel(path, content, extras=()):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("demo-1.0.dist-info/METADATA", content)
        for name, value in extras:
            z.writestr(name, value)


def sdist(path, content):
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo("demo-1.0/PKG-INFO")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))


class ContractTests(unittest.TestCase):
    def compare(self, a, b, **kw):
        return compare_metadata(meta(a, **kw), meta(b), built_from=True)

    def test_static_change(self):
        report = self.compare("Summary: old\n", "Summary: new\n")
        self.assertEqual(report["mismatched_fields"], ["summary"])

    def test_static_absent_is_promise(self):
        self.assertEqual(self.compare("", "Summary: added\n")["status"], "MISMATCH")

    def test_dynamic_old_unconstrained(self):
        for version in ["2.2", "2.3", "2.4", "2.5"]:
            with self.subTest(version=version):
                result = self.compare(
                    "Dynamic: Requires-Dist\nRequires-Dist: a\n",
                    "Requires-Dist: b\n",
                    version=version,
                )
                self.assertEqual(result["status"], "PRESERVED_IN_PROFILE")
                self.assertIn("requires-dist", result["unconstrained_fields"])

    def test_legacy(self):
        self.assertEqual(
            self.compare("Summary: a\n", "Summary: b\n", version="2.1")["status"],
            "PRESERVED_IN_PROFILE",
        )

    def test_dynamic_sequence_cases(self):
        for source, target, expected in [
            (["a", "b"], ["a", "b", "c"], True),
            (["a", "b"], ["c", "a", "d", "b"], True),
            (["a", "b"], ["b", "a"], False),
            (["a", "a"], ["a"], False),
            (["a", "a"], ["a", "b", "a"], True),
            ([], ["a"], True),
            (["a"], [], False),
        ]:
            with self.subTest(source=source, target=target):
                a = "Dynamic: Requires-Dist\n" + "".join(
                    "Requires-Dist: " + x + "\n" for x in source
                )
                b = "".join("Requires-Dist: " + x + "\n" for x in target)
                self.assertEqual(
                    self.compare(a, b)["status"] == "PRESERVED_IN_PROFILE", expected
                )

    def test_exhaustive_sequence_oracle(self):
        # Independent brute-force embeddings, not another greedy implementation.
        sequences = [
            list(x) for n in range(5) for x in itertools.product("ab", repeat=n)
        ]
        for source in sequences:
            for target in sequences:
                expected = any(
                    [target[i] for i in indices] == source
                    for indices in itertools.combinations(
                        range(len(target)), len(source)
                    )
                )
                a = "Dynamic: Classifier\n" + "".join(
                    "Classifier: " + x + "\n" for x in source
                )
                b = "".join("Classifier: " + x + "\n" for x in target)
                result = self.compare(a, b)
                self.assertEqual(
                    result["status"] == "PRESERVED_IN_PROFILE",
                    expected,
                    (source, target),
                )
                check = (
                    next(x for x in result["checks"] if x["field"] == "classifier")
                    if source or target
                    else None
                )
                if expected and check:
                    self.assertEqual(
                        [target[i] for i in check["matched_wheel_indices"]], source
                    )

    def test_static_multiset(self):
        self.assertEqual(
            self.compare(
                "Classifier: a\nClassifier: b\n", "Classifier: b\nClassifier: a\n"
            )["status"],
            "PRESERVED_IN_PROFILE",
        )
        self.assertEqual(
            self.compare("Classifier: a\nClassifier: a\n", "Classifier: a\n")["status"],
            "MISMATCH",
        )

    def test_one_dynamic_does_not_hide_static(self):
        result = self.compare(
            "Dynamic: Requires-Dist\nSummary: a\n", "Requires-Dist: new\nSummary: b\n"
        )
        self.assertEqual(result["mismatched_fields"], ["summary"])

    def test_single_dynamic(self):
        self.assertEqual(
            self.compare("Dynamic: Summary\nSummary: a\n", "Summary: b\n")["status"],
            "PRESERVED_IN_PROFILE",
        )

    def test_identity_normalization(self):
        result = compare_metadata(
            meta(name="Demo.Pkg", release="1.0"),
            meta(name="demo-pkg", release="1.0.0"),
            built_from=True,
        )
        self.assertEqual(result["status"], "PRESERVED_IN_PROFILE")

    def test_header_case_folding(self):
        self.assertEqual(
            self.compare("Summary: one\n two\n", "summary: one two\n")["status"],
            "PRESERVED_IN_PROFILE",
        )

    def test_requirement_spelling_is_not_equivalence(self):
        self.assertEqual(
            self.compare("Requires-Dist: a>=1\n", "Requires-Dist: a >= 1\n")["status"],
            "MISMATCH",
        )

    def test_body_line_endings(self):
        a = meta("\nHello\r\nworld\r\n")
        b = meta("\nHello\nworld\n")
        self.assertEqual(
            compare_metadata(a, b, built_from=True)["status"], "PRESERVED_IN_PROFILE"
        )

    def test_invalid_inputs(self):
        for data in [
            b"",
            b"\xff",
            meta("Summary: a\nSummary: b\n"),
            meta("Unknown: a\n"),
            meta("Dynamic: Name\n"),
            meta("Dynamic: dynamic\n"),
            meta("Dynamic: Summary\nDynamic: Summary\n"),
            meta("Dynamic: Summary\n", version="2.1"),
            meta("", version="2.7"),
            meta("", version="3.0"),
            meta("\x00"),
            meta("\nbody\nDescription: a\n") + b"\x00",
        ]:
            with self.subTest(data=data):
                with self.assertRaises(InputError):
                    parse_metadata(data)
        with self.assertRaises(InputError):
            self.compare("Dynamic: Keywords\nKeywords: a,,b\n", "Keywords: a,b\n")
        with self.assertRaises(InputError):
            compare_metadata(meta(), meta())
        with self.assertRaises(InputError):
            compare_metadata(meta(), meta(name="other"), built_from=True)


class ArchiveTests(unittest.TestCase):
    def test_archive_and_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            s = root / "demo-1.0.tar.gz"
            w = root / "demo-1.0-py3-none-any.whl"
            sdist(s, meta("Dynamic: Requires-Dist\nRequires-Dist: a\n"))
            wheel(w, meta("Requires-Dist: a\nRequires-Dist: b\n"))
            artifact = read_artifact(w, "wheel")
            self.assertEqual(len(artifact.receipt["sha256"]), 64)
            cmd = [sys.executable, "-m", "metapreserve", str(s), str(w)]
            p = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(p.returncode, 2)
            p = subprocess.run(cmd + ["--built-from"], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
            self.assertEqual(json.loads(p.stdout)["status"], "PRESERVED_IN_PROFILE")
            wheel(w, meta("Requires-Dist: b\n"))
            p = subprocess.run(cmd + ["--built-from"], capture_output=True, text=True)
            self.assertEqual(p.returncode, 1)
            wheel(w, meta("Unknown: x\n"))
            p = subprocess.run(cmd + ["--built-from"], capture_output=True, text=True)
            self.assertEqual(p.returncode, 2)
            self.assertNotIn("wheels", json.loads(p.stdout))

    def test_zip_sdist_and_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "demo-1.0.zip"
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("demo-1.0/PKG-INFO", meta())
            self.assertEqual(read_artifact(p, "sdist").metadata, meta())
            with zipfile.ZipFile(p, "a") as z:
                z.writestr("../escape", b"x")
            with self.assertRaises(InputError):
                read_artifact(p, "sdist")

    def test_duplicate_metadata_and_filename_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "demo-1.0-py3-none-any.whl"
            wheel(p, meta(), [("other-1.0.dist-info/METADATA", meta())])
            with self.assertRaises(InputError):
                read_artifact(p, "wheel")
            wheel(p, meta(name="other"))
            with self.assertRaises(InputError):
                read_artifact(p, "wheel")


if __name__ == "__main__":
    unittest.main()
