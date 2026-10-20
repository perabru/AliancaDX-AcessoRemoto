"""Public source cache recovery: pinned input, fail-closed extraction, no shell."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "public_asset_preparation", Path(__file__).resolve().parents[1] / "installer/prepare_assets.py")
assert SPEC is not None and SPEC.loader is not None
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


class PrepareAssetsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "public source"
        (self.project / "installer").mkdir(parents=True)
        self.output = self.root / "cache with spaces"
        self.installer = self.root / "release setup.exe"
        self.installer.write_bytes(b"unexecuted archive fixture")
        self.files = {"python.exe": b"pinned Python fixture", "wheelhouse/dependency.whl": b"pinned wheel fixture"}
        network_names = ("easytier-core.exe", "Packet.dll", "LICENSE.txt", "NOTICE.txt",
                         "THIRD_PARTY_LICENSES.txt", "PROVENANCE.json")
        self.files.update({"easytier_bundle/" + name: ("pinned:" + name).encode() for name in network_names})
        digest = lambda data: hashlib.sha256(data).hexdigest()
        runtime = {"python": {"file": "python.exe", "sha256": digest(self.files["python.exe"])},
                   "wheels": [{"file": "dependency.whl", "sha256": digest(self.files["wheelhouse/dependency.whl"])}]}
        network = {"schema": 1, "package": {"type": "minianydesk-easytier-userspace", "version": "2.6.4",
                                            "architecture": "amd64"},
                   "files": [{"filename": name, "size": len(self.files["easytier_bundle/" + name]),
                              "sha256": digest(self.files["easytier_bundle/" + name])} for name in network_names]}
        (self.project / "installer/assets_manifest.json").write_text(json.dumps(runtime))
        manifest_bytes = json.dumps(network).encode()
        (self.project / "installer/easytier_bundle_manifest.json").write_bytes(manifest_bytes)
        self.files["easytier_bundle/manifest.json"] = manifest_bytes
        self.release_manifest = self.root / "release.json"
        self.release_manifest.write_text(json.dumps({"artifacts": [{"file": "AliancaDX_Host_Setup.exe",
             "bytes": self.installer.stat().st_size, "sha256": digest(self.installer.read_bytes())}]}))

    def recover(self):
        return prepare.recover(self.installer, self.output, "C:/Tools with spaces/7z.exe",
                               project=self.project, release_manifest=self.release_manifest)

    def extract_fixture(self, arguments, **kwargs):
        self.assertIsInstance(arguments, list)
        self.assertNotIn("shell", kwargs)
        destination = Path(next(argument[2:] for argument in arguments if argument.startswith("-o")))
        for name, content in self.files.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    def test_space_paths_and_full_pinned_cache_are_preserved_without_shell(self):
        with patch.object(prepare.subprocess, "run", side_effect=self.extract_fixture) as runner:
            result = self.recover()
        runner.assert_called_once()
        self.assertEqual(result["verified_assets"], len(self.files))
        self.assertFalse(result["installer_executed"])
        self.assertFalse(result["network_used"])
        self.assertEqual(prepare.verify_cache(self.output, self.project), prepare.asset_entries(self.project))
        self.assertEqual(self.installer.read_bytes(), b"unexecuted archive fixture")

    def test_unknown_installer_fails_before_extraction(self):
        self.installer.write_bytes(b"changed untrusted archive")
        with patch.object(prepare.subprocess, "run") as runner, self.assertRaises(RuntimeError):
            self.recover()
        runner.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_corrupt_extracted_dependency_never_publishes_cache(self):
        def corrupted(arguments, **kwargs):
            self.extract_fixture(arguments, **kwargs)
            destination = Path(next(argument[2:] for argument in arguments if argument.startswith("-o")))
            (destination / "easytier_bundle/Packet.dll").write_bytes(b"changed")
        with patch.object(prepare.subprocess, "run", side_effect=corrupted), self.assertRaises(RuntimeError):
            self.recover()
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob("aliancadx-assets-*")))

    def test_existing_directory_is_never_overwritten(self):
        self.output.mkdir()
        existing = self.output / "customer.txt"
        existing.write_bytes(b"preserve")
        with patch.object(prepare.subprocess, "run") as runner, self.assertRaises(RuntimeError):
            self.recover()
        runner.assert_not_called()
        self.assertEqual(existing.read_bytes(), b"preserve")


if __name__ == "__main__":
    unittest.main()
