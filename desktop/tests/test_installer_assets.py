"""The offline network release must match its pinned source and carry its licenses."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "installer_assets_under_test", Path(__file__).resolve().parents[1] / "installer/build_installers.py")
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class OfflineNetworkAssetsTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project"
        self.assets = self.root / "assets"
        (self.project / "installer").mkdir(parents=True)
        self.bundle = self.assets / "easytier_bundle"
        self.bundle.mkdir(parents=True)

        def entry(name: str, data: bytes) -> dict:
            (self.bundle / name).write_bytes(data)
            return {"filename": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

        self.manifest = {"schema": 1, "package": {"type": "minianydesk-easytier-userspace",
                         "architecture": "amd64", "version": "2.6.4"},
                         "files": [entry(name, (name + " test content").encode()) for name in
                                   ("easytier-core.exe", "Packet.dll", "NOTICE.txt", "LICENSE.txt",
                                    "THIRD_PARTY_LICENSES.txt", "PROVENANCE.json")]}
        self.write_manifest()

    def write_manifest(self) -> None:
        encoded = json.dumps(self.manifest, indent=2).encode()
        (self.project / "installer/easytier_bundle_manifest.json").write_bytes(encoded)
        (self.bundle / "manifest.json").write_bytes(encoded)

    def validate(self) -> dict:
        return builder.validate_easytier_bundle(self.assets, self.project)

    def test_pinned_helper_and_all_notices_pass(self) -> None:
        hashes = self.validate()
        self.assertEqual(len(hashes), 7)
        self.assertIn("easytier_bundle/easytier-core.exe", hashes)

    def test_changed_helper_with_same_length_is_rejected(self) -> None:
        target = self.bundle / "easytier-core.exe"
        data = target.read_bytes()
        target.write_bytes(data[:-1] + b"X")
        with self.assertRaisesRegex(RuntimeError, "integrity"):
            self.validate()

    def test_replaced_bundle_manifest_cannot_repin_helper(self) -> None:
        altered = dict(self.manifest, provider="other")
        (self.bundle / "manifest.json").write_text(json.dumps(altered))
        with self.assertRaisesRegex(RuntimeError, "manifest differs"):
            self.validate()

    def test_unlisted_file_cannot_enter_installers(self) -> None:
        (self.bundle / "unexpected.exe").write_bytes(b"unlisted")
        with self.assertRaisesRegex(RuntimeError, "Unlisted"):
            self.validate()

    def test_source_manifest_cannot_escape_bundle_directory(self) -> None:
        self.manifest["files"][0]["filename"] = "../easytier-core.exe"
        self.write_manifest()
        with self.assertRaisesRegex(RuntimeError, "Invalid"):
            self.validate()

    def test_missing_licensed_material_is_rejected_even_if_repinning(self) -> None:
        self.manifest["files"] = [item for item in self.manifest["files"]
                                                 if item["filename"] != "LICENSE.txt"]
        (self.bundle / "LICENSE.txt").unlink()
        self.write_manifest()
        with self.assertRaisesRegex(RuntimeError, "licenses"):
            self.validate()

    def test_boolean_size_is_not_an_asset_length(self) -> None:
        self.manifest["files"][0]["size"] = True
        self.write_manifest()
        with self.assertRaisesRegex(RuntimeError, "Invalid"):
            self.validate()


if __name__ == "__main__":
    unittest.main()
