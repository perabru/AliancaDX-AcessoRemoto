"""Transport regressions with fragmented sockets, bounded queues and real TLS.

All artificial pauses use explicit events or short patched transport deadlines;
there is no requirement for an unreliable external network or long sleeps.
"""
import concurrent.futures
import json
from pathlib import Path
import queue
import socket
import ssl
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import net


def packet(message):
    data = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return struct.pack("!I", len(data)) + data


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(): return
        threading.Event().wait(.005)
    raise AssertionError("Condition did not become true")


class FragmentedSocket:
    """A real socket whose reads split headers/UTF8 and can time out in a packet."""
    def __init__(self, sock, timeouts=()):
        self.sock = sock
        self.reads = 0
        self.timeouts = set(timeouts)

    def recv(self, size):
        self.reads += 1
        if self.reads in self.timeouts:
            raise socket.timeout("one transient receive poll")
        return self.sock.recv(min(size, 1))

    def __getattr__(self, name): return getattr(self.sock, name)


class BlockedWriterSocket:
    def __init__(self, sock):
        self.sock = sock
        self.entered = threading.Event()
        self.release = threading.Event()

    def sendall(self, data):
        self.entered.set()
        if not self.release.wait(3): raise TimeoutError("test writer gate expired")
        return self.sock.sendall(data)

    def send(self, data):
        self.entered.set()
        if not self.release.is_set(): raise BlockingIOError("test writable socket is temporarily backpressured")
        return self.sock.send(data)

    def shutdown(self, how):
        self.release.set()
        return self.sock.shutdown(how)

    def __getattr__(self, name): return getattr(self.sock, name)


class ShortWriteSocket:
    def __init__(self, sock):
        self.sock = sock
        self.writes = 0

    def send(self, data):
        self.writes += 1
        if self.writes == 3: raise ssl.SSLWantWriteError("transient TLS write readiness")
        if self.writes == 5: raise BlockingIOError("transient socket pressure")
        return self.sock.send(data[:7])

    def __getattr__(self, name): return getattr(self.sock, name)


class WriteNeedsReadSocket:
    def __init__(self, sock):
        self.sock = sock
        self.writes = 0
        self.wanted_read = threading.Event()
        self.retried = threading.Event()

    def send(self, data):
        self.writes += 1
        if self.writes == 1:
            self.wanted_read.set()
            raise ssl.SSLWantReadError("TLS write needs incoming handshake data")
        self.retried.set()
        return self.sock.send(data)

    def __getattr__(self, name): return getattr(self.sock, name)


class ReadChangesDirectionSocket:
    def __init__(self, sock, retry_error):
        self.sock = sock
        self.retry_error = retry_error
        self.reads = 0
        self.held = b""
        self.changed = threading.Event()
        self.third_attempt = threading.Event()

    def recv(self, size):
        self.reads += 1
        if self.reads == 1:
            # Model TLS consuming a record before requesting write readiness.
            self.held = self.sock.recv(size)
            raise ssl.SSLWantWriteError("TLS read needs write readiness")
        if self.reads == 2:
            self.changed.set()
            raise self.retry_error("TLS read now needs readable data")
        self.third_attempt.set()
        data = self.sock.recv(size)
        data, self.held = self.held + data, b""
        return data

    def __getattr__(self, name): return getattr(self.sock, name)


class WriteChangesDirectionSocket:
    def __init__(self, sock, retry_error):
        self.sock = sock
        self.retry_error = retry_error
        self.writes = 0
        self.wanted_read = threading.Event()

    def send(self, data):
        self.writes += 1
        if self.writes == 1:
            self.wanted_read.set()
            raise ssl.SSLWantReadError("TLS write needs readable data")
        if self.writes == 2:
            raise self.retry_error("TLS write now needs write readiness")
        return self.sock.send(data)

    def __getattr__(self, name): return getattr(self.sock, name)


