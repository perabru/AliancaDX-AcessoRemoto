"""Rates use real completed I/O, not queued data or assumed link capacity."""
from pathlib import Path
import math
import socket
import sys
import threading
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from connection_metrics import PeerTraffic, session_traffic_text
import net


class Clock:
    def __init__(self): self.now = 0.0
    def __call__(self): return self.now


class PartialSocket:
    def __init__(self, sock):
        self.sock = sock
        self.calls = 0
        self.accepted = 0

    def send(self, data):
        self.calls += 1
        if self.calls % 3 == 1:
            raise BlockingIOError("busy")
        written = self.sock.send(data[:7])
        self.accepted += written
        return written

    def __getattr__(self, name): return getattr(self.sock, name)


class TrafficTests(unittest.TestCase):
    def test_decimal_mbps_window_warmup_and_immutable_lifetime_totals(self):
        clock = Clock(); meter = PeerTraffic(clock)
        meter.record(rx_bytes=125_000, tx_bytes=250_000)
        self.assertEqual(meter.snapshot().rx_mbps, 0)
        clock.now = 1
        sample = meter.snapshot()
        self.assertEqual((sample.rx_mbps, sample.tx_mbps), (1, 2))
        self.assertEqual(sample.period_seconds, 1)
        with self.assertRaises(AttributeError): sample.rx_bytes = 0
        clock.now = 2
        meter.record(rx_bytes=250_000)
        clock.now = 3.1
        sample = meter.snapshot()
        self.assertEqual(sample.rx_bytes, 375_000)
        self.assertEqual(sample.tx_bytes, 250_000)
        self.assertAlmostEqual(sample.rx_mbps, 2 / 3)
        self.assertEqual(sample.tx_mbps, 0)
        clock.now = 5.1
        self.assertEqual((meter.snapshot().rx_mbps, meter.snapshot().tx_mbps), (0, 0))

    def test_bucket_boundary_does_not_duplicate_bytes_or_grow_with_busy_link(self):
        clock = Clock(); meter = PeerTraffic(clock)
        for index in range(100_000):
            clock.now = index / 10_000
            meter.record(tx_bytes=1)
        self.assertLessEqual(len(meter._samples), 32)
        sample = meter.snapshot()
        self.assertEqual(sample.tx_bytes, 100_000)
        self.assertLessEqual(sample.tx_mbps, 31_001 * 8 / 1_000_000 / 3)
        self.assertGreater(sample.tx_mbps, 29_000 * 8 / 1_000_000 / 3)
        self.assertEqual(sample, meter.snapshot())
        clock.now += 3.1
        self.assertEqual(meter.snapshot().tx_mbps, 0)

    def test_thread_safe_per_peer_totals_and_backward_clock(self):
        clock = Clock(); a = PeerTraffic(clock); b = PeerTraffic(clock)
        threads = [threading.Thread(target=lambda: [a.record(rx_bytes=5, tx_bytes=3) for _ in range(1000)]) for _ in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        clock.now = 2
        sample = a.snapshot()
        self.assertEqual((sample.rx_bytes, sample.tx_bytes), (40_000, 24_000))
        self.assertEqual(b.snapshot().rx_bytes, 0)
        clock.now = -1
        self.assertEqual(a.snapshot(), sample)
        self.assertTrue(math.isfinite(sample.rx_mbps))

    def test_rejects_invalid_counts_and_labels_connection_state(self):
        meter = PeerTraffic(Clock())
        for value in (-1, 1.5, True):
            with self.assertRaises(ValueError): meter.record(rx_bytes=value)
        self.assertEqual(session_traffic_text(None), "Sessão · sem conexão")
        peer = SimpleNamespace(closed=False, traffic_stats=meter.snapshot)
        self.assertEqual(session_traffic_text(peer), "Sessão  ↓ 0,00  ↑ 0,00 Mbps")
        peer.closed = True
        self.assertEqual(session_traffic_text(peer), "Sessão · sem conexão")

    def make_peer(self, wrapper=None, callback=None):
        local, remote = socket.socketpair()
        remote.settimeout(2)
        peer = net.Peer(wrapper(local) if wrapper else local, on_message=callback)
        self.addCleanup(remote.close)
        def close():
            peer.close()
            if peer._io_thread: peer._io_thread.join(2)
        self.addCleanup(close)
        return peer, remote

    def test_partial_writes_and_retry_count_only_completed_framed_bytes(self):
        peer, remote = self.make_peer(PartialSocket)
        message = {"type": "input", "action": "release"}
        peer.send(message)
        self.assertEqual(peer.traffic_stats().tx_bytes, 0)
        peer.start()
        self.assertEqual(net._read(remote), message)
        deadline = time.monotonic() + 2
        while peer.traffic_stats().tx_bytes != len(net._packet(message)) and time.monotonic() < deadline:
            threading.Event().wait(.002)
        sample = peer.traffic_stats()
        self.assertEqual(sample.tx_bytes, len(net._packet(message)))
        self.assertEqual(sample.tx_bytes, peer.sock.accepted)
        self.assertGreater(peer.sock.calls, 3)
        self.assertEqual(sample.rx_bytes, 0)

    def test_superseded_frames_are_not_counted_and_fragmented_receive_is_exact(self):
        received = threading.Event()
        peer, remote = self.make_peer(callback=lambda message: received.set())
        stale = {"type": "screen", "image": "stale"}
        latest = {"type": "screen", "image": "latest"}
        peer.send(stale); peer.send(latest)
        self.assertEqual(peer.traffic_stats().tx_bytes, 0)
        peer.start()
        self.assertEqual(net._read(remote), latest)
        packet = net._packet({"type": "input", "action": "release"})
        for start in range(0, len(packet), 3): remote.sendall(packet[start:start + 3])
        self.assertTrue(received.wait(2))
        self.assertEqual(peer.traffic_stats().rx_bytes, len(packet))
        self.assertEqual(peer.traffic_stats().tx_bytes, len(net._packet(latest)))


if __name__ == "__main__": unittest.main()
