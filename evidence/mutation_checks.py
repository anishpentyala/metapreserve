"""Mutation checks for the preservation rules. Each mutant must fail tests."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = {
    "ignore-26-dynamic": (
        'elif left["metadata_version"] < (2, 6):',
        'elif left["metadata_version"] <= (2, 6):',
    ),
    "ignore-dynamic-order-and-multiplicity": (
        "matched, missing = _subsequence_witness(source_items, wheel_items)",
        "matched, missing = ([], None) if set(source_items) <= set(wheel_items) else ([], 0)",
    ),
    "skip-static-fields": (
        "elif key not in dynamic:",
        "elif False:",
    ),
}


def run():
    outcomes = {}
    for name, (old, new) in MUTATIONS.items():
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(
                ROOT / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__")
            )
            shutil.copytree(
                ROOT / "tests",
                root / "tests",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            target = root / "src/metapreserve/core.py"
            text = target.read_text()
            if text.count(old) != 1:
                raise RuntimeError("mutation target is not unique: " + name)
            target.write_text(text.replace(old, new))
            environment = dict(os.environ, PYTHONPATH=str(root / "src"))
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    str(root / "tests"),
                    "-p",
                    "test_contract.py",
                    "-k",
                    "test_dynamic_sequence_cases"
                    if name != "skip-static-fields"
                    else "test_static_",
                    "-v",
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            log = ROOT / "evidence" / ("mutation-" + name + ".log")
            log.write_text(
                "\n".join(
                    line.rstrip()
                    for line in (result.stdout + result.stderr).splitlines()
                )
                + "\n"
            )
            if result.returncode == 0:
                raise RuntimeError("mutation unexpectedly survived: " + name)
            if "FAIL:" not in result.stderr or "ERROR:" in result.stderr:
                raise RuntimeError(
                    "mutant failed by setup/error instead of an assertion: " + name
                )
            outcomes[name] = {
                "exit_code": result.returncode,
                "assertion_failures": result.stderr.count("FAIL:"),
                "log": log.name,
            }
    (ROOT / "evidence/mutations.json").write_text(json.dumps(outcomes, indent=2) + "\n")
    print(json.dumps(outcomes, sort_keys=True))


if __name__ == "__main__":
    run()
