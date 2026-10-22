#!/usr/bin/env python3
"""Build and audit the public 6.2.1 Windows artifacts from pinned offline inputs.

No private signing material is needed: the Android input is the exact APK
already signed and independently verified by its owner. Windows builds retain
the original unsigned distribution model. No produced executable is run.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"
VERSION = "6.2.1"
APK_NAME = "AliancaDX_Acesso_Remoto_6.2.1.apk"
APK_BYTES = 34_716_022
APK_SHA256 = "5d3d15137354f37bb1038e709c38672e0b5fd8dd033c2bb92c6227a7801dc590"
APK_REPORTS = ("docs/APK_ANDROID_6_2_1.json", "docs/ASSINATURA_ANDROID_6_2_1.json")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def checked_download(row: dict, destination: Path) -> None:
    parsed = urllib.parse.urlsplit(row["source_url"])
    if (parsed.scheme != "https" or parsed.hostname != "snapshot.ubuntu.com"
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or Path(parsed.path).name != row["filename"]):
        raise RuntimeError("Unexpected pinned tool package origin")
    if destination.exists():
        if destination.stat().st_size != row["bytes"] or sha256(destination) != row["sha256"]:
            raise RuntimeError("An existing tool package differs from its pin")
        return
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        count = 0
        with urllib.request.urlopen(row["source_url"], timeout=60) as response, temporary.open("xb") as output:
            while block := response.read(1 << 20):
                count += len(block)
                if count > row["bytes"]:
                    raise RuntimeError("Pinned tool package exceeds expected size")
                output.write(block)
        if count != row["bytes"] or sha256(temporary) != row["sha256"]:
            raise RuntimeError("Pinned tool package checksum mismatch")
        temporary.rename(destination)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_tools(directory: Path) -> tuple[Path, Path, list[dict]]:
    packages, unpacked = directory / "packages", directory / "toolchain"
    packages.mkdir(parents=True, exist_ok=True)
    unpacked.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((DESKTOP / "docs/PROVENIENCIA_FERRAMENTAS_6_1_1.json").read_text())
    provenance = []
    for row in manifest["packages"]:
        package = packages / row["filename"]
        checked_download(row, package)
        subprocess.run(["dpkg-deb", "-x", str(package), str(unpacked)], check=True)
        provenance.append({key: row[key] for key in ("filename", "bytes", "sha256", "source_url")})
    executable = directory / "makensis"
    executable.write_text('#!/bin/sh\nexport NSISDIR="' + str(unpacked / "usr/share/nsis")
                          + '"\nexec "' + str(unpacked / "usr/bin/makensis") + '" "$@"\n')
    executable.chmod(0o755)
    sevenzip = unpacked / "usr/lib/7zip/7z"
    subprocess.run([str(executable), "-VERSION"], check=True)
    return executable, sevenzip, provenance


def payload_hashes() -> dict[str, str]:
    builder = load_module("aliancadx_release_builder", DESKTOP / "installer/build_installers.py")
    return {name: sha256(DESKTOP / name) for name in (*builder.PAYLOAD, "README.txt")}


def verify_windows(path: Path, sevenzip: Path, expected: dict[str, str]) -> dict:
    test = subprocess.run([str(sevenzip), "t", str(path)], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if "Everything is Ok" not in test.stdout:
        raise RuntimeError("Windows archive did not pass its complete CRC check")
    listing = subprocess.check_output([str(sevenzip), "l", "-slt", str(path)], text=True)
    members = [line.split(" = ", 1)[1] for line in listing.splitlines() if line.startswith("Path = ")][1:]
    if any(Path(name).suffix.lower() in (".p12", ".pfx", ".jks", ".keystore") for name in members):
        raise RuntimeError("Private-key filename in Windows archive")
    with tempfile.TemporaryDirectory(prefix="verify-aliancadx-release-") as temporary:
        subprocess.run([str(sevenzip), "x", "-y", "-bd", "-bb0", "-o" + temporary,
                        str(path), *sorted(expected)], check=True, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
        actual = {name: sha256(Path(temporary) / name) for name in expected}
    if actual != expected or "adaptive_stream.py" not in actual:
        raise RuntimeError("Embedded Windows payload differs from frozen source")
    data = path.read_bytes()
    if data[:2] != b"MZ":
        raise RuntimeError("Not a Windows PE executable")
    nt = struct.unpack_from("<I", data, 0x3C)[0]
    if data[nt:nt + 4] != b"PE\0\0" or struct.unpack_from("<H", data, nt + 4)[0] != 0x8664:
        raise RuntimeError("Unexpected Windows executable architecture")
    optional = nt + 24
    if struct.unpack_from("<H", data, optional)[0] != 0x20B:
        raise RuntimeError("Expected Windows PE32+ executable")
    security_size = struct.unpack_from("<II", data, optional + 112 + 4 * 8)[1]
    if security_size or "6.2.1.0".encode("utf-16le") not in data:
        raise RuntimeError("Unexpected Windows release metadata")
    return {"file": path.name, "bytes": len(data), "sha256": sha256(path),
            "architecture": "Windows x64", "product_version": VERSION,
            "authenticode_signed": False, "archive_crc_ok": True,
            "embedded_payload_matches_frozen_source": True, "payload_files_checked": len(actual),
            "adaptive_stream_module_included": True, "executed_on_windows": False}


def verify_apk(path: Path) -> dict:
    if path.stat().st_size != APK_BYTES or sha256(path) != APK_SHA256:
        raise RuntimeError("Android input does not match the signed 6.2.1 release")
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("APK archive CRC check failed")
    report = json.loads((ROOT / APK_REPORTS[1]).read_text())
    if report["apk_sha256"] != APK_SHA256 or report["bytes"] != APK_BYTES:
        raise RuntimeError("The committed Android signing report has a different artifact pin")
    return {"file": path.name, "bytes": APK_BYTES, "sha256": APK_SHA256,
            "archive_crc_ok": True, "matches_owner_signed_and_verified_release": True,
            "signing_report": Path(APK_REPORTS[1]).name,
            "signature_verified_in_this_job": False}


def verify_uploaded(release: dict, directory: Path, expected_commit: str) -> None:
    if release.get("tagName") != "v6.2.1" or release.get("targetCommitish") != expected_commit:
        raise RuntimeError("Uploaded release targets an unexpected source revision")
    expected = {path.name: path.stat().st_size for path in directory.iterdir() if path.is_file()}
    assets = release.get("assets", [])
    actual = {item["name"]: item["size"] for item in assets}
    if len(actual) != len(assets):
        raise RuntimeError("Duplicate uploaded release asset names")
    if actual != expected:
        raise RuntimeError("Uploaded release assets do not match the complete artifact set")


def record_github_pins(output: Path, project: Path, expected_commit: str) -> None:
    """Write only the two public metadata files; Git publication is the workflow's job."""
    report = json.loads((output / "release-build-verification.json").read_text())
    if (report["version"] != VERSION or report["source_commit"] != expected_commit
            or report.get("source_dirty") is not False):
        raise RuntimeError("Release metadata was not built from the expected clean revision")
    windows = [row for row in report["artifacts"] if row["file"].endswith(".exe")]
    if {row["file"] for row in windows} != {"AliancaDX_Controlador_Setup.exe", "AliancaDX_Host_Setup.exe"} or len(windows) != 2:
        raise RuntimeError("Expected the two audited Windows installers")
    for row in windows:
        path = output / row["file"]
        if (not row.get("archive_crc_ok") or not row.get("embedded_payload_matches_frozen_source")
                or path.stat().st_size != row["bytes"] or sha256(path) != row["sha256"]):
            raise RuntimeError("Windows artifact changed after its audit")
    if project.resolve() == ROOT.resolve() and payload_hashes() != report["windows_embedded_source_sha256"]:
        raise RuntimeError("Application payload changed after the release build")
    evidence_name = "RELATORIO_INSTALADORES_GITHUB_6_2_1.json"
    evidence = json.loads((output / evidence_name).read_text())
    if (evidence.get("version") != VERSION or evidence["artifacts"] != windows
            or evidence["source_commit"] != expected_commit
            or evidence["embedded_source_sha256"] != report["windows_embedded_source_sha256"]):
        raise RuntimeError("CI evidence does not match the audited artifacts")
    manifest_path = project / "desktop/installer/release_artifacts_6_2_1.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest["version"] != VERSION:
        raise RuntimeError("Unexpected release allowlist version")
    for row in windows:
        pin = {key: row[key] for key in ("file", "bytes", "sha256")}
        if pin not in manifest["artifacts"]:
            manifest["artifacts"].append(pin)
    reference = "../docs/" + evidence_name
    additional = manifest.setdefault("additional_evidence", [])
    if reference not in additional:
        additional.append(reference)
    target = project / "desktop/docs" / evidence_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tools-dir", type=Path)
    parser.add_argument("--verify-upload", type=Path)
    parser.add_argument("--expected-target")
    parser.add_argument("--record-github-pins", action="store_true")
    args = parser.parse_args()
    if args.verify_upload:
        verify_uploaded(json.loads(args.verify_upload.read_text()), args.output_dir,
                        args.expected_target or os.environ["GITHUB_SHA"])
        print("All release asset names/sizes and the target source revision match.")
        return
    if args.record_github_pins:
        record_github_pins(args.output_dir, ROOT, os.environ["GITHUB_SHA"])
        print("Recorded only the two public CI installer metadata files.")
        return
    if args.input_dir is None or args.tools_dir is None:
        parser.error("--input-dir and --tools-dir are required for a build")
    output, tools = args.output_dir.resolve(), args.tools_dir.resolve()
    for location in (output, tools):
        if location == ROOT or ROOT in location.parents:
            parser.error("Use build/output directories outside the source checkout")
    if output.exists():
        parser.error("Output directory must be new")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if os.environ.get("GITHUB_SHA", commit) != commit:
        raise RuntimeError("The checkout differs from GITHUB_SHA")
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    if os.environ.get("GITHUB_ACTIONS") == "true" and dirty:
        raise RuntimeError("Release build requires a clean source checkout")
    expected = payload_hashes()
    compiler, sevenzip, tool_pins = prepare_tools(tools)
    cache = tools / "desktop-assets"
    subprocess.run([sys.executable, str(DESKTOP / "installer/prepare_assets.py"),
                    "--from-installer", str(args.input_dir / "AliancaDX_Controlador_Setup_6.2.0.exe"),
                    "--output", str(cache), "--sevenzip", str(sevenzip)], check=True)
    subprocess.run([sys.executable, str(DESKTOP / "installer/build_installers.py"),
                    "--assets", str(cache), "--reuse-assets", "--output-dir", str(output),
                    "--makensis", str(compiler)], check=True)
    artifacts = [verify_windows(output / ("AliancaDX_" + role + "_Setup.exe"), sevenzip, expected)
                 for role in ("Controlador", "Host")]
    apk_input = args.input_dir / APK_NAME
    apk = verify_apk(apk_input)
    shutil.copyfile(apk_input, output / APK_NAME)
    artifacts.append(apk)
    for name in APK_REPORTS:
        shutil.copyfile(ROOT / name, output / Path(name).name)
    report = {"version": VERSION, "source_commit": commit, "source_dirty": dirty,
              "artifacts": artifacts, "windows_embedded_source_sha256": expected,
              "pinned_tool_packages": tool_pins,
              "scope": "Windows rebuilt and statically audited in GitHub Actions. APK is byte-identical to the owner-signed release; committed signing verification applies to those exact bytes. No Windows/device runtime or real-internet latency test is claimed."}
    (output / "release-build-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    ci_windows = {"version": VERSION, "source_commit": commit,
                  "artifacts": [row for row in artifacts if row["file"].endswith(".exe")],
                  "embedded_source_sha256": expected, "pinned_tool_packages": tool_pins,
                  "scope": "Static PE metadata, complete archive CRC and exact extracted payload hashes; Windows execution not performed."}
    (output / "RELATORIO_INSTALADORES_GITHUB_6_2_1.json").write_text(json.dumps(ci_windows, ensure_ascii=False, indent=2) + "\n")
    checksum_paths = sorted(path for path in output.iterdir() if path.is_file())
    (output / "SHA256SUMS.txt").write_text("".join(sha256(path) + "  " + path.name + "\n" for path in checksum_paths))
    print(json.dumps({"version": VERSION, "source_commit": commit, "artifacts": artifacts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
