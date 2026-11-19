"""Run assertion-based negative controls in disposable source copies."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    (
        "ignore-changed-bytes",
        "src/metapreserve/licensefiles.py",
        'status = "CHANGED_BYTES"',
        'status = "PRESERVED"',
        "test_changed_bytes_including_line_endings",
    ),
    (
        "ignore-missing-source",
        "src/metapreserve/licensefiles.py",
        'status = "MISSING_SOURCE_FILE"',
        'status = "PRESERVED"',
        "test_missing_source_file_is_not_a_pass",
    ),
    (
        "ignore-missing-wheel",
        "src/metapreserve/licensefiles.py",
        'status = "MISSING_WHEEL_FILE"',
        'status = "PRESERVED"',
        "test_missing_wheel_file",
    ),
    (
        "wrong-wheel-root",
        "src/metapreserve/licensefiles.py",
        '"/licenses/" if kind == "wheel" else "/"',
        '"/" if kind == "wheel" else "/"',
        "test_tar_and_zip_nested_receipts",
    ),
    (
        "ignore-utf8",
        "src/metapreserve/licensefiles.py",
        'content.decode("utf-8")',
        'content.decode("utf-8", errors="ignore")',
        "test_utf8_validation_only_on_requested_declared_files",
    ),
    (
        "disable-cli-opt-in",
        "src/metapreserve/cli.py",
        "args = parser.parse_args(argv)",
        "args = parser.parse_args(argv)\n    args.license_files = False",
        "test_cli_opt_in_mismatch_and_atomicity",
    ),
]
for name, path, before, after, test in CASES:
    with tempfile.TemporaryDirectory() as temporary:
        target = Path(temporary)
        shutil.copytree(ROOT / "src", target / "src")
        shutil.copytree(ROOT / "tests", target / "tests")
        file = target / path
        original = file.read_text()
        assert original.count(before) == 1, name
        file.write_text(original.replace(before, after))
        env = dict(os.environ, PYTHONPATH=str(target / "src"))
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                "test_licensefiles.LicenseFileTests." + test,
            ],
            cwd=target / "tests",
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, (name, result.stdout, result.stderr)
        assert "FAIL:" in result.stderr and "AssertionError" in result.stderr, (
            name,
            result.stderr,
        )
        assert "ERROR:" not in result.stderr, (name, result.stderr)
        print(name + ": intended assertion failure PASS")
