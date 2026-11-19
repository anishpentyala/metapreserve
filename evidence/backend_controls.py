"""Produce reproducible metadata mutation controls from our own backend demo.

Only rewrites the METADATA bytes in a locally built wheel. Mutants are explicitly
not genuine backend builds. Their RECORD is stale, which is outside this audit.
"""

import json
import sys
import zipfile
from pathlib import Path

from metapreserve.archives import read_artifact
from metapreserve.core import compare_metadata


def run(source_path, wheel_path, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    source = read_artifact(source_path, "sdist")
    original = read_artifact(wheel_path, "wheel")
    before = original.metadata
    text = before.decode()
    variants = {
        "preserved": text,
        "removed": text.replace("Requires-Dist: packaging\n", ""),
        "reordered": text.replace(
            "Requires-Dist: packaging\nRequires-Dist: typing-extensions>=4",
            "Requires-Dist: typing-extensions>=4\nRequires-Dist: packaging",
        ),
        "appended": text.replace(
            "Requires-Dist: packaging",
            "Requires-Dist: new-dependency\nRequires-Dist: packaging",
        ),
        "changed": text.replace(
            "Requires-Dist: packaging", "Requires-Dist: packaging>=999"
        ),
    }
    reports = {}
    for label, replacement in variants.items():
        if label != "preserved" and replacement == text:
            raise RuntimeError("mutation did not apply: " + label)
        destination = output / label / Path(wheel_path).name
        destination.parent.mkdir(exist_ok=True)
        with (
            zipfile.ZipFile(wheel_path) as reader,
            zipfile.ZipFile(destination, "w") as writer,
        ):
            for info in reader.infolist():
                writer.writestr(
                    info,
                    replacement.encode()
                    if info.filename.endswith(".dist-info/METADATA")
                    else reader.read(info),
                )
        artifact = read_artifact(destination, "wheel")
        report = compare_metadata(source.metadata, artifact.metadata, built_from=True)
        report["receipt"] = artifact.receipt
        report["control_type"] = (
            "genuine_backend_output"
            if label == "preserved"
            else "deliberate_metadata_mutation_not_backend_output"
        )
        reports[label] = report
    expected = {
        "preserved": "PRESERVED_IN_PROFILE",
        "removed": "MISMATCH",
        "reordered": "MISMATCH",
        "appended": "PRESERVED_IN_PROFILE",
        "changed": "MISMATCH",
    }
    for key, value in expected.items():
        if reports[key]["status"] != value:
            raise RuntimeError("unexpected control result: " + key)
    (output / "reports.json").write_text(
        json.dumps({"sdist": source.receipt, "controls": reports}, indent=2) + "\n"
    )
    print(
        json.dumps(
            {key: value["status"] for key, value in reports.items()}, sort_keys=True
        )
    )


if __name__ == "__main__":
    run(*sys.argv[1:])
