# metapreserve

Audit the metadata promises between an explicitly selected Python source
distribution and wheels built from it. No install, import, build hooks, extraction,
network access or dependency resolution occurs during an audit.

Core Metadata 2.6 changes the meaning of `Dynamic`: repeated values already in an
sdist can no longer disappear or change order in a wheel built from it. An audit
that simply skips every dynamic field misses those promises. An audit that skips
the entire distribution whenever *any* field is dynamic also misses unrelated
static changes.

This tool checks a deliberately narrow **field-value preservation profile**.
It is not a general metadata validator or a semantic dependency comparator.
A mismatch is evidence about the reported values under that profile, not an
automatic finding that a package violates the packaging specification.

## Run

Install from this checkout with `python -m pip install .`, then:

```sh
metapreserve dist/demo-1.0.tar.gz dist/demo-1.0-py3-none-any.whl --built-from
```

`--built-from` is required. It is your assertion that every supplied wheel was
built directly from this unmodified sdist. Matching names and versions are not
proof of that relationship. Do not use the assertion merely because two files
came from the same PyPI release. Wheel repair or downstream patches can change
metadata, and the standard explicitly warns against assuming a build relationship.

The output is one JSON document. Exit 0 means no mismatch in the supported
profile, exit 1 means one or more mismatches, and exit 2 means invalid,
unsupported or incomplete input. Argument syntax errors use argparse's stderr
and exit 2. No partial success report is emitted if a later artifact is invalid.
Output write failure also exits 2.

Each input has an archive SHA256 and byte count, plus the exact metadata member
and its SHA256. Every field check records the rule, source/wheel values and result.
A 2.6 dynamic list also records the wheel indices that preserve the source
sequence, or the first source index that cannot be matched in order. JSON reports
embed raw metadata values, which can include author email addresses; check the
audience before sharing them.

## What each metadata version means here

| Sdist metadata | Field | Comparison |
|---|---|---|
| 2.1 | Non-identity | Unconstrained, reported explicitly |
| 2.2 through 2.5 | Listed in `Dynamic` | Unconstrained, reported explicitly |
| 2.2 through 2.6 | Not listed in `Dynamic` | Same unfolded value, or multiset for repeated headers |
| 2.6 | Dynamic repeated header | Source values must appear as an ordered subsequence of wheel values, including duplicate occurrences |
| 2.6 | Dynamic Keywords | Comma-list items must be preserved in relative order, including duplicate occurrences |
| 2.6 | Other dynamic single-value header | Unconstrained, except the refused email comma lists below |

New values may be inserted before or between existing repeated values as long as
the existing values and their relative order are preserved. `packaging` is used
only for name/version and artifact-filename parsing. Names are normalized;
versions compare under PEP 440. The source and wheel metadata format versions
may differ. Wheel `Dynamic` declarations do not weaken source promises.

Fields without values in either file but named in the source's `Dynamic` list
are still included. `unconstrained_fields` and `constrained_nonidentity_fields`
make it visible when a report contains little or no preservation evidence. Exit 0
with zero constrained non-identity fields is not a certificate of metadata equality.

### Comparison limits that matter

- Header names are case-insensitive. Folded non-description headers are unfolded
  to one space; line endings are normalized to LF. Other whitespace is retained.
- Requirement spellings, marker expressions, URL labels, free text and equivalent
  dependency constraints are **not** semantically normalized. For example,
  `a>=1` and `a >= 1` are a profile mismatch even if a resolver interprets them the
  same way. Inspect the field evidence before treating a mismatch as a defect.
- Static repeated headers are compared as multisets, so order alone is not a
  static mismatch. The explicit order rule applies to 2.6 dynamic lists.
- A 2.6 dynamic `Keywords` header is split on commas, with surrounding whitespace
  stripped from each item, matching `packaging`'s keyword parsing. Source items
  must appear as an ordered subsequence of wheel items, including duplicates.
  Item case and internal whitespace are not normalized. The raw unfolded values
  remain in the report; `source_items`, `wheel_items` and `index_unit` make clear
  that matched and unmatched indices refer to comma-list items, not headers.
  Empty items are refused, including a blank header or leading/trailing comma.
  This is a bounded preservation profile, not full keyword validity checking.
  Static Keywords and 2.2 through 2.5 dynamic Keywords keep their prior behavior.
- `Author-Email` and `Maintainer-Email` also have special comma-list extension
  rules in PEP 808. A 2.6 source that supplies either and marks it dynamic is
  still refused. Quoted commas and email syntax are not parsed as keywords.
- Legacy `Description` headers and message bodies are supported, with line-ending
  normalization. A description supplied in both places is refused. Paragraph,
  markup and trailing-whitespace equivalence are not inferred.
