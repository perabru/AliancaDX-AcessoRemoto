#!/usr/bin/env python3
"""Build, independently audit and publish only the complete public 6.4.0 set.

Private Android keys are never used here. Windows installers use the existing
pinned NSIS cross-build; they are inspected without executing the EXEs.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import build_release as windows
import download_release_inputs as inputs
import mobile_release as mobile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "6.4.0"
APK = "AliancaDX_Acesso_Remoto_6.4.0.apk"
PWA = "AliancaDX_iOS_PWA_6.4.0.zip"
WINDOWS = {"AliancaDX_Controlador_Setup.exe", "AliancaDX_Host_Setup.exe"}
PIN = "APK_ANDROID_6_4_0.json"
SIGNING = "ASSINATURA_ANDROID_6_4_0.json"
WINDOWS_REPORT = "release-build-verification.json"
WINDOWS_EVIDENCE = "RELATORIO_INSTALADORES_GITHUB_6_4_0.json"
PWA_REPORT = "PWA_IOS_6_4_0.json"
REPORT = "release-verification.json"
SUMS = "SHA256SUMS.txt"
FILES = WINDOWS | {APK, PWA, PIN, SIGNING, WINDOWS_REPORT, WINDOWS_EVIDENCE, PWA_REPORT, REPORT, SUMS}
BUILD_SOURCES = (
    ".github/workflows/release-6.4.0.yml", "scripts/unified_release.py",
    "scripts/build_release.py", "scripts/mobile_release.py", "scripts/download_release_inputs.py",
    "scripts/package_site.py", "desktop/installer/build_installers.py",
    "desktop/installer/prepare_assets.py", "desktop/installer/MiniAnyDesk.nsi",
    "desktop/installer/assets_manifest.json", "desktop/installer/easytier_bundle_manifest.json",
    "desktop/docs/PROVENIENCIA_FERRAMENTAS_6_1_1.json", "android/signing/release-signing.json",
    "android/tools/sign_release.py", "android/tools/VerifySignerBlocks.java",
    "ios-pwa/public-assets.json", "docs/" + PIN, "docs/" + SIGNING,
)
require = mobile.require
sha256 = mobile.sha256
read_json = mobile.read_json


def source_hashes(root=ROOT):
    result = {}
    for name in BUILD_SOURCES:
        path = root / name
        require(path.is_file() and not path.is_symlink(), "Missing or linked release build input: " + name)
        result[name] = sha256(path)
    return result


def directory_assets(output):
    require(output.is_dir() and not output.is_symlink(), "Missing release directory")
    paths = list(output.iterdir())
    require({p.name for p in paths} == FILES and len(paths) == len(FILES)
            and all(p.is_file() and not p.is_symlink() for p in paths),
            "Release must contain exactly the complete public asset set")
    return {p.name: (p.stat().st_size, "sha256:" + sha256(p)) for p in paths}


def write_sums(output):
    (output / SUMS).write_text("".join(sha256(output / name) + "  " + name + "\n"
                                      for name in sorted(FILES - {SUMS})), encoding="utf-8")


def check_artifact(row, output):
    path = output / row.get("file", "")
    require(path.name in WINDOWS | {APK, PWA} and path.is_file() and not path.is_symlink()
            and row.get("bytes") == path.stat().st_size and row.get("sha256") == sha256(path),
            "Artifact bytes differ from their audit")


def verify_outputs(output, commit, root=ROOT):
    require(mobile.COMMIT.fullmatch(commit), "Expected a concrete source commit")
    directory_assets(output)
    report = read_json(output / REPORT)
    require(report.get("version") == VERSION and report.get("source_commit") == commit
            and report.get("source_dirty") is False and report.get("android_private_key_used_in_ci") is False,
            "Unified release provenance differs from its clean source target")
    require(report.get("build_source_sha256") == source_hashes(root), "Release build inputs changed after audit")
    pin, signing = mobile.load_pin(root, VERSION)
    require(read_json(output / PIN) == pin and read_json(output / SIGNING) == signing,
            "Published Android evidence differs from committed evidence")
    mobile.check_pin(output / APK, pin["artifact"])
    desktop = read_json(output / WINDOWS_REPORT)
    require(desktop.get("version") == VERSION and desktop.get("source_commit") == commit
            and desktop.get("source_dirty") is False, "Windows provenance differs from release target")
    expected = desktop.get("windows_embedded_source_sha256", {})
    if root.resolve() == ROOT.resolve():
        require(windows.payload_hashes() == expected, "Windows payload changed after its build")
    require({"controller.py", "host.py", "net.py", "connection_metrics.py", "adaptive_stream.py",
             "ios_connector.py", "ios_assets_manifest.json"}.issubset(expected),
            "Windows source inventory lacks a required runtime module")
    pwa_sources = mobile.pwa_inventory(root)
    require(read_json(root / "desktop/ios_assets_manifest.json").get("version") == VERSION,
            "Windows browser manifest version is stale")
    require(windows.pwa_payload(expected) == pwa_sources,
            "Windows embedded PWA differs from the public browser inventory")
    pwa = windows.verify_pwa_zip(output / PWA, expected, version=VERSION)
    require(read_json(output / PWA_REPORT) == pwa, "PWA report differs from its exact ZIP bytes")
    desktop_rows = desktop.get("artifacts", [])
    require(isinstance(desktop_rows, list) and len(desktop_rows) == 3
            and {r.get("file") for r in desktop_rows} == WINDOWS | {PWA}, "Windows artifact inventory is incomplete")
    for row in desktop_rows:
        check_artifact(row, output)
        if row["file"] in WINDOWS:
            require(row.get("product_version") == VERSION and row.get("architecture") == "Windows x64"
                    and row.get("archive_crc_ok") is True
                    and row.get("embedded_payload_matches_frozen_source") is True
                    and row.get("connection_metrics_module_included") is True
                    and row.get("payload_files_checked") == len(expected), "Windows audit is incomplete")
        else:
            require(row == pwa, "Windows PWA audit disagrees with its archive")
    evidence = read_json(output / WINDOWS_EVIDENCE)
    windows_rows = [r for r in desktop_rows if r["file"] in WINDOWS]
    require(evidence.get("version") == VERSION and evidence.get("source_commit") == commit
            and evidence.get("artifacts") == windows_rows
            and evidence.get("embedded_source_sha256") == expected
            and evidence.get("pinned_tool_packages") == desktop.get("pinned_tool_packages"),
            "Installer evidence differs from its frozen build report")
    rows = report.get("artifacts", [])
    require(isinstance(rows, list) and len(rows) == 4
            and {r.get("file") for r in rows} == WINDOWS | {APK, PWA}, "Unified artifact inventory is incomplete")
    for row in rows:
        check_artifact(row, output)
        if row["file"] != APK:
            require(row in desktop_rows, "Unified and Windows artifact audits disagree")
        else:
            require(row.get("signature_verified_in_this_job") is True and row.get("archive_crc_ok") is True
                    and row.get("alignment_kb") == 16
                    and len(row.get("sdk_interval_checks", [])) == 7
                    and row.get("application") == signing.get("application")
                    and row.get("sdk_interval_checks") == signing.get("sdk_interval_checks")
                    and row.get("official_apksig_blocks") == signing.get("official_apksig_blocks")
                    and row.get("official_apksig_blocks", {}).get("verified") is True,
                    "Official Android signature audit is incomplete or disagrees with owner evidence")
    expected_sums = "".join(sha256(output / name) + "  " + name + "\n" for name in sorted(FILES - {SUMS}))
    require((output / SUMS).read_text(encoding="utf-8") == expected_sums, "Checksum list differs from complete release set")
    return report


def verify_uploaded(release, output, commit, published=False, root=ROOT):
    verify_outputs(output, commit, root)
    require(release.get("tag_name") == "v" + VERSION and release.get("target_commitish") == commit
            and release.get("draft") is (not published) and release.get("prerelease") is False,
            "Release revision, tag or publication state is unexpected")
    assets = release.get("assets", [])
    require(isinstance(assets, list) and all(isinstance(a, dict) for a in assets), "Invalid uploaded asset list")
    actual = {a.get("name"): (a.get("size"), a.get("digest")) for a in assets}
    require(len(actual) == len(assets) and actual == directory_assets(output)
            and all(a.get("state") == "uploaded" for a in assets),
            "Uploaded names, sizes and SHA-256 digests differ from complete frozen release")
    if published:
        require(release.get("html_url") == "https://github.com/perabru/AliancaDX-AcessoRemoto/releases/tag/v" + VERSION,
                "Unexpected public release URL")


def assemble(output, input_dir, tools_dir, android_tools, java, input_apk=None, root=ROOT):
    commit = mobile.git_identity(root)
    frozen = source_hashes(root)
    pin, signing = mobile.load_pin(root, VERSION)
    require(not output.exists(), "Refusing to replace a release directory")
    for directory in (output, tools_dir):
        require(not directory.resolve().is_relative_to(root.resolve()), "Build/output directories must be outside checkout")
    subprocess.run([sys.executable, str(root / "scripts/build_release.py"), "--version", VERSION,
                    "--input-dir", str(input_dir), "--tools-dir", str(tools_dir), "--output-dir", str(output)],
                   check=True, cwd=root)
    manifest = read_json(root / "desktop/ios_assets_manifest.json")
    require(manifest.get("version") == VERSION, "Windows browser manifest version is stale")
    with tempfile.TemporaryDirectory(prefix="aliancadx-640-input-") as temporary:
        if input_apk is None:
            artifact = inputs.Artifact("android-unified-640", APK, mobile.DRIVE_FILE_ID,
                                       pin["artifact"]["bytes"], pin["artifact"]["sha256"])
            inputs.download(artifact, Path(temporary))
            input_apk = Path(temporary) / APK
        apk = mobile.verify_apk(input_apk, pin, signing, android_tools, java, root)
        with (output / APK).open("xb") as target, input_apk.open("rb") as source:
            shutil.copyfileobj(source, target)
        mobile.check_pin(output / APK, pin["artifact"])
    for name in (PIN, SIGNING):
        with (output / name).open("xb") as target:
            target.write((root / "docs" / name).read_bytes())
    desktop = read_json(output / WINDOWS_REPORT)
    report = {"version": VERSION, "source_commit": commit, "source_dirty": False,
              "artifacts": [*desktop["artifacts"], apk], "build_source_sha256": frozen,
              "android_private_key_used_in_ci": False,
              "scope": "Two Windows x64 installers rebuilt from exact public sources; complete CRC and extracted source hashes checked without executing EXEs. Android owner-signed APK independently verified using official Android tools and public certificate lineage. PWA ZIP exactly matches the runtime embedded in both installers. Physical device tests and reputation approvals are not claimed."}
    (output / REPORT).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_sums(output)
    require(mobile.git_identity(root) == commit and source_hashes(root) == frozen, "Source changed during release audit")
    return verify_outputs(output, commit, root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--tools-dir", type=Path)
    parser.add_argument("--build-tools", type=Path)
    parser.add_argument("--java", type=Path)
    parser.add_argument("--input-apk", type=Path)
    parser.add_argument("--verify-upload", type=Path)
    parser.add_argument("--expected-target")
    parser.add_argument("--published", action="store_true")
    args = parser.parse_args()
    if args.verify_upload:
        require(args.expected_target is not None, "Supply the exact release source target")
        verify_uploaded(read_json(args.verify_upload), args.output_dir, args.expected_target, args.published)
        print("Verified complete 6.4.0 uploaded release set and exact source target")
        return
    require(all((args.input_dir, args.tools_dir, args.build_tools, args.java)), "Supply Windows inputs/tools and Android Build Tools/Java")
    result = assemble(args.output_dir, args.input_dir, args.tools_dir, args.build_tools, args.java, args.input_apk)
    print(json.dumps({"version": VERSION, "source_commit": result["source_commit"], "release_assets": sorted(FILES)}))


if __name__ == "__main__":
    main()
