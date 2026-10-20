"""Recovery regressions: consent boundaries, cancellation and bounded RAM state."""
from pathlib import Path
import socket
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import controller
from controller import Controller
from test_controller_stability import app_stub, Peer, Value


class Reason(str):
    def __new__(cls, text, retryable):
        result = super().__new__(cls, text)
        result.retryable = retryable
        return result


class ImmediateThread:
    def __init__(self, target, **kwargs): self.target = target
    def start(self): self.target()


def network_app():
    app = app_stub()
    app.address_var = Value("office.example")
    app.port_var = Value("64321")
    app.name_var = Value("Controlador de teste")
    app.network_hint_var = Value()
    app.auto_reconnect_var = Value(True)
    app.retry_credentials = ("office.example", 64321, "00123456", "Controlador de teste")
    app.retry_job = app.retry_expiry_job = app.retry_deadline = app.retry_token = None
    app.retry_attempt = 0
    app.retry_attempting = False
    app.connect_cancel = None
    app.password_endpoint = app._credential_endpoint()
    app.settings = SimpleNamespace(data={"pins": {}}, save=Mock())
    app._begin_connection = Mock()
    return app


def job(app, identity):
    return next(item for item in app.root.jobs if item[0] == identity)


class ControllerNetworkRecoveryTests(unittest.TestCase):
    def test_known_transport_failure_discards_old_session_and_schedules_one_retry(self):
        app = network_app()
        old_peer = app.peer
        app.transfers = SimpleNamespace(close=Mock())
        old_transfers = app.transfers
        app.outgoing.put(app.generation, old_peer, {"type": "input", "action": "release"})
        with patch.object(controller.time, "monotonic", return_value=100.0):
            app._connection_closed(Reason("socket reset", True))
            first_job = app.retry_job
            self.assertTrue(app._schedule_reconnect("same notification"))
        self.assertTrue(old_peer.closed)
        old_transfers.close.assert_called_once()
        self.assertIsNone(app.outgoing.get(0))
        self.assertEqual(app.password_var.get(), "")
        self.assertEqual(app.retry_credentials[2], "00123456")
        self.assertEqual(job(app, first_job)[1], 2000)
        self.assertEqual(len(app.root.jobs), 2, "one deadline and one retry are sufficient")
        self.assertFalse(app.reconnect_available)
        with patch.object(controller.time, "monotonic", return_value=102.0):
            job(app, first_job)[2]()
        app._begin_connection.assert_called_once_with("office.example", 64321, "00123456", "Controlador de teste", automatic=True)

    def test_backoff_increases_and_deadline_never_moves_on_failed_retries(self):
        app = network_app()
        app.peer = None
        app.connecting = False
        for attempt, (now, expected) in enumerate(zip((100, 102, 106, 114, 129, 159), (2, 4, 8, 15, 30, 30))):
            with self.subTest(attempt=attempt), patch.object(controller.time, "monotonic", return_value=now):
                self.assertTrue(app._schedule_reconnect("rede temporariamente indisponível"))
                identity = app.retry_job
                self.assertEqual(job(app, identity)[1], expected * 1000)
                self.assertEqual(app.retry_deadline, 220.0)
                job(app, identity)[2]()
        self.assertEqual(app._begin_connection.call_count, 6)

    def test_expiration_cancels_handshake_invalidates_late_publish_and_erases_secret(self):
        app = network_app()
        with patch.object(controller.time, "monotonic", return_value=100.0):
            app._connection_closed(Reason("socket reset", True))
        expiry = job(app, app.retry_expiry_job)[2]
        app.connecting = app.retry_attempting = True
        app.connect_cancel = cancellation = threading.Event()
        generation = app.generation
        expiry()
        self.assertTrue(cancellation.is_set())
        self.assertIsNone(app.retry_credentials)
        self.assertIsNone(app.retry_job)
        self.assertIsNone(app.retry_expiry_job)
        self.assertEqual(app.password_var.get(), "")
        self.assertGreater(app.generation, generation)
        late_peer = Peer()
        app._publish_connection(late_peer, generation)
        self.assertTrue(late_peer.closed)
        self.assertIn("expirou", app.connection_var.get())

    def test_manual_cancel_erases_secret_and_old_retry_or_expiry_cannot_touch_new_session(self):
        app = network_app()
        with patch.object(controller.time, "monotonic", return_value=100.0):
            app._connection_closed(Reason("socket reset", True))
        old_retry = job(app, app.retry_job)[2]
        old_expiry = job(app, app.retry_expiry_job)[2]
        old_ids = (app.retry_job, app.retry_expiry_job)
        app.disconnect()
        self.assertIsNone(app.retry_credentials)
        self.assertTrue(all(identity in app.root.cancelled for identity in old_ids))
        app.retry_credentials = ("office.example", 64321, "88776655", "New user")
        with patch.object(controller.time, "monotonic", return_value=200.0):
            app._schedule_reconnect("socket reset")
        new_identity, new_credentials = app.retry_job, app.retry_credentials
        old_retry()
        old_expiry()
        self.assertEqual(app.retry_job, new_identity)
        self.assertEqual(app.retry_credentials, new_credentials)
        app._begin_connection.assert_not_called()

    def test_disabling_recovery_cancels_pending_attempt_and_forgets_credentials(self):
        app = network_app()
        with patch.object(controller.time, "monotonic", return_value=100.0):
            app._connection_closed(Reason("socket reset", True))
        app.auto_reconnect_var.set(False)
        app._auto_reconnect_changed()
        self.assertIsNone(app.retry_credentials)
        self.assertIsNone(app.retry_job)
        self.assertFalse(app.reconnect_available)
        self.assertEqual(app.password_var.get(), "")

    def test_success_cancels_recovery_timers_and_starts_empty_new_transfer_session(self):
        app = network_app()
        with patch.object(controller.time, "monotonic", return_value=100.0):
            app._connection_closed(Reason("socket reset", True))
        old_retry, old_expiry = app.retry_job, app.retry_expiry_job
        app.connecting = app.retry_attempting = True
        app.connecting_endpoint = ("office.example", 64321)
        app._remember_connected_computer = Mock()
        app.workspace = SimpleNamespace(select=Mock())
        app.remote_page = "session"
        new_peer = Peer()
        transfers = SimpleNamespace(close=Mock())
        with patch("transfer.FileTransfers", return_value=transfers) as factory:
            app._connected(new_peer)
        self.assertIs(app.peer, new_peer)
        self.assertIs(app.transfers, transfers)
        self.assertIs(factory.call_args.args[0], new_peer)
        self.assertIsNone(app.retry_job)
        self.assertIsNone(app.retry_expiry_job)
        self.assertIsNone(app.retry_deadline)
        self.assertEqual(app.retry_attempt, 0)
        self.assertTrue(all(identity in app.root.cancelled for identity in (old_retry, old_expiry)))
        self.assertEqual(app.retry_credentials[2], "00123456")
        self.assertEqual(app.password_var.get(), "")

    def test_closing_window_cancels_recovery_and_erases_credentials(self):
        app = network_app()
        with patch.object(controller.time, "monotonic", return_value=100.0):
            app._connection_closed(Reason("socket reset", True))
        retry = job(app, app.retry_job)[2]
        app.pump_job = app.presence_job = app.resize_job = None
        app.root.destroy = Mock()
        app.close()
        retry()
        self.assertTrue(app.stopping.is_set())
        self.assertIsNone(app.retry_credentials)
        self.assertIsNone(app.retry_job)
        self.assertEqual(app.password_var.get(), "")
        app.root.destroy.assert_called_once()
        app._begin_connection.assert_not_called()

    def test_changed_destination_cancels_recovery_and_does_not_migrate_saved_secret(self):
        app = network_app()
        with patch.object(controller.time, "monotonic", return_value=100.0):
            app._connection_closed(Reason("socket reset", True))
        callback = job(app, app.retry_job)[2]
        app.address_var.set("other.example")
        app._credential_endpoint_changed()
        callback()
        self.assertIsNone(app.retry_credentials)
        self.assertIsNone(app.retry_job)
        app._begin_connection.assert_not_called()

    def test_unapproved_auth_certificate_intentional_and_ambiguous_eof_never_retry(self):
        for reason in (Reason("Sessão encerrada pelo Hospedeiro", False),
                       "O outro dispositivo encerrou a conexão.", "Hospedeiro parou de receber conexões.",
                       "Senha incorreta", "Identidade do hospedeiro não aprovada."):
            with self.subTest(reason=reason):
                app = network_app()
                app._connection_closed(reason, retryable=False if reason == "Senha incorreta" else None)
                self.assertIsNone(app.retry_job)
                self.assertIsNone(app.retry_credentials)
                self.assertEqual(app.password_var.get(), "")

    def test_authentication_failure_in_retry_stops_and_initial_failure_never_auto_retries(self):
        for automatic, error in ((True, PermissionError("Acesso negado")),
                                 (False, ConnectionRefusedError("refused")),
                                 (True, InterruptedError("cancelled"))):
            with self.subTest(automatic=automatic, error=type(error).__name__):
                app = network_app()
                app.peer = None
                app.connecting = False
                app.retry_deadline = 250.0 if automatic else None
                del app._begin_connection
                with patch("net.connect", side_effect=error), patch.object(controller.threading, "Thread", ImmediateThread):
                    app._begin_connection(*app.retry_credentials, automatic=automatic)
                _, callback = app.messages.get_nowait()
                callback()
                self.assertIsNone(app.retry_job)
                self.assertIsNone(app.retry_credentials)

    def test_retry_worker_passes_its_cancellation_and_absolute_deadline_to_transport(self):
        app = network_app()
        app.peer = None
        app.retry_deadline = 250.0
        del app._begin_connection
        with patch("net.connect", side_effect=socket.timeout("timed out")) as connect, patch.object(controller.threading, "Thread", ImmediateThread):
            app._begin_connection(*app.retry_credentials, automatic=True)
        self.assertIs(connect.call_args.kwargs["cancel_event"], app.connect_cancel)
        self.assertEqual(connect.call_args.kwargs["deadline"], 250.0)
        with patch.object(controller.time, "monotonic", return_value=200.0):
            _, callback = app.messages.get_nowait()
            callback()
        self.assertIsNotNone(app.retry_job)

    def test_automatic_retry_accepts_only_original_pin_without_new_trust_prompt(self):
        for saved_pin, expected in (("a" * 64, True), ("b" * 64, False), (None, False)):
            with self.subTest(saved_pin=saved_pin):
                app = network_app()
                app.retry_attempting = True
                if saved_pin:
                    app.settings.data["pins"]["office.example:64321"] = saved_pin
                app._post = lambda callback, generation: callback()
                with patch.object(controller.messagebox, "askyesno") as ask, patch.object(controller.messagebox, "showwarning"):
                    self.assertEqual(app._trust("office.example", 64321, "a" * 64, app.generation), expected)
                ask.assert_not_called()

    def test_invalid_pasted_address_never_starts_transport_but_apipa_remains_allowed(self):
        app = network_app()
        app.peer = None
        app.address_var.set("169.254.223.179 / 192.168.15.100")
        with patch.object(controller.messagebox, "showerror") as error:
            app.connect()
        error.assert_called_once()
        app._begin_connection.assert_not_called()
        app.address_var.set("169.254.223.179")
        app.password_var.set("00123456")
        app._update_address_hint()
        self.assertIn("APIPA", app.network_hint_var.get())
        app.connect()
        app._begin_connection.assert_called_once_with("169.254.223.179", 64321, "00123456", "Controlador de teste")


if __name__ == "__main__":
    unittest.main()
