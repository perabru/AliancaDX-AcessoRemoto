"""Bound screen latency without relying on clocks in two different machines.

The sender owns the timestamps. New controllers opt in to acknowledging an
actually presented frame; old controllers continue to receive ordinary JPEGs.
This class is deliberately independent of Tk, sockets and screen providers.
HostApp holds its control lock while using an instance.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math


STREAM_VERSION = 1
MAX_FRAME_ID = (1 << 53) - 1  # Also exactly representable in Android/JSON clients.
ACK_PROBE_SECONDS = 2.0
RESUME_INTERVAL_SECONDS = 1.0
BACKPRESSURE_POLL_SECONDS = 0.01


@dataclass(frozen=True)
class StreamProfile:
    name: str
    width: int
    height: int
    quality: int
    fps: int


# Start conservatively rather than filling a slow connection with a large first
# frame. Stable low-latency feedback can recover detail, one tier at a time.
PROFILES = (
    StreamProfile("detail", 1920, 1080, 65, 20),
    StreamProfile("balanced", 1280, 720, 55, 12),
    StreamProfile("economy", 960, 540, 44, 10),
    StreamProfile("low", 640, 360, 32, 6),
    StreamProfile("minimum", 480, 270, 25, 4),
)


def _number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


class AdaptiveStream:
    """One display frame in flight, plus at most one recovery probe.

    The probe is necessary if a frame was discarded before presentation (for
    example while the remote UI was paused). A missing ACK never starts an
    unlimited retry stream: after that single probe, only feedback or a local
    capture/consent generation change opens the window again.
    """

    def __init__(self):
        self.negotiated = False
        self._tier = 1
        self._sequence = 0
        self._pending = None
        self._probe_used = False
        self._probe_ready = False
        self._generation = None
        self._good_samples = 0
        self._moderate_samples = 0
        self._last_change = 0.0
        self._transport_sample = None
        self._busy_since = None
        self._busy_reduced = False
        self._last_resume = None

    @property
    def profile(self):
        return PROFILES[self._tier]

    @property
    def outstanding_frame_id(self):
        return self._pending[0] if self._pending else None

    def synchronize(self, generation):
        if generation != self._generation:
            self._generation = generation
            self._pending = None
            self._probe_used = False
            self._probe_ready = False
            # Frame ids are never recycled across a pause or monitor change.

    def enable(self, version):
        if type(version) is not int or version != STREAM_VERSION:
            return False
        if not self.negotiated:
            self.negotiated = True
            self._pending = None
            self._probe_used = False
            self._probe_ready = False
        return True

    def _reduce(self, now, tiers=1):
        self._tier = min(len(PROFILES) - 1, self._tier + tiers)
        self._good_samples = 0
        self._moderate_samples = 0
        self._last_change = now

    def resume(self, version, now):
        """A returned viewport can request one fresh frame, with a bounded rate.

        A UI that was hidden may have discarded both the original frame and
        its recovery probe. This explicit state transition restores liveness;
        it is not a periodic network retry and never skips writer backpressure.
        """
        if (type(version) is not int or version != STREAM_VERSION or not self.negotiated or
                self._last_resume is not None and now - self._last_resume < RESUME_INTERVAL_SECONDS):
            return False
        self._last_resume = now
        self._pending = None
        self._probe_used = False
        self._probe_ready = False
        return True

    def _observe_cycle(self, seconds, processing_seconds, now, *, transport=False):
        # Network jitter and CPU overload both call for smaller frames. A full
        # send-to-display cycle includes RTT; a transport write is not labelled
        # as RTT and has its own more conservative recovery threshold.
        slow = seconds > (0.25 if transport else 0.35) or processing_seconds > 0.08
        moderate = seconds > (0.12 if transport else 0.22)
        fast = seconds < (0.06 if transport else 0.14) and processing_seconds < 0.04
        if slow:
            self._reduce(now, 2 if seconds > 0.8 else 1)
        elif moderate:
            self._good_samples = 0
            self._moderate_samples += 1
            if self._moderate_samples >= 2:
                self._reduce(now)
        elif fast:
            self._moderate_samples = 0
            self._good_samples += 1
            if self._good_samples >= 12 and now - self._last_change >= 4.0:
                self._tier = max(0, self._tier - 1)
                self._good_samples = 0
                self._last_change = now
        else:
            self._good_samples = 0
            self._moderate_samples = 0

    def transport_busy(self, statistics, now):
        """Read only bounded local writer metrics; tolerate older Peer objects."""
        if not isinstance(statistics, Mapping):
            return False
        busy = statistics.get("pending") is True or statistics.get("writing") is True
        if busy:
            if self._busy_since is None:
                self._busy_since = now
            elif now - self._busy_since >= 0.4 and not self._busy_reduced:
                self._reduce(now)
                self._busy_reduced = True
        else:
            self._busy_since = None
            self._busy_reduced = False
        duration, size = statistics.get("lastFrameDuration"), statistics.get("lastFrameBytes")
        if (_number(duration) and 0 <= duration <= 300 and type(size) is int and size > 0):
            sample = (size, duration)
            if sample != self._transport_sample:
                self._transport_sample = sample
                if not self.negotiated:
                    self._observe_cycle(duration, 0.0, now, transport=True)
        return busy

    def ready(self, now, statistics=None):
        if self.transport_busy(statistics, now):
            return False
        if not self.negotiated or self._pending is None:
            return True
        if not self._probe_used and now - self._pending[1] >= ACK_PROBE_SECONDS:
            if not self._probe_ready:
                self._reduce(now)
                self._probe_ready = True
            return True
        return False

    def frame_fields(self, now):
        """Reserve before enqueueing: an immediate ACK cannot race this state."""
        fields = {"streamProfile": self.profile.name}
        if not self.negotiated:
            return fields
        if self._sequence >= MAX_FRAME_ID:
            # A session cannot realistically reach this limit. Refuse recycling
            # rather than accepting an old ACK against a reused identifier.
            raise OverflowError("Limite de quadros desta sessão atingido. Reconecte.")
        self._sequence += 1
        if self._pending is not None:
            self._probe_used = True
        self._probe_ready = False
        self._pending = (self._sequence, now)
        fields.update(streamVersion=STREAM_VERSION, frameId=self._sequence)
        return fields

    def feedback(self, message, now):
        frame_id = message.get("frameId")
        if (not self.negotiated or type(frame_id) is not int or
                not 1 <= frame_id <= MAX_FRAME_ID or self._pending is None or
                frame_id != self._pending[0]):
            return False
        elapsed = max(0.0, now - self._pending[1])
        processing = 0.0
        for key in ("decodeMs", "renderMs"):
            value = message.get(key)
            if _number(value) and 0 <= value <= 60_000:
                processing += value / 1000.0
        self._pending = None
        self._probe_used = False
        self._probe_ready = False
        self._observe_cycle(elapsed, processing, now)
        return True
