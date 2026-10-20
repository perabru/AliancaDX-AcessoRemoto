#!/usr/bin/env python3
"""Fetch pinned official sources, never native binaries. Requires Python 3.12+.

This explicit step downloads 669 dependency archives and expands roughly 1 GiB.
Cargo compilation is a separate build_android.sh operation.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import urllib.parse
import urllib.request

from prepare_native_source import configure

HERE = Path(__file__).resolve().parent


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_record(record):
    url = urllib.parse.urlsplit(record["url"])
    if url.scheme != "https" or url.hostname not in {"codeload.github.com", "static.crates.io"}:
        raise ValueError("Expected pinned official GitHub/crates.io HTTPS source")
    if not re.fullmatch(r"[a-f0-9]{64}", record["sha256"]):
        raise ValueError("Invalid pinned SHA-256")
    return record


def source_records():
    upstream = json.loads((HERE / "upstream-source.json").read_text())
    inventory = json.loads((HERE / "source-inventory.json").read_text())
    records = [validate_record({"url": upstream["url"], "sha256": upstream["sha256"],
                               "prefix": "upstream", "upstream": True})]
    for package in inventory["packages"]:
        if not package.get("download_url"):
            continue
        prefix = package["expanded_prefix"]
        parts = PurePosixPath(prefix).parts
        if len(parts) != 3 or parts[0] != "locked-sources" or parts[1] not in {"registry", "git"} or ".." in parts:
            raise ValueError("Invalid dependency extraction prefix")
        records.append(validate_record({"url": package["download_url"], "sha256": package["sha256"],
                                        "prefix": prefix, "upstream": False}))
    for filename, field in (("Cargo.lock.upstream", "cargo_lock_sha256"), ("Cargo.toml.upstream", "cargo_toml_sha256")):
        if sha256(HERE / filename) != upstream[field]:
            raise ValueError("Pinned upstream metadata was modified: " + filename)
    return upstream, records


def fetch(record, cache):
    archive = cache / (record["sha256"] + ".tar.gz")
    if archive.exists():
        if sha256(archive) != record["sha256"]:
            raise ValueError("Cached archive failed SHA-256: " + archive.name)
        return archive
    cache.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(record["url"], headers={"User-Agent": "AliancaDX-source-bootstrap/6.2.0"})
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as stream:
            temporary = Path(stream.name)
            with urllib.request.urlopen(request, timeout=120) as response:
                shutil.copyfileobj(response, stream, length=1 << 20)
        if sha256(temporary) != record["sha256"]:
            raise ValueError("Downloaded archive failed SHA-256: " + record["url"])
        os.replace(temporary, archive)
        return archive
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def extract(archive, destination, upstream=False):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unsafe source archive member")
        members = [member for member in source.getmembers() if not upstream or
                   Path(member.name).suffix.lower() not in {".dll", ".sys", ".exe", ".jks", ".keystore", ".p12", ".pfx", ".pk8", ".key"}]
        source.extractall(destination, members=members, filter="data")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=HERE / ".build")
    parser.add_argument("--cache", type=Path, default=HERE / ".downloads")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--verify-manifest", action="store_true", help="Validate pins and lock metadata without network requests")
    args = parser.parse_args()
    upstream, records = source_records()
    if args.verify_manifest:
        print(json.dumps({"upstream_commit": upstream["commit"], "verified_source_records": len(records)}))
        return
    destination, cache = args.destination.resolve(), args.cache.resolve()
    if destination.exists():
        if (destination / "EasyTier-2.6.4/.aliancadx-native-prepared.json").is_file():
            print("Prepared sources already present; retaining local edits: " + str(destination))
            return
        parser.error("Destination already exists. Choose a new destination; no source directory will be overwritten.")
    if not 1 <= args.workers <= 16:
        parser.error("--workers must be 1..16")
    cache.mkdir(parents=True, exist_ok=True)
    unique = {record["sha256"]: record for record in records}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        archives = dict(zip(unique, pool.map(lambda record: fetch(record, cache), unique.values())))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".native-sources-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "prepared"
        stage.mkdir()
        for record in records:
            extract(archives[record["sha256"]], stage / record["prefix"], upstream=record["upstream"])
        upstream_folder = stage / "upstream" / ("EasyTier-" + upstream["commit"])
        for filename, field in (("Cargo.lock", "cargo_lock_sha256"), ("Cargo.toml", "cargo_toml_sha256")):
            if sha256(upstream_folder / filename) != upstream[field]:
                raise ValueError("Upstream metadata does not match the pinned release: " + filename)
        upstream_folder.rename(stage / "EasyTier-2.6.4")
        (stage / "upstream").rmdir()
        # Publish verified inputs before embedding absolute vendor paths.
        stage.rename(destination)
    root = configure(destination)
    (destination / "bootstrap-verification.json").write_text(json.dumps({
        "upstream_commit": upstream["commit"], "upstream_sha256": upstream["sha256"],
        "dependency_records": len(records) - 1, "all_downloaded_archives_sha256_verified": True,
        "rust_runtime_source_changed": False, "prebuilt_android_libraries_downloaded": False}, indent=2) + "\n")
    print(root)


if __name__ == "__main__":
    main()
