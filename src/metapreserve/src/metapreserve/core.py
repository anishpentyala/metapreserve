"""Conservative field-preservation checks, not metadata semantic equivalence."""

import re
from collections import Counter
from email import policy
from email.parser import Parser

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version


class InputError(ValueError):
    """Input falls outside the explicitly supported comparison profile."""


# Core Metadata 2.6 field cardinality. Deprecated fields remain recognized.
MULTIPLE = frozenset(
    """
platform supported-platform classifier requires-dist requires-external project-url
provides-extra import-name import-namespace provides-dist obsoletes-dist license-file
requires provides obsoletes dynamic
""".split()
)
SINGLE = frozenset(
    """
metadata-version name version summary description description-content-type keywords
author author-email maintainer maintainer-email license license-expression
requires-python home-page download-url
""".split()
)
KNOWN = MULTIPLE | SINGLE
IDENTITY = frozenset({"name", "version", "metadata-version"})
# PEP 808 allows these comma-list fields to be extended despite single headers.
# Their sub-item interpretation is deliberately outside this first profile.
COMMA_LISTS = frozenset({"keywords", "author-email", "maintainer-email"})
FIELD_MIN = {
    "dynamic": (2, 2),
    "license-expression": (2, 4),
    "license-file": (2, 4),
    "import-name": (2, 5),
    "import-namespace": (2, 5),
}


def parse_metadata(data):
    """Parse a bounded UTF-8 metadata document into ordered header values.

    Preserves repeated values and their order. Header names are case-insensitive;
    normal header folding is unfolded to one space. Other whitespace, requirement
    spelling and marker logic are NOT canonicalized. Description uses normalized
    line endings and supports either the legacy header encoding or a body.
    """
    if not isinstance(data, bytes) or len(data) > 1048576:
        raise InputError("metadata must be bytes, at most 1 MiB")
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise InputError("metadata is not UTF-8") from exc
    if "\x00" in text or "\r" in text.replace("\r\n", ""):
        raise InputError("NUL or bare carriage return in metadata")
    text = text.replace("\r\n", "\n")
    message = Parser(policy=policy.compat32).parsestr(text)
    if message.defects or message.is_multipart():
        raise InputError("malformed metadata header block")
    fields = {}
    for name, value in message.raw_items():
        key = name.lower()
        if key not in KNOWN:
            raise InputError("unsupported metadata field: " + name)
        if key == "description":
            value = value.replace("\n       |", "\n")
        else:
            value = re.sub(r"\n[ \t]+", " ", value)
        fields.setdefault(key, []).append(value)
    for key, values in fields.items():
        if key in SINGLE and len(values) != 1:
            raise InputError("repeated single-use field: " + key)
    body = message.get_payload()
    if body:
        if "description" in fields:
            raise InputError("description present in both header and body")
        fields["description"] = [body]
    for key in IDENTITY:
        if key not in fields or not fields[key][0].strip():
            raise InputError("missing required metadata field: " + key)
    version_text = fields["metadata-version"][0]
    if version_text not in {"2.1", "2.2", "2.3", "2.4", "2.5", "2.6"}:
        raise InputError("supported metadata versions are 2.1 through 2.6")
    metadata_version = tuple(map(int, version_text.split(".")))
    for key in fields:
        if metadata_version < FIELD_MIN.get(key, (1, 0)):
            raise InputError("field predates declared metadata version: " + key)
    try:
        name = canonicalize_name(fields["name"][0], validate=True)
        version = Version(fields["version"][0])
    except (ValueError, InvalidVersion) as exc:
        raise InputError("invalid distribution identity") from exc
    dynamic = []
    for value in fields.get("dynamic", []):
        key = value.lower()
        if key not in KNOWN or key in IDENTITY or key == "dynamic":
            raise InputError("invalid or unsupported Dynamic field: " + value)
        if key in dynamic:
            raise InputError("repeated Dynamic declaration: " + value)
        dynamic.append(key)
    return {
        "fields": fields,
        "metadata_version": metadata_version,
        "name": name,
        "version": str(version),
        "dynamic": dynamic,
    }


def _subsequence_witness(source, target):
    """Greedy embedding is complete for preserving an ordered subsequence."""
    matched = []
    position = 0
    for source_index, value in enumerate(source):
        while position < len(target) and target[position] != value:
            position += 1
        if position == len(target):
            return matched, source_index
        matched.append(position)
        position += 1
    return matched, None


def compare_metadata(source, target, *, built_from=False):
    """Return field-level evidence relative to a caller's build-lineage assertion.

    MISMATCH means failure under this lexical preservation profile, not proof of
    semantic inequivalence or a packaging-spec violation. No inference about
    provenance, binary files, installability, dependency solving or safety.
    """
    if type(built_from) is not bool:
        raise InputError("built_from must be a bool")
    left = parse_metadata(source)
    right = parse_metadata(target)
    if (left["name"], Version(left["version"])) != (
        right["name"],
        Version(right["version"]),
    ):
        raise InputError("source and wheel distribution identities differ")
    if not built_from:
        raise InputError("comparison requires an explicit built-from assertion")
    old = left["fields"]
    new = right["fields"]
    dynamic = set(left["dynamic"])
    checks = []
    changed = []
    skipped = []
    for key in sorted(
        (set(old) | set(new) | dynamic) - {"dynamic", "metadata-version"}
    ):
        a, b = old.get(key, []), new.get(key, [])
        if key in {"name", "version"}:
            checks.append(
                {"field": key, "rule": "normalized_identity", "status": "PRESERVED"}
            )
            continue
        if left["metadata_version"] < (2, 2):
            rule = "legacy_unconstrained"
        elif key not in dynamic:
            rule = "static_equal"
        elif left["metadata_version"] < (2, 6):
            rule = "dynamic_unconstrained"
        elif key in COMMA_LISTS and a:
            raise InputError(
                "2.6 dynamic comma-list preservation not supported: " + key
            )
        elif key in MULTIPLE:
            rule = "dynamic_preserve_sequence"
        else:
            rule = "dynamic_unconstrained"
        item = {"field": key, "rule": rule, "source": a, "wheel": b}
        if rule.endswith("unconstrained"):
            item["status"] = "UNCONSTRAINED"
            skipped.append(key)
        elif rule == "static_equal":
            # Repeated static values are compared as a multiset. Unlike 2.6
            # partial-dynamic lists, the static rule does not specify ordering.
            equal = Counter(a) == Counter(b) if key in MULTIPLE else a == b
            item["status"] = "PRESERVED" if equal else "MISMATCH"
        else:
            matched, missing = _subsequence_witness(a, b)
            item["matched_wheel_indices"] = matched
            if missing is None:
                item["status"] = "PRESERVED"
            else:
                item["status"] = "MISMATCH"
                item["first_unmatched_source_index"] = missing
        if item["status"] == "MISMATCH":
            changed.append(key)
        checks.append(item)
    return {
        "schema_version": 1,
        "status": "MISMATCH" if changed else "PRESERVED_IN_PROFILE",
        "basis": "caller_asserted_sdist_to_wheel_lineage",
        "comparison": "unfolded_field_values_not_semantic_equivalence",
        "source_metadata_version": ".".join(map(str, left["metadata_version"])),
        "wheel_metadata_version": ".".join(map(str, right["metadata_version"])),
        "identity": {"name": left["name"], "version": left["version"]},
        "mismatched_fields": changed,
        "constrained_nonidentity_fields": sum(
            item["status"] != "UNCONSTRAINED" and item["rule"] != "normalized_identity"
            for item in checks
        ),
        "unconstrained_fields": skipped,
        "checks": checks,
  }
