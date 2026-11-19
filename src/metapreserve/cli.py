"""Machine-readable CLI. Exit 0 preserved, 1 mismatch, 2 invalid/unsupported."""

import argparse
import json
import os
import sys

from .archives import read_artifact
from .core import InputError, compare_metadata


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check sdist-to-wheel field preservation under an asserted build relationship."
    )
    parser.add_argument("sdist")
    parser.add_argument("wheels", nargs="+")
    parser.add_argument(
        "--built-from",
        action="store_true",
        help="assert every wheel was built directly from this unmodified sdist",
    )
    args = parser.parse_args(argv)
    try:
        if not args.built_from:
            raise InputError(
                "--built-from is required; filenames do not prove build lineage"
            )
        if len(args.wheels) > 8:
            raise InputError("at most eight wheels per invocation")
        source = read_artifact(args.sdist, "sdist")
        reports = []
        total = source.receipt["bytes"]
        for path in args.wheels:
            wheel = read_artifact(path, "wheel")
            total += wheel.receipt["bytes"]
            if total > 256 * 1024 * 1024:
                raise InputError("invocation archive byte limit exceeded")
            report = compare_metadata(source.metadata, wheel.metadata, built_from=True)
            report["wheel"] = wheel.receipt
            reports.append(report)
        mismatch = any(report["status"] == "MISMATCH" for report in reports)
        payload = {
            "schema_version": 1,
            "status": "MISMATCH" if mismatch else "PRESERVED_IN_PROFILE",
            "sdist": source.receipt,
            "wheels": reports,
            "limits": "64 MiB/archive, 128 MiB expanded tar, 10000 members, 1 MiB metadata, 8 wheels, 256 MiB invocation",
            "not_established": [
                "build lineage",
                "semantic equivalence",
                "general metadata validity",
                "installability",
                "dependency compatibility",
                "execution safety",
            ],
        }
        status = int(mismatch)
    except InputError as exc:
        payload = {"schema_version": 1, "status": "INVALID", "error": str(exc)}
        status = 2
    try:
        sys.stdout.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, OSError):
        # Avoid an interpreter-shutdown flush overriding exit 2 with exit 120.
        try:
            fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(fd, sys.stdout.fileno())
            finally:
                os.close(fd)
        except (OSError, ValueError, AttributeError):
            pass
        return 2
    return status
