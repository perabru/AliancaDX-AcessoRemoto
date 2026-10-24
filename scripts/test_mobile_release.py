"""Release-integrity boundaries; no private keys, live network or Android device."""
from pathlib import Path
import copy
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("mobile_release_under_test", SCRIPTS / "mobile_release.py")
mobile = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mobile
SPEC.loader.exec_module(mobile)


class MobileReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.root = self.folder / "source"
        self.root.mkdir()
        self.output = self.folder / "release"
        self.output.mkdir()
        self.commit = "a" * 40
        self.apk = b"an inert pinned APK fixture; never installed or signature-tested"
        self.source_names = [
            "android/app/build.gradle.kts", "android/app/src/main/AndroidManifest.xml",
            "android/app/src/main/java/br/com/perabru/minianydesk/RemoteScreenView.kt",
            "android/app/src/main/java/br/com/perabru/minianydesk/ControllerSession.kt",
            "android/app/src/main/java/br/com/perabru/minianydesk/MouseControlButton.kt",
        ]
        sources = {}
        for name in self.source_names:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("public source " + name)
            sources[name] = mobile.sha256(path)
        self.pin = {
            "version": "6.3.1", "version_code": 63100,
            "application_id": "br.com.perabru.minianydesk.controlador",
            "min_sdk": 26, "target_sdk": 36, "debuggable": False,
            "gradle_release_assemble": "PASS", "gradle_lint_release": "PASS",
            "update_signing_identity_preserved": True,
            "artifact": {"file": mobile.APK_NAME, "bytes": len(self.apk), "sha256": hashlib.sha256(self.apk).hexdigest()},
            "signature_report": "ASSINATURA_ANDROID_6_3_1.json", "source_sha256": sources,
        }
        self.signing = {"apk_sha256": self.pin["artifact"]["sha256"], "bytes": len(self.apk)}
        self.write_pins()
        self.runtime = {"index.html": b"<html>PWA</html>", "bridge.html": b"<html>Connector</html>",
                        "manifest.webmanifest": b'{"name":"AliancaDX"}',
                        "sw.js": b"self.addEventListener('fetch',()=>{});"}
        base = self.root / "ios-pwa"
        base.mkdir()
        for name, data in self.runtime.items():
            (base / name).write_bytes(data)
        (base / "public-assets.json").write_text(json.dumps(list(self.runtime)))

    def write_pins(self):
        docs = self.root / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "APK_ANDROID_6_3_1.json").write_text(json.dumps(self.pin))
        (docs / "ASSINATURA_ANDROID_6_3_1.json").write_text(json.dumps(self.signing))

    def prepare_outputs(self):
        (self.output / mobile.APK_NAME).write_bytes(self.apk)
        pwa = mobile.package_pwa(self.output / mobile.PWA_NAME, self.root)
        report = {"version": "6.3.1", "source_commit": self.commit, "source_dirty": False,
                  "artifacts": [self.pin["artifact"], pwa]}
        (self.output / mobile.REPORT_NAME).write_text(json.dumps(report))
        self.write_sums()
        self.release = {"tag_name": "v6.3.1", "target_commitish": self.commit,
                        "draft": True, "prerelease": False, "assets": []}
        self.refresh_assets()

    def write_sums(self):
        (self.output / "SHA256SUMS.txt").write_text("".join(
            mobile.sha256(self.output / name) + "  " + name + "\n"
            for name in sorted(mobile.FILES - {"SHA256SUMS.txt"})))

    def refresh_assets(self):
        self.release["assets"] = [{"name": path.name, "size": path.stat().st_size,
                                    "digest": "sha256:" + mobile.sha256(path), "state": "uploaded"}
                                   for path in self.output.iterdir()]

    def check_upload(self, release=None, published=False):
        mobile.verify_uploaded(release or self.release, self.output, self.commit, published, self.root)

    def test_public_pin_binds_build_version_identity_and_signature_report(self):
        pin, signing = mobile.load_pin(self.root)
        self.assertEqual(pin, self.pin)
        self.assertEqual(signing, self.signing)
        for key, value in [("version", "6.2.2"), ("version_code", 62200), ("debuggable", True),
                           ("gradle_lint_release", "FAIL"), ("update_signing_identity_preserved", False)]:
            with self.subTest(key=key):
                changed = dict(self.pin); changed[key] = value
                (self.root / "docs/APK_ANDROID_6_3_1.json").write_text(json.dumps(changed))
                with self.assertRaises(RuntimeError): mobile.load_pin(self.root)
        self.write_pins()

    def test_wrong_source_or_signature_pin_is_rejected(self):
        for mutate in ("source", "signature"):
            with self.subTest(mutate=mutate):
                if mutate == "source":
                    (self.root / self.source_names[0]).write_text("tampered public source")
                else:
                    self.signing["apk_sha256"] = "0" * 64; self.write_pins()
                with self.assertRaises(RuntimeError): mobile.load_pin(self.root)
                if mutate == "source":
                    (self.root / self.source_names[0]).write_text("public source " + self.source_names[0])

    def test_path_escape_or_private_key_inventory_is_refused(self):
        for name in ("../private.key", "android/../../outside", "android/key.p12"):
            with self.subTest(name=name):
                changed = copy.deepcopy(self.pin); changed["source_sha256"][name] = "a" * 64
                (self.root / "docs/APK_ANDROID_6_3_1.json").write_text(json.dumps(changed))
                with self.assertRaises(RuntimeError): mobile.load_pin(self.root)

    def test_apk_same_size_tamper_fails_before_archive_or_android_tools(self):
        path = self.folder / "tampered.apk"; path.write_bytes(b"X" + self.apk[1:])
        with patch.object(mobile, "inspect_archive", side_effect=AssertionError("must not inspect")):
            with self.assertRaisesRegex(RuntimeError, "pin"):
                mobile.verify_apk(path, self.pin, self.signing, self.folder, self.folder / "java", self.root)

    def test_apk_archive_refuses_private_key_paths_and_headers(self):
        for name in ("secret.p12", "config/private.key", "../escape.txt", "config.pem"):
            path = self.folder / "bad.apk"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("AndroidManifest.xml", b"compiled manifest fixture")
                archive.writestr("classes.dex", b"dex fixture")
                archive.writestr(name, b"-----BEGIN PRIVATE KEY-----")
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                mobile.inspect_archive(path)

    def test_pwa_zip_is_deterministic_and_exactly_inventoried(self):
        first = self.folder / "first.zip"; second = self.folder / "second.zip"
        report = mobile.package_pwa(first, self.root)
        for name in self.runtime: os.utime(self.root / "ios-pwa" / name, (1500000000,1500000000))
        mobile.package_pwa(second, self.root)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(report["runtime_files_checked"], 4)
        self.assertFalse(report["physical_iphone_tested"])
        with zipfile.ZipFile(first) as archive:
            self.assertEqual({name:archive.read(name) for name in archive.namelist()},
                             {"ios-pwa/"+name:data for name,data in self.runtime.items()})

    def test_pwa_private_file_symlink_and_same_size_tamper_are_refused(self):
        path = self.folder / "pwa.zip"; report = mobile.package_pwa(path, self.root)
        expected = report["source_sha256"]
        with zipfile.ZipFile(path, "a") as archive: archive.writestr("secret.key", b"private")
        with self.assertRaises(RuntimeError): mobile.verify_pwa(path, expected)
        private = self.root / "ios-pwa/private.key"; private.write_bytes(b"private")
        inventory = self.root / "ios-pwa/public-assets.json"
        inventory.write_text(json.dumps([*self.runtime, "private.key"]))
        with self.assertRaises(RuntimeError): mobile.pwa_inventory(self.root)
        inventory.write_text(json.dumps(list(self.runtime)))
        entry = self.root / "ios-pwa/index.html"; entry.unlink(); entry.symlink_to(private)
        with self.assertRaises(ValueError): mobile.pwa_inventory(self.root)

    def test_complete_uploaded_set_accepts_reordering(self):
        self.prepare_outputs(); self.release["assets"].reverse(); self.check_upload()

    def test_uploaded_missing_extra_duplicate_digest_size_or_state_is_rejected(self):
        self.prepare_outputs()
        cases = []
        for key,value in (("digest", "sha256:"+"0"*64),("size",1),("state","new")):
            r = copy.deepcopy(self.release); r["assets"][0][key]=value; cases.append(r)
        for assets in (self.release["assets"][:-1],self.release["assets"]*2,
                       [*self.release["assets"],{"name":"private.p12","size":10,"state":"uploaded"}]):
            r=copy.deepcopy(self.release);r["assets"]=assets;cases.append(r)
        for r in cases:
            with self.subTest(assets=r["assets"]), self.assertRaises(RuntimeError):self.check_upload(r)

    def test_upload_revision_and_publication_state_are_exact(self):
        self.prepare_outputs()
        for key,value in (("tag_name","v6.3.0"),("target_commitish","main"),("draft",False),("prerelease",True)):
            r=copy.deepcopy(self.release);r[key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):self.check_upload(r)
        r={**self.release,"draft":False,"html_url":"https://github.com/perabru/AliancaDX-AcessoRemoto/releases/tag/v6.3.1"}
        self.check_upload(r,published=True)

    def test_local_output_changes_cannot_be_hidden_by_updating_uploaded_digest(self):
        self.prepare_outputs()
        path=self.output/mobile.APK_NAME;path.write_bytes(b"X"+self.apk[1:]);self.refresh_assets()
        with self.assertRaises(RuntimeError):self.check_upload()

    def test_checksum_list_and_output_provenance_must_match(self):
        self.prepare_outputs()
        sums=self.output/"SHA256SUMS.txt";original=sums.read_text();sums.write_text("incorrect checksums\n");self.refresh_assets()
        with self.assertRaisesRegex(RuntimeError,"checksum"):self.check_upload()
        sums.write_text(original)
        path=self.output/mobile.REPORT_NAME;r=json.loads(path.read_text());r["source_dirty"]=True;path.write_text(json.dumps(r));self.refresh_assets()
        with self.assertRaisesRegex(RuntimeError,"provenance"):self.check_upload()

    def test_extra_or_linked_output_is_never_accepted_for_publication(self):
        self.prepare_outputs();extra=self.output/"signing-password.txt";extra.write_text("private")
        with self.assertRaisesRegex(RuntimeError,"outputs"):mobile.directory_assets(self.output)
        extra.unlink();sums=self.output/"SHA256SUMS.txt";sums.unlink();sums.symlink_to(self.root/"docs/APK_ANDROID_6_3_1.json")
        with self.assertRaises(RuntimeError):mobile.directory_assets(self.output)


if __name__ == "__main__":unittest.main()
