#!/usr/bin/env python3
"""Build, independently audit and publish only the complete public 6.4.0 set.

Private Android keys are never used here. Windows installers use the existing
pinned NSIS cross-build; they are inspected without executing the EXEs.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
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
import github_release_inputs as github_inputs

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
@dataclass(frozen=True)
class ReleaseProfile:
    version: str

    @property
    def suffix(self): return self.version.replace(".", "_")
    @property
    def apk(self): return "AliancaDX_Acesso_Remoto_" + self.version + ".apk"
    @property
    def pwa(self): return "AliancaDX_iOS_PWA_" + self.version + ".zip"
    @property
    def pin(self): return "APK_ANDROID_" + self.suffix + ".json"
    @property
    def signing(self): return "ASSINATURA_ANDROID_" + self.suffix + ".json"
    @property
    def windows_evidence(self): return "RELATORIO_INSTALADORES_GITHUB_" + self.suffix + ".json"
    @property
    def pwa_report(self): return "PWA_IOS_" + self.suffix + ".json"
    @property
    def files(self):
        return WINDOWS | {self.apk, self.pwa, self.pin, self.signing, WINDOWS_REPORT,
                          self.windows_evidence, self.pwa_report, REPORT, SUMS}
    @property
    def build_sources(self):
        values = tuple(name.replace("6.4.0", self.version).replace("6_4_0", self.suffix)
                       for name in BUILD_SOURCES)
        extra = ((("scripts/unified_release_7.py" if self.version == "7.0.0" else "scripts/unified_release_" + ("7_6_1" if self.version == "7.6.1" else "_".join(self.version.split(".")[:2])) + ".py"), "android/tools/apk_payload.py")
                 if self.version in ("7.0.0", "7.1.0", "7.2.0", "7.3.0", "7.4.0", "7.5.0", "7.6.0", "7.6.1") else ())
        return (values + extra
                + (("scripts/github_release_inputs.py",) if self.version in ("7.2.0", "7.3.0", "7.4.0", "7.5.0", "7.6.0", "7.6.1") else ())
                + (("desktop/tests/windows_identity_smoke.py",) if self.version == "7.6.1" else ()))


DEFAULT_PROFILE = ReleaseProfile(VERSION)


def profile_for(version):
    if version not in (VERSION, "7.0.0", "7.1.0", "7.2.0", "7.3.0", "7.4.0", "7.5.0", "7.6.0", "7.6.1"):
        raise RuntimeError("Unsupported unified release version")
    return ReleaseProfile(version)


require = mobile.require
sha256 = mobile.sha256
read_json = mobile.read_json


def source_hashes(root=ROOT, profile=DEFAULT_PROFILE):
    result = {}
    for name in profile.build_sources:
        path = root / name
        require(path.is_file() and not path.is_symlink(), "Missing or linked release build input: " + name)
        result[name] = sha256(path)
    return result


def directory_assets(output, profile=DEFAULT_PROFILE):
    require(output.is_dir() and not output.is_symlink(), "Missing release directory")
    paths = list(output.iterdir())
    require({p.name for p in paths} == profile.files and len(paths) == len(profile.files)
            and all(p.is_file() and not p.is_symlink() for p in paths),
            "Release must contain exactly the complete public asset set")
    return {p.name: (p.stat().st_size, "sha256:" + sha256(p)) for p in paths}


def write_sums(output, profile=DEFAULT_PROFILE):
    (output / SUMS).write_text("".join(sha256(output / name) + "  " + name + "\n"
                                      for name in sorted(profile.files - {SUMS})), encoding="utf-8")


def check_artifact(row, output, profile=DEFAULT_PROFILE):
    path = output / row.get("file", "")
    require(path.name in WINDOWS | {profile.apk, profile.pwa} and path.is_file() and not path.is_symlink()
            and row.get("bytes") == path.stat().st_size and row.get("sha256") == sha256(path),
            "Artifact bytes differ from their audit")


def verify_outputs(output, commit, root=ROOT, profile=DEFAULT_PROFILE):
    require(mobile.COMMIT.fullmatch(commit), "Expected a concrete source commit")
    directory_assets(output, profile)
    report = read_json(output / REPORT)
    require(report.get("version") == profile.version and report.get("source_commit") == commit
            and report.get("source_dirty") is False and report.get("android_private_key_used_in_ci") is False,
            "Unified release provenance differs from its clean source target")
    require(report.get("build_source_sha256") == source_hashes(root, profile), "Release build inputs changed after audit")
    pin, signing = mobile.load_pin(root, profile.version)
    require(read_json(output / profile.pin) == pin and read_json(output / profile.signing) == signing,
            "Published Android evidence differs from committed evidence")
    mobile.check_pin(output / profile.apk, pin["artifact"])
    desktop = read_json(output / WINDOWS_REPORT)
    require(desktop.get("version") == profile.version and desktop.get("source_commit") == commit
            and desktop.get("source_dirty") is False, "Windows provenance differs from release target")
    expected = desktop.get("windows_embedded_source_sha256", {})
    if root.resolve() == ROOT.resolve():
        require(windows.payload_hashes() == expected, "Windows payload changed after its build")
    require({"controller.py", "host.py", "net.py", "connection_metrics.py", "adaptive_stream.py",
             "ios_connector.py", "ios_assets_manifest.json"}.issubset(expected),
            "Windows source inventory lacks a required runtime module")
    if profile.version in ("7.4.0", "7.5.0", "7.6.0", "7.6.1"):
        require("updates.py" in expected, "Windows source inventory lacks the updater module")
    if profile.version in ("7.6.0", "7.6.1"):
        require("update_install.py" in expected, "Windows source inventory lacks the in-app updater module")
    if profile.version == "7.6.1":
        require({"app_identity.py", "assets/brand/aliancadx.ico", "assets/brand/aliancadx-app-256.png"}.issubset(expected),
                "Windows source inventory lacks the application identity or icon")
    pwa_sources = mobile.pwa_inventory(root)
    require(read_json(root / "desktop/ios_assets_manifest.json").get("version") == profile.version,
            "Windows browser manifest version is stale")
    require(windows.pwa_payload(expected) == pwa_sources,
            "Windows embedded PWA differs from the public browser inventory")
    pwa = windows.verify_pwa_zip(output / profile.pwa, expected, version=profile.version)
    require(read_json(output / profile.pwa_report) == pwa, "PWA report differs from its exact ZIP bytes")
    desktop_rows = desktop.get("artifacts", [])
    require(isinstance(desktop_rows, list) and len(desktop_rows) == 3
            and {r.get("file") for r in desktop_rows} == WINDOWS | {profile.pwa}, "Windows artifact inventory is incomplete")
    for row in desktop_rows:
        check_artifact(row, output, profile)
        if row["file"] in WINDOWS:
            require(row.get("product_version") == profile.version and row.get("architecture") == "Windows x64"
                    and row.get("archive_crc_ok") is True
                    and row.get("embedded_payload_matches_frozen_source") is True
                    and row.get("connection_metrics_module_included") is True
                    and row.get("payload_files_checked") == len(expected), "Windows audit is incomplete")
            if profile.version in ("7.4.0", "7.5.0", "7.6.0", "7.6.1"):
                require(row.get("updates_module_included") is True, "Windows updater audit is incomplete")
            if profile.version in ("7.6.0", "7.6.1"):
                require(row.get("in_app_update_module_included") is True, "Windows in-app updater audit is incomplete")
            if profile.version == "7.6.1":
                require(row.get("app_identity_module_included") is True and row.get("app_icon_included") is True,
                        "Windows application identity or icon audit is incomplete")
        else:
            require(row == pwa, "Windows PWA audit disagrees with its archive")
    evidence = read_json(output / profile.windows_evidence)
    windows_rows = [r for r in desktop_rows if r["file"] in WINDOWS]
    require(evidence.get("version") == profile.version and evidence.get("source_commit") == commit
            and evidence.get("artifacts") == windows_rows
            and evidence.get("embedded_source_sha256") == expected
            and evidence.get("pinned_tool_packages") == desktop.get("pinned_tool_packages"),
            "Installer evidence differs from its frozen build report")
    rows = report.get("artifacts", [])
    require(isinstance(rows, list) and len(rows) == 4
            and {r.get("file") for r in rows} == WINDOWS | {profile.apk, profile.pwa}, "Unified artifact inventory is incomplete")
    for row in rows:
        check_artifact(row, output, profile)
        if row["file"] != profile.apk:
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
            if profile.version in ("7.0.0", "7.1.0", "7.2.0", "7.3.0", "7.4.0", "7.5.0", "7.6.0", "7.6.1"):
                payload = row.get("dex_payload_verification", {})
                require(payload.get("required_defined_classes") == pin.get("new_session_and_input_classes_in_apk")
                        and payload.get("dex_defined_class_counts")
                        and payload.get("method") == "DEX class_defs/type_ids/string_ids parser",
                        "Android 7 DEX class definition audit is incomplete")
            if profile.version in ("7.6.0", "7.6.1"):
                require(row.get("self_update_manifest_verification") == {
                    "permissions": sorted(mobile.SELF_UPDATE_PERMISSIONS),
                    "receiver": mobile.SELF_UPDATE_RECEIVER, "receiver_exported": False},
                    "Android self-update permission and receiver audit is incomplete")
    expected_sums = "".join(sha256(output / name) + "  " + name + "\n" for name in sorted(profile.files - {SUMS}))
    require((output / SUMS).read_text(encoding="utf-8") == expected_sums, "Checksum list differs from complete release set")
    return report


def verify_uploaded(release, output, commit, published=False, root=ROOT, profile=DEFAULT_PROFILE):
    verify_outputs(output, commit, root, profile)
    require(release.get("tag_name") == "v" + profile.version and release.get("target_commitish") == commit
            and release.get("draft") is (not published) and release.get("prerelease") is False,
            "Release revision, tag or publication state is unexpected")
    assets = release.get("assets", [])
    require(isinstance(assets, list) and all(isinstance(a, dict) for a in assets), "Invalid uploaded asset list")
    actual = {a.get("name"): (a.get("size"), a.get("digest")) for a in assets}
    require(len(actual) == len(assets) and actual == directory_assets(output, profile)
            and all(a.get("state") == "uploaded" for a in assets),
            "Uploaded names, sizes and SHA-256 digests differ from complete frozen release")
    if published:
        require(release.get("html_url") == "https://github.com/perabru/AliancaDX-AcessoRemoto/releases/tag/v" + profile.version,
                "Unexpected public release URL")


def assemble(output, input_dir, tools_dir, android_tools, java, input_apk=None, root=ROOT, profile=DEFAULT_PROFILE):
    commit = mobile.git_identity(root)
    frozen = source_hashes(root, profile)
    pin, signing = mobile.load_pin(root, profile.version)
    require(not output.exists(), "Refusing to replace a release directory")
    for directory in (output, tools_dir):
        require(not directory.resolve().is_relative_to(root.resolve()), "Build/output directories must be outside checkout")
    subprocess.run([sys.executable, str(root / "scripts/build_release.py"), "--version", profile.version,
                    "--input-dir", str(input_dir), "--tools-dir", str(tools_dir), "--output-dir", str(output)],
                   check=True, cwd=root)
    manifest = read_json(root / "desktop/ios_assets_manifest.json")
    require(manifest.get("version") == profile.version, "Windows browser manifest version is stale")
    with tempfile.TemporaryDirectory(prefix="aliancadx-" + profile.version.replace(".", "") + "-input-") as temporary:
        if input_apk is None:
            if profile.version in ("7.2.0", "7.3.0", "7.4.0", "7.5.0", "7.6.0", "7.6.1"):
                input_apk = github_inputs.signed_apk(pin, Path(temporary))
            else:
                artifact = inputs.Artifact("android-unified-" + profile.version.replace(".", ""), profile.apk, mobile.DRIVE_FILE_ID,
                                           pin["artifact"]["bytes"], pin["artifact"]["sha256"])
                inputs.download(artifact, Path(temporary))
                input_apk = Path(temporary) / profile.apk
        apk = mobile.verify_apk(input_apk, pin, signing, android_tools, java, root)
        with (output / profile.apk).open("xb") as target, input_apk.open("rb") as source:
            shutil.copyfileobj(source, target)
        mobile.check_pin(output / profile.apk, pin["artifact"])
    for name in (profile.pin, profile.signing):
        with (output / name).open("xb") as target:
            target.write((root / "docs" / name).read_bytes())
    desktop = read_json(output / WINDOWS_REPORT)
    report = {"version": profile.version, "source_commit": commit, "source_dirty": False,
              "artifacts": [*desktop["artifacts"], apk], "build_source_sha256": frozen,
              "android_private_key_used_in_ci": False,
              "scope": "Two Windows x64 installers rebuilt from exact public sources; complete CRC and extracted source hashes checked without executing EXEs. Android owner-signed APK independently verified using official Android tools and public certificate lineage. PWA ZIP exactly matches the runtime embedded in both installers. Physical device tests and reputation approvals are not claimed."}
    (output / REPORT).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_sums(output, profile)
    require(mobile.git_identity(root) == commit and source_hashes(root, profile) == frozen, "Source changed during release audit")
    return verify_outputs(output, commit, root, profile)


def main(profile=DEFAULT_PROFILE):
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
        verify_uploaded(read_json(args.verify_upload), args.output_dir, args.expected_target, args.published, profile=profile)
        print("Verified complete " + profile.version + " uploaded release set and exact source target")
        return
    require(all((args.input_dir, args.tools_dir, args.build_tools, args.java)), "Supply Windows inputs/tools and Android Build Tools/Java")
    result = assemble(args.output_dir, args.input_dir, args.tools_dir, args.build_tools, args.java, args.input_apk, profile=profile)
    print(json.dumps({"version": profile.version, "source_commit": result["source_commit"], "release_assets": sorted(profile.files)}))


if __name__ == "__main__":
    main()
