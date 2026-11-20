"""Run private same-target negative controls without changing the checkout."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
production = (root / "src/metapreserve/core.py").read_text()
mutations = {
    "naive-comma-split": (
        "    text = values[0]\n    items = []",
        '    text = values[0]\n    return [x.strip() for x in text.split(",")]\n    items = []',
    ),
    "lose-order-and-duplicates": (
        "                source_items, wheel_items = split(a), split(b)",
        "                source_items, wheel_items = sorted(set(split(a))), sorted(set(split(b)))",
    ),
    "accept-comments": (
        'if char in "():;[]" or (ord(char) < 32 and char != "\\t"):',
        'if char in ":;[]" or (ord(char) < 32 and char != "\\t"):',
    ),
    "leak-parser-exceptions": (
        "except (\n            errors.HeaderParseError,\n            IndexError,\n            ValueError,\n            RecursionError,\n            AttributeError,\n        ) as exc:",
        "except errors.HeaderParseError as exc:",
    ),
}
results = []
for name, (old, new) in mutations.items():
    assert production.count(old) == 1, name
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        shutil.copytree(root / "src/metapreserve", folder / "metapreserve")
        (folder / "metapreserve/core.py").write_text(production.replace(old, new))
        env = os.environ.copy()
        env["PYTHONPATH"] = (
            str(folder)
            + os.pathsep
            + str(root / "tests")
            + os.pathsep
            + env.get("PYTHONPATH", "")
        )
        run = subprocess.run(
            [sys.executable, "-m", "unittest", "-v", "test_mailbox_lists"],
            env=env,
            cwd=folder,
            capture_output=True,
            text=True,
        )
        log = Path(tempfile.gettempdir()) / (
            "metapreserve-mailbox-mutant-" + name + ".log"
        )
        log.write_text(run.stdout + run.stderr)
        if run.returncode == 0:
            raise AssertionError("survived: " + name)
        results.append({"mutant": name, "exit": run.returncode, "log": str(log.name)})
print(json.dumps(results, indent=2))
