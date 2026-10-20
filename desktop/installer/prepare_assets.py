#!/usr/bin/env python3
"""Recover the exact offline build assets from a pinned AliançaDX 6.2.0 installer.

The installer is inspected as an archive and is never executed. This utility
performs no downloads. Download a release installer through the project's
published link first; its bytes must match release_artifacts_6_2_0.json.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
RELEASE_MANIFEST = PROJECT / "installer/release_artifacts_6_2_0.json"
SPEC = importlib.util.spec_from_file_location("aliancadx_asset_builder", PROJECT / "installer/build_installers.py")
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def asset_entries(project: Path = PROJECT) -> dict[str, str]:
    """Use the original release manifests, including all corresponding sources."""
    runtime = json.loads((project / "installer/assets_manifest.json").read_text(encoding="utf-8"))
    network = json.loads((project / "installer/easytier_bundle_manifest.json").read_text(encoding="utf-8"))
    entries = {runtime["python"]["file"]: runtime["python"]["sha256"]}
    entries.update({"wheelhouse/" + row["file"]: row["sha256"] for row in runtime["wheels"]})
    entries.update({"easytier_bundle/" + row["filename"]: row["sha256"] for row in network["files"]})
    entries["easytier_bundle/manifest.json"] = builder.sha256_file(project / "installer/easytier_bundle_manifest.json")
    for name, digest in entries.items():
        parts = PurePosixPath(name)
        if (parts.is_absolute() or ".." in parts.parts or "\\" in name or ":" in name
                or not isinstance(digest, str) or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)):
            raise RuntimeError("Invalid pinned asset manifest")
    return entries


def verify_cache(cache: Path, project: Path = PROJECT) -> dict[str, str]:
    entries = asset_entries(project)
    for name, digest in entries.items():
        path = cache / name
        if path.is_symlink() or not path.is_file() or builder.sha256_file(path) != digest:
            raise RuntimeError("Missing or altered pinned asset: " + name)
    expected_wheels = {name.split("/", 1)[1] for name in entries if name.startswith("wheelhouse/")}
    if {path.name for path in (cache / "wheelhouse").iterdir()} != expected_wheels:
        raise RuntimeError("Unlisted wheelhouse file")
    builder.validate_easytier_bundle(cache, project=project)
    return entries


def recover(installer: Path, output: Path, sevenzip: str, *, project: Path = PROJECT,
            release_manifest: Path = RELEASE_MANIFEST) -> dict:
    installer = installer.resolve(strict=True)
    output = output.resolve()
    project = project.resolve()
    if output == project or project in output.parents:
        raise RuntimeError("Choose an asset cache outside the public source checkout")
    if output.exists():
        raise RuntimeError("Output must be a new directory; existing files are never overwritten")
    release = json.loads(release_manifest.read_text(encoding="utf-8"))
    digest = builder.sha256_file(installer)
    matched = next((row for row in release["artifacts"]
                    if row["sha256"] == digest and row["bytes"] == installer.stat().st_size), None)
    if matched is None:
        raise RuntimeError("Installer does not match either pinned AliançaDX 6.2.0 release artifact")
    entries = asset_entries(project)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aliancadx-assets-", dir=output.parent) as temporary:
        stage = Path(temporary) / "cache"
        stage.mkdir()
        # Exact release archive plus explicit known member paths. No EXE is run.
        subprocess.run([sevenzip, "x", "-y", "-bd", "-bb0", "-o" + str(stage), str(installer),
                        *sorted(entries)], check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        verified = verify_cache(stage, project)
        if output.exists():
            raise RuntimeError("Output appeared during extraction; refusing to overwrite it")
        stage.rename(output)
    return {"release_installer": matched["file"], "installer_sha256": digest,
            "output": str(output), "verified_assets": len(verified),
            "network_manifest_sha256": verified["easytier_bundle/manifest.json"],
            "installer_executed": False, "network_used": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-installer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new cache directory outside this checkout")
    parser.add_argument("--sevenzip", default=shutil.which("7zz") or shutil.which("7z"),
                        help="7-Zip executable (7zz, 7z or full path to 7z.exe)")
    args = parser.parse_args()
    if not args.sevenzip:
        parser.error("7-Zip is required; set --sevenzip to its executable")
    try:
        result = recover(args.from_installer, args.output, args.sevenzip)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, "Asset preparation failed: " + str(error) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