class PeerStabilityTests(unittest.TestCase):
    def pair(self, wrapper=None, received=None, closed=None):
        local, remote = socket.socketpair()
        local.settimeout(.05)
        remote.settimeout(2)
        self.addCleanup(remote.close)
        peer = net.Peer(wrapper(local) if wrapper else local,
                        received or (lambda message: None), closed or (lambda reason: None))
        self.addCleanup(peer.close)
        return peer, remote

    def test_fragmented_header_and_multibyte_utf8_preserve_packet_boundaries(self):
        received = queue.Queue()
        peer, remote = self.pair(FragmentedSocket, received.put)
        peer.start()
        messages = [{"type": "input", "action": "move", "captureId": "ação-日本語", "x": .25, "y": .75},
                    {"type": "file_cancel", "id": "d3159e03-9114-4b58-b24d-697b4525405a"}]
        remote.sendall(b"".join(packet(message) for message in messages))
        self.assertEqual([received.get(timeout=2) for _ in messages], messages)
        self.assertFalse(peer.closed)

    def test_partial_header_and_body_resume_after_transient_receive_timeouts(self):
        received = queue.Queue()
        peer, remote = self.pair(lambda sock: FragmentedSocket(sock, (3, 8, 20, 40)), received.put)
        peer.start()
        messages = [{"type": "input", "action": "key", "key": "ctrl", "down": True,
                     "captureId": "ação-日本語"}, {"type": "state", "sharing": True}]
        remote.sendall(b"".join(packet(message) for message in messages))
        self.assertEqual([received.get(timeout=2) for _ in messages], messages)
        self.assertFalse(peer.closed)

    def test_partial_writes_and_retryable_ssl_write_preserve_exact_packet_offsets(self):
        peer, remote = self.pair(ShortWriteSocket)
        messages = [{"type": "control", "text": "ação 日本語" * 200},
                    {"type": "input", "action": "release"}]
        for message in messages: peer.send(message)
        peer.start()
        self.assertEqual([net._read(remote) for _ in messages], messages)
        self.assertGreater(peer.sock.writes, 5)
        self.assertFalse(peer.closed)

    def test_ssl_write_want_read_waits_for_readability_instead_of_spinning_on_writable_socket(self):
        peer, remote = self.pair(WriteNeedsReadSocket)
        message = {"type": "input", "action": "release"}
        peer.send(message)
        peer.start()
        self.assertTrue(peer.sock.wanted_read.wait(1))
        self.assertFalse(peer.sock.retried.wait(.04), "SSL write retried without incoming readable data")
        self.assertEqual(peer.sock.writes, 1)
        remote.sendall(packet({"type": "pong"}))
        self.assertEqual(net._read(remote), message)
        self.assertTrue(peer.sock.retried.is_set())
        self.assertFalse(peer.closed)

    def test_tls_read_returns_to_read_poll_after_want_write_changes_direction(self):
        for retry_error in (ssl.SSLWantReadError, BlockingIOError, socket.timeout):
            with self.subTest(retry=retry_error.__name__):
                received = queue.Queue()
                peer, remote = self.pair(lambda sock: ReadChangesDirectionSocket(sock, retry_error), received.put)
                peer.start()
                message = {"type": "input", "action": "release"}
                remote.sendall(packet(message))
                self.assertTrue(peer.sock.changed.wait(1))
                self.assertFalse(peer.sock.third_attempt.wait(.04), "TLS read kept polling a writable socket after direction changed")
                self.assertEqual(peer.sock.reads, 2)
                remote.sendall(packet({"type": "pong"}))
                self.assertEqual(received.get(timeout=1), message)
                self.assertFalse(peer.closed)
                peer.close()

    def test_tls_write_returns_to_write_poll_after_want_read_changes_direction(self):
        for retry_error in (ssl.SSLWantWriteError, BlockingIOError, socket.timeout):
            with self.subTest(retry=retry_error.__name__):
                peer, remote = self.pair(lambda sock: WriteChangesDirectionSocket(sock, retry_error))
                message = {"type": "input", "action": "release"}
                peer.send(message)
                peer.start()
                self.assertTrue(peer.sock.wanted_read.wait(1))
                remote.sendall(packet({"type": "pong"}))
                self.assertEqual(net._read(remote), message)
                self.assertGreaterEqual(peer.sock.writes, 3)
                self.assertFalse(peer.closed)
                peer.close()

    def test_stalled_send_expires_even_while_complete_pongs_keep_receive_alive(self):
        reasons = []
        closed = threading.Event()
        def disconnected(reason):
            reasons.append(reason)
            closed.set()
        with patch.object(net, "SESSION_SOCKET_TIMEOUT", .005), patch.object(net, "HEARTBEAT_INTERVAL", 60), \
             patch.object(net, "PEER_IDLE_TIMEOUT", .12):
            peer, remote = self.pair(BlockedWriterSocket, closed=disconnected)
            started = time.monotonic()
            peer.send({"type": "control", "sequence": 1})
            peer.start()
            self.assertTrue(peer.sock.entered.wait(1))
            sent = 0
            while not closed.is_set() and time.monotonic() - started < 1:
                try:
                    remote.sendall(packet({"type": "pong"}))
                    sent += 1
                except OSError:
                    break
                closed.wait(.01)
            self.assertTrue(closed.wait(.2), "Pongs hid a permanently stalled sender")
            self.assertGreaterEqual(sent, 2)
            self.assertGreater(peer._last_received, started + .05)
            self.assertEqual(len(reasons), 1)
            self.assertIn("Envio sem progresso", reasons[0])
            self.assertTrue(peer.closed)

    def test_slow_writer_coalesces_only_adjacent_same_capture_moves_without_dropping_barriers(self):
        peer, remote = self.pair(BlockedWriterSocket)
        first = {"type": "input", "action": "key", "key": "ctrl", "down": True}
        peer.send(first)
        peer.start()
        self.assertTrue(peer.sock.entered.wait(1))
        expected = [first]
        barriers = [
            {"type": "file_chunk", "id": "d3159e03-9114-4b58-b24d-697b4525405a", "offset": 0, "data": "AA=="},
            {"type": "input", "action": "button", "button": "left", "down": True},
            {"type": "input", "action": "wheel", "delta": 1},
            {"type": "input", "action": "button", "button": "left", "down": False},
            {"type": "input", "action": "key", "key": "ctrl", "down": False},
        ]
        try:
            for batch, barrier in enumerate(barriers):
                for index in range(200):
                    message = {"type": "input", "action": "move", "captureId": "live",
                               "x": index / 199, "y": batch / 5, "sequence": batch * 200 + index}
                    peer.send(message)
                expected.extend([message, barrier])
                peer.send(barrier)
            # A geometry switch is a boundary, even between adjacent moves.
            for capture in ("old-capture", "new-capture"):
                message = {"type": "input", "action": "move", "captureId": capture, "x": 0, "y": 0}
                peer.send(message)
                expected.append(message)
            self.assertFalse(peer.closed)
            peer.sock.release.set()
            self.assertEqual([net._read(remote) for _ in expected], expected)
            self.assertFalse(peer.closed)
        finally:
            peer.sock.release.set()

    def test_full_queue_discards_only_disposable_mouse_move_and_keeps_reliable_messages(self):
        peer, remote = self.pair()
        messages = [{"type": "control", "sequence": n} for n in range(128)]
        for message in messages: peer.send(message)
        self.assertIs(peer.send({"type": "input", "action": "move", "captureId": "live", "x": 1, "y": 1}), False)
        self.assertFalse(peer.closed)
        peer.start()
        self.assertEqual([net._read(remote) for _ in messages], messages)
        self.assertFalse(peer.closed)

    def test_reliable_queue_waits_for_brief_backpressure_and_delivers_release(self):
        peer, remote = self.pair(BlockedWriterSocket)
        peer.send({"type": "control", "sequence": -1})
        peer.start()
        self.assertTrue(peer.sock.entered.wait(1))
        for index in range(128): peer.send({"type": "control", "sequence": index})
        started, done = threading.Event(), threading.Event()
        errors = []
        release = {"type": "input", "action": "release"}
        def enqueue():
            started.set()
            try: peer.send(release)
            except Exception as error: errors.append(error)
            finally: done.set()
        with patch.object(net, "SEND_QUEUE_TIMEOUT", .5):
            thread = threading.Thread(target=enqueue, daemon=True)
            thread.start()
            self.assertTrue(started.wait(1))
            self.assertFalse(done.wait(.03), "Reliable enqueue should wait for queue space")
            peer.sock.release.set()
            self.assertTrue(done.wait(1))
            thread.join(1)
        self.assertEqual(errors, [])
        observed = [net._read(remote) for _ in range(130)]
        self.assertEqual([msg["sequence"] for msg in observed[:-1]], [-1] + list(range(128)))
        self.assertEqual(observed[-1], release)
        self.assertFalse(peer.closed)

    def test_persistent_reliable_backpressure_closes_once_instead_of_losing_key_release(self):
        closed = Mock()
        peer, remote = self.pair(BlockedWriterSocket, closed=closed)
        peer.send({"type": "control", "sequence": -1})
        peer.start()
        self.assertTrue(peer.sock.entered.wait(1))
        for index in range(128): peer.send({"type": "control", "sequence": index})
        with patch.object(net, "SEND_QUEUE_TIMEOUT", .03):
            with self.assertRaises(ConnectionError):
                peer.send({"type": "input", "action": "release"})
        self.assertTrue(peer.closed)
        peer.close("second reason")
        closed.assert_called_once()

    def test_file_ack_generated_inside_io_survives_temporarily_full_reliable_queue(self):
        attempted = threading.Event()
        errors = []
        peer, remote = self.pair(BlockedWriterSocket)
        ack = {"type": "file_ack", "id": "d3159e03-9114-4b58-b24d-697b4525405a", "offset": 1}
        def receive(message):
            try: peer.send(ack)
            except Exception as error: errors.append(error)
            finally: attempted.set()
        peer.on_message = receive
        seed = {"type": "control", "sequence": -1}
        peer.send(seed)
        peer.start()
        self.assertTrue(peer.sock.entered.wait(1))
        pending = [{"type": "control", "sequence": index} for index in range(128)]
        for message in pending: peer.send(message)
        try:
            remote.sendall(packet({"type": "file_chunk", "id": ack["id"], "offset": 0, "data": "AA=="}))
            self.assertTrue(attempted.wait(1))
            self.assertEqual(errors, [])
            self.assertFalse(peer.closed, "A protocol response closed the session before persistent congestion")
            peer.sock.release.set()
            self.assertEqual([net._read(remote) for _ in range(130)], [seed] + pending + [ack])
            self.assertFalse(peer.closed)
        finally:
            peer.sock.release.set()

    def test_silent_peer_expires_but_heartbeat_pong_keeps_a_live_peer_open(self):
        closed = threading.Event()
        with patch.object(net, "SESSION_SOCKET_TIMEOUT", .01), patch.object(net, "HEARTBEAT_INTERVAL", .02), \
             patch.object(net, "PEER_IDLE_TIMEOUT", .12):
            silent, remote = self.pair(closed=lambda reason: closed.set())
            silent.start()
            self.assertTrue(closed.wait(1), "Peer with no complete packets did not expire")
            self.assertTrue(silent.closed)
            live, other = self.pair()
            live.start()
            deadline = time.monotonic() + .25
            while time.monotonic() < deadline:
                message = net._read(other)
                self.assertEqual(message, {"type": "ping"})
                other.sendall(packet({"type": "pong"}))
            self.assertFalse(live.closed)
            live.close()

    def test_close_wakes_io_poll_and_deduplicates_concurrent_shutdown(self):
        closed = Mock()
        peer, remote = self.pair(closed=closed)
        with patch.object(net, "HEARTBEAT_INTERVAL", 60), patch.object(net, "SESSION_SOCKET_TIMEOUT", 60):
            peer.start()
            barrier = threading.Barrier(9)
            def close(index):
                barrier.wait(1)
                peer.close(f"close {index}")
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as workers:
                jobs = [workers.submit(close, index) for index in range(8)]
                barrier.wait(1)
                for job in jobs: job.result(1)
            peer._io_thread.join(.3)
            self.assertFalse(peer._io_thread.is_alive(), "close() left the I/O poll blocked")
        closed.assert_called_once()
        self.assertTrue(peer.closed)

    def test_repeated_identical_state_can_coalesce_without_merging_pause_resume_barrier(self):
        peer, remote = self.pair()
        peer.send({"type": "screen", "captureId": "old"})
        pause = {"type": "state", "sharing": False, "localPaused": True,
                 "promptActive": False, "captureAvailable": True, "revision": 1}
        peer.send(pause)
        pause = dict(pause, revision=2)
        peer.send(pause)
        resume = dict(pause, sharing=True, localPaused=False, revision=3)
        peer.send(resume)
        latest = {"type": "screen", "captureId": "new"}
        peer.send(latest)
        peer.start()
        self.assertEqual([net._read(remote) for _ in range(3)], [pause, resume, latest])
        self.assertFalse(peer.closed)

    def test_eof_halfway_through_packet_never_delivers_partial_message(self):
        for invalid in (b"\x00\x00", struct.pack("!I", 20) + b'{"type":"input"'):
            with self.subTest(packet=invalid):
                closed = threading.Event()
                received = Mock()
                peer, remote = self.pair(received=received, closed=lambda reason: closed.set())
                peer.start()
                remote.sendall(invalid)
                remote.shutdown(socket.SHUT_WR)
                self.assertTrue(closed.wait(1))
                received.assert_not_called()
                self.assertTrue(peer.closed)


