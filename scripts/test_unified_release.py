"""Publication boundaries for the unified set; no live network or executable run."""
from pathlib import Path
import copy
import hashlib
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import unified_release as release


class UnifiedReleaseTests(unittest.TestCase):
    profile = release.DEFAULT_PROFILE

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        self.output = Path(self.temp.name) / "release"
        self.root.mkdir(); self.output.mkdir()
        self.commit = "a" * 40
        for name in self.profile.build_sources:
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("public source fixture " + name)
        sources = {}
        for name in ("android/app/build.gradle.kts", "android/app/src/main/AndroidManifest.xml",
                     *("android/app/src/main/java/br/com/perabru/minianydesk/" + name
                       for name in ("RemoteScreenView.kt", "ControllerSession.kt", "MouseControlButton.kt"))):
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("public Android fixture " + name); sources[name] = release.sha256(path)
        if self.profile.version in ("7.0.0", "7.1.0", "7.2.0"):
            sources["android/tools/apk_payload.py"] = release.sha256(self.root / "android/tools/apk_payload.py")
        if self.profile.version in ("7.1.0", "7.2.0"):
            path = self.root / "android/app/src/main/java/br/com/perabru/minianydesk/MouseControlsPad.kt"
            path.write_text("public touchpad source fixture"); sources[str(path.relative_to(self.root))] = release.sha256(path)
        if self.profile.version == "7.2.0":
            path = self.root / "android/app/src/main/java/br/com/perabru/minianydesk/StreamFeedback.kt"
            path.write_text("public adaptive feedback fixture"); sources[str(path.relative_to(self.root))] = release.sha256(path)
        apk = b"inert APK fixture; signatures are not exercised by these publication tests"
        self.pin = {"version": self.profile.version, "version_code": {"6.4.0":64000,"7.0.0":70000,"7.1.0":70100,"7.2.0":70200}[self.profile.version],
                    "application_id": "br.com.perabru.minianydesk.controlador", "min_sdk": 26, "target_sdk": 36,
                    "debuggable": False, "gradle_release_assemble": "PASS", "gradle_lint_release": "PASS",
                    "update_signing_identity_preserved": True, "signature_report": self.profile.signing,
                    "source_sha256": sources,
                    "artifact": {"file": self.profile.apk, "bytes": len(apk), "sha256": hashlib.sha256(apk).hexdigest()}}
        if self.profile.version in ("7.0.0", "7.1.0", "7.2.0"):
            self.pin["new_session_and_input_classes_in_apk"] = {name:True for name in
                ("ControllerSession", "MouseControlButton", "RemoteScreenView", "PointerClicks", "NativeMeshInstances",
                 "SessionTrafficWindow", "SessionTrafficRate", "SessionCommandsVisibility", "FullscreenCommandsBar",
                 "ComputerBook", "MeshCodeVault", "ConnectionRecovery", "SavedComputerAccess")}
        if self.profile.version in ("7.1.0", "7.2.0"):
            self.pin["new_session_and_input_classes_in_apk"].update({"MouseControlsPad": True, "MouseControlButton$Action": True})
        if self.profile.version == "7.2.0":
            self.pin["new_session_and_input_classes_in_apk"]["StreamFeedback"] = True
        self.signing = {"bytes": len(apk), "apk_sha256": self.pin["artifact"]["sha256"],
                        "application": {"version_name": self.profile.version, "version_code": str({"6.4.0":64000,"7.0.0":70000,"7.1.0":70100,"7.2.0":70200}[self.profile.version])},
                        "sdk_interval_checks": [{"fixture_interval": i} for i in range(7)],
                        "official_apksig_blocks": {"verified": True, "test_fixture": True}}
        for name, value in ((self.profile.pin, self.pin), (self.profile.signing, self.signing)):
            self.write(self.root / "docs" / name, value); self.write(self.output / name, value)
        runtime = {"index.html": b"<html>PWA</html>", "bridge.html": b"<html>Connector</html>",
                   "sw.js": b"self.addEventListener('fetch',()=>{});", "manifest.webmanifest": b'{"name":"AliancaDX"}'}
        base = self.root / "ios-pwa"; base.mkdir(exist_ok=True)
        for name, data in runtime.items(): (base / name).write_bytes(data)
        self.write(base / "public-assets.json", list(runtime))
        self.expected = release.mobile.pwa_inventory(self.root)
        for name in ("controller.py", "host.py", "net.py", "connection_metrics.py", "adaptive_stream.py",
                     "ios_connector.py", "ios_assets_manifest.json"):
            path = self.root / "desktop" / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("public Windows fixture " + name)
            self.expected[name] = release.sha256(path)
        self.write(self.root / "desktop/ios_assets_manifest.json", {"version": self.profile.version})
        self.expected["ios_assets_manifest.json"] = release.sha256(self.root / "desktop/ios_assets_manifest.json")
        pwa = release.windows.package_pwa(self.output / self.profile.pwa, self.expected,
                                          project=self.root, resolver=lambda root, name: root / name, version=self.profile.version)
        windows = []
        for name in sorted(release.WINDOWS):
            path = self.output / name; path.write_bytes(("inert EXE fixture " + name).encode())
            windows.append({"file": name, "bytes": path.stat().st_size, "sha256": release.sha256(path),
                            "product_version": self.profile.version, "architecture": "Windows x64", "archive_crc_ok": True,
                            "embedded_payload_matches_frozen_source": True,
                            "connection_metrics_module_included": True, "payload_files_checked": len(self.expected)})
        self.desktop = {"version": self.profile.version, "source_commit": self.commit, "source_dirty": False,
                        "windows_embedded_source_sha256": self.expected, "artifacts": [*windows, pwa],
                        "pinned_tool_packages": [{"fixture": "pinned NSIS"}]}
        evidence = {"version": self.profile.version, "source_commit": self.commit, "artifacts": windows,
                    "embedded_source_sha256": self.expected, "pinned_tool_packages": self.desktop["pinned_tool_packages"]}
        (self.output / self.profile.apk).write_bytes(apk)
        apk_report = {**self.pin["artifact"], "signature_verified_in_this_job": True, "archive_crc_ok": True,
                      "alignment_kb": 16, **{key:self.signing[key] for key in
                                             ("application", "sdk_interval_checks", "official_apksig_blocks")}}
        if self.profile.version in ("7.0.0", "7.1.0", "7.2.0"):
            apk_report["dex_payload_verification"] = {
                "method": "DEX class_defs/type_ids/string_ids parser", "dex_defined_class_counts": {"classes.dex": 13},
                "required_defined_classes": self.pin["new_session_and_input_classes_in_apk"]}
        self.report = {"version": self.profile.version, "source_commit": self.commit, "source_dirty": False,
                       "android_private_key_used_in_ci": False, "artifacts": [*windows, pwa, apk_report],
                       "build_source_sha256": release.source_hashes(self.root, self.profile)}
        for name, value in ((release.WINDOWS_REPORT, self.desktop), (self.profile.windows_evidence, evidence),
                            (self.profile.pwa_report, pwa), (release.REPORT, self.report)):
            self.write(self.output / name, value)
        release.write_sums(self.output, self.profile)
        self.upload = {"tag_name": "v"+self.profile.version, "target_commitish": self.commit, "draft": True, "prerelease": False}
        self.refresh()

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def refresh(self):
        self.upload["assets"] = [{"name": p.name, "size": p.stat().st_size,
                                  "digest": "sha256:" + release.sha256(p), "state": "uploaded"}
                                 for p in self.output.iterdir()]

    def check(self, upload=None, published=False):
        return release.verify_uploaded(upload or self.upload, self.output, self.commit, published, self.root, self.profile)

    def test_exact_eleven_assets_and_both_publication_phases(self):
        self.assertEqual(len(self.profile.files), 11)
        self.upload["assets"].reverse(); self.check()
        public = {**self.upload, "draft": False,
                  "html_url": "https://github.com/perabru/AliancaDX-AcessoRemoto/releases/tag/v"+self.profile.version}
        self.check(public, published=True)

    def test_missing_extra_duplicate_wrong_size_digest_and_unfinished_upload_fail(self):
        cases = [self.upload["assets"][:-1], self.upload["assets"]*2,
                 [*self.upload["assets"], {"name": "private.p12", "size": 1}]]
        for key, value in (("size", 1), ("digest", "sha256:"+"0"*64), ("state", "new")):
            assets = copy.deepcopy(self.upload["assets"]); assets[0][key] = value; cases.append(assets)
        for assets in cases:
            with self.subTest(assets=assets), self.assertRaises(RuntimeError):
                self.check({**self.upload, "assets": assets})

    def test_wrong_tag_target_and_publication_state_fail(self):
        for key, value in (("tag_name", "v6.3.1"), ("target_commitish", "main"), ("draft", False), ("prerelease", True)):
            with self.subTest(key=key), self.assertRaises(RuntimeError): self.check({**self.upload, key:value})

    def test_private_key_extra_file_and_linked_output_fail(self):
        extra = self.output / "private.key"; extra.write_bytes(b"fixture")
        with self.assertRaises(RuntimeError): self.check()
        extra.unlink()
        apk = self.output / self.profile.apk; original = apk.read_bytes(); apk.unlink()
        outside = self.root / "outside.apk"; outside.write_bytes(original); apk.symlink_to(outside)
        with self.assertRaises(RuntimeError): self.check()

    def test_pinned_apk_tamper_fails_even_after_regenerating_published_digests(self):
        path = self.output / self.profile.apk; data = path.read_bytes(); path.write_bytes(b"X"+data[1:])
        release.write_sums(self.output, self.profile); self.refresh()
        with self.assertRaisesRegex(RuntimeError, "pin"): self.check()

    def test_source_change_after_audit_fails(self):
        (self.root / "scripts/build_release.py").write_text("changed build source")
        with self.assertRaisesRegex(RuntimeError, "inputs changed"): self.check()

    def test_stale_windows_manifest_or_missing_network_helper_fails(self):
        original = copy.deepcopy(self.desktop)
        del self.desktop["windows_embedded_source_sha256"]["connection_metrics.py"]
        self.write(self.output / release.WINDOWS_REPORT, self.desktop)
        with self.assertRaisesRegex(RuntimeError, "required runtime"): self.check()
        self.write(self.output / release.WINDOWS_REPORT, original)
        self.write(self.root / "desktop/ios_assets_manifest.json", {"version": "6.3.0"})
        with self.assertRaisesRegex(RuntimeError, "manifest version"): self.check()

    def test_android_evidence_without_official_audit_fails(self):
        self.report["artifacts"][-1]["signature_verified_in_this_job"] = False
        self.write(self.output / release.REPORT, self.report)
        with self.assertRaisesRegex(RuntimeError, "signature audit"): self.check()

    def test_pwa_runtime_change_and_checksum_tamper_fail(self):
        (self.output / release.SUMS).write_text("bad checksum list")
        with self.assertRaisesRegex(RuntimeError, "Checksum"): self.check()
        release.write_sums(self.output, self.profile)
        (self.root / "ios-pwa/sw.js").write_text("different runtime")
        with self.assertRaisesRegex(RuntimeError, "embedded PWA"): self.check()

    def test_640_pin_cannot_be_replaced_with_historical_android(self):
        release.mobile.load_pin(self.root, self.profile.version)
        self.pin["version_code"] = 63100
        self.write(self.root / "docs" / self.profile.pin, self.pin)
        with self.assertRaisesRegex(RuntimeError, "identity"): release.mobile.load_pin(self.root, self.profile.version)


if __name__ == "__main__":
    unittest.main()
