"""Real local transport checks for intentional close and cancellable login."""
from pathlib import Path
import queue
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import net
import presence


class NetworkLifecycleTests(unittest.TestCase):
    def test_ending_session_rejects_new_outgoing_and_incoming_application_data(self):
        left, right = socket.socketpair()
        reasons, messages = queue.Queue(), queue.Queue()
        class Backpressured:
            def __getattr__(self, name):
                return getattr(left, name)
            def send(self, _data):
                raise BlockingIOError("temporary pressure")
        peer = net.Peer(Backpressured(), messages.put, reasons.put)
        self.addCleanup(peer.close)
        self.addCleanup(right.close)
        peer.start()
        peer.end_session("Responsável encerrou.")
        with self.assertRaises(ConnectionError):
            peer.send({"type": "input", "action": "key", "key": "x", "down": True})
        net._write(right, {"type": "input", "action": "key", "key": "x", "down": True})
        reason = reasons.get(timeout=2)
        self.assertFalse(reason.retryable)
        self.assertTrue(messages.empty(), "Ending sessions must no longer dispatch control or files")

    def test_explicit_end_marker_is_received_as_nonretryable_close(self):
        left, right = socket.socketpair()
        reasons = queue.Queue()
        messages = queue.Queue()
        host = net.Peer(left)
        controller = net.Peer(right, messages.put, reasons.put)
        self.addCleanup(host.close)
        self.addCleanup(controller.close)
        host.start()
        controller.start()
        host.end_session("Encerrado no Hospedeiro.")
        reason = reasons.get(timeout=2)
        self.assertEqual(reason, "Encerrado no Hospedeiro.")
        self.assertFalse(reason.retryable)
        self.assertTrue(messages.empty(), "Control marker must not reach file/input handlers")

    def test_generic_eof_remains_nonretryable_for_older_hosts(self):
        left, right = socket.socketpair()
        reasons = queue.Queue()
        peer = net.Peer(left, on_close=reasons.put)
        self.addCleanup(peer.close)
        self.addCleanup(right.close)
        peer.start()
        right.close()
        self.assertFalse(reasons.get(timeout=2).retryable)

    def test_only_transport_failures_are_retryable(self):
        for error in (TimeoutError("timeout"), ConnectionResetError("reset"),
                      BrokenPipeError("pipe"), OSError(10051, "network unavailable")):
            with self.subTest(error=error):
                self.assertTrue(net._transport_close_reason(error).retryable)
        for error in (ValueError("bad packet"), PermissionError("not approved"),
                      ConnectionError("EOF")):
            with self.subTest(error=error):
                self.assertFalse(net._transport_close_reason(error).retryable)

    def test_cancel_during_blocked_dns_returns_without_waiting_for_resolver(self):
        entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
        errors = queue.Queue()
        def resolver(*_arguments):
            entered.set()
            release.wait(3)
            return []
        def client():
            try:
                net.connect("lookup.invalid", 64321, "12345678", "test",
                            lambda _m: None, lambda _r: None, lambda *_a: True,
                            cancel_event=cancel)
            except Exception as error:
                errors.put(error)
        with patch.object(presence.socket, "getaddrinfo", resolver):
            worker = threading.Thread(target=client, daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                cancel.set()
                worker.join(1)
                self.assertFalse(worker.is_alive())
                self.assertIsInstance(errors.get(timeout=1), InterruptedError)
            finally:
                release.set()
                worker.join(1)

    def test_cancel_while_waiting_for_host_approval_closes_tls_attempt(self):
        entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
        errors = queue.Queue()
        def approve(_name, _address):
            entered.set()
            release.wait(3)
            return True
        with tempfile.TemporaryDirectory() as directory:
            server = net.HostServer("127.0.0.1", 0, "12345678", approve,
                                    lambda *_a: None, lambda *_a: None, directory)
            server.start()
            def client():
                try:
                    peer = net.connect("127.0.0.1", server.port, "12345678", "test",
                                       lambda _m: None, lambda _r: None, lambda *_a: True,
                                       cancel_event=cancel)
                    peer.close()
                except Exception as error:
                    errors.put(error)
            worker = threading.Thread(target=client, daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                cancel.set()
                worker.join(1)
                self.assertFalse(worker.is_alive())
                self.assertIsInstance(errors.get(timeout=1), InterruptedError)
            finally:
                release.set()
                server.stop()
                worker.join(1)

    def test_expired_deadline_does_not_open_a_socket(self):
        with patch.object(net.socket, "socket") as factory:
            with self.assertRaises(TimeoutError):
                net.connect("127.0.0.1", 64321, "12345678", "test",
                            lambda _m: None, lambda _r: None, lambda *_a: True,
                            deadline=time.monotonic() - 1)
            factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
