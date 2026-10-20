"""Credential lifecycle and effective-password regressions without a Tk display."""
from pathlib import Path
import queue
import string
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import host
import controller
import net


class Value:
    def __init__(self, value=None): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class Widget:
    def __init__(self): self.options = {}
    def configure(self, **options): self.options.update(options)
    def focus_set(self): pass


class ImmediateThread:
    def __init__(self, target, **kwargs): self.target = target
    def start(self): self.target()


def host_form(password="00123456"):
    app = host.HostApp.__new__(host.HostApp)
    app.server = None
    app.peer = None
    app.starting = False
    app.control_lock = threading.RLock()
    app.shutdown = threading.Event()
    app.state_dir = Path("unused-test-host-state")
    app.password = Value(password)
    app.show_password = Value(False)
    app.port = Value("64321")
    app.status = Value()
    app.fingerprint = Value("A" * 64)
    app.addresses = Value("192.0.2.10")
    app._address_candidates = ("192.0.2.10",)
    app.copy_notice = Value()
    app.root = Mock()
    app.log = Mock()
    app._presence_name = Mock(return_value="Computador de teste")
    app._connection_request = Mock()
    app._connected = Mock()
    app._disconnected = Mock()
    app.dispatch = lambda callback: callback()
    for name in ("start_button", "stop_button", "port_entry", "password_entry",
                 "new_password_button", "password_toggle_button"):
        setattr(app, name, Widget())
    return app


def controller_form():
    app = controller.Controller.__new__(controller.Controller)
    app.peer = None
    app.connecting = False
    app.password_var = Value("00123456")
    app.password_visible = False
    app.password_entry = Widget()
    app.password_toggle_button = Widget()
    app.address_var = Value("old.example")
    app.port_var = Value("64321")
    app.password_endpoint = app._credential_endpoint()
    return app


class TemporaryPasswordTests(unittest.TestCase):
    def test_generator_uses_secure_digit_choices_and_preserves_leading_zeroes(self):
        choices = iter("00123456")
        def choose(alphabet):
            self.assertEqual(alphabet, string.digits)
            return next(choices)
        with patch.object(host.secrets, "choice", side_effect=choose) as secure_choice:
            self.assertEqual(host.generate_session_password(), "00123456")
        self.assertEqual(secure_choice.call_count, 8)

    def test_new_password_masks_the_replacement(self):
        app = host_form("custom-password")
        app._set_password_visible(True)
        with patch.object(host, "generate_session_password", return_value="00001234"):
            app.new_password()
        self.assertEqual(app.password.get(), "00001234")
        self.assertFalse(app.show_password.get())
        self.assertEqual(app.password_entry.options["show"], "*")

    def test_password_cannot_rotate_during_start_or_active_listener(self):
        for server, starting in ((None, True), (object(), False)):
            with self.subTest(starting=starting):
                app = host_form()
                app.server, app.starting = server, starting
                with patch.object(host, "generate_session_password") as generate:
                    app.new_password()
                generate.assert_not_called()
                self.assertEqual(app.password.get(), "00123456")

    def test_host_toggle_changes_only_visibility_and_copy_preserves_exact_password(self):
        app = host_form("  00123456  ")
        app.server = object()
        app._toggle_password_visibility()
        self.assertTrue(app.show_password.get())
        self.assertEqual(app.password_entry.options["show"], "")
        app.copy_password()
        self.assertEqual(app.root.clipboard_append.call_args.args[0], "  00123456  ")
        app.root.reset_mock()
        app.copy_connection()
        copied = app.root.clipboard_append.call_args.args[0]
        self.assertIn("Senha:   00123456  \n", copied)
        self.assertIn("Porta TCP: 64321", copied)
        self.assertIn("Certificado SHA-256: " + "A" * 64, copied)
        self.assertNotIn("00123456", repr(app.log.call_args_list))
        app._toggle_password_visibility()
        self.assertFalse(app.show_password.get())
        self.assertEqual(app.password_entry.options["show"], "*")
        self.assertEqual(app.password.get(), "  00123456  ")

    def test_host_start_accepts_full_protocol_boundaries_without_stripping(self):
        for password in ("00123456", "x" * 256, "  00123456  "):
            with self.subTest(length=len(password)):
                app = host_form(password)
                server = Mock(fingerprint="A" * 64)
                server.start.return_value = 64321
                with patch.object(net, "HostServer", return_value=server) as factory, \
                     patch.object(host.threading, "Thread", ImmediateThread), \
                     patch.object(host.messagebox, "showerror") as errors:
                    app.start_listener()
                errors.assert_not_called()
                self.assertEqual(factory.call_args.args[2], password)
                self.assertIs(app.server, server)
                self.assertFalse(app.starting)
                self.assertEqual(app.password_entry.options["state"], "disabled")
                self.assertEqual(app.new_password_button.options["state"], "disabled")
                app.copy_connection()
                self.assertIn("Senha: " + password + "\n", app.root.clipboard_append.call_args.args[0])

    def test_host_invalid_password_does_not_start_or_lock_form(self):
        for password in ("", "1234567", "x" * 257, " " * 8, "\t\n" * 4):
            with self.subTest(password=repr(password)):
                app = host_form(password)
                with patch.object(net, "HostServer") as factory, \
                     patch.object(host.threading, "Thread") as worker, \
                     patch.object(host.messagebox, "showerror") as errors:
                    app.start_listener()
                factory.assert_not_called()
                worker.assert_not_called()
                errors.assert_called_once()
                self.assertFalse(app.starting)
                self.assertIsNone(app.server)
                self.assertNotEqual(app.password_entry.options.get("state"), "disabled")

    def test_stopping_host_conceals_password_and_keeps_listener_controls_editable(self):
        app = host_form()
        app._set_password_visible(True)
        app.generation = 0
        app.last_screen = None
        app.inputs = Mock()
        app.pending_consents = set()
        app._emit_state = Mock()
        app._sync_gui_state = Mock()
        app.stop_listener()
        self.assertFalse(app.show_password.get())
        self.assertEqual(app.password_entry.options["show"], "*")
        self.assertEqual(app.password_entry.options["state"], "normal")
        self.assertEqual(app.new_password_button.options["state"], "normal")
        self.assertEqual(app.password.get(), "00123456")


