"""Opt-in declared-license-file receipts and byte-preservation profile.

A byte difference is an audit finding, not a legal conclusion or a claim that
PEP 639 requires byte identity for every sdist-to-wheel transformation.
"""

import hashlib

from .core import InputError, parse_metadata

MAX_LICENSE_FILES = 64
MAX_LICENSE_FILE = 1048576
MAX_LICENSE_TOTAL = 8 * 1048576


def license_members(metadata, metadata_member, kind):
    parsed = parse_metadata(metadata)
    if parsed["metadata_version"] < (2, 4):
        raise InputError("license-file audit requires Metadata-Version 2.4 or newer")
    paths = parsed["fields"].get("license-file", [])
    if len(paths) > MAX_LICENSE_FILES:
        raise InputError("license-file count exceeds limit")
    if len(set(paths)) != len(paths):
        raise InputError("duplicate License-File declaration")
    root = metadata_member.split("/")[0]
    prefix = root + ("/licenses/" if kind == "wheel" else "/")
    result = []
    for path in paths:
        if (
            not path
            or "\\" in path
            or "\x00" in path
            or path.startswith("/")
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or ":" in path
        ):
            raise InputError("unsupported License-File path")
        result.append((path, prefix + path))
    return result


def file_receipt(path, member, content):
    if content is None:
        return {"path": path, "member": member, "present": False}
    if len(content) > MAX_LICENSE_FILE:
        raise InputError("license-file byte limit exceeded")
    try:
        content.decode("utf-8")
    except UnicodeError as exc:
        raise InputError("license-file content is not UTF-8") from exc
    return {
        "path": path,
        "member": member,
        "present": True,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def compare_license_files(source, wheel):
    """Compare declared file receipts from the same snapshots as metadata.

    Extra wheel declarations are admitted only when their files are present.
    No search for undeclared notices, license interpretation or legal advice.
    """
    if source.license_files is None or wheel.license_files is None:
        raise InputError("license-file receipts were not requested")
    left = {item["path"]: item for item in source.license_files}
    right = {item["path"]: item for item in wheel.license_files}
    checks = []
    for path in sorted(left.keys() | right.keys()):
        a, b = left.get(path), right.get(path)
        if a is not None and not a["present"]:
            status = "MISSING_SOURCE_FILE"
        elif b is None:
            status = "MISSING_WHEEL_DECLARATION"
        elif not b["present"]:
            status = "MISSING_WHEEL_FILE"
        elif a is None:
            status = "ADDED_WHEEL_FILE"
        elif a["sha256"] != b["sha256"] or a["bytes"] != b["bytes"]:
            status = "CHANGED_BYTES"
        else:
            status = "PRESERVED"
        checks.append({"path": path, "status": status})
    mismatch = any(
        item["status"] not in {"PRESERVED", "ADDED_WHEEL_FILE"} for item in checks
    )
    return {
        "profile": "declared_license_file_bytes_v1",
        "status": "MISMATCH" if mismatch else "PRESERVED_IN_PROFILE",
        "limits": {
            "files_per_artifact": MAX_LICENSE_FILES,
            "bytes_per_file": MAX_LICENSE_FILE,
            "bytes_per_artifact": MAX_LICENSE_TOTAL,
        },
        "source_files": source.license_files,
        "wheel_files": wheel.license_files,
        "checks": checks,
        "not_established": [
            "license validity or compatibility",
            "legal compliance",
            "undeclared notice completeness",
            "whether a byte difference violates a packaging specification",
        ],
    }
