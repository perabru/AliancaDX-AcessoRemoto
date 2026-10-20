"""Address selection, asynchronous discovery and shutdown without a Tk display."""
from pathlib import Path
import queue
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import host


class Value:
    def __init__(self, value=""):
        self.value = value
        self.owner = threading.get_ident()

    def get(self):
        assert threading.get_ident() == self.owner, "Tk variable read from worker"
        return self.value

    def set(self, value):
        assert threading.get_ident() == self.owner, "Tk variable written from worker"
        self.value = value


class Widget:
    def __init__(self):
        self.owner = threading.get_ident()
        self.options = {}

    def configure(self, **options):
        assert threading.get_ident() == self.owner, "Tk widget changed from worker"
        self.options.update(options)


def app_stub():
    app = host.HostApp.__new__(host.HostApp)
    app.control_lock = threading.RLock()
    app.shutdown = threading.Event()
    app.ui_queue = queue.Queue()
    app._address_refresh_busy = False
    app._address_candidates = ()
    app._manual_address = None
    app._address_after_id = "network-job"
    app._ui_after_id = "queue-job"
    app._gui_after_id = "state-job"
    app.addresses = Value()
    app.address_hint = Value()
    app.address_details = Value()
    app.copy_notice = Value()
    app.port = Value("64321")
    app.password = Value("00001234")
    app.fingerprint = Value("AB:CD")
    app.status = Value()
    app.refresh_address_button = Widget()
    app.address_entry = Widget()
    app.copy_ip_button = Widget()
    app.copy_button = Widget()
    app.root = Mock()
    app.log = Mock()
    app._diagnostic = Mock()
    app._sync_gui_state = Mock()
    app._computer_name_cache = "PC do laboratório"
    app.server = Mock()
    app.peer = Mock()
    app.transfers = None
    app.inputs = Mock()
    app.sharing = True
    app.local_paused = False
    app.prompt_count = 0
    app.monitor_id = "all"
    app.last_screen = object()
    app.generation = 0
    app.pending_consents = set()
    return app


class AddressSelectionTests(unittest.TestCase):
    def test_single_suggested_lan_ip_and_separate_apipa_details(self):
        app = app_stub()
        app._apply_addresses(("192.168.15.100", "169.254.223.179"))
        self.assertEqual(app.addresses.get(), "192.168.15.100")
        self.assertEqual(app.address_entry.options["values"], ("192.168.15.100", "169.254.223.179"))
        self.assertIn("APIPA", app.address_details.get())
        app.copy_connection()
        copied = app.root.clipboard_append.call_args.args[0]
        self.assertIn("\nIP: 192.168.15.100\n", copied)
        self.assertNotIn("169.254", copied)
        self.assertIn("Senha: 00001234", copied)

    def test_copy_ip_contains_only_selected_address(self):
        app = app_stub()
        app._apply_addresses(("192.168.15.100", "10.8.0.5"))
        app.addresses.set("10.8.0.5")
        app._choose_address()
        app.copy_address()
        app.root.clipboard_append.assert_called_once_with("10.8.0.5")

    def test_manual_vpn_selection_survives_route_order_and_new_addresses(self):
        app = app_stub()
        app._apply_addresses(("192.168.15.100", "10.8.0.5"))
        app.addresses.set("10.8.0.5")
        app._choose_address()
        app._apply_addresses(("192.168.0.2", "192.168.15.100", "10.8.0.5"))
        self.assertEqual(app.addresses.get(), "10.8.0.5")
        self.assertEqual(app._manual_address, "10.8.0.5")
        self.assertIn("escolhido", app.address_hint.get())

    def test_removed_manual_address_falls_back_to_current_primary(self):
        app = app_stub()
        app._apply_addresses(("192.168.15.100", "10.8.0.5"))
        app.addresses.set("10.8.0.5")
        app._choose_address()
        app._apply_addresses(("192.168.0.2",))
        self.assertEqual(app.addresses.get(), "192.168.0.2")
        self.assertIsNone(app._manual_address)

    def test_automatic_apipa_is_replaced_when_dhcp_recovers_without_stopping_session(self):
        app = app_stub()
        peer, listener = app.peer, app.server
        app._apply_addresses(("169.254.223.179",))
        self.assertIn("DHCP", app.address_hint.get())
        app._apply_addresses(("192.168.15.100", "169.254.223.179"))
        self.assertEqual(app.addresses.get(), "192.168.15.100")
        self.assertIs(app.peer, peer)
        self.assertIs(app.server, listener)
        listener.stop.assert_not_called()
        peer.close.assert_not_called()

    def test_explicit_local_link_selection_remains_available_after_dhcp(self):
        app = app_stub()
        app._apply_addresses(("169.254.223.179",))
        app._choose_address()
        app._apply_addresses(("192.168.15.100", "169.254.223.179"))
        self.assertEqual(app.addresses.get(), "169.254.223.179")
        self.assertIn("APIPA", app.address_hint.get())
        app.copy_address()
        app.root.clipboard_append.assert_called_once_with("169.254.223.179")

    def test_missing_network_or_injected_placeholder_cannot_be_copied(self):
        app = app_stub()
        for candidates, value in [((), ""), ((), "Consulte o IPv4 em ipconfig."),
                                  (("192.168.0.2",), "192.168.0.2 / 169.254.1.1")]:
            with self.subTest(value=value):
                app._apply_addresses(candidates)
                app.addresses.set(value)
                app.copy_address()
                app.copy_connection()
        app.root.clipboard_clear.assert_not_called()
        app.root.clipboard_append.assert_not_called()
        self.assertIn("Nenhum IP", app.copy_notice.get())

    def test_repeated_unchanged_poll_does_not_flood_log(self):
        app = app_stub()
        for _ in range(100):
            app._apply_addresses(("192.168.0.2",))
        self.assertEqual(app.log.call_count, 1)

    def test_copy_feedback_is_reset_when_address_changes_or_selection_changes(self):
        app = app_stub()
        app._apply_addresses(("192.168.0.2", "10.8.0.5"))
        app.copy_address()
        self.assertIn("IP copiado", app.copy_notice.get())
        app._apply_addresses(("192.168.0.3", "10.8.0.5"))
        self.assertNotIn("IP copiado", app.copy_notice.get())
        self.assertIn("Copie novamente", app.copy_notice.get())
        app.copy_address()
        app.addresses.set("10.8.0.5")
        app._choose_address()
        self.assertNotIn("IP copiado", app.copy_notice.get())
        self.assertIn("Copie novamente", app.copy_notice.get())


