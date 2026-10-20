#!/usr/bin/env python3
"""Collect the locked EasyTier source and license inventory without running Cargo.

Downloads are content checked against Cargo.lock. The conservative dependency
closure includes target-specific and development/build dependencies; its presence
does not imply that every crate is linked into the distributed Windows binaries.
This is a packaging-time utility and never runs on an installed computer.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import time
import tomllib
import urllib.request


def locked_closure(source: Path) -> list[dict]:
    packages = tomllib.loads((source / "Cargo.lock").read_text())["package"]
    by_name: dict[str, list[dict]] = {}
    for package in packages:
        by_name.setdefault(package["name"], []).append(package)
    selected: dict[tuple[str, str], dict] = {}

    def visit(package: dict) -> None:
        key = package["name"], package["version"]
        if key in selected:
            return
        selected[key] = package
        for dependency in package.get("dependencies", []):
            parts = dependency.split()
            choices = [p for p in by_name[parts[0]] if len(parts) == 1 or p["version"] == parts[1]]
            if len(choices) != 1:
                raise RuntimeError(f"Ambiguous locked dependency: {dependency}")
            visit(choices[0])

    visit(by_name["easytier"][0])
    visit(by_name["easytier-rpc-build"][0])
    return sorted(selected.values(), key=lambda p: (p["name"], p["version"]))


def download(url: str, destination: Path, checksum: str | None = None) -> bytes:
    if destination.exists():
        data = destination.read_bytes()
        if checksum is None or hashlib.sha256(data).hexdigest() == checksum:
            return data
    last_error = None
    for attempt in range(5):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "MiniAnyDesk-license-inventory/1.0"})
            with urllib.request.urlopen(request, timeout=50) as response:
                data = response.read()
            if checksum and hashlib.sha256(data).hexdigest() != checksum:
                raise ValueError(f"Checksum mismatch: {destination.name}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(destination.suffix + ".part")
            temporary.write_bytes(data)
            temporary.replace(destination)
            return data
        except Exception as error:
            last_error = error
            time.sleep(min(1 + attempt, 5))
    raise RuntimeError(f"Cannot download {url}: {last_error}")


def is_notice(path: str) -> bool:
    basename = PurePosixPath(path).name.lower()
    return (bool(re.match(r"^(licen[cs]e|copying|copyright|notice|authors)([-._].*|$)", basename))
            or any(part.lower() in {"licenses", "licences"} for part in PurePosixPath(path).parts))


def inventory_archive(data: bytes, name: str, version: str) -> tuple[dict, list[tuple[str, str]]]:
    metadata: dict = {}
    notices: list[tuple[str, str]] = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        seen_notices = set()
        for member in archive.getmembers():
            if not member.isfile() or member.size > 3_000_000:
                continue
            parts = PurePosixPath(member.name).parts
            if member.name.endswith("/Cargo.toml") and len(parts) == 2:
                raw = archive.extractfile(member).read()
                try:
                    metadata = tomllib.loads(raw.decode("utf-8"))["package"]
                except (ValueError, KeyError):
                    pass
            if is_notice(member.name):
                text = archive.extractfile(member).read().decode("utf-8", errors="replace")
                notices.append((member.name, text))
                seen_notices.add(member.name)
        license_file = metadata.get("license-file")
        if license_file:
            for member in archive.getmembers():
                if (member.isfile() and member.size < 3_000_000
                        and member.name.endswith("/" + license_file)
                        and member.name not in seen_notices):
                    notices.append((member.name, archive.extractfile(member).read().decode("utf-8", errors="replace")))
    return {"name": name, "version": version, "license": metadata.get("license"),
            "license_file": metadata.get("license-file"), "repository": metadata.get("repository"),
            "notice_files": [n for n, _ in notices]}, notices


def collect_one(package: dict, cache: Path) -> tuple[dict, list[tuple[str, str]]]:
    name, version = package["name"], package["version"]
    source = package.get("source", "")
    if source.startswith("registry+"):
        url = f"https://static.crates.io/crates/{name}/{name}-{version}.crate"
        filename = f"{name}-{version}.crate"
        data = download(url, cache / filename, package["checksum"])
    elif source.startswith("git+"):
        repo, revision = source[4:].split("#", 1)
        repo = repo.split("?", 1)[0].removesuffix(".git")
        owner_path = repo.removeprefix("https://github.com/")
        url = f"https://codeload.github.com/{owner_path}/tar.gz/{revision}"
        filename = f"git-{name}-{version}-{revision}.tar.gz"
        data = download(url, cache / filename)
    else:
        return {"name": name, "version": version, "source": "EasyTier source archive"}, []
    row, notices = inventory_archive(data, name, version)
    row.update({"source": source, "download_url": url, "archive": filename,
                "sha256": hashlib.sha256(data).hexdigest()})
    return row, notices


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=20)
    args = parser.parse_args()
    packages = locked_closure(args.source)
    results = []
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(collect_one, package, args.cache): package for package in packages}
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            package = futures[future]
            try:
                results.append(future.result())
            except Exception as error:
                errors.append({"package": package["name"], "version": package["version"], "error": str(error)})
            if index % 25 == 0 or index == len(packages):
                print(json.dumps({"completed": index, "total": len(packages), "errors": len(errors)}), flush=True)
    args.output.mkdir(parents=True, exist_ok=True)
    results.sort(key=lambda pair: (pair[0]["name"], pair[0]["version"]))
    inventory = {"schema": 1, "scope": "conservative Cargo.lock dependency closure of easytier and easytier-rpc-build; all platforms/build/dev dependencies", "packages": [row for row, _ in results], "errors": errors}
    (args.output / "license-inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n")
    notice_parts = ["THIRD-PARTY SOURCE AND LICENSE INVENTORY — EasyTier 2.6.4\n",
                    "This is a conservative source-level dependency closure, not a claim that every listed package is linked into the Windows executables.\n",
                    "Each registry archive was verified against its SHA-256 in upstream Cargo.lock. Git sources are pinned to the upstream lock-file commit.\n"]
    for row, notices in results:
        notice_parts += ["\n" + "=" * 78 + "\n", f"{row['name']} {row['version']}\n", f"License expression: {row.get('license') or row.get('license_file') or 'see source'}\n", f"Source: {row.get('download_url') or row['source']}\n"]
        if not notices and row.get("archive"):
            notice_parts.append("No separately named license notice in this source archive. Consult the complete corresponding source included with the package.\n")
        for filename, text in notices:
            notice_parts += [f"\n--- {filename} ---\n", text, "\n"]
    (args.output / "CARGO_THIRD_PARTY_LICENSES.txt").write_text("".join(notice_parts))
    if errors:
        raise SystemExit(f"Incomplete inventory: {len(errors)} source downloads failed")


if __name__ == "__main__":
    main()