class HostHandshakeStabilityTests(unittest.TestCase):
    def test_stop_restart_cancels_all_incomplete_tls_handshakes_and_accepts_new_login(self):
        with tempfile.TemporaryDirectory() as folder:
            consent = Mock(return_value=True)
            server = net.HostServer("127.0.0.1", 0, "00123456", consent,
                lambda peer, name, ip: None, lambda peer, reason: None, state_dir=folder)
            clients = []
            try:
                server.start()
                clients = [socket.create_connection(("127.0.0.1", server.port), timeout=1) for _ in range(4)]
                def all_pending():
                    with server._lock:
                        return len(server._sockets) == 4 and all(isinstance(sock, ssl.SSLSocket) or sock.fileno() < 0 for sock in server._sockets)
                wait_for(all_pending)
                # All four workers have accepted a TCP connection that sends no TLS ClientHello.
                server.stop()
                for client in clients:
                    client.settimeout(.3)
                    try: self.assertEqual(client.recv(1), b"", "stop() did not close a pending TLS socket")
                    except ConnectionResetError: pass
                server.start()
                peer = net.connect("127.0.0.1", server.port, "00123456", "QA reinício",
                    lambda message: None, lambda reason: None,
                    lambda address, port, pin: pin == server.fingerprint)
                try:
                    self.assertFalse(peer.closed)
                    consent.assert_called_once_with("QA reinício", "127.0.0.1")
                finally:
                    peer.close()
            finally:
                for client in clients: client.close()
                server.stop()


if __name__ == "__main__":
    unittest.main()
