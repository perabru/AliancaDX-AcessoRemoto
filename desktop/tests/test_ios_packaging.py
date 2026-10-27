"""Credential handoff and exact browser-asset packaging for the iOS connector."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

DESKTOP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESKTOP))
import ios_assets
import ios_launcher

SPEC = importlib.util.spec_from_file_location("ios_packaging_builder", DESKTOP / "installer/build_installers.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class IOSLauncherTests(unittest.TestCase):
    def test_host_password_only_travels_over_stdin_not_argv_or_environment(self):
        process = Mock()
        bootstrap = {"invitation": "MDA6-private", "password": "private-password", "mode": "local"}
        with patch.object(Path, "is_file", return_value=True), patch.object(ios_launcher.subprocess, "Popen", return_value=process) as popen:
            self.assertIs(ios_launcher.launch_connector(bootstrap), process)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[-1], "--bootstrap-stdin")
        self.assertNotIn("private-password", repr(argv))
        self.assertNotIn("MDA6-private", repr(argv))
        self.assertNotIn("env", popen.call_args.kwargs)
        raw = process.stdin.write.call_args.args[0]
        self.assertEqual(json.loads(raw), bootstrap)
        self.assertTrue(raw.endswith(b"\n"))
        process.stdin.close.assert_called_once()

    def test_standalone_connector_has_no_bootstrap_pipe(self):
        with patch.object(Path, "is_file", return_value=True), patch.object(ios_launcher.subprocess, "Popen") as popen:
            ios_launcher.launch_connector()
        self.assertNotIn("--bootstrap-stdin", popen.call_args.args[0])
        self.assertEqual(popen.call_args.kwargs["stdin"], ios_launcher.subprocess.DEVNULL)

    def test_oversized_or_missing_connector_never_spawns(self):
        with patch.object(Path, "is_file", return_value=True), patch.object(ios_launcher.subprocess, "Popen") as popen:
            with self.assertRaises(ValueError):
                ios_launcher.launch_connector({"password": "x" * ios_launcher.MAX_BOOTSTRAP})
            popen.assert_not_called()
        with patch.object(Path, "is_file", return_value=False), patch.object(ios_launcher.subprocess, "Popen") as popen:
            with self.assertRaises(RuntimeError):
                ios_launcher.launch_connector()
            popen.assert_not_called()

    def test_failed_private_handoff_terminates_child_without_exposing_secret(self):
        process = Mock()
        process.stdin.write.side_effect = BrokenPipeError("secret-text")
        with patch.object(Path, "is_file", return_value=True), patch.object(ios_launcher.subprocess, "Popen", return_value=process):
            with self.assertRaises(RuntimeError) as raised:
                ios_launcher.launch_connector({"password": "secret-text"})
        self.assertNotIn("secret-text", str(raised.exception))
        process.terminate.assert_called_once()


class IOSDesktopIntegrationTests(unittest.TestCase):
    def test_host_bootstrap_pins_local_tls_and_preserves_the_password_in_memory(self):
        import host
        import mesh
        app = host.HostApp.__new__(host.HostApp)
        app.root = Mock()
        app.server = Mock(fingerprint="A" * 64)
        app.starting = False
        app.port = Mock(get=lambda: "64325")
        app.password = Mock(get=lambda: "only-local-password")
        app._presence_name = lambda: "Sala 12"
        app.copy_notice = Mock()
        with patch.object(ios_launcher, "launch_connector") as launch:
            app.open_ios_connector()
        bootstrap = launch.call_args.args[0]
        pairing = mesh.parse_pairing(bootstrap["invitation"])
        self.assertEqual((pairing.port, pairing.fingerprint), (64325, "A" * 64))
        self.assertEqual(bootstrap["mode"], "local")
        self.assertEqual(bootstrap["password"], "only-local-password")
        self.assertEqual(bootstrap["name"], "Sala 12")
        app.root.clipboard_append.assert_not_called()

    def test_controller_does_not_disconnect_its_desktop_peer_to_open_iphone(self):
        import controller
        app = controller.Controller.__new__(controller.Controller)
        app.root = Mock()
        app.peer = Mock()
        with patch.object(ios_launcher, "launch_connector") as launch:
            app._open_ios_connector()
        launch.assert_called_once_with()
        app.peer.close.assert_not_called()

    def test_repeated_open_preserves_the_existing_connector(self):
        import controller
        app = controller.Controller.__new__(controller.Controller)
        app.root = Mock()
        app._ios_connector_process = Mock(poll=lambda: None)
        with patch.object(ios_launcher, "launch_connector") as launch, patch.object(controller.messagebox, "showinfo"):
            app._open_ios_connector()
        launch.assert_not_called()
        app._ios_connector_process.terminate.assert_not_called()

    def test_stop_only_terminates_the_connector_owned_by_this_window(self):
        import controller
        first = controller.Controller.__new__(controller.Controller)
        other = controller.Controller.__new__(controller.Controller)
        first._ios_connector_process = Mock(poll=lambda: None)
        owned = first._ios_connector_process
        other._ios_connector_process = Mock(poll=lambda: None)
        first._stop_ios_connector()
        owned.terminate.assert_called_once()
        other._ios_connector_process.terminate.assert_not_called()
        self.assertIsNone(first._ios_connector_process)


class IOSBrowserAssetsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "desktop"
        self.project.mkdir()
        self.browser = self.root / "ios-pwa"
        self.browser.mkdir()
        self.entries = []
        for name in ("index.html", "bridge.html", "manifest.webmanifest", "sw.js", "vendor/LICENSE", "icons/app.png"):
            data = (name + " fixture").encode()
            path = self.browser / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.entries.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        self.write_manifest()

    def write_manifest(self):
        (self.project / "ios_assets_manifest.json").write_text(json.dumps({"schema": 1, "version": "7.1.0", "files": self.entries}))

    def test_public_source_and_installed_paths_are_both_verified(self):
        names = ios_assets.verify_browser_assets(self.project)
        self.assertIn("ios-pwa/vendor/LICENSE", names)
        self.browser.rename(self.project / "ios-pwa")
        self.assertEqual(ios_assets.verify_browser_assets(self.project), names)

    def test_same_length_browser_asset_change_is_rejected(self):
        path = self.browser / "index.html"
        data = path.read_bytes()
        path.write_bytes(data[:-1] + b"X")
        with self.assertRaises(RuntimeError):
            ios_assets.verify_browser_assets(self.project)

    def test_path_traversal_nsis_tokens_and_duplicate_names_are_rejected(self):
        original = self.entries[0]["path"]
        for name in ("../index.html", "/index.html", "a\\index.html", "a//b", "${INSTDIR}.html", 'a".html'):
            self.entries[0]["path"] = name
            self.write_manifest()
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                ios_assets.browser_assets(self.project)
        self.entries[0]["path"] = original
        self.entries.append(dict(self.entries[0]))
        self.write_manifest()
        with self.assertRaises(RuntimeError):
            ios_assets.browser_assets(self.project)

    def test_symlinked_asset_parent_is_rejected(self):
        other = self.root / "other"
        (self.browser / "icons").rename(other)
        (self.browser / "icons").symlink_to(other, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            ios_assets.verify_browser_assets(self.project)

    def test_nsis_has_exact_backup_restore_install_removal_and_preserves_unknown_files(self):
        include = builder.write_ios_include(self.project, self.root / "browser.nsh").read_text()
        for entry in self.entries:
            windows = "ios-pwa\\" + entry["path"].replace("/", "\\")
            self.assertIn('CopyFiles /SILENT "$INSTDIR\\' + windows + '"', include)
            self.assertIn('CopyFiles /SILENT "$BackupFolder\\' + windows + '"', include)
            self.assertIn('Delete "$INSTDIR\\' + windows + '"', include)
            self.assertIn(str(self.browser / entry["path"]), include)
        self.assertNotIn("RMDir /r", include)
        self.assertNotIn("*.*", include)
        self.assertNotIn("*.mjs", include)

    def test_both_installers_bundle_the_same_connector_and_browser_keys(self):
        import installer_python
        self.assertEqual(set(builder.PAYLOAD), set(installer_python.PAYLOAD))
        self.assertTrue({"ios_connector.py", "ios_launcher.py", "ios_assets.py", "ios_assets_manifest.json"}.issubset(builder.PAYLOAD))


if __name__ == "__main__":
    unittest.main()
