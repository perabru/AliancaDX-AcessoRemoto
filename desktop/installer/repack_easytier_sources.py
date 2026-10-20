#!/usr/bin/env python3
"""Losslessly expand the verified dependency archives into one solid XZ tar.

The registry archive SHA-256 from Cargo.lock remains recorded. Each source
member is copied, including directories, symlinks, mode and payload. A separate
semantic tree SHA-256 is computed from the original archive while copying and
then recomputed independently by reading the finished XZ archive. No source
archive is extracted to the filesystem and no downloaded code is executed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile


class HashingReader:
    def __init__(self, stream):
        self.stream = stream
        self.hash = hashlib.sha256()

    def read(self, size=-1):
        data = self.stream.read(size)
        self.hash.update(data)
        return data


def update_tree(tree, member, relative_path, content_digest):
    row = {"path": relative_path, "type": member.type.decode("ascii"),
           "mode": member.mode & 0o7777, "size": member.size,
           "linkname": member.linkname, "payload_sha256": content_digest}
    tree.update((json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode())


def expand(inventory_path: Path, cache: Path, output: Path, supplemental: Path):
    inventory = json.loads(inventory_path.read_text())
    packages = [p for p in inventory["packages"] if p.get("archive")]
    if inventory.get("errors"):
        raise RuntimeError("Source inventory is incomplete")
    partial = output.with_suffix(output.suffix + ".part")
    with partial.open("wb") as destination:
        compressor = subprocess.Popen(
            ["xz", "-6", "-T2", "--lzma2=dict=64MiB", "--block-size=256MiB", "-c"],
            stdin=subprocess.PIPE, stdout=destination)
        try:
            with tarfile.open(fileobj=compressor.stdin, mode="w|") as archive:
                for index, package in enumerate(packages, 1):
                    path = cache / package["archive"]
                    if hashlib.sha256(path.read_bytes()).hexdigest() != package["sha256"]:
                        raise ValueError(f"Original source checksum mismatch: {path.name}")
                    namespace = "git" if package["source"].startswith("git+") else "registry"
                    prefix = f"locked-sources/{namespace}/{package['name']}-{package['version']}"
                    package["expanded_prefix"] = prefix
                    tree = hashlib.sha256()
                    members = byte_count = 0
                    with tarfile.open(path, "r:gz") as original:
                        for member in original:
                            if PurePosixPath(member.name).is_absolute() or ".." in PurePosixPath(member.name).parts:
                                raise ValueError(f"Unsafe original source path: {member.name}")
                            # Keep the entire original member path underneath the package namespace.
                            cloned = copy.copy(member)
                            cloned.pax_headers = dict(member.pax_headers)
                            cloned.name = prefix + "/" + member.name
                            cloned.pax_headers.pop("path", None)
                            if member.islnk():
                                cloned.linkname = prefix + "/" + member.linkname
                                cloned.pax_headers.pop("linkpath", None)
                            reader = HashingReader(original.extractfile(member)) if member.isfile() else None
                            archive.addfile(cloned, reader)
                            content = reader.hash.hexdigest() if reader else None
                            update_tree(tree, member, member.name, content)
                            members += 1
                            byte_count += member.size if member.isfile() else 0
                    package["expanded_tree_sha256"] = tree.hexdigest()
                    package["expanded_members"] = members
                    package["expanded_file_bytes"] = byte_count
                    if index % 25 == 0 or index == len(packages):
                        print(json.dumps({"expanded": index, "packages": len(packages)}), flush=True)
                data = supplemental.read_bytes()
                member = tarfile.TarInfo("locked-sources/supplemental-notices.json")
                member.size = len(data)
                member.mode = 0o644
                member.mtime = 0
                archive.addfile(member, io.BytesIO(data))
            compressor.stdin.close()
            if compressor.wait() != 0:
                raise RuntimeError("XZ source compression failed")
        except BaseException:
            compressor.stdin.close()
            compressor.terminate()
            compressor.wait()
            raise
    partial.replace(output)
    inventory["expanded_source_archive"] = {
        "filename": output.name,
        "format": "tar.xz; complete original member contents expanded under per-package namespaces",
        "compression": "xz -6 -T2 --lzma2=dict=64MiB --block-size=256MiB",
        "original_artifact_sha256_preserved": True,
        "tree_digest": "SHA-256 over original archive member order, one UTF-8 JSON line per member; sort_keys=True, separators=(',',':'), ensure_ascii=True; keys: path,type,mode,size,linkname,payload_sha256. mode is permission bits (original mode & 07777), with file type recorded separately, because standard tar writers strip redundant file-type bits from the mode header. Original archive member path and original linkname are used; payload_sha256 is SHA-256 of regular-file bytes, otherwise null.",
        "package_archives": len(packages),
        "total_members": sum(p["expanded_members"] for p in packages),
        "total_regular_file_bytes": sum(p["expanded_file_bytes"] for p in packages),
    }
    return inventory


def inspect_originals(inventory_path: Path, cache: Path, output: Path):
    """Recreate original proofs to independently verify an existing candidate."""
    inventory = json.loads(inventory_path.read_text())
    packages = [p for p in inventory["packages"] if p.get("archive")]
    for index, package in enumerate(packages, 1):
        path = cache / package["archive"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != package["sha256"]:
            raise ValueError(f"Original source checksum mismatch: {path.name}")
        namespace = "git" if package["source"].startswith("git+") else "registry"
        package["expanded_prefix"] = f"locked-sources/{namespace}/{package['name']}-{package['version']}"
        tree = hashlib.sha256()
        members = byte_count = 0
        with tarfile.open(path, "r:gz") as original:
            for member in original:
                content = None
                if member.isfile():
                    digest = hashlib.sha256()
                    stream = original.extractfile(member)
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                    content = digest.hexdigest()
                update_tree(tree, member, member.name, content)
                members += 1
                byte_count += member.size if member.isfile() else 0
        package["expanded_tree_sha256"] = tree.hexdigest()
        package["expanded_members"] = members
        package["expanded_file_bytes"] = byte_count
        if index % 100 == 0 or index == len(packages):
            print(json.dumps({"originals_inspected": index, "packages": len(packages)}), flush=True)
    inventory["expanded_source_archive"] = {
        "filename": output.name,
        "format": "tar.xz; complete original member contents expanded under per-package namespaces",
        "compression": "xz -6 -T2 --lzma2=dict=64MiB --block-size=256MiB",
        "original_artifact_sha256_preserved": True,
        "tree_digest": "SHA-256 over original archive member order, one UTF-8 JSON line per member; sort_keys=True, separators=(',',':'), ensure_ascii=True; keys: path,type,mode,size,linkname,payload_sha256. mode is permission bits (original mode & 07777), with file type recorded separately, because standard tar writers strip redundant file-type bits from the mode header. Original archive member path and original linkname are used; payload_sha256 is SHA-256 of regular-file bytes, otherwise null.",
        "package_archives": len(packages),
        "total_members": sum(p["expanded_members"] for p in packages),
        "total_regular_file_bytes": sum(p["expanded_file_bytes"] for p in packages),
    }
    return inventory


def verify(inventory, output: Path):
    subprocess.run(["xz", "--test", str(output)], check=True)
    packages = [p for p in inventory["packages"] if p.get("expanded_prefix")]
    by_prefix = {p["expanded_prefix"]: p for p in packages}
    trees = {prefix: hashlib.sha256() for prefix in by_prefix}
    counts = {prefix: [0, 0] for prefix in by_prefix}
    with tarfile.open(output, "r|xz") as archive:
        for member in archive:
            if member.name == "locked-sources/supplemental-notices.json":
                continue
            components = member.name.split("/")
            prefix = "/".join(components[:3])
            if prefix not in by_prefix:
                raise ValueError(f"Unexpected expanded source member: {member.name}")
            restored = copy.copy(member)
            restored.name = "/".join(components[3:])
            if member.islnk():
                if not member.linkname.startswith(prefix + "/"):
                    raise ValueError("Invalid expanded hardlink")
                restored.linkname = member.linkname[len(prefix) + 1:]
            content = None
            if member.isfile():
                stream = archive.extractfile(member)
                digest = hashlib.sha256()
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                content = digest.hexdigest()
            update_tree(trees[prefix], restored, restored.name, content)
            counts[prefix][0] += 1
            counts[prefix][1] += member.size if member.isfile() else 0
    for prefix, package in by_prefix.items():
        if trees[prefix].hexdigest() != package["expanded_tree_sha256"]:
            raise ValueError(f"Expanded source tree differs: {prefix}")
        if counts[prefix] != [package["expanded_members"], package["expanded_file_bytes"]]:
            raise ValueError(f"Expanded source member count differs: {prefix}")
    digest = hashlib.sha256()
    with output.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    inventory["expanded_source_archive"].update({"sha256": digest.hexdigest(),
        "bytes": output.stat().st_size, "verified_against_every_original_member": True,
        "xz_container_integrity_verified": True})
    return inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--supplemental", type=Path, required=True)
    parser.add_argument("--output-inventory", type=Path, required=True)
    parser.add_argument("--verify-existing", action="store_true")
    args = parser.parse_args()
    if args.verify_existing:
        inventory = inspect_originals(args.inventory, args.cache, args.output)
    else:
        inventory = expand(args.inventory, args.cache, args.output, args.supplemental)
    inventory = verify(inventory, args.output)
    args.output_inventory.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(inventory["expanded_source_archive"], indent=2), flush=True)


if __name__ == "__main__":
    main()
