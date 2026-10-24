"""Installer rollback and cancellation checks using temporary folders only."""
from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch


INSTALLER_PATH = Path(__file__).resolve().parents[1] / "installer_python.py"
SPEC = importlib.util.spec_from_file_location("installer_under_test", INSTALLER_PATH)
assert SPEC is not None and SPEC.loader is not None
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallerTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.source = self.folder / "source"
        self.target = self.folder / "installation"
        self.source.mkdir()
        self.target.mkdir()
        self.bundled_network()
        for name in installer.PAYLOAD:
            content = b"# replacement source\n" if name.endswith(".py") else b"replacement requirement\n"
            (self.source / name).parent.mkdir(parents=True, exist_ok=True)
            (self.source / name).write_bytes(content)
        browser_names = [name.removeprefix("ios-pwa/") for name in installer.PAYLOAD if name.startswith("ios-pwa/")]
        # The exact browser manifest is part of the transaction, so fixture
        # assets must satisfy the same integrity checks as a shipped package.
        for name in ("index.html", "bridge.html", "manifest.webmanifest", "sw.js"):
            if name not in browser_names:
                browser_names.append(name)
                path = self.source / "ios-pwa" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture browser asset\n")
        manifest = {"schema": 1, "version": "7.0.0", "files": [
            {"path": name, "bytes": (self.source / "ios-pwa" / name).stat().st_size,
             "sha256": hashlib.sha256((self.source / "ios-pwa" / name).read_bytes()).hexdigest()}
            for name in sorted(browser_names)]}
        (self.source / "ios_assets_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        # Construct the worker without creating a Tk root or inspecting the
        # current user's Windows installation directories.
        self.subject = installer.Installer.__new__(installer.Installer)
        self.subject.from_nsis = True
        self.subject.uninstall_mode = False
        self.subject.events = queue.Queue()
        self.subject.cancelled = threading.Event()
        self.subject.process = None
        self.subject.busy = True
        self.subject.committing = False
        self.subject.closing = False
        self.subject.result = 2
        self.subject.root = MagicMock()
        self.subject.status = MagicMock()
        self.subject.primary = MagicMock()
        self.subject.secondary = MagicMock()
        self.messages: list[str] = []
        self.subject.write = self.messages.append
        self.command_calls: list[list[str]] = []
        self.private = self.target / ".venv"
        self.previous_files: dict[str, bytes] = {}

    def previous_installation(self, valid_environment: bool = True) -> None:
        for name in installer.PAYLOAD:
            content = b"# previous source\n" if name.endswith(".py") else b"previous requirement\n"
            self.previous_files[name] = content
            (self.target / name).parent.mkdir(parents=True, exist_ok=True)
            (self.target / name).write_bytes(content)
        marker = json.dumps({"application": "MiniAnyDeskNative", "role": "host", "version": "previous"}).encode()
        self.previous_files[".minianydesk-app.json"] = marker
        (self.target / ".minianydesk-app.json").write_bytes(marker)
        self.private.mkdir()
        (self.private / "previous-installation").write_bytes(b"retain this environment")
        if valid_environment:
            (self.private / "pyvenv.cfg").write_bytes(b"home = C:/PreviousPython\n")

    def command_success(self, arguments: list[str], cwd: Path) -> None:
        self.assertEqual(cwd, self.target)
        self.command_calls.append(arguments)
        if "-m" in arguments and "venv" in arguments:
            self.private.mkdir()
            (self.private / "pyvenv.cfg").write_bytes(b"home = C:/ReplacementPython\n")
            (self.private / "replacement-installation").write_bytes(b"replacement")

    def run_worker(self) -> list[tuple[str, str]]:
        with patch.object(installer, "SOURCE", self.source), \
                patch.object(installer, "check_platform", return_value=None), \
                patch.object(installer, "install_path", return_value=self.target):
            self.subject.worker("host")
        events = []
        while not self.subject.events.empty():
            events.append(self.subject.events.get_nowait())
        return events

    def assert_previous_files_restored(self) -> None:
        for name, expected in self.previous_files.items():
            self.assertEqual((self.target / name).read_bytes(), expected, name)
        self.assertEqual((self.private / "previous-installation").read_bytes(), b"retain this environment")
        self.assertFalse(list(self.target.glob(".venv-backup-*")))

    def test_changed_browser_asset_is_rejected_before_installation_mutates_files(self) -> None:
        self.previous_installation()
        (self.source / "ios-pwa/index.html").write_bytes(b"unexpected browser replacement")
        self.subject.run_command = MagicMock()
        events = self.run_worker()
        self.assertEqual(events[-1][0], "error")
        self.assertIn("iPhone ausente ou alterado", events[-1][1])
        self.subject.run_command.assert_not_called()
        self.assert_previous_files_restored()

    def test_fresh_failure_removes_new_brand_folders_without_touching_other_assets(self) -> None:
        (self.target / "assets").mkdir()
        unrelated = self.target / "assets/customer.txt"
        unrelated.write_bytes(b"user-owned asset")

        def fail(arguments, cwd):
            self.command_success(arguments, cwd)
            self.assertTrue((self.target / "assets/brand/aliancadx.ico").is_file())
            raise RuntimeError("Injected brand upgrade failure")

        self.subject.run_command = fail
        self.assertEqual(self.run_worker()[-1], ("error", "Injected brand upgrade failure"))
        self.assertFalse((self.target / "assets/brand").exists())
        self.assertEqual(unrelated.read_bytes(), b"user-owned asset")

    def test_brand_parent_symlink_is_rejected_without_writing_outside_target(self) -> None:
        external = self.folder / "external-brand"
        external.mkdir()
        (self.target / "assets").symlink_to(external, target_is_directory=True)
        self.subject.run_command = MagicMock()
        events = self.run_worker()
        self.assertEqual(events[-1][0], "error")
        self.assertIn("link", events[-1][1])
        self.assertEqual(list(external.iterdir()), [])
        self.subject.run_command.assert_not_called()

    def test_shortcut_migration_preserves_other_role_and_custom_links(self) -> None:
        desktop = self.folder / "Desktop"
        programs = self.folder / "Programs"
        legacy = programs / "MiniAnyDesk"
        current = programs / installer.DISPLAY_NAME
        for directory in (desktop, legacy, current):
            directory.mkdir(parents=True, exist_ok=True)
        old = [desktop / "MiniAnyDesk Host.lnk", programs / "MiniAnyDesk Host.lnk",
               legacy / "MiniAnyDesk Host.lnk", legacy / "Desinstalar Host.lnk"]
        preserved = [legacy / "MiniAnyDesk Controlador.lnk", legacy / "Meu atalho.lnk",
                     current / f"{installer.DISPLAY_NAME} Host.lnk"]
        for link in (*old, *preserved):
            link.write_bytes(b"shortcut fixture")
        installer.cleanup_shortcuts("host", desktop, programs, legacy_only=True)
        self.assertTrue(all(not link.exists() for link in old))
        self.assertTrue(all(link.is_file() for link in preserved))
        installer.cleanup_shortcuts("host", desktop, programs)
        self.assertFalse(preserved[-1].exists())
        self.assertTrue(all(link.is_file() for link in preserved[:-1]))

    def test_upgrade_failure_restores_previous_installation(self) -> None:
        self.previous_installation()

        def fail_after_environment_creation(arguments: list[str], cwd: Path) -> None:
            self.command_success(arguments, cwd)
            raise RuntimeError("Injected installation failure")

        self.subject.run_command = fail_after_environment_creation
        events = self.run_worker()

        self.assertEqual(events[-1], ("error", "Injected installation failure"))
        self.assert_previous_files_restored()
        self.assertEqual((self.private / "pyvenv.cfg").read_bytes(), b"home = C:/PreviousPython\n")
        self.assertFalse((self.private / "replacement-installation").exists())

    def test_old_30_upgrade_failure_removes_new_modules(self) -> None:
        self.previous_installation()
        added_modules = ("computer_book.py", "presence.py", "desktop_ui.py")
        for name in added_modules:
            self.assertIn(name, installer.PAYLOAD)
            (self.target / name).unlink()
            self.previous_files.pop(name)

        def fail_after_environment_creation(arguments: list[str], cwd: Path) -> None:
            self.command_success(arguments, cwd)
            for name in added_modules:
                self.assertTrue((self.target / name).is_file())
            raise RuntimeError("Injected failure upgrading the 3.0 payload")

        self.subject.run_command = fail_after_environment_creation
        events = self.run_worker()

        self.assertEqual(events[-1], ("error", "Injected failure upgrading the 3.0 payload"))
        self.assert_previous_files_restored()
        for name in added_modules:
            self.assertFalse((self.target / name).exists(), name)

    def test_old_31_upgrade_failure_removes_new_desktop_theme(self) -> None:
        self.previous_installation()
        new_module = "desktop_ui.py"
        self.assertIn(new_module, installer.PAYLOAD)
        (self.target / new_module).unlink()
        self.previous_files.pop(new_module)

        def fail_after_dependency_installation(arguments: list[str], cwd: Path) -> None:
            self.command_success(arguments, cwd)
            if "install" in arguments:
                self.assertTrue((self.target / new_module).is_file())
                raise RuntimeError("Injected failure upgrading the 3.1 payload")

        self.subject.run_command = fail_after_dependency_installation
        events = self.run_worker()

        self.assertEqual(events[-1], ("error", "Injected failure upgrading the 3.1 payload"))
        self.assert_previous_files_restored()
        self.assertFalse((self.target / new_module).exists())
        self.assertFalse((self.private / "replacement-installation").exists())

    def test_old_322_upgrade_failure_removes_new_network_module(self) -> None:
        self.previous_installation()
        new_module = "network_addresses.py"
        self.assertIn(new_module, installer.PAYLOAD)
        (self.target / new_module).unlink()
        self.previous_files.pop(new_module)

        def fail_after_dependency_installation(arguments: list[str], cwd: Path) -> None:
            self.command_success(arguments, cwd)
            if "install" in arguments:
                self.assertTrue((self.target / new_module).is_file())
                raise RuntimeError("Injected failure upgrading the 3.2.2 payload")

        self.subject.run_command = fail_after_dependency_installation
        events = self.run_worker()

        self.assertEqual(events[-1], ("error", "Injected failure upgrading the 3.2.2 payload"))
        self.assert_previous_files_restored()
        self.assertFalse((self.target / new_module).exists())
        self.assertFalse((self.private / "replacement-installation").exists())

    def test_old_51_upgrade_failure_removes_new_network_modules(self) -> None:
        self.previous_installation()
        added_modules = ("mesh.py", "mesh_ui.py")
        for name in added_modules:
            self.assertIn(name, installer.PAYLOAD)
            (self.target / name).unlink()
            self.previous_files.pop(name)

        def fail_after_dependency_installation(arguments: list[str], cwd: Path) -> None:
            self.command_success(arguments, cwd)
            if "install" in arguments:
                for name in added_modules:
                    self.assertTrue((self.target / name).is_file())
                raise RuntimeError("Injected failure upgrading the 3.2.3 payload")

        self.subject.run_command = fail_after_dependency_installation
        events = self.run_worker()

        self.assertEqual(events[-1], ("error", "Injected failure upgrading the 3.2.3 payload"))
        self.assert_previous_files_restored()
        for name in added_modules:
            self.assertFalse((self.target / name).exists(), name)
        self.assertFalse((self.private / "replacement-installation").exists())

    def bundled_network(self) -> None:
        folder = self.source / "easytier_bundle"
        folder.mkdir(exist_ok=True)
        files = []
        for name in ("easytier-core.exe", "Packet.dll", "LICENSE.txt", "NOTICE.txt",
                     "THIRD_PARTY_LICENSES.txt", "PROVENANCE.json"):
            data = ("replacement:" + name).encode()
            (folder / name).write_bytes(data)
            files.append({"filename": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        manifest = {"schema": 1, "package": {"type": "minianydesk-easytier-userspace",
                    "version": "2.6.4", "architecture": "amd64"}, "files": files}
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_network_bundle_is_copied_and_validated_without_external_setup(self) -> None:
        (self.source / "wheelhouse").mkdir()
        self.subject.run_command = self.command_success
        with patch.object(installer.subprocess, "Popen") as external_process:
            events = self.run_worker()
        external_process.assert_not_called()
        self.assertEqual(events[-1][0], "done")
        self.assertIn("sem login", events[-1][1])
        for filename in installer.bundle_files(self.source / "easytier_bundle"):
            self.assertEqual((self.target / "easytier_bundle" / filename).read_bytes(),
                             (self.source / "easytier_bundle" / filename).read_bytes())
        for argv in self.command_calls:
            if "pip" in argv:
                self.assertIn("--no-index", argv)
                self.assertNotIn("--index-url", argv)

    def test_corrupt_network_runtime_is_rejected_before_replacing_installation(self) -> None:
        self.previous_installation()
        (self.source / "easytier_bundle/easytier-core.exe").write_bytes(b"modified binary")
        self.subject.run_command = MagicMock()
        events = self.run_worker()
        self.assertEqual(events[-1][0], "error")
        self.assertIn("alterado", events[-1][1])
        self.subject.run_command.assert_not_called()
        self.assert_previous_files_restored()

    def test_old_payload_and_network_bundle_are_restored_after_failed_upgrade(self) -> None:
        self.previous_installation()
        old_bundle = self.target / "easytier_bundle"
        old_bundle.mkdir()
        for name in installer.bundle_files(self.source / "easytier_bundle"):
            data = ("old bundle:" + name).encode()
            self.previous_files["easytier_bundle/" + name] = data
            (old_bundle / name).write_bytes(data)

        def fail(arguments, cwd):
            self.command_success(arguments, cwd)
            raise RuntimeError("Injected offline installation failure")

        self.subject.run_command = fail
        events = self.run_worker()
        self.assertEqual(events[-1][0], "error")
        self.assert_previous_files_restored()

    def test_cancellation_removes_new_bundle_but_preserves_old_shared_vpn(self) -> None:
        shared_vpn = self.folder / "Program Files" / "MiniAnyDeskVPN" / "tailscaled.exe"
        shared_vpn.parent.mkdir(parents=True)
        shared_vpn.write_bytes(b"existing shared service")

        def cancel_after_copy(arguments, cwd):
            self.command_success(arguments, cwd)
            self.subject.cancelled.set()
            raise RuntimeError("Instalacao cancelada.")

        self.subject.run_command = cancel_after_copy
        events = self.run_worker()
        self.assertEqual(events[-1], ("error", "Instalacao cancelada."))
        self.assertFalse((self.target / "easytier_bundle").exists())
        self.assertFalse((self.target / "mesh.py").exists())
        self.assertEqual(shared_vpn.read_bytes(), b"existing shared service")

    def test_cancel_waits_for_worker_rollback_without_destroying_ui(self) -> None:
        with patch.object(installer.messagebox, "askyesno", return_value=True):
            self.subject.close()
        self.assertTrue(self.subject.cancelled.is_set())
        self.subject.root.destroy.assert_not_called()
        self.assertIn("restaurando", self.subject.status.set.call_args.args[0])

    def test_uninstall_preserves_external_registry_settings_and_certificates(self) -> None:
        self.previous_installation()
        preserved = {}
        for relative in ("MiniAnyDesk/computers.json", "MiniAnyDesk/control.json",
                         "MiniAnyDesk/mesh-controller.json",
                         "MiniAnyDeskNativo/Host/host-settings.json",
                         "MiniAnyDeskNativo/Host/mesh-host.json",
                         "MiniAnyDeskNativo/Host/host-key.pem",
                         "MiniAnyDeskNativo/Host/host-cert.pem"):
            path = self.folder / "localappdata" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            content = ("retain:" + relative).encode()
            path.write_bytes(content)
            preserved[path] = content
        additional = self.target / "arquivo-do-usuario.txt"
        additional.write_bytes(b"user file in application directory")
        shared_vpn = self.folder / "Program Files" / "MiniAnyDeskVPN" / "tailscaled.exe"
        shared_vpn.parent.mkdir(parents=True)
        shared_vpn.write_bytes(b"shared VPN service, must remain")
        bundled_vpn = self.target / "easytier_bundle"
        bundled_vpn.mkdir()
        (bundled_vpn / "easytier-core.exe").write_bytes(b"bundled installer only")
        shortcut_folders = {name: self.folder / name for name in ("Desktop", "Programs")}
        for directory in shortcut_folders.values():
            directory.mkdir()
            (directory / "MiniAnyDesk Host.lnk").write_bytes(b"owned shortcut")
        registry = MagicMock()
        registry.QueryValueEx.side_effect = lambda key, value: (str(shortcut_folders[value]), 1)

        with patch.dict("sys.modules", {"winreg": registry}):
            removed = self.subject.uninstall("host", self.target)

        self.assertTrue(removed)
        self.assertFalse(self.private.exists())
        self.assertFalse(bundled_vpn.exists())
        self.assertEqual(shared_vpn.read_bytes(), b"shared VPN service, must remain")
        for name in (*installer.PAYLOAD, ".minianydesk-app.json"):
            self.assertFalse((self.target / name).exists(), name)
        for path, expected in preserved.items():
            self.assertEqual(path.read_bytes(), expected)
        self.assertEqual(additional.read_bytes(), b"user file in application directory")
        for directory in shortcut_folders.values():
            self.assertFalse((directory / "MiniAnyDesk Host.lnk").exists())
        registry.DeleteKey.assert_called_once_with(registry.HKEY_CURRENT_USER, installer.app_registry("host"))

    def test_pre_cancel_preserves_installation_without_running_commands(self) -> None:
        self.previous_installation()
        self.subject.cancelled.set()
        command = MagicMock(side_effect=AssertionError("Cancelled worker ran a command"))
        self.subject.run_command = command

        events = self.run_worker()

        self.assertEqual(events[-1], ("error", "Instalacao cancelada."))
        command.assert_not_called()
        self.assert_previous_files_restored()

    def test_fresh_environment_failure_is_cleaned_up_and_retry_succeeds(self) -> None:
        def fail_before_configuration(arguments: list[str], cwd: Path) -> None:
            self.assertEqual(cwd, self.target)
            self.command_calls.append(arguments)
            self.private.mkdir()
            (self.private / "incomplete-environment").write_bytes(b"incomplete")
            raise RuntimeError("Injected failure before pyvenv.cfg")

        self.subject.run_command = fail_before_configuration
        first_events = self.run_worker()

        self.assertEqual(first_events[-1], ("error", "Injected failure before pyvenv.cfg"))
        self.assertFalse(self.private.exists())
        self.assertFalse((self.target / ".minianydesk-app.json").exists())
        for name in installer.PAYLOAD:
            self.assertFalse((self.target / name).exists(), name)

        self.subject.run_command = self.command_success
        retry_events = self.run_worker()

        self.assertEqual(retry_events[-1][0], "done")
        self.assertTrue((self.private / "pyvenv.cfg").is_file())
        self.assertFalse((self.private / "incomplete-environment").exists())
        marker = json.loads((self.target / ".minianydesk-app.json").read_text())
        self.assertEqual(marker["version"], installer.VERSION)
        self.assertEqual(marker["role"], "host")

    def test_preexisting_invalid_environment_is_preserved(self) -> None:
        self.previous_installation(valid_environment=False)
        command = MagicMock(side_effect=AssertionError("Invalid existing environment was replaced"))
        self.subject.run_command = command

        events = self.run_worker()

        self.assertEqual(events[-1][0], "error")
        self.assertIn(".venv existente", events[-1][1])
        command.assert_not_called()
        self.assert_previous_files_restored()
        self.assertFalse((self.private / "pyvenv.cfg").exists())

    def test_commit_phase_window_close_does_not_cancel_or_destroy_ui(self) -> None:
        self.previous_installation()
        self.subject.from_nsis = False
        self.subject.run_command = self.command_success

        def register_shortcuts(role: str, target: Path) -> None:
            self.assertEqual((role, target), ("host", self.target))
            self.assertTrue(self.subject.committing)
            with patch.object(installer.messagebox, "showinfo") as info, \
                    patch.object(installer.messagebox, "askyesno") as confirmation:
                self.subject.close()
                info.assert_called_once()
                confirmation.assert_not_called()
            self.assertFalse(self.subject.cancelled.is_set())
            self.subject.root.destroy.assert_not_called()

        self.subject.shortcuts_and_registry = register_shortcuts
        events = self.run_worker()

        self.assertIn(("committing", ""), events)
        self.assertEqual(events[-1][0], "done")
        self.assertFalse(self.subject.cancelled.is_set())
        self.assertFalse(list(self.target.glob(".venv-backup-*")))

    def test_log_open_failure_does_not_spawn_subprocess(self) -> None:
        # A directory at the log path reliably fails open() on Windows and
        # Linux, without depending on platform-specific permission semantics.
        (self.target / "instalacao.log").mkdir()
        with patch.object(installer.subprocess, "Popen") as popen:
            with self.assertRaises(OSError):
                self.subject.run_command(["unused-command"], self.target)
            popen.assert_not_called()
        self.assertIsNone(self.subject.process)


if __name__ == "__main__":
    unittest.main()
