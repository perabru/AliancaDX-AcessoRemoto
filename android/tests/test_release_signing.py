"""Fail-closed release orchestration with fake tools, never private real keys.

These tests exercise command ordering, policy checks and publication failure
handling. Actual APK cryptography is checked separately by official apksigner.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile


SPEC = importlib.util.spec_from_file_location("release_signing_under_test",
    Path(__file__).resolve().parents[1] / "tools/sign_release.py")
assert SPEC and SPEC.loader
signer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(signer)


class ReleaseSigningTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.public = self.folder / "public"
        self.public.mkdir()
        self.tools = self.folder / "SDK & Tools"
        self.tools.mkdir()
        (self.tools / "lib").mkdir()
        (self.tools / "lib/apksigner.jar").write_bytes(b"unexecuted official JAR fixture")
        for name in ("aapt", "zipalign", "java", "keytool"):
            (self.tools / name).write_bytes(b"unexecuted test tool")
        self.legacy, self.production = b"legacy public certificate fixture", b"production public certificate fixture"
        old, new = (hashlib.sha256(value).hexdigest() for value in (self.legacy, self.production))
        self.patch = patch.object(signer, "LEGACY_SHA256", old)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        (self.public / "legacy.der").write_bytes(self.legacy)
        (self.public / "production.der").write_bytes(self.production)
        (self.public / "release.lineage").write_bytes(b"lineage fixture")
        self.policy = {"schema": 1, "application_id": "br.com.perabru.minianydesk.controlador",
            "version_code": 70100, "version_name": "7.1.0", "min_sdk": 26, "target_sdk": 36,
            "rotation_min_sdk": 33,
            "legacy": {"alias": "minianydesk-legacy", "sha256": old, "certificate_der": "legacy.der"},
            "production": {"alias": "minianydesk-release", "sha256": new, "certificate_der": "production.der"},
            "lineage": {"file": "release.lineage", "sha256": hashlib.sha256(b"lineage fixture").hexdigest()}}
        self.policy_path = self.public / "release-signing.json"
        self.policy_path.write_text(json.dumps(self.policy))
        self.source = self.folder / "input.apk"
        with zipfile.ZipFile(self.source, "w") as archive:
            archive.writestr("AndroidManifest.xml", b"manifest fixture parsed by fake aapt")
            archive.writestr("classes.dex", b"dex fixture")
        self.store = self.folder / "private.p12"
        self.store.write_bytes(b"fake encrypted keystore; never opened by Java")
        self.store.chmod(0o600)
        self.environment = {"MINIANYDESK_ANDROID_KEYSTORE": str(self.store),
                            "MINIANYDESK_ANDROID_PASSWORD": secrets.token_urlsafe(32)}
        self.args = SimpleNamespace(input=self.source, output=self.folder / "release.apk",
            report=self.folder / "verification.json", policy=self.policy_path, build_tools=self.tools,
            keytool=str(self.tools / "keytool"))
        self.calls = []
        self.bad_certificate = False
        self.bad_sdk = None
        self.crlf = False
        self.test_only_value = None
        self.missing_launcher = False
        self.bad_v2_block = False

    def fake_tool(self, command, stage):
        self.calls.append((command, stage))
        if Path(command[0]).name == "java":
            if command[1] == "--class-path":
                return json.dumps({"verified": True, "warnings": 0, "min_sdk": 26, "max_sdk": 37,
                    "v1": False, "v2": True, "v3": True, "v31": True, "v4": False,
                    "v2_certificate_sha256": self.policy["production" if self.bad_v2_block else "legacy"]["sha256"],
                    "v3_certificate_sha256": self.policy["legacy"]["sha256"],
                    "v31_certificate_sha256": self.policy["production"]["sha256"],
                    "v3_min_sdk": 24, "v3_max_sdk": 32, "v31_min_sdk": 33, "v31_max_sdk": 2147483647,
                    "lineage": [self.policy["legacy"]["sha256"], self.policy["production"]["sha256"]]}).encode()
            self.assertEqual(command[1:3], ["-jar", str(self.tools / "lib/apksigner.jar")])
            command = [str(self.tools / "apksigner"), *command[3:]]
        name = Path(command[0]).name
        def text_output(text):
            return text.replace("\n", "\r\n").encode() if self.crlf else text.encode()
        if name == "keytool":
            if self.bad_certificate:
                return b"wrong certificate"
            return self.legacy if command[-1] == "minianydesk-legacy" else self.production
        if name == "aapt":
            if "xmltree" in command:
                text = "E: manifest\n  E: application\n"
                if self.test_only_value is not None:
                    text += "A: android:testOnly(0x01010272)=" + self.test_only_value + "\n"
                return text_output(text)
            return text_output("package: name='br.com.perabru.minianydesk.controlador' versionCode='70100' versionName='7.1.0'\n"
                    "sdkVersion:'26'\ntargetSdkVersion:'36'\n" + ("" if self.missing_launcher else
                    "launchable-activity: name='br.com.perabru.minianydesk.MainActivity'\n"))
        if name == "zipalign":
            if "-c" not in command:
                shutil.copyfile(command[-2], command[-1])
            return b"Verification successful"
        if name == "apksigner" and command[1] == "lineage":
            text = ""
            for role in ("legacy", "production"):
                text += "Signer in lineage certificate SHA-256 digest: " + self.policy[role]["sha256"] + "\n"
                for capability, value in signer.CAPABILITIES.items():
                    text += "Has " + capability + " capability: " + str(value).lower() + "\n"
            return text_output(text)
        if name == "apksigner" and command[1] == "sign":
            shutil.copyfile(command[command.index("--in")+1], command[command.index("--out")+1])
            return b""
        if name == "apksigner" and command[1] == "verify":
            if "--print-certs" not in command:
                return b"Verifies"
            minimum = int(command[command.index("--min-sdk-version")+1])
            maximum = int(command[command.index("--max-sdk-version")+1])
            descriptions = {"v1": "JAR signing", "v2": "APK Signature Scheme v2",
                "v3": "APK Signature Scheme v3", "v3.1": "APK Signature Scheme v3.1", "v4": "APK Signature Scheme v4"}
            lines = ["Verifies", "Number of signers: 1"]
            if maximum >= 33:
                modern = self.policy["legacy" if minimum == self.bad_sdk else "production"]["sha256"]
                lines += ["Signer (minSdkVersion=33, maxSdkVersion=2147483647) certificate SHA-256 digest: " + modern,
                          "Signer (minSdkVersion=24, maxSdkVersion=32) certificate SHA-256 digest: " + self.policy["legacy"]["sha256"]]
            else:
                lines += ["Signer #1 certificate SHA-256 digest: " + self.policy["legacy"]["sha256"]]
            required = (["v2"] if minimum < 28 else []) + (["v3"] if minimum <= 32 and maximum >= 28 else [])
            if maximum >= 33:
                required.append("v3.1")
            lines += ["Verified using " + version + " scheme (" + label + "): " + str(version in required).lower()
                      for version, label in descriptions.items()]
            return text_output("\n".join(lines)+"\n")
        raise AssertionError("Unexpected tool stage " + stage)

    def execute(self, environment=None):
        return signer.sign_release(self.args, self.environment if environment is None else environment, self.fake_tool)

    def test_missing_private_credentials_never_starts_a_tool_or_creates_output(self):
        with self.assertRaises(signer.ReleaseError):
            self.execute({})
        self.assertEqual(self.calls, [])
        self.assertFalse(self.args.output.exists())

    def test_wrong_keystore_certificate_stops_before_alignment(self):
        self.bad_certificate = True
        with self.assertRaisesRegex(signer.ReleaseError, "Wrong private keystore certificate"):
            self.execute()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.args.output.exists())

    def test_tampered_lineage_is_rejected_before_any_external_tool(self):
        (self.public / "release.lineage").write_bytes(b"changed lineage")
        with self.assertRaisesRegex(signer.ReleaseError, "lineage hash"):
            self.execute()
        self.assertEqual(self.calls, [])

    def test_bad_modern_api_certificate_never_publishes_apk_or_report(self):
        self.bad_sdk = 33
        with self.assertRaisesRegex(signer.ReleaseError, "certificates"):
            self.execute()
        self.assertFalse(self.args.output.exists())
        self.assertFalse(self.args.report.exists())
        self.assertFalse(list(self.folder.glob(".minianydesk-signing-*")))

    def test_output_must_not_replace_input_or_an_existing_file(self):
        original = self.source.read_bytes()
        self.args.output = self.source
        with self.assertRaises(signer.ReleaseError):
            self.execute()
        self.assertEqual(self.source.read_bytes(), original)
        self.args.output = self.folder / "existing.apk"
        self.args.output.write_bytes(b"retain")
        with self.assertRaises(signer.ReleaseError):
            self.execute()
        self.assertEqual(self.args.output.read_bytes(), b"retain")

    def test_success_orders_alignment_signing_sdk_checks_and_uses_only_env_secrets(self):
        original = self.source.read_bytes()
        result = self.execute()
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual([(entry["min_sdk"], entry["max_sdk"]) for entry in result["sdk_interval_checks"]],
                         [(26, 32), (33, 37), (26, 37), (32, 32), (33, 33), (36, 36), (37, 37)])
        stages = [stage for _, stage in self.calls]
        self.assertLess(stages.index("16 KB APK alignment"), stages.index("Release APK signing"))
        command = next(command for command, stage in self.calls if stage == "Release APK signing")
        self.assertEqual(command.count("--ks-key-alias"), 2)
        self.assertIn("--next-signer", command)
        self.assertEqual(command[command.index("--rotation-min-sdk-version")+1], "33")
        self.assertNotIn("--max-sdk-version", command)
        for option, value in (("--v1-signing-enabled", "false"), ("--v2-signing-enabled", "true"),
                              ("--v3-signing-enabled", "true"), ("--v4-signing-enabled", "false")):
            self.assertEqual(command[command.index(option)+1], value)
        for argv, _ in self.calls:
            self.assertFalse(any(arg.startswith("pass:") for arg in argv))
            self.assertNotIn(self.environment["MINIANYDESK_ANDROID_PASSWORD"], argv)
            if "verify" in argv:
                self.assertIn("-Werr", argv)
        text = self.args.report.read_text()
        self.assertNotIn(self.environment["MINIANYDESK_ANDROID_PASSWORD"], text)
        self.assertNotIn(str(self.store), text)

    def test_windows_crlf_and_metacharacter_paths_use_java_jar_without_batch(self):
        self.crlf = True
        self.execute()
        apksigner_calls = [argv for argv, stage in self.calls if stage == "Release APK signing" or "APK verification API" in stage]
        for argv in apksigner_calls:
            self.assertEqual(argv[:3], [str(self.tools / "java"), "-jar", str(self.tools / "lib/apksigner.jar")])
            self.assertFalse(any(arg.lower().endswith(".bat") for arg in argv))

    def test_test_only_true_or_invalid_boolean_is_refused(self):
        for value in ("(type 0x12)0xffffffff", "(type 0x10)0x0"):
            with self.subTest(value=value), self.assertRaisesRegex(signer.ReleaseError, "testOnly"):
                self.test_only_value = value
                self.execute()
            self.assertFalse(self.args.output.exists())

    def test_missing_launcher_is_refused(self):
        self.missing_launcher = True
        with self.assertRaisesRegex(signer.ReleaseError, "launcher"):
            self.execute()
        self.assertFalse(self.args.output.exists())

    def test_private_key_file_in_apk_is_refused(self):
        with zipfile.ZipFile(self.source, "a") as archive:
            archive.writestr("assets/private-owner.p12", b"must not ship")
        with self.assertRaisesRegex(signer.ReleaseError, "private-key filename"):
            self.execute()
        self.assertFalse(self.args.output.exists())

    def test_independent_v2_block_identity_must_match_existing_installations(self):
        self.bad_v2_block = True
        with self.assertRaisesRegex(signer.ReleaseError, "block certificates"):
            self.execute()
        self.assertFalse(self.args.output.exists())

    def test_keyboard_interrupt_after_atomic_publication_removes_only_our_files(self):
        real_link = signer.os.link
        def interrupt_after_report(origin, destination):
            real_link(origin, destination)
            if destination == self.args.report:
                raise KeyboardInterrupt()
        with patch.object(signer.os, "link", side_effect=interrupt_after_report), self.assertRaises(KeyboardInterrupt):
            self.execute()
        self.assertFalse(self.args.output.exists())
        self.assertFalse(self.args.report.exists())


if __name__ == "__main__":
    unittest.main()