- Unknown fields, unsupported metadata versions, duplicate single-use headers,
  malformed headers, forbidden/unknown `Dynamic` names and invalid identity are
  input errors. Many other field-specific validity rules are not checked. A
  preserved report does not mean all values are valid metadata.

## Archive boundary

Inputs are wheels, `.tar.gz` sdists or `.zip` sdists. Metadata must be in one
unambiguous top-level distribution directory: `*.dist-info/METADATA` for wheels,
`*/PKG-INFO` for sdists. Metadata identity must match the artifact filename. Wheel
metadata directory identity must also match the filename.

Files are snapshotted before parsing. Archives are never extracted. Member paths
must be canonical relative POSIX paths. Duplicate names, special/link members,
encrypted ZIP entries and oversized inputs are refused. Unrelated package file
contents are not imported or interpreted.

Admission limits: 64 MiB compressed/archive bytes, 128 MiB expanded tar bytes,
10,000 members per archive, 1 MiB per metadata member, eight wheels per invocation
and 256 MiB aggregate archive bytes. These are not a hostile-input sandbox or
hard CPU/memory limits. Gzip expansion and ZIP central directories still consume
resources. Payload RECORD integrity, package files, compression methods for
unread members and full wheel/sdist conformance are outside the model.

## Checked backend example

`evidence/backend-demo` contains our small local project using
scikit-build-core 1.1.2 and dynamic-metadata 0.7.0. Running `python -m build`
built an actual Metadata 2.6 sdist, then a Metadata 2.2 wheel from that sdist.
The sdist's dynamic `Requires-Dist` values were `packaging` and
`typing-extensions>=4`. The wheel preserved both. The auditor returned
`PRESERVED_IN_PROFILE` with matched indices `[0, 1]`.

`evidence/backend_controls.py` creates deliberate metadata mutations of that
local wheel. Removing or changing a promised dependency or reversing its order
returns `MISMATCH`. Inserting an extra dependency while preserving the source
sequence passes. Mutants are **not** genuine backend output and their RECORD
hashes are deliberately stale, which this tool does not audit. No upstream
backend defect, production incident or user adoption is claimed.

## Reproduce

Run the implementation mutation checks with:

```sh
PYTHONPATH=src python evidence/mutation_checks.py
```

Rebuild the local demo (requires the pinned backend and a build frontend), then
run its archive controls. Build hooks execute only during this explicit demo
build, not during an audit:

```sh
cd evidence/backend-demo
python -m build
cd ../..
PYTHONPATH=src python evidence/backend_controls.py \
  evidence/backend-demo/dist/metapreserve_demo-1.0.tar.gz \
  evidence/backend-demo/dist/metapreserve_demo-1.0-py3-none-any.whl \
  /tmp/metapreserve-backend-controls
```

The control script labels genuine preserved output separately from deliberate
METADATA mutants. Development logs in the private review packet include an older
32-test run; the committed final test logs contain 33 tests, with the output
failure regression added before review. The 32-test run is not the final total.

## Existing tools and scope

- [package-autopsy](https://github.com/lupaxa-developers-toolbox/package-autopsy)
  provides much broader distribution inspection and a wheel/sdist consistency
  command. Its implementation inspected during development compares name,
  version, Python requirement and dependency sets, and marks a pair indeterminate
  when the sdist declares dynamic fields. This project instead gives a per-field
  version-aware preservation report with 2.6 ordered-list witnesses. It is not a
  replacement for package-autopsy's other checks.
- [wheeltruth](https://pypi.org/project/wheeltruth/) checks package contents,
  RECORD and entry-point module presence, with optional install/import smoke
  tests. This project does none of those checks.
- [check-sdist](https://github.com/henryiii/check-sdist) compares sdist contents
  against Git. This project compares selected metadata fields between artifacts.

The basis is the current [Core Metadata specification](https://packaging.python.org/en/latest/specifications/core-metadata/),
including the 2.6 change approved in May 2026, and
[PEP 808](https://peps.python.org/pep-0808/). These source rules are implemented
only within the comparison and archive boundary above.

## Development

Python 3.10+; runtime dependency `packaging>=24`.

```sh
python -m pip install packaging pytest ruff build
PYTHONPATH=src python -m pytest -q
ruff check src tests evidence/backend_controls.py
ruff format --check src tests evidence/backend_controls.py
```

Tests include an independent brute-force embedding oracle for every pair of
binary sequences of length zero through four, archive limits and malformed
inputs, legacy/dynamic/static controls, and all-or-nothing CLI outcomes.
MIT licensed. No registry release is implied by this source checkout.
