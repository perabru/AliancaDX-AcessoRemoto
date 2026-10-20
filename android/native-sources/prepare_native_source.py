#!/usr/bin/env python3
"""Prepare pinned EasyTier sources without changing Rust runtime behavior.

Legacy distribution archives remain supported. In a public source checkout,
run bootstrap_native_sources.py first; it verifies/downloads the exact inputs.
"""
from pathlib import Path
import argparse
import hashlib
import json
import tarfile
import tomllib

ARCHIVES = {
    "easytier-2.6.4-source.tar.gz": "4f1134e7d12a9c2722f78cf6021f8d973d8eb8cd48e1dcbb09ec562bc2087c2b",
    "easytier-2.6.4-dependency-sources.tar.xz": "e47c98f7ebd0c9fa8b28f477cb5cc23982b8072c8be7de525bf7beb325b240f1",
}


def configure(destination: Path) -> Path:
    root = destination / "EasyTier-2.6.4"
    marker = root / ".aliancadx-native-prepared.json"
    if marker.exists():
        return root
    lock_bytes = (root / "Cargo.lock").read_bytes()
    lock = tomllib.loads(lock_bytes.decode())
    vendor = destination / "vendor"
    vendor.mkdir(exist_ok=True)
    for package in lock["package"]:
        if not str(package.get("source", "")).startswith("registry+"):
            continue
        name = f"{package['name']}-{package['version']}"
        sub = destination / "locked-sources/registry" / name / name
        if not sub.is_dir():
            continue
        dest = vendor / name
        if not dest.exists():
            dest.symlink_to(sub.resolve(), target_is_directory=True)
        checksums = {str(p.relative_to(sub)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted(sub.rglob("*")) if p.is_file()
                     and p.name != ".cargo-checksum.json"}
        (sub / ".cargo-checksum.json").write_text(json.dumps({
            "package": package["checksum"], "files": checksums}))
    cfg = root / ".cargo/config.toml"
    cfg.write_text(cfg.read_text().replace(
        'rustflags = ["-C", "link-arg=-fuse-ld=mold"]', 'rustflags = []') +
        '\n[source.crates-io]\nreplace-with = "local-vendor"\n'
        '[source.local-vendor]\ndirectory = ' + json.dumps(str(vendor.resolve())) + '\n')
    text = (root / "Cargo.toml").read_text()
    start = text.index("members = [")
    end = text.index("]\n", start) + 2
    text = text[:start] + 'members = ["easytier", "easytier-rpc-build", "easytier-contrib/easytier-ffi"]\n' + text[end:]
    text = text.replace('default-members = ["easytier", "easytier-web"]',
                        'default-members = ["easytier-contrib/easytier-ffi"]')
    patches = {}
    for package in lock["package"]:
        source = package.get("source", "")
        if not source.startswith("git+"):
            continue
        key = source[4:].split("?")[0].split("#")[0]
        folder = destination / "locked-sources/git" / f"{package['name']}-{package['version']}"
        for candidate in folder.rglob("Cargo.toml"):
            try:
                meta = tomllib.loads(candidate.read_text()).get("package", {})
            except (OSError, ValueError):
                continue
            if meta.get("name") == package["name"]:
                patches.setdefault(key, []).append((package["name"], str(candidate.parent.resolve())))
                break
        else:
            raise ValueError("Missing exact git dependency source: " + package["name"])
    for key, items in patches.items():
        text += '\n[patch.' + json.dumps(key) + ']\n'
        for name, path in items:
            text += name + ' = { path = ' + json.dumps(path) + ' }\n'
    (root / "Cargo.toml").write_text(text)
    ffi = root / "easytier-contrib/easytier-ffi/Cargo.toml"
    ffi.write_text(ffi.read_text().replace('easytier = { path = "../../easytier" }',
        'easytier = { path = "../../easytier", default-features = false, features = ["smoltcp", "socks5", "websocket", "aes-gcm", "tun", "magic-dns"] }'))
    marker.write_text(json.dumps({"upstream_lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
        "rust_runtime_source_changed": False, "prepared_by": "prepare_native_source.py"}, indent=2) + "\n")
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    args.destination = args.destination.resolve()
    args.destination.mkdir(parents=True, exist_ok=True)
    for name, digest in ARCHIVES.items():
        source = args.sources / name
        if not source.is_file():
            parser.error("Legacy archive missing. Public checkout: run bootstrap_native_sources.py instead.")
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            parser.error("Wrong legacy source archive: " + name)
        with tarfile.open(source) as archive:
            archive.extractall(args.destination, filter="data")
    print(configure(args.destination))


if __name__ == "__main__":
    main()