class ImmediateSessionRevocationTests(unittest.TestCase):
    def test_disconnect_revokes_peer_input_and_transfers_before_graceful_close_starts(self):
        app = app_stub()
        peer = app.peer
        transfers = app.transfers = Mock()
        app.last_screen = {"captureId": "stale-capture"}
        with patch.object(host.threading, "Thread") as thread:
            app.disconnect()
            # The close worker is deliberately not run: local revocation cannot
            # depend on it or on the one-second network notification deadline.
            self.assertIsNone(app.peer)
            self.assertIsNone(app.transfers)
            self.assertIsNone(app.last_screen)
            self.assertGreater(app.generation, 0)
            transfers.close.assert_called_once()
            app.inputs.release.assert_called()
            app._message(peer, {"type": "input", "action": "key", "key": "x", "down": True,
                                "captureId": "stale-capture"})
            app._input({"action": "key", "key": "x", "down": True, "captureId": "stale-capture"})
            app.inputs.key.assert_not_called()
            transfers.handle.assert_not_called()
            thread.return_value.start.assert_called_once()
            thread.call_args.kwargs["target"]()
        peer.end_session.assert_called_once_with("Sessão encerrada no Hospedeiro.")
        # The eventual network callback has no remaining local state to revoke.
        before = app.generation
        app._disconnected(peer, "closed")
        self.assertEqual(app.generation, before)
        transfers.close.assert_called_once()

    def test_stop_listener_revokes_peer_and_pending_consent_before_server_worker(self):
        app = app_stub()
        peer, server = app.peer, app.server
        transfers = app.transfers = Mock()
        consent = threading.Event()
        app.pending_consents.add(consent)
        app._listener_controls = Mock()
        app._set_password_visible = Mock()
        with patch.object(host.threading, "Thread") as thread:
            app.stop_listener()
            self.assertIsNone(app.server)
            self.assertIsNone(app.peer)
            self.assertIsNone(app.transfers)
            transfers.close.assert_called_once()
            self.assertTrue(consent.is_set())
            app._message(peer, {"type": "input", "action": "key", "key": "x", "down": True})
            app.inputs.key.assert_not_called()
            server.stop.assert_not_called()
            thread.call_args.kwargs["target"]()
        server.stop.assert_called_once()

    def test_stopped_listener_cannot_attach_late_approved_peer(self):
        app = app_stub()
        app.server = None
        app.peer = None
        peer = Mock(closed=False)
        app._connected(peer, "Visitante", ("192.168.0.2", 1))
        self.assertIsNone(app.peer)
        peer.close.assert_called_once()

    def test_capture_already_in_progress_is_discarded_after_local_disconnect(self):
        app = app_stub()
        peer = app.peer
        monitors = [{"left": 0, "top": 0, "width": 64, "height": 48}] * 2

        class OneIteration(threading.Event):
            def wait(self, timeout=None):
                self.set()
                return True

        class Capture:
            def __init__(self):
                self.monitors = monitors

            def grab(self, rectangle):
                app.disconnect()
                return SimpleNamespace(size=(64, 48), rgb=b"")

            def close(self):
                pass

        class Image:
            size = (64, 48)

            def thumbnail(self, *args):
                pass

            def save(self, output, **options):
                output.write(b"fixture-jpeg")

            def close(self):
                pass

        app.shutdown = OneIteration()
        with patch.dict(sys.modules, {
            "mss": SimpleNamespace(mss=Capture),
            "PIL": SimpleNamespace(Image=SimpleNamespace(frombytes=lambda *args: Image(),
                                                          Resampling=SimpleNamespace(LANCZOS=1))),
        }), patch.object(host.threading, "Thread"):
            app._capture_loop()
        self.assertIsNone(app.peer)
        self.assertIsNone(app.last_screen)
        peer.send.assert_not_called()


