#!/usr/bin/env python3
"""Align, sign and verify the Android release with protected private keys.

Requires official Android Build Tools and keytool. Credentials come exclusively
from environment variables or an external password file. No signing key is
included in this public project. Successful verification is not Play Protect
approval or a statement about antivirus classification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile


PROJECT = Path(__file__).resolve().parents[1]
LEGACY_SHA256 = "266dd27bd7af6914f0ddaebbbbbe30febb056c17939a94a8440fa6a33750b3d0"
PASSWORD_ENV = "MINIANYDESK_ANDROID_PASSWORD_INTERNAL"
VERIFY_INTERVALS = ((26, 32), (33, 37), (26, 37), (32, 32), (33, 33), (36, 36), (37, 37))
CAPABILITIES = {"installed data": True, "shared UID": False, "permission": False,
                "rollback": False, "auth": False}


class ReleaseError(Exception):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def public_file(folder: Path, name: str) -> Path:
    require(isinstance(name, str) and name and Path(name).name == name
            and "/" not in name and "\\" not in name, "Invalid public signing filename.")
    path = (folder / name).resolve(strict=True)
    require(path.parent == folder.resolve() and path.is_file(), "Invalid public signing file.")
    return path


def load_policy(path: Path) -> tuple[dict, Path]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    require(policy.get("schema") == 1, "Unsupported signing policy.")
    require(policy.get("application_id") == "br.com.perabru.minianydesk.controlador"
            and policy.get("version_code") == 70500 and policy.get("version_name") == "7.5.0"
            and policy.get("min_sdk") == 26 and policy.get("target_sdk") == 36
            and policy.get("rotation_min_sdk") == 33, "Unexpected release identity or SDK policy.")
    require(policy.get("legacy", {}).get("sha256") == LEGACY_SHA256,
            "The legacy certificate does not match existing installations.")
    for role, alias in (("legacy", "minianydesk-legacy"), ("production", "minianydesk-release")):
        entry = policy.get(role, {})
        require(entry.get("alias") == alias and isinstance(entry.get("sha256"), str)
                and re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]) is not None,
                "Invalid expected certificate for " + role + ".")
        cert = public_file(path.parent, entry["certificate_der"])
        require(sha256(cert) == entry["sha256"], "Public certificate was changed: " + role + ".")
    require(policy["production"]["sha256"] != LEGACY_SHA256, "Production and legacy keys must differ.")
    lineage = public_file(path.parent, policy["lineage"]["file"])
    require(sha256(lineage) == policy["lineage"]["sha256"], "Signing lineage hash mismatch.")
    return policy, lineage


def credentials(environment: dict[str, str]) -> tuple[Path, dict[str, str]]:
    location = environment.get("MINIANYDESK_ANDROID_KEYSTORE")
    password = environment.get("MINIANYDESK_ANDROID_PASSWORD")
    password_file = environment.get("MINIANYDESK_ANDROID_PASSWORD_FILE")
    require(bool(location), "Set MINIANYDESK_ANDROID_KEYSTORE to the external private PKCS12.")
    require(bool(password) != bool(password_file),
            "Provide exactly one private password source: PASSWORD or PASSWORD_FILE environment variable.")
    store = Path(location).expanduser().resolve(strict=True)
    require(store.is_file() and PROJECT != store and PROJECT not in store.parents,
            "Keep the private keystore outside the public project.")
    if password_file:
        secret_path = Path(password_file).expanduser().resolve(strict=True)
        require(PROJECT != secret_path and PROJECT not in secret_path.parents,
                "Keep the private password file outside the public project.")
        lines = secret_path.read_text(encoding="utf-8").splitlines()
        require(len(lines) == 1, "The password file must contain one line.")
        password = lines[0]
        if os.name != "nt":
            require(secret_path.stat().st_mode & 0o077 == 0, "Password file permissions must be private.")
    require(password is not None and len(password) >= 20 and not any(c in password for c in "\x00\r\n"),
            "A strong, single-line private keystore password is required.")
    if os.name != "nt":
        require(store.stat().st_mode & 0o077 == 0, "Private keystore permissions must exclude other users.")
    child_env = dict(environment)
    child_env[PASSWORD_ENV] = password
    return store, child_env


def runner(environment: dict[str, str]):
    def run(command: list[str], stage: str) -> bytes:
        # Commands contain environment-variable references, never passwords.
        result = subprocess.run(command, env=environment, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        if result.returncode:
            # Some providers echo diagnostics. Keep raw stderr out of public
            # reports/logs; no credential value is included in this exception.
            raise ReleaseError(stage + " failed (exit " + str(result.returncode) + ").")
        return result.stdout
    return run


def verify_lineage(text: str, policy: dict) -> None:
    digests = re.findall(r"certificate SHA-256 digest: ([a-fA-F0-9]{64})", text)
    require([d.lower() for d in digests] == [policy["legacy"]["sha256"], policy["production"]["sha256"]],
            "The lineage does not contain exactly the expected old and new certificates.")
    for label, expected in CAPABILITIES.items():
        values = re.findall(r"Has " + re.escape(label) + r" capability\s*:\s*(true|false)", text)
        require(values == [str(expected).lower()] * 2, "Unexpected lineage capability: " + label + ".")


def apk_identity(text: str, policy: dict) -> dict:
    text = "\n".join(text.splitlines())
    package = re.search(r"^package: (.+)$", text, re.MULTILINE)
    require(package is not None, "aapt could not read the APK identity.")
    values = dict(re.findall(r"(\w+)='([^']*)'", package[1]))
    actual = {"application_id": values.get("name"), "version_code": values.get("versionCode"),
              "version_name": values.get("versionName")}
    require(actual == {"application_id": policy["application_id"], "version_code": str(policy["version_code"]),
                       "version_name": policy["version_name"]}, "Unexpected APK package or version.")
    for label, expected in (("sdkVersion", policy["min_sdk"]), ("targetSdkVersion", policy["target_sdk"])):
        match = re.search(r"^" + label + r":'([^']*)'", text, re.MULTILINE)
        require(match is not None and match[1] == str(expected), "Unexpected APK SDK declaration.")
    require("application-debuggable" not in text, "Refusing a debuggable release APK.")
    launcher = re.findall(r"^launchable-activity: name='([^']+)'", text, re.MULTILINE)
    require(launcher == ["br.com.perabru.minianydesk.MainActivity"], "Unexpected or missing controller launcher.")
    return actual


def manifest_safety(text: str) -> None:
    # A missing attribute has Android's documented false default. Explicit true
    # or a value that is not a compiled boolean is refused.
    for attribute in ("testOnly", "debuggable"):
        values = re.findall(r"android:" + attribute + r"\([^)]*\)=([^\r\n]+)", text)
        require(len(values) <= 1, "Duplicate manifest safety attribute.")
        if values:
            value = re.fullmatch(r"\(type 0x12\)0x([0-9a-fA-F]+)\s*", values[0])
            require(value is not None and int(value[1], 16) == 0,
                    "Refusing true or invalid android:" + attribute + ".")


def require_unsigned(path: Path, private_password: str) -> dict:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require("AndroidManifest.xml" in names and "classes.dex" in names, "Invalid release APK contents.")
        require(len(names) == len(set(names)), "Duplicate entries in unsigned APK.")
        require(not any(name.upper().startswith("META-INF/") and
                        name.upper().endswith((".RSA", ".DSA", ".EC", ".SF")) for name in names),
                "Input already contains JAR signing material; supply the unsigned Gradle release.")
        for name in names:
            suffix = Path(name).suffix.lower()
            require(suffix not in (".jks", ".keystore", ".p12", ".pfx", ".pk8", ".key"),
                    "A private-key filename is present in the APK.")
            contents = archive.read(name)
            require(private_password.encode("utf-8") not in contents, "Private password bytes found in the APK.")
            if suffix in (".pem", ".txt", ".json", ".xml", ".properties"):
                require(re.search(rb"-----BEGIN (?:RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----", contents) is None,
                        "A textual private-key header is present in the APK.")
        central_directory = archive.start_dir
    with path.open("rb") as stream:
        stream.seek(max(0, central_directory - 16))
        require(stream.read(16) != b"APK Sig Block 42", "Input already has an APK Signing Block.")
    return {"forbidden_keyfile_names": "absent", "private_password_bytes": "absent",
            "textual_private_key_headers": "absent; textual extensions inspected"}


def verify_signature(text: str, minimum: int, maximum: int, policy: dict) -> dict:
    text = "\n".join(text.splitlines())
    certs = re.findall(r"^Signer #\d+ certificate SHA-256 digest: ([a-fA-F0-9]{64})$", text, re.MULTILINE)
    targets = re.findall(r"^Signer \(minSdkVersion=(\d+), maxSdkVersion=(\d+)\) certificate SHA-256 digest: ([a-fA-F0-9]{64})$",
                         text, re.MULTILINE)
    expected = policy["legacy" if maximum < policy["rotation_min_sdk"] else "production"]["sha256"]
    if targets:
        require(len(targets) == 2 and {digest.lower() for _, _, digest in targets}
                == {policy["legacy"]["sha256"], policy["production"]["sha256"]}, "Unexpected SDK-targeted certificates.")
        active = [digest.lower() for low, high, digest in targets if int(low) <= maximum <= int(high)]
    else:
        require(maximum <= 32, "Missing SDK-targeted v3.1 certificate information.")
        active = [cert.lower() for cert in certs]
    require(active == [expected], "Unexpected active certificate for API " + str(minimum) + ".." + str(maximum) + ".")
    require("Number of signers: 1" in text, "Unexpected signer count.")
    schemes = {}
    for version in ("v1", "v2", "v3", "v3.1", "v4"):
        match = re.search(r"^Verified using " + re.escape(version) + r" scheme \([^\n]*\): (true|false)$",
                          text, re.MULTILINE)
        require(match is not None, "Missing signature-scheme result for " + version + ".")
        schemes[version] = match[1] == "true"
    require(not schemes["v1"] and not schemes["v4"], "Unexpected v1 or v4 signing.")
    required = (["v2"] if minimum < 28 else []) + (["v3"] if minimum <= 32 and maximum >= 28 else [])
    if maximum >= 33:
        required.append("v3.1")
    require(all(schemes[version] for version in required), "A required signature scheme failed for checked SDK interval.")
    return {"min_sdk": minimum, "max_sdk": maximum, "highest_sdk_active_certificate_sha256": expected, "schemes": schemes}


def verify_blocks(result: dict, policy: dict) -> None:
    require(result.get("verified") is True and result.get("warnings") == 0
            and result.get("min_sdk") == 26 and result.get("max_sdk") == 37,
            "Official apksig block verification failed.")
    require(result.get("v1") is False and result.get("v4") is False
            and all(result.get(version) is True for version in ("v2", "v3", "v31")),
            "Unexpected verified signing blocks.")
    require(result.get("v2_certificate_sha256") == policy["legacy"]["sha256"]
            and result.get("v3_certificate_sha256") == policy["legacy"]["sha256"]
            and result.get("v31_certificate_sha256") == policy["production"]["sha256"],
            "Official apksig found unexpected block certificates.")
    require(result.get("v3_min_sdk", 100) <= 26 and result.get("v3_max_sdk") == 32
            and result.get("v31_min_sdk") == 33 and result.get("v31_max_sdk") == 2147483647,
            "Unexpected rotation platform bounds.")
    require(result.get("lineage") == [policy["legacy"]["sha256"], policy["production"]["sha256"]],
            "Official apksig found an unexpected embedded lineage.")


def sign_release(args, environment: dict[str, str] | None = None, run=None) -> dict:
    environment = dict(os.environ if environment is None else environment)
    store, child_env = credentials(environment)
    source = args.input.resolve(strict=True)
    output = args.output.resolve()
    report_path = (args.report or args.output.with_suffix(".signing.json")).resolve()
    require(source.is_file() and output != source and report_path != source and report_path != output,
            "Input, output and report must be distinct files.")
    require(not output.exists() and not report_path.exists(), "Refusing to overwrite an existing output or report.")
    policy, lineage = load_policy(args.policy.resolve(strict=True))
    tools = args.build_tools.resolve(strict=True)
    apk_signer_jar = tools / "lib/apksigner.jar"
    aligner = tools / ("zipalign.exe" if os.name == "nt" else "zipalign")
    aapt = tools / ("aapt.exe" if os.name == "nt" else "aapt")
    keytool = Path(args.keytool).resolve(strict=True) if args.keytool else Path(shutil.which("keytool") or "")
    java_option = getattr(args, "java", None)
    java = Path(java_option).resolve(strict=True) if java_option else keytool.with_name("java.exe" if os.name == "nt" else "java")
    require(all(path.is_file() for path in (apk_signer_jar, java, aligner, aapt, keytool)), "Required Android/JDK tool is missing.")
    # Invoke the official JAR through a native Java executable. No .bat wrapper,
    # shell, or evaluation of path metacharacters is involved on Windows.
    apk_signer = [str(java), "-jar", str(apk_signer_jar)]
    execute = run or runner(child_env)
    for role in ("legacy", "production"):
        der = execute([str(keytool), "-exportcert", "-keystore", str(store), "-storetype", "PKCS12",
                       "-storepass:env", PASSWORD_ENV, "-alias", policy[role]["alias"]], "Certificate export " + role)
        require(hashlib.sha256(der).hexdigest() == policy[role]["sha256"], "Wrong private keystore certificate: " + role + ".")
    lineage_text = execute(apk_signer + ["lineage", "--in", str(lineage), "--print-certs"], "Lineage validation").decode()
    verify_lineage(lineage_text, policy)
    private_checks = require_unsigned(source, child_env[PASSWORD_ENV])
    source_hash = sha256(source)
    identity = apk_identity(execute([str(aapt), "dump", "badging", str(source)], "Unsigned APK identity").decode(), policy)
    manifest_safety(execute([str(aapt), "dump", "xmltree", str(source), "AndroidManifest.xml"], "Unsigned manifest safety").decode())
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    publication_candidates = {}
    report_stage = None
    def publish_no_replace(origin, destination):
        info = origin.stat()
        publication_candidates[destination] = (info.st_dev, info.st_ino)
        # Same-filesystem hard links publish complete verified bytes atomically
        # and fail if the destination already exists. No partial public copy.
        os.link(origin, destination)
    try:
        with tempfile.TemporaryDirectory(prefix=".minianydesk-signing-", dir=output.parent) as temporary:
            folder = Path(temporary)
            aligned, signed = folder / "aligned.apk", folder / "signed.apk"
            execute([str(aligner), "-P", "16", "-f", "4", str(source), str(aligned)], "16 KB APK alignment")
            command = apk_signer + ["sign", "--in", str(aligned), "--out", str(signed),
                       "--min-sdk-version", "26",
                       "--rotation-min-sdk-version", "33", "--lineage", str(lineage),
                       "--v1-signing-enabled", "false", "--v2-signing-enabled", "true",
                       "--v3-signing-enabled", "true", "--v4-signing-enabled", "false",
                       "--debuggable-apk-permitted", "false", "--alignment-preserved", "true"]
            for index, role in enumerate(("legacy", "production")):
                if index:
                    command += ["--next-signer"]
                command += ["--ks", str(store), "--ks-type", "PKCS12", "--ks-key-alias", policy[role]["alias"],
                            "--ks-pass", "env:" + PASSWORD_ENV, "--key-pass", "env:" + PASSWORD_ENV]
            execute(command, "Release APK signing")
            execute([str(aligner), "-c", "-P", "16", "4", str(signed)], "Signed APK alignment verification")
            apk_identity(execute([str(aapt), "dump", "badging", str(signed)], "Signed APK identity").decode(), policy)
            manifest_safety(execute([str(aapt), "dump", "xmltree", str(signed), "AndroidManifest.xml"], "Signed manifest safety").decode())
            checks = []
            for minimum, maximum in VERIFY_INTERVALS:
                text = execute(apk_signer + ["verify", "-Werr", "--verbose", "--print-certs",
                                "--min-sdk-version", str(minimum), "--max-sdk-version", str(maximum), str(signed)],
                               "APK verification API " + str(minimum) + ".." + str(maximum)).decode()
                checks.append(verify_signature(text, minimum, maximum, policy))
            block_helper = Path(__file__).with_name("VerifySignerBlocks.java")
            require(block_helper.is_file(), "The official apksig block-inspection helper is missing.")
            block_result = json.loads(execute([str(java), "--class-path", str(apk_signer_jar), str(block_helper), str(signed)],
                                               "Official apksig block certificates").decode())
            verify_blocks(block_result, policy)
            embedded = execute(apk_signer + ["lineage", "--in", str(signed), "--print-certs"],
                               "Embedded lineage validation").decode()
            verify_lineage(embedded, policy)
            require(sha256(source) == source_hash, "Input APK changed during signing.")
            digest = sha256(signed)
            report = {"application": identity, "input_sha256": source_hash, "apk_sha256": digest,
                      "bytes": signed.stat().st_size, "rotation_min_sdk": 33,
                      "legacy_certificate_sha256": policy["legacy"]["sha256"],
                      "production_certificate_sha256": policy["production"]["sha256"],
                      "lineage_sha256": policy["lineage"]["sha256"], "alignment_kb": 16,
                      "verification_warnings_as_errors": True, "sdk_interval_checks": checks,
                      "official_apksig_blocks": block_result,
                      "verification_scope_note": "All complete intervals and exact SDK32/33/36/37 checks run with -Werr. Exact SDK26-only apksigner35 sees the legitimate v3 anti-stripping reference as unknown; it is not reported as a strict pass or silently filtered. SDK26 is covered by strict complete intervals26..32 and26..37 plus verified V2 legacy-certificate inspection.",
                      "apksigner_backend": "native Java -jar official apksigner.jar; no shell or batch wrapper",
                      "private_material_checks": private_checks, "play_protect_approval": "not tested or claimed",
                      "limitations": ["API 26–32 retain the previously exposed legacy key for update compatibility.",
                                      "No real-device installation or Play Protect approval is implied."]}
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=report_path.parent,
                                             prefix=".signing-report-", delete=False) as stream:
                report_stage = Path(stream.name)
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            publish_no_replace(signed, output)
            publish_no_replace(report_stage, report_path)
            require(sha256(output) == digest, "Published APK hash mismatch.")
            return report
    except BaseException:
        for destination, identity in publication_candidates.items():
            try:
                info = destination.stat()
                if (info.st_dev, info.st_ino) == identity:
                    destination.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        if report_stage is not None:
            report_stage.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="unsigned release APK from Gradle")
    parser.add_argument("--output", type=Path, required=True, help="new signed APK; existing files are never overwritten")
    parser.add_argument("--build-tools", type=Path, required=True, help="official Android SDK Build Tools directory")
    parser.add_argument("--keytool", help="optional JDK keytool executable")
    parser.add_argument("--java", help="optional JDK java executable; defaults to keytool's sibling")
    parser.add_argument("--policy", type=Path, default=PROJECT / "signing/release-signing.json")
    parser.add_argument("--report", type=Path, help="optional new public verification report")
    args = parser.parse_args()
    try:
        report = sign_release(args)
    except (ReleaseError, OSError, ValueError, KeyError, TypeError, AttributeError,
            zipfile.BadZipFile, subprocess.SubprocessError) as error:
        print("Signing stopped: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps({"apk": str(args.output), "sha256": report["apk_sha256"],
                      "verified_sdk_intervals": [list(pair) for pair in VERIFY_INTERVALS],
                      "play_protect_approval": "not claimed"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
