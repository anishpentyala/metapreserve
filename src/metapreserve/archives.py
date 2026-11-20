"""Bounded metadata and optional declared-license receipts. Never extract."""

import gzip
import hashlib
import io
import os
import stat
import tarfile
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from packaging.utils import parse_sdist_filename, parse_wheel_filename
from packaging.version import Version

from .core import InputError, parse_metadata
from .licensefiles import (
    MAX_LICENSE_FILE,
    MAX_LICENSE_TOTAL,
    file_receipt,
    license_members,
)

MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXPANDED_TAR = 128 * 1024 * 1024
MAX_MEMBERS = 10000
MAX_METADATA = 1048576


@dataclass(frozen=True)
class Artifact:
    metadata: bytes
    receipt: dict
    license_files: list | None = None


def _path(name):
    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
        raise InputError("noncanonical archive path")
    stripped = name[:-1] if name.endswith("/") else name
    if any(piece in {"", ".", ".."} for piece in stripped.split("/")):
        raise InputError("noncanonical archive path")
    return PurePosixPath(stripped).parts


def _zip_regular(info):
    """ZIP directory markers need not agree with the filename's final slash."""
    return (
        not info.is_dir()
        and stat.S_IFMT(info.external_attr >> 16) != stat.S_IFDIR
        and not info.external_attr & 0x10
    )


def _zip_metadata(data, kind, inspect_licenses=False):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            raise InputError("archive member count exceeds limit")
        seen = set()
        candidates = []
        for info in infos:
            # ZipInfo.filename can be silently truncated at NUL. Validate the
            # stored central-directory name, before any library sanitization.
            parts = _path(info.orig_filename)
            if info.filename in seen:
                raise InputError("duplicate archive member")
            seen.add(info.filename)
            mode = info.external_attr >> 16
            if stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise InputError("special archive member")
            if info.flag_bits & 1:
                raise InputError("encrypted archive member")
            if kind == "wheel":
                selected = (
                    len(parts) == 2
                    and parts[0].endswith(".dist-info")
                    and parts[1] == "METADATA"
                )
            else:
                selected = len(parts) == 2 and parts[1] == "PKG-INFO"
            if selected:
                if not _zip_regular(info) or info.file_size > MAX_METADATA:
                    raise InputError("invalid or oversized metadata member")
                candidates.append(info)
        if len(candidates) != 1:
            raise InputError(
                "expected exactly one top-level distribution metadata member"
            )
        info = candidates[0]
        with archive.open(info) as source:
            metadata = source.read(MAX_METADATA + 1)
        if len(metadata) > MAX_METADATA:
            raise InputError("metadata exceeds limit")
        licenses = None
        if inspect_licenses:
            licenses = []
            total = 0
            members = {entry.filename: entry for entry in infos}
            for path, name in license_members(metadata, info.filename, kind):
                entry = members.get(name)
                content = None
                if entry is not None:
                    if not _zip_regular(entry) or entry.file_size > MAX_LICENSE_FILE:
                        raise InputError("invalid or oversized license-file member")
                    with archive.open(entry) as source:
                        content = source.read(MAX_LICENSE_FILE + 1)
                    total += len(content)
                    if total > MAX_LICENSE_TOTAL:
                        raise InputError("license-file total byte limit exceeded")
                licenses.append(file_receipt(path, name, content))
        return info.filename, metadata, licenses


def _tar_metadata(data, inspect_licenses=False):
    # A bounded expanded snapshot prevents a tiny gzip stream from causing an
    # unbounded archive walk. This is an admission limit, not a resource sandbox.
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as source:
        expanded = source.read(MAX_EXPANDED_TAR + 1)
    if len(expanded) > MAX_EXPANDED_TAR:
        raise InputError("expanded tar exceeds limit")
    with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
        seen = set()
        candidates = []
        for count, member in enumerate(archive, 1):
            if count > MAX_MEMBERS:
                raise InputError("archive member count exceeds limit")
            parts = _path(member.name)
            if member.name in seen:
                raise InputError("duplicate archive member")
            seen.add(member.name)
            if not (member.isfile() or member.isdir()):
                raise InputError("special archive member")
            if len(parts) == 2 and parts[1] == "PKG-INFO":
                if not member.isfile() or member.size > MAX_METADATA:
                    raise InputError("invalid or oversized metadata member")
                candidates.append(member)
        if len(candidates) != 1:
            raise InputError("expected exactly one top-level PKG-INFO member")
        member = candidates[0]
        source = archive.extractfile(member)
        if source is None:
            raise InputError("metadata is not a regular file")
        with source:
            metadata = source.read(MAX_METADATA + 1)
        if len(metadata) > MAX_METADATA:
            raise InputError("metadata exceeds limit")
        licenses = None
        if inspect_licenses:
            licenses = []
            total = 0
            members = {entry.name: entry for entry in archive.getmembers()}
            for path, name in license_members(metadata, member.name, "sdist"):
                entry = members.get(name)
                content = None
                if entry is not None:
                    if not entry.isfile() or entry.size > MAX_LICENSE_FILE:
                        raise InputError("invalid or oversized license-file member")
                    with archive.extractfile(entry) as source:
                        content = source.read(MAX_LICENSE_FILE + 1)
                    total += len(content)
                    if total > MAX_LICENSE_TOTAL:
                        raise InputError("license-file total byte limit exceeded")
                licenses.append(file_receipt(path, name, content))
        return member.name, metadata, licenses


def read_artifact(path, kind, *, inspect_licenses=False):
    """Snapshot an artifact; optionally inspect declared PEP 639 license files.

    Metadata and license receipts always come from the same immutable snapshot.
    """
    if kind not in {"wheel", "sdist"}:
        raise InputError("artifact kind must be wheel or sdist")
    try:
        path = Path(path)
        filename = path.name
        if kind == "wheel":
            name, version, _, _ = parse_wheel_filename(filename)
        else:
            name, version = parse_sdist_filename(filename)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise InputError("artifact must be a regular file")
            data = source.read(MAX_ARCHIVE + 1)
        if len(data) > MAX_ARCHIVE:
            raise InputError("archive byte limit exceeded")
        if filename.endswith((".whl", ".zip")):
            member, metadata, licenses = _zip_metadata(data, kind, inspect_licenses)
        elif filename.endswith(".tar.gz") and kind == "sdist":
            member, metadata, licenses = _tar_metadata(data, inspect_licenses)
        else:
            raise InputError(
                "supported archives are .whl, .zip sdist and .tar.gz sdist"
            )
        parsed = parse_metadata(metadata)
        if parsed["name"] != name or Version(parsed["version"]) != version:
            raise InputError("metadata identity differs from archive filename")
        top = member.split("/")[0]
        if kind == "wheel":
            # Wheel dist-info stems use normalized distribution and version.
            expected = filename[:-4].split("-")[:2]
            if top != "-".join(expected) + ".dist-info":
                raise InputError("wheel metadata directory differs from filename")
        return Artifact(
            metadata,
            {
                "filename": filename,
                "kind": kind,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "metadata_member": member,
                "metadata_sha256": hashlib.sha256(metadata).hexdigest(),
            },
            licenses,
        )
    except InputError:
        raise
    except (
        OSError,
        ValueError,
        TypeError,
        EOFError,
        zipfile.BadZipFile,
        tarfile.TarError,
        zlib.error,
        RuntimeError,
        NotImplementedError,
    ) as exc:
        raise InputError("cannot inspect artifact: " + str(exc)) from exc