class ControllerCredentialTests(unittest.TestCase):
    def test_controller_toggle_never_changes_password(self):
        app = controller_form()
        app._toggle_password_visibility()
        self.assertTrue(app.password_visible)
        self.assertEqual(app.password_entry.options["show"], "")
        app._toggle_password_visibility()
        self.assertFalse(app.password_visible)
        self.assertEqual(app.password_entry.options["show"], "*")
        self.assertEqual(app.password_var.get(), "00123456")

    def test_switching_endpoint_clears_and_conceals_previous_password(self):
        app = controller_form()
        app._set_password_visible(True)
        app.address_var.set("other.example")
        app._credential_endpoint_changed()
        self.assertEqual(app.password_var.get(), "")
        self.assertFalse(app.password_visible)
        self.assertEqual(app.password_entry.options["show"], "*")

    def test_equivalent_endpoint_does_not_clear_current_password(self):
        app = controller_form()
        app.address_var.set("OLD.EXAMPLE")
        app._credential_endpoint_changed()
        self.assertEqual(app.password_var.get(), "00123456")

    def test_leaving_connect_page_conceals_local_password(self):
        app = controller_form()
        app._set_password_visible(True)
        app.workspace = SimpleNamespace(page_name=lambda page: page, show=Mock())
        app.fullscreen = False
        app._release_input = Mock()
        app._update_controls = Mock()
        app.page_buttons = {}
        app.selected_page = "connect"
        app._show_page("computers")
        self.assertFalse(app.password_visible)
        self.assertEqual(app.password_entry.options["show"], "*")
        self.assertEqual(app.password_var.get(), "00123456")

    def test_disconnect_clears_and_conceals_credentials(self):
        app = controller_form()
        app._set_password_visible(True)
        app.peer = None
        app.transfers = None
        app.generation = 0
        app.computer_presence = {}
        app.connected_endpoint = app.connecting_endpoint = None
        app._invalidate_presence_endpoint = Mock()
        app._clear_frame = Mock()
        app._placeholder = Mock()
        app._update_controls = Mock()
        app.fullscreen = False
        for name in ("connection_var", "screen_var", "file_var", "progress_var"):
            setattr(app, name, Value())
        app._connection_closed("Teste encerrado")
        self.assertEqual(app.password_var.get(), "")
        self.assertFalse(app.password_visible)
        self.assertEqual(app.password_entry.options["show"], "*")


class NumericPasswordTransportTests(unittest.TestCase):
    def test_eight_digit_password_keeps_certificate_check_and_local_consent(self):
        with tempfile.TemporaryDirectory() as folder:
            consent = Mock(return_value=True)
            connected = queue.Queue()
            server = net.HostServer("127.0.0.1", 0, "00123456", consent,
                lambda peer, *_args: connected.put(peer), lambda *_args: None, state_dir=folder)
            self.addCleanup(server.stop)
            server.start()
            with self.assertRaises(PermissionError):
                net.connect("127.0.0.1", server.port, "00123456", "QA", lambda msg: None,
                    lambda reason: None, lambda *_args: False)
            consent.assert_not_called()
            with self.assertRaises(PermissionError):
                net.connect("127.0.0.1", server.port, "12345678", "QA", lambda msg: None,
                    lambda reason: None, lambda *_args: True)
            consent.assert_not_called()
            peer = net.connect("127.0.0.1", server.port, "00123456", "QA", lambda msg: None,
                lambda reason: None, lambda _address, _port, pin: pin == server.fingerprint)
            self.addCleanup(peer.close)
            self.assertFalse(peer.closed)
            connected.get(timeout=2)
            consent.assert_called_once_with("QA", "127.0.0.1")

    def test_five_wrong_numeric_passwords_rate_limit_even_correct_password(self):
        with tempfile.TemporaryDirectory() as folder:
            consent = Mock(return_value=True)
            server = net.HostServer("127.0.0.1", 0, "00123456", consent,
                lambda *_args: None, lambda *_args: None, state_dir=folder)
            self.addCleanup(server.stop)
            server.start()
            for password in ["99999999"] * 5 + ["00123456"]:
                with self.subTest(password=password), self.assertRaises(PermissionError):
                    net.connect("127.0.0.1", server.port, password, "QA", lambda msg: None,
                        lambda reason: None, lambda *_args: True)
            consent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
