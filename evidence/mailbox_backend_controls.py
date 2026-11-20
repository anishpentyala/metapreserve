"""Audit genuine demo output, then label deliberate METADATA-only controls.

Usage: PYTHONPATH=src python evidence/mailbox_backend_controls.py SDIST WHEEL
These rewritten wheels are test mutations, not actual backend output; RECORD
hashes are stale and are outside metapreserve's audit profile.
"""

import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from metapreserve.archives import read_artifact
from metapreserve.core import _mailbox_items


def main():
    source, target = map(Path, sys.argv[1:])
    original = read_artifact(target, "wheel").metadata.decode()
    items = {}
    for line in original.splitlines():
        if line.startswith(("Author-Email:", "Maintainer-Email:")):
            field, value = line.split(":", 1)
            items[field] = _mailbox_items([value.strip()])
    assert len(items["Author-Email"]) >= 2
    assert len(items["Maintainer-Email"]) >= 1
    outcomes = []

    def audit(path, label, expected):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "metapreserve",
                str(source),
                str(path),
                "--built-from",
            ],
            capture_output=True,
            text=True,
        )
        report = json.loads(result.stdout)
        assert report["status"] == expected, (label, report)
        assert result.returncode == (0 if expected == "PRESERVED_IN_PROFILE" else 1)
        outcomes.append(
            {"label": label, "status": report["status"], "exit": result.returncode}
        )

    audit(target, "genuine backend output", "PRESERVED_IN_PROFILE")
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        for field in items:
            source_items = items[field]
            changes = {
                "remove-promised": (source_items[1:], "MISMATCH"),
                "change-promised": (
                    ["changed@example.com"] + source_items[1:],
                    "MISMATCH",
                ),
                "insert-quoted-comma": (
                    [source_items[0], '"Extra, Person" <extra@example.com>']
                    + source_items[1:],
                    "PRESERVED_IN_PROFILE",
                ),
            }
            if len(source_items) >= 2:
                changes["reorder-promised"] = (list(reversed(source_items)), "MISMATCH")
            for label, (replacement, expected) in changes.items():
                rewritten = (
                    "\n".join(
                        field + ": " + ", ".join(replacement)
                        if line.startswith(field + ":")
                        else line
                        for line in original.splitlines()
                    )
                    + "\n"
                )
                # Empty replacement is a removed header, not an invalid empty item.
                if not replacement:
                    rewritten = rewritten.replace(field + ": \n", "")
                path = folder / target.name
                with zipfile.ZipFile(target) as old, zipfile.ZipFile(path, "w") as new:
                    for item in old.infolist():
                        data = (
                            rewritten.encode()
                            if item.filename.endswith("/METADATA")
                            else old.read(item)
                        )
                        new.writestr(item, data)
                audit(path, "deliberate " + field + " " + label, expected)
    print(json.dumps(outcomes, indent=2))


if __name__ == "__main__":
    main()
