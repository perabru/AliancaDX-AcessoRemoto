"""Exercise idle wakeups and bounded video under real socket backpressure."""
from pathlib import Path
import queue
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import net


class GateWriter:
    def __init__(self, sock):
        self.sock = sock
        self.entered = threading.Event()
        self.release = threading.Event()

    def send(self, data):
        self.entered.set()
        if not self.release.is_set():
            raise BlockingIOError("simulated slow uplink")
        return self.sock.send(data)

    def __getattr__(self, name):
        return getattr(self.sock, name)


class BufferedReader:
    """Model decrypted TLS bytes even when the network socket is not readable."""
    def __init__(self, sock, message):
        self.sock = sock
        self.buffer = net._packet(message)

    def pending(self):
        return len(self.buffer)

    def recv(self, size):
        if self.buffer:
            data, self.buffer = self.buffer[:size], self.buffer[size:]
            return data
        return self.sock.recv(size)

    def __getattr__(self, name):
        return getattr(self.sock, name)


class LatencyTransportTests(unittest.TestCase):
    def pair(self, wrapper=None, on_message=None):
        local, remote = socket.socketpair()
        remote.settimeout(1)
        peer = net.Peer(wrapper(local) if wrapper else local,
                        on_message=on_message)
        self.addCleanup(remote.close)
        self.addCleanup(peer.close)
        return peer, remote

    def test_queued_input_wakes_an_idle_poll_instead_of_waiting_for_poll_deadline(self):
        peer, remote = self.pair()
        idle_poll = threading.Event()
        real_select = net.select.select

        def select(readers, writers, errors, timeout):
            if threading.current_thread() is peer._io_thread and not writers:
                idle_poll.set()
            return real_select(readers, writers, errors, timeout)

        # The large timeout makes this a functional wakeup check, not a fragile
        # millisecond benchmark that depends on the machine running the tests.
        with patch.object(net, "SESSION_SOCKET_TIMEOUT", 60), \
                patch.object(net, "HEARTBEAT_INTERVAL", 60), \
                patch.object(net.select, "select", select):
            peer.start()
            self.assertTrue(idle_poll.wait(1))
            message = {"type": "input", "action": "release"}
            peer.send(message)
            self.assertEqual(net._read(remote), message)
            peer.close()
            peer._io_thread.join(1)
        self.assertFalse(peer._io_thread.is_alive())
        self.assertIsNone(peer._wake_reader)
        self.assertIsNone(peer._wake_writer)

    def test_buffered_tls_record_is_delivered_without_waiting_for_new_network_bytes(self):
        received = queue.Queue()
        message = {"type": "stream_feedback", "frameId": 4}
        peer, _ = self.pair(lambda sock: BufferedReader(sock, message), received.put)
        with patch.object(net, "SESSION_SOCKET_TIMEOUT", 60), \
                patch.object(net, "HEARTBEAT_INTERVAL", 60):
            peer.start()
            self.assertEqual(received.get(timeout=1), message)
            peer.close()
            peer._io_thread.join(1)
        self.assertFalse(peer._io_thread.is_alive())

    def test_video_pressure_keeps_current_and_latest_frame_without_a_fifo_backlog(self):
        peer, remote = self.pair(GateWriter)
        frames = [{"type": "screen", "frameId": index, "image": "jpeg"}
                  for index in range(1, 4)]
        peer.send(frames[0])
        self.assertTrue(peer.stream_stats()["pending"])
        peer.start()
        self.assertTrue(peer.sock.entered.wait(1))
        self.assertTrue(peer.stream_stats()["writing"])
        self.assertFalse(peer.stream_stats()["pending"])
        peer.send(frames[1])
        peer.send(frames[2])
        self.assertTrue(peer.stream_stats()["pending"])
        peer.sock.release.set()
        self.assertEqual(net._read(remote), frames[0])
        self.assertEqual(net._read(remote), frames[2])
        deadline = time.monotonic() + 1
        while peer.stream_stats()["writing"] and time.monotonic() < deadline:
            threading.Event().wait(.002)
        stats = peer.stream_stats()
        self.assertFalse(stats["pending"])
        self.assertFalse(stats["writing"])
        self.assertEqual(stats["lastFrameBytes"], len(net._packet(frames[2])))
        self.assertGreaterEqual(stats["lastFrameDuration"], 0)
        self.assertFalse(peer.closed)

    def test_frame_feedback_can_use_protocol_reserve_under_input_pressure(self):
        peer, remote = self.pair()
        for index in range(net.MAX_PENDING_MESSAGES):
            peer.send({"type": "control", "sequence": index})
        feedback = {"type": "stream_feedback", "frameId": 7}
        peer.send(feedback)
        peer.start()
        messages = [net._read(remote) for _ in range(net.MAX_PENDING_MESSAGES + 1)]
        self.assertEqual(messages[-1], feedback)
        self.assertFalse(peer.closed)


if __name__ == "__main__":
    unittest.main()
