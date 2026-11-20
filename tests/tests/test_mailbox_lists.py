"""Conservative lexical mailbox-list preservation, never identity equivalence."""

import itertools
import json
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_contract import meta, sdist, wheel

from metapreserve import InputError, compare_metadata, core


class MailboxListTests(unittest.TestCase):
    def compare(self, source, target, field="Author-Email", version="2.6"):
        a = meta(f"Dynamic: {field}\n{field}: {source}\n", version=version)
        b = meta(f"{field}: {target}\n", version=version)
        return compare_metadata(a, b, built_from=True)

    def test_quoted_commas_and_insertions(self):
        for field in ["Author-Email", "Maintainer-Email"]:
            source = '"Last, First" <first@example.com>, second@example.com'
            target = 'added@example.com, "Last, First" <first@example.com>, middle@example.com, second@example.com'
            result = self.compare(source, target, field)
            item = next(c for c in result["checks"] if c["field"] == field.lower())
            self.assertEqual(result["status"], "PRESERVED_IN_PROFILE")
            self.assertEqual(item["matched_wheel_indices"], [1, 3])
            self.assertEqual(item["index_unit"], "comma_list_item")
            self.assertEqual(
                item["source_items"],
                ['"Last, First" <first@example.com>', "second@example.com"],
            )

    def test_escaped_quotes_and_quoted_local_parts(self):
        for value in [
            '"Name \\"nickname\\"" <name@example.com>',
            '"comma,local"@example.com',
            '"angle < bracket" <name@example.com>',
            "Name Person <person@example.com>",
            "Person <person@xn--bcher-kva.example>",
        ]:
            with self.subTest(value=value):
                self.assertEqual(core._mailbox_items([value]), [value])

    def test_changes_reorder_removal_and_duplicate_occurrences(self):
        for source, target, missing in [
            ("a@example.com, b@example.com", "b@example.com, a@example.com", 1),
            ("a@example.com, a@example.com", "a@example.com", 1),
            ("a@example.com", "A@example.com", 0),
            ("Name <a@example.com>", "a@example.com", 0),
        ]:
            result = self.compare(source, target)
            self.assertEqual(result["status"], "MISMATCH")
            item = next(c for c in result["checks"] if c["field"] == "author-email")
            self.assertEqual(item["first_unmatched_source_index"], missing)

    def test_unsupported_or_malformed_inputs_refused(self):
        values = [
            "",
            "a@example.com,",
            ",a@example.com",
            "a@example.com,,b@example.com",
            "Team: a@example.com;",
            "A (comment) <a@example.com>",
            "localonly",
            "<a@example.com",
            "a@example.com>",
            "A <<a@example.com>>",
            '"unfinished <a@example.com>',
            "a@example.com b@example.com",
            "Name <a@example.com> junk",
            "a@[IPv6:2001:db8::1]",
            "a@[é",
            "a@[é]",
            "a@[127.0.0.1]",
            "a@[abc]",
        ]
        for value in values:
            for side in ["source", "wheel"]:
                with (
                    self.subTest(value=value, side=side),
                    self.assertRaises(InputError),
                ):
                    self.compare(
                        value if side == "source" else "a@example.com",
                        value if side == "wheel" else "a@example.com",
                    )

    def test_static_and_older_dynamic_semantics_unchanged(self):
        result = self.compare("not a mailbox", "different", version="2.5")
        self.assertEqual(result["status"], "PRESERVED_IN_PROFILE")
        a = meta("Author-Email: a@example.com, b@example.com\n")
        b = meta("Author-Email: b@example.com, a@example.com\n")
        self.assertEqual(compare_metadata(a, b, built_from=True)["status"], "MISMATCH")

    def test_exhaustive_sequence_oracle(self):
        alphabet = ["a@example.com", '"B, Name" <b@example.com>']
        sequences = [p for n in range(5) for p in itertools.product(alphabet, repeat=n)]
        for field in ["Author-Email", "Maintainer-Email"]:
            for source in sequences:
                for target in sequences:
                    expected = any(
                        tuple(target[i] for i in indices) == source
                        for indices in itertools.combinations(
                            range(len(target)), len(source)
                        )
                    )
                    a = meta(
                        f"Dynamic: {field}\n"
                        + (f"{field}: " + ", ".join(source) + "\n" if source else "")
                    )
                    b = meta(f"{field}: " + ", ".join(target) + "\n" if target else "")
                    result = compare_metadata(a, b, built_from=True)
                    self.assertEqual(
                        result["status"] == "PRESERVED_IN_PROFILE", expected
                    )

    def test_missing_headers_and_folding(self):
        for field in ["Author-Email", "Maintainer-Email"]:
            with self.subTest(field=field):
                absent = compare_metadata(
                    meta(f"Dynamic: {field}\n"),
                    meta(f"{field}: deliberately unsupported\n"),
                    built_from=True,
                )
                self.assertIn(field.lower(), absent["unconstrained_fields"])
                removed = compare_metadata(
                    meta(f"Dynamic: {field}\n{field}: a@example.com\n"),
                    meta(),
                    built_from=True,
                )
                self.assertEqual(removed["status"], "MISMATCH")
                folded = self.compare(
                    '"Last, First" <a@example.com>,\n b@example.com',
                    '"Last, First" <a@example.com>, b@example.com, c@example.com',
                    field,
                )
                self.assertEqual(folded["status"], "PRESERVED_IN_PROFILE")

    def test_parser_exception_is_input_error(self):
        from unittest.mock import patch

        for error in [IndexError, ValueError, RecursionError, AttributeError]:
            with (
                self.subTest(error=error),
                patch("metapreserve.core.HeaderRegistry", side_effect=error),
                self.assertRaises(InputError),
            ):
                self.compare("a@example.com", "a@example.com")

    def test_bounded_parser_noise_is_refused_not_crashed(self):
        rng = random.Random(405)
        for _ in range(3000):
            text = "".join(
                rng.choice('a@.,<>\\"():; []é中') for _ in range(rng.randrange(30))
            )
            try:
                core._mailbox_items([text])
            except InputError:
                pass

    def test_unicode_domain_literal_cli_refuses_with_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "demo-1.0.tar.gz"
            target = root / "demo-1.0-py3-none-any.whl"
            for field in ["Author-Email", "Maintainer-Email"]:
                for value in ["a@[é", "a@[é]", "a@[中", "a@[中]"]:
                    for side in ["source", "wheel"]:
                        with self.subTest(field=field, value=value, side=side):
                            source_value = (
                                value if side == "source" else "a@example.com"
                            )
                            target_value = value if side == "wheel" else "a@example.com"
                            sdist(
                                source,
                                meta(f"Dynamic: {field}\n{field}: {source_value}\n"),
                            )
                            wheel(target, meta(f"{field}: {target_value}\n"))
                            result = subprocess.run(
                                [
                                    sys.executable,
                                    "-m",
                                    "metapreserve",
                                    str(source),
                                    str(target),
                                    "--built-from",
                                ],
                                capture_output=True,
                                text=True,
                            )
                            self.assertEqual(result.returncode, 2)
                            self.assertEqual(
                                json.loads(result.stdout)["status"], "INVALID"
                            )
                            self.assertEqual(result.stderr, "")

    def test_cli_late_unsupported_wheel_is_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "demo-1.0.tar.gz"
            target = root / "demo-1.0-py3-none-any.whl"
            valid = root / "demo-1.0-1-py3-none-any.whl"
            sdist(source, meta("Dynamic: Author-Email\nAuthor-Email: a@example.com\n"))
            wheel(valid, meta("Author-Email: a@example.com, added@example.com\n"))
            wheel(target, meta("Author-Email: Group: a@example.com;\n"))
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "metapreserve",
                    str(source),
                    str(valid),
                    str(target),
                    "--built-from",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "INVALID")
            self.assertNotIn("wheels", payload)
