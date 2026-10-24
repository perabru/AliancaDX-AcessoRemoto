"""Bounded, local measurements of actual application traffic on one Peer.

This is throughput, not available internet capacity. TLS/IP overhead, the
handshake and the private mesh's own traffic are outside this measurement.
100 ms buckets keep memory and snapshot work bounded even on a fast LAN.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import threading
import time


WINDOW_SECONDS = 3.0
BUCKET_SECONDS = 0.1


@dataclass(frozen=True)
class TrafficSnapshot:
    rx_mbps: float
    tx_mbps: float
    rx_bytes: int
    tx_bytes: int
    period_seconds: float


class PeerTraffic:
    """Thread-safe counters; record only bytes accepted by send/returned by recv.

    The rolling rate averages approximately the last three seconds (bucket
    boundary precision: 100 ms). Lifetime totals remain exact. Sampling never
    sends data and does not inspect or retain message contents.
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._started = self._last_time = clock()
        self._samples = deque(maxlen=32)
        self._rx_bytes = self._tx_bytes = 0

    def _now(self):
        # Production uses a monotonic clock. Clamping also prevents invalid
        # rates if a test/custom clock moves backwards.
        self._last_time = max(self._last_time, self._clock())
        return self._last_time

    def _prune(self, now):
        cutoff = now - WINDOW_SECONDS
        while self._samples and self._samples[0][1] <= cutoff:
            self._samples.popleft()

    def record(self, *, rx_bytes=0, tx_bytes=0):
        if type(rx_bytes) is not int or type(tx_bytes) is not int or min(rx_bytes, tx_bytes) < 0:
            raise ValueError("Traffic counts must be nonnegative integers.")
        if not (rx_bytes or tx_bytes):
            return
        with self._lock:
            now = self._now()
            self._prune(now)
            tick = math.floor(now / BUCKET_SECONDS)
            if self._samples and self._samples[-1][0] == tick:
                sample = self._samples[-1]
                sample[1] = now
                sample[2] += rx_bytes
                sample[3] += tx_bytes
            else:
                self._samples.append([tick, now, rx_bytes, tx_bytes])
            self._rx_bytes += rx_bytes
            self._tx_bytes += tx_bytes

    def snapshot(self):
        with self._lock:
            now = self._now()
            self._prune(now)
            period = min(WINDOW_SECONDS, max(0.0, now - self._started))
            rx = sum(sample[2] for sample in self._samples)
            tx = sum(sample[3] for sample in self._samples)
            # The first instant has no measurable duration yet.
            factor = 8 / 1_000_000 / period if period >= .001 else 0.0
            return TrafficSnapshot(rx * factor, tx * factor, self._rx_bytes,
                                   self._tx_bytes, period)


def session_traffic_text(peer):
    """Compact PT-BR label shared by the two native interfaces."""
    if peer is None or peer.closed:
        return "Sessão · sem conexão"
    reader = getattr(peer, "traffic_stats", None)
    if reader is None:
        return "Sessão · medição indisponível"
    value = reader()
    rx = f"{value.rx_mbps:.2f}".replace(".", ",")
    tx = f"{value.tx_mbps:.2f}".replace(".", ",")
    return f"Sessão  ↓ {rx}  ↑ {tx} Mbps"
