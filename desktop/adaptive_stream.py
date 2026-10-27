"""Bound screen latency without relying on clocks in two different machines.

The sender owns the timestamps. New controllers opt in to acknowledging an
actually presented frame; old controllers continue to receive ordinary JPEGs.
This class is deliberately independent of Tk, sockets and screen providers.
HostApp holds its control lock while using an instance.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
import math


STREAM_VERSION = 1
MAX_FRAME_ID = (1 << 53) - 1  # Also exactly representable in Android/JSON clients.
ACK_PROBE_SECONDS = 2.0
RESUME_INTERVAL_SECONDS = 1.0
BACKPRESSURE_POLL_SECONDS = 0.005
MAX_PIPELINED_FRAME_BYTES = 1024 * 1024
WINDOW_FAST_SAMPLES = 3
WINDOW_MAX_CYCLE_SECONDS = 0.25
WINDOW_FAST_CYCLE_SECONDS = 0.22
WINDOW_MAX_WRITE_SECONDS = 0.08
DOWNGRADE_COOLDOWN_SECONDS = 0.65
UPGRADE_MAX_WRITE_SECONDS = 0.12


@dataclass(frozen=True)
class StreamProfile:
    name: str
    width: int
    height: int
    quality: int
    fps: int
    subsampling: int = 2


# Start conservatively rather than filling a slow connection with a large first
# frame. Stable low-latency feedback can recover detail, one tier at a time.
PROFILES = (
    StreamProfile("detail", 1920, 1080, 76, 20, 0),
    StreamProfile("balanced", 1280, 720, 60, 12, 0),
    StreamProfile("economy", 960, 540, 46, 10),
    StreamProfile("low", 640, 360, 32, 6),
    StreamProfile("minimum", 480, 270, 25, 4),
)


STREAM_SETTINGS_VERSION = 1
STREAM_MODES = frozenset(("auto", "latency", "quality"))
LATENCY_PROFILES = (
    StreamProfile("detail", 1280, 720, 58, 24),
    StreamProfile("balanced", 960, 540, 48, 20),
    StreamProfile("economy", 800, 450, 40, 15),
    StreamProfile("low", 640, 360, 32, 10),
    StreamProfile("minimum", 480, 270, 25, 6),
)
QUALITY_PROFILES = (
    StreamProfile("detail", 1920, 1080, 86, 14, 0),
    StreamProfile("balanced", 1600, 900, 80, 10, 0),
    StreamProfile("economy", 1280, 720, 72, 8, 0),
    StreamProfile("low", 800, 450, 48, 6),
    StreamProfile("minimum", 480, 270, 32, 4),
)


@dataclass(frozen=True)
class StreamPolicy:
    profiles: tuple[StreamProfile, ...]
    frame_budget: int
    write_slow: float
    write_moderate: float
    write_fast: float
    upgrade_write: float
    window_write: float
    window_cycle: float
    processing_budget: float


# These are priorities, not promises of fixed resolution or zero network delay.
# Every mode still degrades automatically under real CPU/network pressure.
STREAM_POLICIES = {
    "auto": StreamPolicy(PROFILES, 1024 * 1024, .25, .12, .08, .12, .08, .22, .08),
    "latency": StreamPolicy(LATENCY_PROFILES, 384 * 1024, .16, .075, .045, .075, .05, .16, .06),
    "quality": StreamPolicy(QUALITY_PROFILES, 2 * 1024 * 1024, .40, .22, .14, .22, .10, .32, .12),
}


def _number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


class AdaptiveStream:
    """One display frame by default; at most two with explicitly negotiated ACKs.

    The probe is necessary if a frame was discarded before presentation (for
    example while the remote UI was paused). A missing ACK never starts an
    unlimited retry stream: after that single probe, only feedback or a local
    capture/consent generation change opens the window again.
    """

    def __init__(self):
        self.negotiated = False
        self._mode = "auto"
        self._tier = 1
        self._sequence = 0
        self._pending = []
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
        self._window_requested = False
        self._window_limit = 1
        self._window_fast_samples = 0
        self._last_reduction = None
        self._last_transport_duration = None
        self._minimum_cycle = None
        self._cycle_samples = deque(maxlen=24)

    @property
    def profile(self):
        return self.profiles[self._tier]

    @property
    def mode(self):
        return self._mode

    @property
    def policy(self):
        return STREAM_POLICIES[self._mode]

    @property
    def profiles(self):
        return self.policy.profiles

    def set_mode(self, version, mode, now):
        """Apply a priority without canceling any already reserved ACK id.

        The Host calls this only under its authenticated peer/control lock.
        Existing bytes/ACKs must finish normally; a mode switch cannot bypass
        backpressure, reset the single recovery probe or recycle identifiers.
        """
        if (type(version) is not int or version != STREAM_SETTINGS_VERSION
                or type(mode) is not str or mode not in STREAM_MODES
                or not _number(now)):
            return False
        if mode == self._mode:
            return True
        self._mode = mode
        self._window_limit = 1
        self._window_fast_samples = 0
        self._good_samples = self._moderate_samples = 0
        self._last_change = now
        self._last_reduction = None
        self._minimum_cycle = None
        self._cycle_samples.clear()
        self._transport_sample = None
        return True

    @property
    def outstanding_frame_id(self):
        return self._pending[-1][0] if self._pending else None

    @property
    def max_in_flight(self):
        return self._window_limit

    @property
    def outstanding_count(self):
        return len(self._pending)

    def synchronize(self, generation):
        if generation != self._generation:
            self._generation = generation
            self._pending = []
            self._probe_used = False
            self._probe_ready = False
            self._window_limit = 1
            self._window_fast_samples = 0
            # Frame ids are never recycled across a pause or monitor change.

    def enable(self, version, max_in_flight=None, ack_cumulative=False):
        if type(version) is not int or version != STREAM_VERSION:
            return False
        if not self.negotiated:
            self.negotiated = True
            self._window_requested = (type(max_in_flight) is int and max_in_flight == 2
                                      and ack_cumulative is True)
            self._pending = []
            self._probe_used = False
            self._probe_ready = False
        return True

    def _reduce(self, now, tiers=1):
        # Hysteresis prevents a short burst from jumping through every tier.
        # Downshifts are one tier at a time; recovery needs sustained feedback.
        self._window_limit = 1
        self._window_fast_samples = 0
        if self._last_reduction is not None and now - self._last_reduction < DOWNGRADE_COOLDOWN_SECONDS:
            self._good_samples = 0
            self._moderate_samples = 0
            return
        self._last_reduction = now
        self._tier = min(len(self.profiles) - 1, self._tier + 1)
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
        self._window_limit = 1
        self._window_fast_samples = 0
        self._pending = []
        self._probe_used = False
        self._probe_ready = False
        return True

    def _observe_cycle(self, seconds, processing_seconds, now, *, transport=False):
        # Network jitter and CPU overload both call for smaller frames. A full
        # send-to-display cycle includes RTT; a transport write is not labelled
        # as RTT and has its own more conservative recovery threshold.
        policy = self.policy
        processing_fast = min(.04, policy.processing_budget / 2)
        if not transport and self._last_transport_duration is not None:
            # A stable long RTT is not evidence of a low-bandwidth link. Use
            # actual writer pressure and delay above the best observed cycle,
            # while a slow writer/decoder remains an absolute downshift signal.
            baseline = self._minimum_cycle if self._minimum_cycle is not None else seconds
            excess = max(0.0, seconds - baseline)
            write = self._last_transport_duration
            slow = write > policy.write_slow or excess > policy.write_slow or processing_seconds > policy.processing_budget
            moderate = write > policy.write_moderate or excess > policy.write_moderate
            fast = write < policy.write_fast and excess < policy.write_fast and processing_seconds < processing_fast
        else:
            # Older Peer implementations have no writer timing. Keep their
            # original conservative ACK/write-based adaptation.
            slow = seconds > (policy.write_slow if transport else policy.write_slow + .10) or processing_seconds > policy.processing_budget
            moderate = seconds > (policy.write_moderate if transport else policy.write_moderate + .10)
            fast = seconds < (policy.write_fast * .75 if transport else policy.write_fast + .06) and processing_seconds < processing_fast
        if slow:
            self._reduce(now)
        elif moderate:
            self._good_samples = 0
            self._moderate_samples += 1
            if self._moderate_samples >= 2:
                self._reduce(now)
        elif fast:
            self._moderate_samples = 0
            self._good_samples += 1
            if (self._good_samples >= 12 and now - self._last_change >= 4.0
                    and self._upgrade_fits_write_budget()):
                self._tier = max(0, self._tier - 1)
                self._good_samples = 0
                self._last_change = now
        else:
            self._good_samples = 0
            self._moderate_samples = 0

    def _upgrade_fits_write_budget(self):
        if self._tier == 0 or self._last_transport_duration is None:
            return True
        current, higher = self.profiles[self._tier], self.profiles[self._tier - 1]
        # A deliberately conservative projection, not a speed-test result:
        # account for extra pixels, JPEG quality and a move from 4:2:0 to 4:4:4.
        growth = (higher.width * higher.height) / (current.width * current.height)
        growth *= higher.quality / current.quality
        if higher.subsampling == 0 and current.subsampling != 0:
            growth *= 1.3
        return self._last_transport_duration * growth <= self.policy.upgrade_write

    def content_limited(self, profile, now):
        """Keep the next capture at a tier already required by image complexity."""
        index = self.profiles.index(profile)
        if index > self._tier:
            self._tier = index
            self._last_change = now
            self._last_reduction = now
            self._good_samples = self._moderate_samples = 0
            self._window_limit = 1
            self._window_fast_samples = 0

    def capture_finished(self, seconds, now):
        """Bound local capture/resize/JPEG CPU pressure without remote clocks."""
        if _number(seconds) and seconds > self.policy.processing_budget:
            self._reduce(now)

    def transport_busy(self, statistics, now):
        """Read only bounded local writer metrics; tolerate older Peer objects."""
        if not isinstance(statistics, Mapping):
            return False
        busy = statistics.get("pending") is True or statistics.get("writing") is True
        if busy:
            if self._busy_since is None:
                self._busy_since = now
            elif now - self._busy_since >= self.policy.window_cycle - .07:
                self._window_limit = 1
                self._window_fast_samples = 0
            if now - self._busy_since >= self.policy.write_slow + .15 and not self._busy_reduced:
                self._reduce(now)
                self._busy_reduced = True
        else:
            self._busy_since = None
            self._busy_reduced = False
        duration, size = statistics.get("lastFrameDuration"), statistics.get("lastFrameBytes")
        if (_number(duration) and 0 <= duration <= 300 and type(size) is int and size > 0):
            self._last_transport_duration = duration
            if duration > self.policy.window_write:
                self._window_limit = 1
                self._window_fast_samples = 0
            sample = (size, duration)
            if sample != self._transport_sample:
                self._transport_sample = sample
                if not self.negotiated:
                    self._observe_cycle(duration, 0.0, now, transport=True)
        return busy

    def ready(self, now, statistics=None):
        if self.transport_busy(statistics, now):
            return False
        if (self._window_limit > 1 and self._pending
                and now - self._pending[0][1] >= self.policy.window_cycle + .03):
            self._window_limit = 1
            self._window_fast_samples = 0
        if not self.negotiated or len(self._pending) < self._window_limit:
            return True
        if not self._probe_used and now - self._pending[0][1] >= ACK_PROBE_SECONDS:
            if not self._probe_ready:
                self._reduce(now)
                self._probe_ready = True
            return True
        return False

    def frame_fields(self, now, frame_bytes=None):
        """Reserve before enqueueing, keeping a bounded list of known ACK ids.

        ``frame_bytes`` is the encoded transport-frame size, not a claim about
        total internet capacity. A large frame never opens the two-frame path.
        A single timeout probe retires old ids, just as in the one-frame mode.
        """
        fields = {"streamProfile": self.profile.name,
                  "streamSettingsVersion": STREAM_SETTINGS_VERSION, "streamMode": self._mode}
        if not self.negotiated:
            return fields
        size = frame_bytes if type(frame_bytes) is int and frame_bytes > 0 else None
        if size is not None and size > MAX_PIPELINED_FRAME_BYTES:
            self._window_limit = 1
            self._window_fast_samples = 0
        if self._pending and not self._probe_ready and len(self._pending) >= self._window_limit:
            # The freshly encoded image may be larger than previous samples.
            # Drop this candidate instead of placing a second large frame on
            # the wire or treating ordinary backpressure as capture failure.
            return None
        if self._sequence >= MAX_FRAME_ID:
            raise OverflowError("Limite de quadros desta sessão atingido. Reconecte.")
        if self._pending and (self._probe_ready or len(self._pending) >= self._window_limit):
            # ready() grants this replacement only for the bounded recovery
            # probe. Ordinary callers cannot accumulate unbounded reservations.
            if not self._probe_ready:
                raise RuntimeError("Aguardando apresentação do quadro anterior.")
            self._probe_used = True
            self._pending = []
        self._probe_ready = False
        self._sequence += 1
        self._pending.append((self._sequence, now, size))
        fields.update(streamVersion=STREAM_VERSION, frameId=self._sequence)
        return fields

    def feedback(self, message, now):
        frame_id = message.get("frameId")
        if (not self.negotiated or type(frame_id) is not int
                or not 1 <= frame_id <= MAX_FRAME_ID):
            return False
        sample = next((row for row in self._pending if row[0] == frame_id), None)
        if sample is None:
            return False
        elapsed = max(0.0, now - sample[1])
        processing = 0.0
        for key in ("decodeMs", "renderMs"):
            value = message.get(key)
            if _number(value) and 0 <= value <= 60_000:
                processing += value / 1000.0
        if self._window_requested and message.get("cumulative") is True:
            self._pending = [row for row in self._pending if row[0] > frame_id]
        else:
            self._pending.remove(sample)
        self._probe_used = False
        self._probe_ready = False
        self._cycle_samples.append(elapsed)
        self._minimum_cycle = min(self._cycle_samples)
        self._observe_cycle(elapsed, processing, now)
        if (self._window_requested and elapsed < self.policy.window_cycle and processing < min(.04, self.policy.processing_budget / 2)
                and self._last_transport_duration is not None
                and self._last_transport_duration <= self.policy.window_write
                and sample[2] is not None and sample[2] <= MAX_PIPELINED_FRAME_BYTES):
            self._window_fast_samples += 1
            if self._window_fast_samples >= WINDOW_FAST_SAMPLES:
                self._window_limit = 2
        else:
            self._window_limit = 1
            self._window_fast_samples = 0
        return True


def capture_rgb_image(shot, image_module):
    """Use Pillow's C BGRX decoder instead of MSS's Python RGB slicing.

    MSS exposes BGRA bytes, while JPEG needs RGB. The raw decoder drops only
    the unused alpha byte and preserves every RGB sample. Providers without
    BGRA retain the existing RGB path (including older embedders/test doubles).
    """
    bgra = getattr(shot, "bgra", None)
    if bgra is not None:
        if len(bgra) != shot.size[0] * shot.size[1] * 4:
            raise ValueError("Buffer de captura BGRA incompleto.")
        return image_module.frombytes("RGB", shot.size, bgra, "raw", "BGRX")
    return image_module.frombytes("RGB", shot.size, shot.rgb)


def encode_screen_image(image, profile, image_module, mode="auto"):
    """Encode before enqueueing; high-entropy images cannot create huge writes.

    Ordinary desktop text stays at the selected quality. If its encoded packet
    would exceed the selected mode's image budget, lower one profile at a time before any
    bytes are sent. The returned profile and image dimensions describe the
    actual JPEG, so input geometry and the automatic-quality label stay true.
    """
    if type(mode) is not str or mode not in STREAM_MODES:
        raise ValueError("Prioridade de imagem inválida.")
    policy = STREAM_POLICIES[mode]
    for candidate in policy.profiles[policy.profiles.index(profile):]:
        resampling = (getattr(image_module.Resampling, "BICUBIC", image_module.Resampling.LANCZOS)
                      if mode == "quality" or mode == "auto" and candidate.name == "detail" else
                      getattr(image_module.Resampling, "BILINEAR", image_module.Resampling.LANCZOS))
        image.thumbnail((candidate.width, candidate.height), resampling)
        with BytesIO() as out:
            image.save(out, format="JPEG", quality=candidate.quality,
                       subsampling=candidate.subsampling, optimize=False)
            data = out.getvalue()
        if 4 * ((len(data) + 2) // 3) + 32 * 1024 <= policy.frame_budget:
            return data, candidate
    raise ValueError("Imagem excede o orçamento de transmissão.")
