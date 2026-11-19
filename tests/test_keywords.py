"""PEP 808 Keywords uses comma-list items, not whole-header equality."""

import io
import itertools
import json
import os
import random
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from packaging.metadata import parse_email

from metapreserve import InputError, compare_metadata


def metadata(value=None, *, version="2.6", dynamic=True):
    text = f"Metadata-Version: {version}\nName: demo\nVersion: 1\n"
    if dynamic:
        text += "Dynamic: Keywords\n"
    if value is not None:
        text += "Keywords: " + value + "\n"
    return text.encode()


def compare(a, b, **kwargs):
    return compare_metadata(metadata(a, **kwargs), metadata(b), built_from=True)


def keywords(result):
    return next(item for item in result["checks"] if item["field"] == "keywords")


class KeywordTests(unittest.TestCase):
    def test_inserted_values_and_raw_receipts(self):
        item = keywords(compare(" alpha ,beta", "new,alpha,middle, beta ,tail"))
        self.assertEqual(item["status"], "PRESERVED")
        self.assertEqual(item["matched_wheel_indices"], [1, 3])
        self.assertEqual(item["index_unit"], "comma_list_item")
        self.assertEqual(item["source"], ["alpha ,beta"])
        self.assertEqual(item["wheel"], ["new,alpha,middle, beta ,tail"])
        self.assertEqual(item["source_items"], ["alpha", "beta"])
        self.assertEqual(
            item["wheel_items"], ["new", "alpha", "middle", "beta", "tail"]
        )

    def test_removal_reordering_and_replacement(self):
        for target, missing in [("alpha", 1), ("beta,alpha", 1), ("alpha,gamma", 1)]:
            with self.subTest(target=target):
                result = compare("alpha,beta", target)
                self.assertEqual(result["mismatched_fields"], ["keywords"])
                self.assertEqual(
                    keywords(result)["first_unmatched_source_index"], missing
                )

    def test_duplicates_require_distinct_occurrences(self):
        item = keywords(compare("a,a,b", "a,b"))
        self.assertEqual(item["status"], "MISMATCH")
        self.assertEqual(item["matched_wheel_indices"], [0])
        self.assertEqual(item["first_unmatched_source_index"], 1)
        self.assertEqual(
            keywords(compare("a,a,b", "x,a,a,b"))["matched_wheel_indices"], [1, 2, 3]
        )

    def test_case_and_internal_space_are_not_normalized(self):
        for target in ["alpha", "ALPHA", "alpha beta"]:
            with self.subTest(target=target):
                self.assertEqual(compare("Alpha", target)["status"], "MISMATCH")
        self.assertEqual(compare("alpha  beta", "alpha beta")["status"], "MISMATCH")

    def test_missing_target_is_mismatch(self):
        item = keywords(compare("a,b", None))
        self.assertEqual(item["wheel_items"], [])
        self.assertEqual(item["status"], "MISMATCH")
        self.assertEqual(item["first_unmatched_source_index"], 0)

    def test_empty_items_are_refused_on_either_side(self):
        for value in ["", " ", ",a", "a,", "a,,b", "a, \t,b"]:
            for a, b in [(value, "a,b"), ("a,b", value)]:
                with (
                    self.subTest(a=a, b=b),
                    self.assertRaisesRegex(InputError, "empty"),
                ):
                    compare(a, b)

    def test_absent_source_has_no_static_keyword_promise(self):
        result = compare(None, "new,items")
        self.assertIn("keywords", result["unconstrained_fields"])
        self.assertEqual(result["status"], "PRESERVED_IN_PROFILE")

    def test_static_and_old_dynamic_behavior_unchanged(self):
        self.assertEqual(compare("a,b", "a, b", dynamic=False)["status"], "MISMATCH")
        self.assertEqual(
            compare("a,b", "a,b", dynamic=False)["status"], "PRESERVED_IN_PROFILE"
        )
        for version in ["2.2", "2.3", "2.4", "2.5"]:
            with self.subTest(version=version):
                result = compare("a,,b", "different", version=version)
                self.assertEqual(keywords(result)["rule"], "dynamic_unconstrained")
                self.assertEqual(result["status"], "PRESERVED_IN_PROFILE")

    def test_folded_and_unicode_keywords_match_packaging(self):
        for value in ["alpha,\n beta", "café,東京", " a\t,\t b ", "a b,c", '"a,b",c']:
            with self.subTest(value=value):
                item = keywords(compare(value, value))
                raw, unparsed = parse_email(metadata(value))
                self.assertEqual(unparsed, {})
                self.assertEqual(item["source_items"], raw["keywords"])
                self.assertEqual(item["status"], "PRESERVED")

    def test_exhaustive_independent_embedding_oracle(self):
        sequences = [p for n in range(1, 5) for p in itertools.product("ab", repeat=n)]
        for source in sequences:
            for target in sequences:
                # Enumerating index combinations is independent of the greedy scan.
                embeddings = [
                    list(ix)
                    for ix in itertools.combinations(range(len(target)), len(source))
                    if tuple(target[i] for i in ix) == source
                ]
                item = keywords(compare(",".join(source), ",".join(target)))
                self.assertEqual(item["status"] == "PRESERVED", bool(embeddings))
                if embeddings:
                    self.assertEqual(item["matched_wheel_indices"], embeddings[0])

    def test_seeded_parser_oracle_and_preservation(self):
        rng = random.Random(808)
        tokens = ["alpha", "beta", "a  b", "café", "東京", "Case"]
        for _ in range(200):
            source = [rng.choice(tokens) for _ in range(rng.randrange(1, 8))]
            target = ["inserted"]
            for value in source:
                target.extend([value, "inserted"])
            raw_source = " ,\t".join(source)
            raw_target = ", ".join(target)
            item = keywords(compare(raw_source, raw_target))
            parsed, _ = parse_email(metadata(raw_source))
            self.assertEqual(item["source_items"], parsed["keywords"])
            self.assertEqual(
                item["matched_wheel_indices"], list(range(1, 2 * len(source), 2))
            )

    def test_cli_archives_and_atomic_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "demo-1.tar.gz"
            target = Path(tmp) / "demo-1-py3-none-any.whl"
            with tarfile.open(source, "w:gz") as archive:
                data = metadata("a,b")
                info = tarfile.TarInfo("demo-1/PKG-INFO")
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("demo-1.dist-info/METADATA", metadata("x,a,b"))
            command = [
                sys.executable,
                "-m",
                "metapreserve",
                str(source),
                str(target),
                "--built-from",
            ]
            env = dict(
                os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src")
            )
            result = subprocess.run(command, env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                keywords(json.loads(result.stdout)["wheels"][0])[
                    "matched_wheel_indices"
                ],
                [1, 2],
            )
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("demo-1.dist-info/METADATA", metadata("b,a"))
            result = subprocess.run(command, env=env, capture_output=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["status"], "MISMATCH")
            second = Path(tmp) / "demo-1-py2.py3-none-any.whl"
            with zipfile.ZipFile(second, "w") as archive:
                archive.writestr("demo-1.dist-info/METADATA", metadata("a,,b"))
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("demo-1.dist-info/METADATA", metadata("a,b"))
            result = subprocess.run(
                command[:-1] + [str(second), command[-1]], env=env, capture_output=True
            )
            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "INVALID")
            self.assertNotIn("wheels", payload)
