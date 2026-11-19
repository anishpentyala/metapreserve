# Qualification, 2026-11-19

Private first implementation. No registry release, upstream defect or adoption
claim. The artifact controls use a locally authored demo, not someone else's
production package.

## Demand and differentiation

Python's current Core Metadata 2.6 specification (approved May 2026) adds
preservation of already-present repeated values even when a field is dynamic.
The older 2.2–2.5 rule permits a dynamic field to change entirely. The distinction
is directly exercised by scikit-build-core's PEP 808 implementation and tests.
This is a metadata-contract audit, not a new dependency resolver.

Read sources:

- https://packaging.python.org/en/latest/specifications/core-metadata/
  Canonical 2.6 Dynamic rules, parser guidance, identity rules, and warning that
  matching release artifacts do not establish build lineage.
- https://peps.python.org/pep-0808/
  Partial-dynamic field rationale, preservation of order and values, comma-list
  special cases. The tool refuses the supported-profile gap for dynamic
  Keywords/Author-Email/Maintainer-Email rather than incorrectly passing it.
- https://github.com/scikit-build/scikit-build-core/commit/293e093e9933a83fc6772124a3d7d0167c42e4d0
  Actual backend implementation and tests for 2.6 source metadata with a
  static portion of dynamic dependencies. The separate local demo was authored
  for this audit; it is not copied third-party source code.
- https://github.com/pypa/setuptools/issues/4336
  Real historical example of different but equivalent requirement formatting.
  This is why profile mismatches must not be called semantic inequivalence or
  automatic specification violations. No claim that the historical issue is
  still unfixed.
- https://github.com/lupaxa-developers-toolbox/package-autopsy
  Direct comparator. Its `src/lupaxa/package_autopsy/diff.py` implementation was
  read through the live repository tree and Git blob API on this date. Its
  consistency report compares identity, Python requirement and dependency sets,
  with a distribution-wide dynamic indeterminate flag, not version-aware
  per-field 2.6 ordered preservation witnesses.
- https://pypi.org/project/wheeltruth/
  Comparator for files, RECORD, module presence and optional installed smoke
  checks, not this field-preservation contract.
- https://github.com/henryiii/check-sdist
  Comparator for sdist versus Git file coverage, not metadata promises between
  artifacts.

These checks support a distinct narrow purpose, not a claim to be the first
metadata comparison program or to cover every packaging standard.

## Executed controls

- Python 3.10.12 and 3.15.2: 33 tests passed, 62 subtests passed on each runtime.
  Includes 961 source/target sequence pairs checked against independent
  brute-force embeddings. Those are iterations within a test, not 961 tests.
- Every repeated-header category has a reversed-order 2.6 dynamic control.
- Genuine current-backend demo: scikit-build-core 1.1.2 with dynamic-metadata
  0.7.0 produced a 2.6 sdist, then a 2.2 wheel directly from that sdist. Both
  promised dependency values were preserved. Receipt and index witnesses are
  in `backend-controls/reports.json`.
- Deliberate wheel METADATA mutants: removal, modification and reversed order
  are mismatches. Insertion of a new dependency preserves the sequence. These
  mutants are not backend builds; RECORD becomes stale and is not checked here.
- Three implementation mutants were tested with focused assertions, not setup
  errors: ignoring 2.6 dynamic promises (3 failures), replacing ordered/multiplicity
  preservation with set containment (2 failures), and skipping static promises
  (3 failures). Logs are under `evidence/mutation-*.log`.
- Source build into sdist and wheel succeeded. The tool audited its own actual
  sdist-to-wheel output as preserved in profile. Self-build receipt is a checked
  development snapshot, not an immutable receipt for later commits.
- CLI preserved/mismatch/invalid exits and invalid-later-artifact all-or-nothing
  behavior are tested. `/dev/full` yields exit 2 instead of a shutdown-flush error.
  POSIX FIFO input is refused without waiting for a writer.
- Ruff selected correctness/import checks and formatting passed.

Two setup mistakes were corrected during development: a 3.15 test invocation
initially omitted PYTHONPATH, and an old preinstalled setuptools did not satisfy
this project's build requirement. Neither was counted as an implementation
negative control. Final tests used the proper source path and final builds used
an upgraded backend satisfying setuptools>=77.

## Not established

No Windows/macOS execution yet. The code is intended to be portable, but only
Linux runtime results are claimed. No hostile-input fuzz campaign, archive
conformance proof, dependency semantic equivalence, actual backend bug,
production usefulness measurement, external users, stars or registry install
has been established. Archive size/member checks are admission limits, not a
hard resource sandbox. JSON reports may contain metadata values such as author
addresses; choose the audience before sharing a report.
