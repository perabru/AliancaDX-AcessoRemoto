"""Pairing identity, secret persistence and cancellation boundaries."""
from pathlib import Path
import queue
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import controller
from controller import Controller, Settings, pin_key
import mesh
from mesh_ui import MeshPanel, is_pairing_address, pairing_address
from test_controller import computers_app
from test_controller_network import ImmediateThread, network_app


class LoginlessIdentityTests(unittest.TestCase):
    def setUp(self):
        self.pairing = mesh.generate_pairing(port=64322, fingerprint="A" * 64)
        self.address = pairing_address(self.pairing)

    def trust_app(self):
        app = Controller.__new__(Controller)
        app.generation = 4
        app.stopping = threading.Event()
        app.settings = SimpleNamespace(data={"pins": {}}, save=Mock())
        app._post = lambda callback, generation: callback()
        app._trust = Mock(return_value=False)
        return app

    def test_code_certificate_checked_before_password_and_pinned_to_host_identity(self):
        app = self.trust_app()
        self.assertTrue(app._trust_pairing(self.address, 64322, "a" * 64, 4, self.pairing))
        self.assertEqual(app.settings.data["pins"], {f"{self.address}:64322": "a" * 64})
        app._trust.assert_not_called()

    def test_wrong_certificate_or_cancelled_generation_is_rejected(self):
        app = self.trust_app()
        self.assertFalse(app._trust_pairing(self.address, 64322, "b" * 64, 4, self.pairing))
        self.assertFalse(app._trust_pairing(self.address, 64322, "a" * 64, 3, self.pairing))
        self.assertFalse(app.settings.data["pins"])

    def test_existing_different_pin_requires_the_normal_explicit_identity_change_dialog(self):
        app = self.trust_app()
        app.settings.data["pins"][pin_key(self.address, 64322)] = "b" * 64
        self.assertFalse(app._trust_pairing(self.address, 64322, "a" * 64, 4, self.pairing))
        app._trust.assert_called_once_with(self.address, 64322, "a" * 64, 4)
        self.assertEqual(app.settings.data["pins"][pin_key(self.address, 64322)], "b" * 64)

    def test_settings_roundtrip_keeps_public_identity_without_pairing_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(Path(directory) / "control.json")
            settings.data.update(address=self.address, port=64322)
            settings.data["pins"][pin_key(self.address, 64322)] = "a" * 64
            settings.save()
            text = settings.path.read_text()
            self.assertNotIn(self.pairing.code, text)
            self.assertNotIn(self.pairing.network_secret, text)
            restored = Settings(settings.path)
            self.assertEqual(restored.data["address"], self.address)
            self.assertEqual(restored.data["pins"], settings.data["pins"])

    def test_virtual_destination_validation_is_strict(self):
        self.assertTrue(is_pairing_address(self.address))
        for address in ("127.0.0.1", self.address + ".evil", "mesh-../other.invalid", "mesh-A" + "a" * 23 + ".invalid"):
            self.assertFalse(is_pairing_address(address))

    def test_unprepared_saved_internet_host_never_causes_dns_probe_or_false_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            app = computers_app(directory)
            app.book.add("Casa", self.address, 64322)
            app.mesh_pairing = None
            app.mesh_panel = SimpleNamespace(manager=SimpleNamespace(snapshot=lambda: mesh.MeshStatus()))
            app._refresh_computers(force=True)
            self.assertTrue(app.presence_tasks.empty())
            result = next(iter(app.computer_presence.values()))
            self.assertEqual(result["state"], "unverified")

    def test_prepared_internet_presence_uses_tunnel_but_keeps_logical_record(self):
        with tempfile.TemporaryDirectory() as directory:
            app = computers_app(directory)
            record = app.book.add("Casa", self.address, 64322)
            app.mesh_pairing = self.pairing
            app.mesh_panel = SimpleNamespace(manager=SimpleNamespace(snapshot=lambda: mesh.MeshStatus(state="ready", endpoint=("127.0.0.1", 51483))))
            app._refresh_computers(force=True)
            token, address, port, pin = app.presence_tasks.get_nowait()
            self.assertEqual((address, port, pin), ("127.0.0.1", 51483, "A" * 64))
            self.assertEqual(token[2], pin_key(self.address, 64322))
            self.assertEqual(app.book.entries()[0]["address"], self.address)
            self.assertEqual(record["id"], token[0])

    def test_tunnel_connection_routes_existing_tls_to_localport_and_trusts_real_identity(self):
        app = network_app()
        app.peer = None
        app._begin_connection = Controller._begin_connection.__get__(app)
        app.settings.data.update(address=self.address, port=64322)
        app.mesh_pairing = self.pairing
        app.mesh_start_lock = threading.RLock()
        manager = SimpleNamespace(start=Mock(), wait_ready=Mock(return_value=("127.0.0.1", 51483)))
        app.mesh_panel = SimpleNamespace(manager=manager)
        peer = SimpleNamespace(closed=False, close=Mock())
        app._post = Mock()
        app._trust_pairing = Mock(return_value=True)
        with patch.object(controller.threading, "Thread", ImmediateThread), patch("net.connect", return_value=peer) as connect:
            app._begin_connection(self.address, 64322, "12345678", "Bruno")
        manager.start.assert_called_once_with(self.pairing)
        self.assertEqual(connect.call_args.args[:2], ("127.0.0.1", 51483))
        connect.call_args.kwargs["trust_callback"]("127.0.0.1", 51483, "A" * 64)
        app._trust_pairing.assert_called_once_with(self.address, 64322, "A" * 64, app.generation, self.pairing)
        self.assertEqual(app.settings.data["address"], self.address)
        self.assertEqual(app.connecting_endpoint, (self.address, 64322))

    def _automatic_mesh_failure(self, error):
        """Exercise the actual connection worker before it can send a password."""
        app = network_app()
        app.peer = None
        app._begin_connection = Controller._begin_connection.__get__(app)
        app.settings.data.update(address=self.address, port=64322)
        app.mesh_pairing = self.pairing
        app.mesh_start_lock = threading.RLock()
        manager = SimpleNamespace(start=Mock(), wait_ready=Mock(side_effect=error))
        app.mesh_panel = SimpleNamespace(manager=manager)
        app._connection_closed = Mock()
        app._post = lambda callback, generation: callback()
        with patch.object(controller.threading, "Thread", ImmediateThread), patch("net.connect") as connect:
            app._begin_connection(self.address, 64322, "12345678", "Bruno", automatic=True)
        manager.start.assert_called_once_with(self.pairing)
        connect.assert_not_called()
        app._connection_closed.assert_called_once()
        return app._connection_closed.call_args

    def test_automatic_mesh_route_timeout_can_retry_within_existing_recovery_window(self):
        result = self._automatic_mesh_failure(mesh.MeshTimeoutError("Host ainda não encontrado."))
        self.assertTrue(result.kwargs["retryable"])

    def test_automatic_mesh_certificate_failure_is_terminal_without_password_or_retry(self):
        result = self._automatic_mesh_failure(mesh.MeshError("O certificado do Host não corresponde ao código privado."))
        self.assertFalse(result.kwargs["retryable"])

    def test_remembered_code_cannot_escape_vault_or_substitute_another_host(self):
        panel = MeshPanel.__new__(MeshPanel)
        with tempfile.TemporaryDirectory() as directory:
            panel.config_dir = Path(directory)
            self.assertIsNone(panel.load_remembered("mesh-../other.invalid"))
            path = panel._vault_path(self.pairing)
            mesh.save_pairing_code(path, self.pairing.code)
            self.assertEqual(panel.load_remembered(self.address), self.pairing)
            other = mesh.generate_pairing(fingerprint="B" * 64)
            mesh.save_pairing_code(path, other.code)
            self.assertIsNone(panel.load_remembered(self.address))


if __name__ == "__main__":
    unittest.main()
