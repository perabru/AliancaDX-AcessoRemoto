#!/usr/bin/env python3
"""Build and audit the public 6.3.0 Windows and iOS PWA release.

The Android 6.2.2 APK remains a separate, unchanged release. No private signing
material is needed. Windows builds retain the original unsigned distribution
model. No produced executable is run.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import struct
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "desktop"
VERSION = "6.3.0"
PWA_NAME = "AliancaDX_iOS_PWA_6.3.0.zip"
PWA_REPORT = "PWA_IOS_6_3_0.json"
APK_NAME = "AliancaDX_Acesso_Remoto_6.2.2.apk"
APK_BYTES = 34716022
APK_SHA256 = "aafd1f38274da3e9580cf077e4f23b8c5ab79c8094c99b7b9cc1313c3298dd5f"
APK_REPORTS = ("docs/APK_ANDROID_6_2_2.json", "docs/ASSINATURA_ANDROID_6_2_2.json")


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
    return {name: sha256(builder.payload_path(DESKTOP, name)) for name in (*builder.PAYLOAD, "README.txt")}


def pwa_payload(expected: dict[str, str]) -> dict[str, str]:
    selected = {name: digest for name, digest in expected.items() if name.startswith("ios-pwa/")}
    required = {"ios-pwa/index.html", "ios-pwa/bridge.html", "ios-pwa/manifest.webmanifest", "ios-pwa/sw.js"}
    if not required.issubset(selected):
        raise RuntimeError("The curated iOS PWA payload is incomplete")
    for name, digest in selected.items():
        parts = PurePosixPath(name)
        if (parts.is_absolute() or ".." in parts.parts or "\\" in name or ":" in name
                or parts.suffix.lower() in (".p12", ".pfx", ".jks", ".keystore", ".key", ".pk8")
                or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest)):
            raise RuntimeError("Invalid or private-key filename in the curated iOS PWA payload")
    return selected


def verify_pwa_zip(path: Path, expected: dict[str, str], *, version: str = VERSION) -> dict:
    selected = pwa_payload(expected)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if (len(names) != len(set(names)) or set(names) != set(selected)
                or archive.testzip() is not None):
            raise RuntimeError("iOS PWA archive contents or CRC differ from the curated payload")
        actual = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in names}
    if actual != selected:
        raise RuntimeError("iOS PWA archive bytes differ from the frozen browser sources")
    return {"file": path.name, "bytes": path.stat().st_size, "sha256": sha256(path),
            "version": version, "archive_crc_ok": True, "payload_files_checked": len(actual),
            "source_sha256": actual, "matches_windows_embedded_pwa": True,
            "native_ios_binary": False, "physical_iphone_tested": False}


def package_pwa(path: Path, expected: dict[str, str], *, project: Path = DESKTOP, resolver=None,
                version: str = VERSION) -> dict:
    selected = pwa_payload(expected)
    if resolver is None:
        builder = load_module("aliancadx_pwa_builder", project / "installer/build_installers.py")
        resolver = builder.payload_path
    if path.exists():
        raise RuntimeError("Refusing to replace an existing iOS PWA archive")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".aliancadx-pwa-", dir=path.parent) as temporary:
        stage = Path(temporary) / path.name
        with zipfile.ZipFile(stage, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name in sorted(selected):
                source = resolver(project, name)
                if source.is_symlink() or not source.is_file():
                    raise RuntimeError("Missing or linked iOS PWA source: " + name)
                data = source.read_bytes()
                if hashlib.sha256(data).hexdigest() != selected[name]:
                    raise RuntimeError("iOS PWA source changed after its freeze: " + name)
                entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        verify_pwa_zip(stage, expected, version=version)
        os.link(stage, path)
    return verify_pwa_zip(path, expected, version=version)


def verify_windows(path: Path, sevenzip: Path, expected: dict[str, str], *, version: str = VERSION) -> dict:
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
    required = {"adaptive_stream.py", "ios_connector.py"}
    if version in ("6.4.0", "7.0.0", "7.1.0", "7.2.0", "7.3.0", "7.4.0", "7.5.0"):
        required.add("connection_metrics.py")
    if version in ("7.4.0", "7.5.0"):
        required.add("updates.py")
    if actual != expected or not required.issubset(actual):
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
    if security_size or (version + ".0").encode("utf-16le") not in data:
        raise RuntimeError("Unexpected Windows release metadata")
    return {"file": path.name, "bytes": len(data), "sha256": sha256(path),
            "architecture": "Windows x64", "product_version": version,
            "authenticode_signed": False, "archive_crc_ok": True,
            "embedded_payload_matches_frozen_source": True, "payload_files_checked": len(actual),
            "updates_module_included": "updates.py" in actual,
            "adaptive_stream_module_included": True, "ios_connector_module_included": "ios_connector.py" in actual,
            "connection_metrics_module_included": "connection_metrics.py" in actual,
            "ios_pwa_files_checked": len(pwa_payload(actual)), "executed_on_windows": False}


def verify_apk(path: Path) -> dict:
    if path.stat().st_size != APK_BYTES or sha256(path) != APK_SHA256:
        raise RuntimeError("Android input does not match the signed 6.2.2 release")
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


def verify_uploaded(release: dict, directory: Path, expected_commit: str, *, published: bool = False) -> None:
    if (release.get("tag_name") != "v" + VERSION or release.get("target_commitish") != expected_commit
            or release.get("draft") is not (not published) or release.get("prerelease") is not False):
        raise RuntimeError("Uploaded release targets an unexpected revision or publication state")
    expected = {path.name: (path.stat().st_size, "sha256:" + sha256(path))
                for path in directory.iterdir() if path.is_file()}
    assets = release.get("assets", [])
    actual = {item["name"]: (item["size"], item.get("digest")) for item in assets}
    if len(actual) != len(assets):
        raise RuntimeError("Duplicate uploaded release asset names")
    if actual != expected or any(item.get("state") != "uploaded" for item in assets):
        raise RuntimeError("Uploaded release assets do not match the complete names/sizes/SHA-256 set")
    if published and release.get("html_url") != "https://github.com/perabru/AliancaDX-AcessoRemoto/releases/tag/v" + VERSION:
        raise RuntimeError("Unexpected published release URL")


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
    pwa_rows = [row for row in report["artifacts"] if row["file"] == PWA_NAME]
    if len(pwa_rows) != 1:
        raise RuntimeError("Expected the audited iOS PWA archive")
    pwa = pwa_rows[0]
    if (verify_pwa_zip(output / PWA_NAME, report["windows_embedded_source_sha256"]) != pwa
            or json.loads((output / PWA_REPORT).read_text()) != pwa):
        raise RuntimeError("iOS PWA artifact or evidence changed after its audit")
    if project.resolve() == ROOT.resolve() and payload_hashes() != report["windows_embedded_source_sha256"]:
        raise RuntimeError("Application payload changed after the release build")
    evidence_name = "RELATORIO_INSTALADORES_GITHUB_6_3_0.json"
    evidence = json.loads((output / evidence_name).read_text())
    if (evidence.get("version") != VERSION or evidence["artifacts"] != windows
            or evidence["source_commit"] != expected_commit
            or evidence["embedded_source_sha256"] != report["windows_embedded_source_sha256"]):
        raise RuntimeError("CI evidence does not match the audited artifacts")
    manifest_path = project / "desktop/installer/release_artifacts_6_3_0.json"
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
    parser.add_argument("--published", action="store_true")
    parser.add_argument("--version", choices=(VERSION, "6.4.0", "7.0.0", "7.1.0", "7.2.0", "7.3.0", "7.4.0", "7.5.0"), default=VERSION)
    args = parser.parse_args()
    version = args.version
    pwa_name = "AliancaDX_iOS_PWA_" + version + ".zip"
    suffix = version.replace(".", "_")
    if version != VERSION and (args.verify_upload or args.record_github_pins):
        parser.error("Use unified_release.py to publish or verify unified releases")
    if args.verify_upload:
        verify_uploaded(json.loads(args.verify_upload.read_text()), args.output_dir,
                        args.expected_target or os.environ["GITHUB_SHA"], published=args.published)
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
    assets_module = load_module("aliancadx_release_assets", DESKTOP / "installer/prepare_assets.py")
    if cache.exists():
        assets_module.verify_cache(cache)
    else:
        base_name = "AliancaDX_Controlador_Setup_6.2.2.exe" if version in ("7.2.0", "7.3.0", "7.4.0", "7.5.0") else "AliancaDX_Controlador_Setup_6.2.0.exe"
        assets_module.recover(args.input_dir / base_name, cache, str(sevenzip))
    subprocess.run([sys.executable, str(DESKTOP / "installer/build_installers.py"),
                    "--assets", str(cache), "--reuse-assets", "--output-dir", str(output),
                    "--makensis", str(compiler)], check=True)
    artifacts = [verify_windows(output / ("AliancaDX_" + role + "_Setup.exe"), sevenzip, expected, version=version)
                 for role in ("Controlador", "Host")]
    artifacts.append(package_pwa(output / pwa_name, expected, version=version))
    if payload_hashes() != expected:
        raise RuntimeError("Application payload changed during the release build")
    (output / ("PWA_IOS_" + suffix + ".json")).write_text(json.dumps(artifacts[-1], ensure_ascii=False, indent=2) + "\n")
    report = {"version": version, "source_commit": commit, "source_dirty": dirty,
              "artifacts": artifacts, "windows_embedded_source_sha256": expected,
              "pinned_tool_packages": tool_pins,
              "scope": "Windows rebuilt and statically audited; iOS PWA ZIP is byte-identical to the browser files embedded in both installers. Android is audited independently. No physical Windows/iPhone installation, App Store distribution or real-internet runtime test is claimed."}
    (output / "release-build-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    ci_windows = {"version": version, "source_commit": commit,
                  "artifacts": [row for row in artifacts if row["file"].endswith(".exe")],
                  "embedded_source_sha256": expected, "pinned_tool_packages": tool_pins,
                  "scope": "Static PE metadata, complete archive CRC and exact extracted payload hashes; Windows execution not performed."}
    (output / ("RELATORIO_INSTALADORES_GITHUB_" + suffix + ".json")).write_text(json.dumps(ci_windows, ensure_ascii=False, indent=2) + "\n")
    checksum_paths = sorted(path for path in output.iterdir() if path.is_file())
    (output / "SHA256SUMS.txt").write_text("".join(sha256(path) + "  " + path.name + "\n" for path in checksum_paths))
    print(json.dumps({"version": version, "source_commit": commit, "artifacts": artifacts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