class DiscoveryWorkerTests(unittest.TestCase):
    def start_worker(self, app, discover):
        real_thread = threading.Thread
        workers = []

        def thread_factory(*args, **kwargs):
            result = real_thread(*args, **kwargs)
            workers.append(result)
            return result

        return workers, patch.object(host, "local_addresses", side_effect=discover), patch.object(host.threading, "Thread", side_effect=thread_factory)

    def test_worker_is_single_and_coalesced_until_ui_consumes_result(self):
        app = app_stub()
        entered, release = threading.Event(), threading.Event()
        main_thread = threading.get_ident()
        owners = []

        def discover():
            owners.append(threading.get_ident())
            entered.set()
            if not release.wait(2):
                raise AssertionError("test worker was not released")
            return ["192.168.0.2", "192.168.0.2", "169.254.1.1", "0.0.0.0", "invalid"]

        workers, discovery_patch, thread_patch = self.start_worker(app, discover)
        with discovery_patch as discovery, thread_patch:
            try:
                self.assertTrue(app.refresh_addresses())
                self.assertTrue(entered.wait(1))
                for _ in range(100):
                    self.assertFalse(app.refresh_addresses())
                release.set()
                workers[0].join(2)
                self.assertFalse(workers[0].is_alive())
                self.assertFalse(app.refresh_addresses())
                self.assertEqual(discovery.call_count, 1)
                self.assertEqual(app.ui_queue.qsize(), 1)
                # Discovery has not touched any Tk variable from its worker.
                self.assertEqual(app.addresses.get(), "")
                app.ui_queue.get_nowait()()
            finally:
                release.set()
        self.assertNotEqual(owners, [main_thread])
        self.assertEqual(app._address_candidates, ("192.168.0.2", "169.254.1.1"))
        self.assertEqual(app.addresses.get(), "192.168.0.2")
        self.assertFalse(app._address_refresh_busy)

    def test_discovery_failure_is_delivered_as_a_non_modal_ui_state(self):
        app = app_stub()

        def failure():
            raise OSError("interface changed")

        workers, discovery_patch, thread_patch = self.start_worker(app, failure)
        with discovery_patch, thread_patch:
            app.refresh_addresses()
            workers[0].join(2)
        app.ui_queue.get(timeout=1)()
        self.assertFalse(app._address_refresh_busy)
        self.assertIn("tente Atualizar", app.address_hint.get())
        self.assertEqual(app.copy_ip_button.options["state"], "disabled")
        app._diagnostic.assert_called_once()

    def test_closing_during_discovery_cancels_jobs_and_never_enqueues_result(self):
        app = app_stub()
        app.server = None
        entered, release = threading.Event(), threading.Event()

        def discover():
            entered.set()
            if not release.wait(2):
                raise AssertionError("test worker was not released")
            return ["192.168.0.2"]

        workers, discovery_patch, thread_patch = self.start_worker(app, discover)
        with discovery_patch, thread_patch:
            try:
                app.refresh_addresses()
                self.assertTrue(entered.wait(1))
                app.close()
                release.set()
                workers[0].join(2)
                self.assertFalse(workers[0].is_alive())
            finally:
                release.set()
        self.assertTrue(app.ui_queue.empty())
        self.assertFalse(app.dispatch(lambda: None))
        self.assertFalse(app.refresh_addresses())
        self.assertEqual([call.args[0] for call in app.root.after_cancel.call_args_list],
                         ["network-job", "queue-job", "state-job"])
        app.root.destroy.assert_called_once()

    def test_result_already_queued_before_close_does_not_touch_destroyed_ui(self):
        app = app_stub()
        app.server = None
        app._address_refresh_busy = True
        app.dispatch(lambda: app._apply_addresses(("192.168.0.2",)))
        app.close()
        app.ui_queue.get_nowait()()
        self.assertEqual(app.addresses.get(), "")
        self.assertEqual(app.address_entry.options, {})
        self.assertEqual(app.refresh_address_button.options, {})
        app._sync_gui_state.assert_not_called()


if __name__ == "__main__":
    unittest.main()
