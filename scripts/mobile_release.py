#!/usr/bin/env python3
"""Audit the owner-signed Android APK and package the public PWA for v6.3.1.

This job never signs an APK and never receives a keystore or password. Its
input is a public, exact-size/SHA-256 pinned APK. Android Build Tools validate
the signatures independently using only the committed public policy.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

import download_release_inputs as inputs
import package_site

ROOT = Path(__file__).resolve().parents[1]
VERSION = "6.3.1"
APK_NAME = "AliancaDX_Acesso_Remoto_6.3.1.apk"
PWA_NAME = "AliancaDX_iOS_PWA_6.3.1.zip"
REPORT_NAME = "mobile-release-verification.json"
FILES = {APK_NAME, PWA_NAME, REPORT_NAME, "SHA256SUMS.txt"}
DRIVE_FILE_ID = "1r7QwgsDlbiLbFPsD6zTuF3WQ9YFKWp9m"
PRIVATE_SUFFIXES = {".p12", ".pfx", ".jks", ".keystore", ".pk8", ".key", ".env"}
PRIVATE_HEADER = re.compile(rb"-----BEGIN (?:RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----")
SHA = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
APK_7_REQUIRED_CLASSES = {"ControllerSession", "MouseControlButton", "RemoteScreenView", "PointerClicks",
                          "NativeMeshInstances", "SessionTrafficWindow", "SessionTrafficRate",
                          "SessionCommandsVisibility", "FullscreenCommandsBar", "ComputerBook", "MeshCodeVault",
                          "ConnectionRecovery", "SavedComputerAccess"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read_json(path):
    require(path.is_file() and not path.is_symlink(), "Missing or linked public release report")
    return json.loads(path.read_text(encoding="utf-8"))


def load_signer(root=ROOT):
    path = root / "android/tools/sign_release.py"
    spec = importlib.util.spec_from_file_location("mobile_public_signature_checks", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def dex_class_inventory(pin):
    required = APK_7_REQUIRED_CLASSES | ({"MouseControlsPad", "MouseControlButton$Action"} if pin.get("version") in ("7.1.0", "7.2.0", "7.3.0") else set())
    if pin.get("version") in ("7.2.0", "7.3.0"):
        required.add("StreamFeedback")
    if pin.get("version") == "7.3.0":
        required.update({"StreamSettings", "StreamSettingsPanel", "StreamSettingsButton"})
    declared = pin.get("new_session_and_input_classes_in_apk", {})
    require(isinstance(declared, dict) and required.issubset(declared)
            and all(isinstance(name, str) and value is True for name, value in declared.items()),
            "Android 7 payload class inventory is incomplete")
    return declared


def load_pin(root=ROOT, version=VERSION, version_code=None):
    require(version in (VERSION, "6.4.0", "7.0.0", "7.1.0", "7.2.0", "7.3.0"), "Unsupported pinned Android release")
    suffix = version.replace(".", "_")
    if version_code is None:
        version_code = {VERSION: 63100, "6.4.0": 64000, "7.0.0": 70000, "7.1.0": 70100, "7.2.0": 70200, "7.3.0": 70300}[version]
    report = read_json(root / ("docs/APK_ANDROID_" + suffix + ".json"))
    row = report.get("artifact", {})
    require(report.get("version") == version and report.get("version_code") == version_code
            and report.get("application_id") == "br.com.perabru.minianydesk.controlador"
            and report.get("min_sdk") == 26 and report.get("target_sdk") == 36
            and report.get("debuggable") is False
            and report.get("gradle_release_assemble") == "PASS"
            and report.get("gradle_lint_release") == "PASS"
            and report.get("update_signing_identity_preserved") is True,
            "Unexpected Android release identity or build evidence")
    require(row.get("file") == "AliancaDX_Acesso_Remoto_" + version + ".apk" and type(row.get("bytes")) is int
            and 1 <= row["bytes"] <= 100 * 1024 * 1024
            and isinstance(row.get("sha256"), str) and SHA.fullmatch(row["sha256"]),
            "Android artifact pin is not frozen")
    require(report.get("signature_report") == "ASSINATURA_ANDROID_" + suffix + ".json",
            "Unexpected signature report reference")
    signing = read_json(root / ("docs/ASSINATURA_ANDROID_" + suffix + ".json"))
    require(signing.get("apk_sha256") == row["sha256"] and signing.get("bytes") == row["bytes"],
            "APK pin and owner signature report disagree")
    sources = report.get("source_sha256", {})
    required = {"android/app/build.gradle.kts", "android/app/src/main/AndroidManifest.xml",
                "android/app/src/main/java/br/com/perabru/minianydesk/RemoteScreenView.kt",
                "android/app/src/main/java/br/com/perabru/minianydesk/ControllerSession.kt",
                "android/app/src/main/java/br/com/perabru/minianydesk/MouseControlButton.kt"}
    if version in ("7.1.0", "7.2.0", "7.3.0"):
        required.add("android/app/src/main/java/br/com/perabru/minianydesk/MouseControlsPad.kt")
    if version in ("7.2.0", "7.3.0"):
        required.add("android/app/src/main/java/br/com/perabru/minianydesk/StreamFeedback.kt")
    if version == "7.3.0":
        required.update("android/app/src/main/java/br/com/perabru/minianydesk/" + name
                        for name in ("StreamSettings.kt", "StreamSettingsPanel.kt", "StreamSettingsButton.kt"))
    if version in ("7.0.0", "7.1.0", "7.2.0", "7.3.0"):
        required.add("android/tools/apk_payload.py")
        dex_class_inventory(report)
    require(isinstance(sources, dict) and required.issubset(sources), "Android source inventory is incomplete")
    for name, digest in sources.items():
        path = PurePosixPath(name)
        require(isinstance(name, str) and name.startswith("android/")
                and not path.is_absolute() and path.as_posix() == name and ".." not in path.parts
                and "\\" not in name and ":" not in name and path.suffix.lower() not in PRIVATE_SUFFIXES
                and isinstance(digest, str) and SHA.fullmatch(digest), "Unsafe Android source inventory entry")
        file = root / name
        require(file.is_file() and not file.is_symlink() and file.resolve().is_relative_to(root.resolve())
                and sha256(file) == digest, "Android source differs from the signed APK build: " + name)
    return report, signing


def check_pin(path, row):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size == row["bytes"]
            and sha256(path) == row["sha256"], "APK bytes differ from the committed size/SHA-256 pin")


def inspect_archive(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)) and archive.testzip() is None, "APK duplicate names or CRC failure")
        require("AndroidManifest.xml" in names and "classes.dex" in names, "APK lacks its application payload")
        for name in names:
            pure = PurePosixPath(name)
            require(not pure.is_absolute() and ".." not in pure.parts and "\\" not in name,
                    "Unsafe archive path")
            require(pure.suffix.lower() not in PRIVATE_SUFFIXES, "APK contains a forbidden private-key filename")
            if pure.suffix.lower() in {".pem", ".txt", ".json", ".xml", ".properties"}:
                require(PRIVATE_HEADER.search(archive.read(name)) is None, "APK contains a textual private key")
    return {"archive_crc_ok": True, "private_keyfile_names": "absent", "textual_private_key_headers": "absent"}


def verify_apk(path, pin, signing, tools, java, root=ROOT, run=None):
    row = pin["artifact"]
    check_pin(path, row)
    archive = inspect_archive(path)
    payload = {}
    if pin.get("version") in ("7.0.0", "7.1.0", "7.2.0", "7.3.0"):
        declared = dex_class_inventory(pin)
        spec = importlib.util.spec_from_file_location("mobile_dex_payload_checks", root / "android/tools/apk_payload.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        payload["dex_payload_verification"] = module.verify_required_classes(path, declared)
    signer = load_signer(root)
    policy, lineage = signer.load_policy(root / "android/signing/release-signing.json")
    require(signing.get("legacy_certificate_sha256") == policy["legacy"]["sha256"]
            and signing.get("production_certificate_sha256") == policy["production"]["sha256"]
            and signing.get("lineage_sha256") == policy["lineage"]["sha256"],
            "Owner report does not match the public certificate policy")
    tools = tools.resolve(strict=True)
    java = java.resolve(strict=True)
    require(all(p.is_file() for p in (tools / "aapt", tools / "zipalign", tools / "lib/apksigner.jar", java)),
            "Official Android Build Tools or Java is missing")
    def execute(argv):
        if run:
            return run(argv)
        result = subprocess.run(argv, check=False, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        require(result.returncode == 0, "Public APK verification command failed (exit " + str(result.returncode) + ")")
        return result.stdout.decode("utf-8")
    aapt = str(tools / "aapt")
    apk_signer = [str(java), "-jar", str(tools / "lib/apksigner.jar")]
    identity = signer.apk_identity(execute([aapt, "dump", "badging", str(path)]), policy)
    signer.manifest_safety(execute([aapt, "dump", "xmltree", str(path), "AndroidManifest.xml"]))
    execute([str(tools / "zipalign"), "-c", "-P", "16", "4", str(path)])
    signer.verify_lineage(execute(apk_signer + ["lineage", "--in", str(lineage), "--print-certs"]), policy)
    checks = []
    for low, high in signer.VERIFY_INTERVALS:
        result = execute(apk_signer + ["verify", "-Werr", "--verbose", "--print-certs",
                         "--min-sdk-version", str(low), "--max-sdk-version", str(high), str(path)])
        checks.append(signer.verify_signature(result, low, high, policy))
    blocks = json.loads(execute([str(java), "--class-path", str(tools / "lib/apksigner.jar"),
                                str(root / "android/tools/VerifySignerBlocks.java"), str(path)]))
    signer.verify_blocks(blocks, policy)
    signer.verify_lineage(execute(apk_signer + ["lineage", "--in", str(path), "--print-certs"]), policy)
    check_pin(path, row)
    return {**row, **archive, **payload, "application": identity, "signature_verified_in_this_job": True,
            "alignment_kb": 16, "sdk_interval_checks": checks, "official_apksig_blocks": blocks,
            "physical_android_install_tested": False, "play_protect_approval": "not tested or claimed"}


def pwa_inventory(root=ROOT):
    rows = package_site.ios_public_assets(root)
    selected = {}
    for name, path in rows:
        require(PurePosixPath(name).suffix.lower() not in PRIVATE_SUFFIXES, "Private file in PWA inventory")
        data = path.read_bytes()
        require(PRIVATE_HEADER.search(data) is None, "Private key in public PWA content")
        selected["ios-pwa/" + name] = hashlib.sha256(data).hexdigest()
    return selected


def verify_pwa(path, expected):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)) and set(names) == set(expected)
                and archive.testzip() is None, "PWA ZIP inventory or CRC mismatch")
        actual = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in names}
    require(actual == expected, "PWA ZIP bytes differ from the frozen runtime")
    return {"file": path.name, "bytes": path.stat().st_size, "sha256": sha256(path),
            "version": VERSION, "archive_crc_ok": True, "runtime_files_checked": len(actual),
            "source_sha256": actual, "native_ios_binary": False, "physical_iphone_tested": False,
            "windows_connector_compatible": "6.3.0; no Windows installer is rebuilt in this release"}


def package_pwa(path, root=ROOT):
    require(not path.exists(), "Refusing to replace a PWA output")
    expected = pwa_inventory(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mobile-pwa-", dir=path.parent) as temporary:
        stage = Path(temporary) / path.name
        with zipfile.ZipFile(stage, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name in sorted(expected):
                source = root / name
                data = source.read_bytes()
                require(hashlib.sha256(data).hexdigest() == expected[name], "PWA source changed during packaging")
                entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.create_system = 3; entry.external_attr = 0o100644 << 16
                archive.writestr(entry, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        verify_pwa(stage, expected)
        os.link(stage, path)
    return verify_pwa(path, expected)


def git_identity(root=ROOT):
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=root, text=True))
    require(COMMIT.fullmatch(commit) and not dirty, "Release requires the exact clean source commit")
    expected = os.environ.get("GITHUB_SHA")
    require(not expected or expected == commit, "Checkout does not match GITHUB_SHA")
    return commit


def directory_assets(directory):
    require(directory.is_dir() and not directory.is_symlink(), "Missing release output directory")
    paths = list(directory.iterdir())
    require({p.name for p in paths} == FILES and len(paths) == len(FILES)
            and all(p.is_file() and not p.is_symlink() for p in paths), "Unexpected or linked release outputs")
    return {p.name: (p.stat().st_size, "sha256:" + sha256(p)) for p in paths}


def verify_uploaded(release, directory, expected_commit, published=False, root=ROOT):
    require(COMMIT.fullmatch(expected_commit), "Expected a concrete source commit")
    require(release.get("tag_name") == "v" + VERSION
            and release.get("target_commitish") == expected_commit
            and release.get("draft") is (not published) and release.get("prerelease") is False,
            "Unexpected release revision, tag, or publication state")
    assets = release.get("assets", [])
    require(isinstance(assets, list) and all(isinstance(a, dict) for a in assets), "Invalid release asset list")
    actual = {a.get("name"): (a.get("size"), a.get("digest")) for a in assets}
    require(len(actual) == len(assets) and actual == directory_assets(directory)
            and all(a.get("state") == "uploaded" for a in assets), "Uploaded names/sizes/SHA-256 digests are not the complete frozen set")
    if published:
        require(release.get("html_url") == "https://github.com/perabru/AliancaDX-AcessoRemoto/releases/tag/v" + VERSION,
                "Unexpected published release URL")
    report = read_json(directory / REPORT_NAME)
    require(report.get("version") == VERSION and report.get("source_commit") == expected_commit
            and report.get("source_dirty") is False, "Output provenance does not match release target")
    pin, _ = load_pin(root)
    check_pin(directory / APK_NAME, pin["artifact"])
    verify_pwa(directory / PWA_NAME, pwa_inventory(root))
    rows = report.get("artifacts", [])
    require(isinstance(rows, list) and len(rows) == 2
            and {row.get("file") for row in rows} == {APK_NAME, PWA_NAME}, "Output artifact report is incomplete")
    for row in rows:
        path = directory / row["file"]
        require(row.get("bytes") == path.stat().st_size and row.get("sha256") == sha256(path),
                "Artifact changed after its audit")
    expected_sums = "".join(sha256(directory / name) + "  " + name + "\n"
                            for name in sorted(FILES - {"SHA256SUMS.txt"}))
    require((directory / "SHA256SUMS.txt").read_text() == expected_sums,
            "Published checksum list differs from the complete artifact set")


def assemble(output, tools, java, input_apk=None, root=ROOT):
    commit = git_identity(root)
    pin, signing = load_pin(root)
    output.mkdir(parents=True, exist_ok=True)
    require(not list(output.iterdir()), "Release output directory must be empty")
    with tempfile.TemporaryDirectory(prefix="aliancadx-mobile-input-") as temporary:
        if input_apk is None:
            artifact = inputs.Artifact("mobile-android-631", APK_NAME, DRIVE_FILE_ID,
                                       pin["artifact"]["bytes"], pin["artifact"]["sha256"])
            inputs.download(artifact, Path(temporary))
            input_apk = Path(temporary) / APK_NAME
        apk = verify_apk(input_apk, pin, signing, tools, java, root)
        stage = Path(temporary) / "audited-copy.apk"
        shutil.copyfile(input_apk, stage)
        check_pin(stage, pin["artifact"])
        # Copy into the output filesystem first, then publish with no replacement.
        with tempfile.TemporaryDirectory(prefix=".mobile-release-", dir=output) as publication:
            copy = Path(publication) / APK_NAME
            shutil.copyfile(stage, copy); check_pin(copy, pin["artifact"])
            os.link(copy, output / APK_NAME)
        pwa = package_pwa(output / PWA_NAME, root)
        require(pwa_inventory(root) == pwa["source_sha256"], "PWA changed after packaging")
        require(git_identity(root) == commit, "Source changed during release verification")
        report = {"version": VERSION, "source_commit": commit, "source_dirty": False,
                  "artifacts": [apk, pwa], "android_private_key_used_in_ci": False,
                  "windows_installer_version": "6.3.0 (unchanged)",
                  "signing_policy_sha256": sha256(root / "android/signing/release-signing.json")}
        (output / REPORT_NAME).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        checksum_paths = sorted(output.iterdir())
        (output / "SHA256SUMS.txt").write_text("".join(sha256(p) + "  " + p.name + "\n" for p in checksum_paths))
        directory_assets(output)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--build-tools", type=Path)
    p.add_argument("--java", type=Path, default=Path(shutil.which("java") or "/missing-java"))
    p.add_argument("--input-apk", type=Path)
    p.add_argument("--verify-upload", type=Path)
    p.add_argument("--expected-target")
    p.add_argument("--published", action="store_true")
    a = p.parse_args()
    if a.verify_upload:
        require(a.expected_target is not None, "Supply the frozen release target")
        verify_uploaded(read_json(a.verify_upload), a.output_dir, a.expected_target, a.published)
        print(json.dumps({"verified_uploaded_release": True, "published": a.published}))
        return
    require(a.build_tools is not None, "Supply official Android Build Tools")
    report = assemble(a.output_dir, a.build_tools, a.java, a.input_apk)
    print(json.dumps({"version": VERSION, "source_commit": report["source_commit"],
                      "verified_artifacts": [x["file"] for x in report["artifacts"]]}))


if __name__ == "__main__":
    main()
