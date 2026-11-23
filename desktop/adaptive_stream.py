"""Bound screen latency without relying on clocks in two different machines.

The sender owns the timestamps. New controllers opt in to acknowledging an
actually presented frame; old controllers continue to receive ordinary JPEGs.
This class is deliberately independent of Tk, sockets and screen providers.
HostApp holds its control lock while using an instance.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, replace
from io import BytesIO
import hashlib
import math
import struct
import time
import zlib


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
UNCHANGED_REFRESH_SECONDS = .5
MAX_LOSSLESS_DIMENSION = 3840
MAX_LOSSLESS_PIXELS = 3840 * 2160
MAX_REFINEMENT_FRAME_BYTES = 3 * 1024 * 1024
STATIC_REFINEMENT_SECONDS = .35
UNCHANGED_HEARTBEAT_SECONDS = 2.0
IDLE_CAPTURE_FPS = 5
IDLE_CAPTURE_SECONDS = 1.0
MOVING_PNG_SECONDS = .06


@dataclass(frozen=True)
class StreamProfile:
    name: str
    width: int
    height: int
    quality: int
    fps: int
    subsampling: int = 2


# Start at Full HD, preserving ordinary desktop text before a user opens the
# settings. Writer/presentation feedback still lowers the budget on slow links.
PROFILES = (
    StreamProfile("detail", 2560, 1440, 90, 15, 0),
    StreamProfile("balanced", 1920, 1080, 84, 12, 0),
    StreamProfile("economy", 1280, 720, 72, 8),
    StreamProfile("low", 800, 450, 48, 5),
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
    StreamProfile("detail", 3840, 2160, 94, 10, 0),
    StreamProfile("balanced", 2560, 1440, 92, 8, 0),
    StreamProfile("economy", 1920, 1080, 88, 6, 0),
    StreamProfile("low", 1280, 720, 80, 4, 0),
    StreamProfile("minimum", 480, 270, 35, 2),
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
    "quality": StreamPolicy(QUALITY_PROFILES, 2 * 1024 * 1024, .40, .22, .14, .22, .10, .32, .20),
}

# Existing JPEG-only peers keep their original image budgets and exact bytes.
# PNG-capable receivers use a fluent preview and an independent native-detail
# refinement. Large moving frames must become cheaper under pressure rather
# than retaining 4K at two frames/second. Text keeps full chroma in every tier.
READABLE_PROFILES = {
    "auto": (
        StreamProfile("detail", 3840, 2160, 94, 30, 0),
        StreamProfile("balanced", 2560, 1440, 90, 24, 0),
        StreamProfile("economy", 1920, 1080, 86, 24, 0),
        StreamProfile("low", 1280, 720, 80, 20, 0),
        StreamProfile("minimum", 960, 540, 74, 15, 0),
    ),
    "quality": (
        StreamProfile("detail", 3840, 2160, 98, 24, 0),
        StreamProfile("balanced", 2560, 1440, 95, 24, 0),
        StreamProfile("economy", 1920, 1080, 94, 20, 0),
        StreamProfile("low", 1600, 900, 92, 18, 0),
        StreamProfile("minimum", 1280, 720, 84, 16, 0),
    ),
}
READABLE_STREAM_POLICIES = {
    mode: replace(STREAM_POLICIES[mode], profiles=profiles)
    for mode, profiles in READABLE_PROFILES.items()
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
        self._last_transport_bytes = None
        self._last_present_cycle = None
        self._last_present_bytes = None
        self._last_present_processing = None
        self._last_presented_frame_id = None
        self._last_feedback_at = None
        self._minimum_cycle = None
        self._cycle_samples = deque(maxlen=24)
        self._delivery_samples = deque(maxlen=8)
        self._quality_cadence = 0
        self.png_enabled = False
        self.unchanged_enabled = False
        self.content_revision = 0
        self._preview_budget = None
        self._preview_budget_feedback = None
        self._preview_budget_history = deque(maxlen=3)

    @property
    def native_png_budget(self):
        """Measured spare writer capacity, expressed as complete wire bytes.

        Never extrapolate from RTT, a user-entered speed or a nominal network
        label. The conservative writer target is half the mode's fast-write
        limit. Actual presentation ACKs must independently demonstrate room: a
        fast socket buffer alone cannot authorize a larger native picture.
        A PNG still obeys the mode budget, and deep CPU/probe/low-tier pressure
        disables native opportunism completely.
        """
        duration = self._last_transport_duration
        if (not self.png_enabled or self._mode == "latency" or self._tier > 1
                or self._quality_cadence >= 2 or self._probe_ready or duration is None
                or self._last_transport_bytes is None or duration >= self.policy.write_fast * .5
                or self._last_present_cycle is None or self._last_present_bytes is None
                or self._last_present_cycle > self.policy.window_cycle):
            return 0
        target = self.policy.write_fast * .5
        return min(self.policy.frame_budget,
                   int(self._last_transport_bytes * target / max(.001, duration)),
                   int(self._last_present_bytes * self.policy.window_cycle / max(.001, self._last_present_cycle)))

    @property
    def refinement_png_budget(self):
        """A complete, one-shot static frame budget backed by real ACKs.

        A kernel write alone never proves internet capacity. Both the measured
        send-to-presentation cycle and the actual writer duration must fit the
        projected budget. A stable RTT is included, making this projection
        deliberately conservative. The larger cap is never a moving-preview
        budget and may only be used with no pending frame and a stable scene.
        """
        if (not self.png_enabled or self._mode == "latency" or self._pending
                or self._probe_ready or self._last_present_cycle is None
                or self._last_present_bytes is None or self._last_transport_duration is None
                or self._last_transport_bytes is None or self._last_present_processing is None
                or self._last_present_processing > .10):
            return 0
        seconds = .75 if self._mode == "quality" else .45
        cycle = self._last_present_cycle
        if self._stable_rtt_evidence():
            cycle = max(self._last_present_processing,
                        cycle - (self._minimum_cycle or 0) + .02)
        return min(MAX_REFINEMENT_FRAME_BYTES,
                   int(self._last_present_bytes * seconds / max(.001, cycle)),
                   int(self._last_transport_bytes * seconds / max(.001, self._last_transport_duration)))

    def _stable_rtt_evidence(self):
        """Distinct delivered sizes with stable cycles establish an RTT floor.

        Identical tiny packets and fast socket buffering alone are insufficient.
        Slow serialization varies with payload size and retains the conservative
        full-cycle projection until this independent evidence is available.
        """
        samples = [row for row in self._delivery_samples if row[0] is not None and row[2] is not None]
        if len(samples) < 3:
            return False
        sizes = [row[0] for row in samples]
        cycles = [row[1] for row in samples]
        writes = [row[2] for row in samples]
        return (max(sizes) >= min(sizes) * 1.5 and max(writes) <= self.policy.write_fast
                and max(cycles) - min(cycles) <= min(.05, min(cycles) * .15))

    @property
    def recovery_probe_ready(self):
        return self._probe_ready

    @property
    def last_presented_frame_id(self):
        return self._last_presented_frame_id

    def observed_capacity_fresh(self, now):
        return (self._last_feedback_at is not None and _number(now)
                and 0 <= now - self._last_feedback_at <= 2.0)

    def fresh_refinement_png_budget(self, now):
        return self.refinement_png_budget if self.observed_capacity_fresh(now) else 0

    def preview_wire_budget(self, now):
        """Bound a moving frame by actual delivery, including the first frame.

        Begin conservatively rather than queueing a1–2MiB screenshot on an
        unknown phone/link. Positive capacity can grow at most twice per ACK;
        a slow measured writer or receiver contracts the next preview at once.
        The separate settled-detail budget is never used for animations.
        """
        if not self.png_enabled or self._mode not in READABLE_STREAM_POLICIES:
            return self.policy.frame_budget
        initial = (128 if self._mode == "auto" else 192) * 1024
        if (not self.observed_capacity_fresh(now) or self._last_present_bytes is None
                or self._last_transport_bytes is None or self._last_transport_duration is None
                or self._last_present_cycle is None):
            self._preview_budget = initial
            self._preview_budget_feedback = None
            self._preview_budget_history.clear()
            return initial
        target = .15 if self._mode == "auto" else .22
        if self._preview_budget_feedback != self._last_presented_frame_id:
            cycle = self._last_present_cycle
            # A proven fast ACK may include stable network RTT. Keep20ms of
            # overhead and all decoder/queue excess; a slow first ACK cannot
            # use that subtraction to claim bandwidth from a socket buffer.
            if (cycle <= self.policy.window_cycle or self._stable_rtt_evidence()) and self._last_present_processing is not None:
                cycle = max(self._last_present_processing,
                            cycle - (self._minimum_cycle or 0) + min(.02, self._minimum_cycle or 0))
            acknowledged_capacity = int(self._last_present_bytes * target / max(.001, cycle))
            samples = list(self._delivery_samples)[-3:]
            if (len(samples) == 3 and all(row[1] <= .08 and row[2] is not None and row[2] <= .01 for row in samples)
                    and self._last_present_processing is not None and self._last_present_processing <= .02
                    and not self._pending and not self._probe_ready):
                # Three complete fast presentations may earn one bounded probe.
                # Otherwise a very compressible tiny preview could never prove
                # capacity for its sharper successor. Serialize this probe and
                # let its actual write/ACK decide the following budget.
                acknowledged_capacity = max(acknowledged_capacity, (self._preview_budget or initial) * 2)
                self._window_limit = 1
                self._window_fast_samples = 0
            self._preview_budget = max(24 * 1024, min(self.policy.frame_budget,
                (self._preview_budget or initial) * 2,
                int(self._last_transport_bytes * target / max(.001, self._last_transport_duration)),
                acknowledged_capacity))
            self._preview_budget_feedback = self._last_presented_frame_id
            self._preview_budget_history.append(self._preview_budget)
        return self._preview_budget

    def can_retain_preview(self, source_size, encoded_size):
        """Let a static photo finish learning capacity before freezing JPEG."""
        if len(self._delivery_samples) < 3:
            return False
        width, height = source_size
        scale = min(1.0, 1920 / width, 1080 / height,
                    self.profile.width / width, self.profile.height / height)
        if encoded_size[0] >= int(width * scale) and encoded_size[1] >= int(height * scale):
            return True
        history = self._preview_budget_history
        return (len(history) == 3 and max(history) - min(history) <= max(history) * .10)

    @property
    def profile(self):
        return self.profiles[self._tier]

    @property
    def mode(self):
        return self._mode

    @property
    def policy(self):
        if self.png_enabled and self._mode in READABLE_STREAM_POLICIES:
            return READABLE_STREAM_POLICIES[self._mode]
        return STREAM_POLICIES[self._mode]

    @property
    def profiles(self):
        return self.policy.profiles

    @property
    def target_fps(self):
        """Reduce capture frequency before readable pixels under CPU pressure."""
        floor = 6 if self.png_enabled and self._mode in READABLE_STREAM_POLICIES else 2
        return max(floor, self.profile.fps // (1 << self._quality_cadence))

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
        self._last_present_cycle = self._last_present_bytes = self._last_present_processing = None
        self._last_presented_frame_id = self._last_feedback_at = None
        self.content_revision += 1
        self._window_limit = 1
        self._window_fast_samples = 0
        self._good_samples = self._moderate_samples = 0
        self._last_change = now
        self._last_reduction = None
        self._minimum_cycle = None
        self._cycle_samples.clear()
        self._delivery_samples.clear()
        self._preview_budget = self._preview_budget_feedback = None
        self._preview_budget_history.clear()
        self._transport_sample = None
        self._quality_cadence = 0
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
            self._last_present_cycle = self._last_present_bytes = self._last_present_processing = None
            self._last_presented_frame_id = self._last_feedback_at = None
            self.content_revision += 1
            self._pending = []
            self._probe_used = False
            self._probe_ready = False
            self._window_limit = 1
            self._window_fast_samples = 0
            self._delivery_samples.clear()
            self._cycle_samples.clear()
            self._minimum_cycle = None
            self._preview_budget = self._preview_budget_feedback = None
            self._preview_budget_history.clear()
            # Frame ids are never recycled across a pause or monitor change.

    def enable(self, version, max_in_flight=None, ack_cumulative=False, image_formats=None, unchanged_frames=False):
        if type(version) is not int or version != STREAM_VERSION:
            return False
        if not self.negotiated:
            self.negotiated = True
            self.png_enabled = (type(image_formats) is list and 1 <= len(image_formats) <= 4
                                and all(type(value) is str for value in image_formats)
                                and "jpeg" in image_formats and "png" in image_formats)
            self.unchanged_enabled = self.png_enabled and unchanged_frames is True
            self.content_revision += 1
            self._window_requested = (type(max_in_flight) is int and max_in_flight == 2
                                      and ack_cumulative is True)
            self._pending = []
            self._probe_used = False
            self._probe_ready = False
        return True

    def _reduce(self, now, tiers=1, *, force_resolution=False, prefer_cadence=False):
        # Hysteresis prevents a short burst from jumping through every tier.
        # Downshifts are one tier at a time; recovery needs sustained feedback.
        self._window_limit = 1
        self._window_fast_samples = 0
        if self._last_reduction is not None and now - self._last_reduction < DOWNGRADE_COOLDOWN_SECONDS:
            self._good_samples = 0
            self._moderate_samples = 0
            return
        self._last_reduction = now
        if self.png_enabled and self._mode in READABLE_STREAM_POLICIES:
            if self._tier < len(self.profiles) - 1:
                self._tier += 1
                self._quality_cadence = 0
            elif self.target_fps > 6:
                self._quality_cadence += 1
        elif ((self._mode == "quality" or prefer_cadence) and not force_resolution
                and self._quality_cadence < 2 and self.target_fps > 2):
            self._quality_cadence += 1
        else:
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
        self._last_present_cycle = self._last_present_bytes = self._last_present_processing = None
        self._last_presented_frame_id = self._last_feedback_at = None
        self.content_revision += 1
        self._window_limit = 1
        self._window_fast_samples = 0
        self._pending = []
        self._probe_used = False
        self._probe_ready = False
        self._delivery_samples.clear()
        self._cycle_samples.clear()
        self._minimum_cycle = None
        self._preview_budget = self._preview_budget_feedback = None
        self._preview_budget_history.clear()
        return True

    def _observe_cycle(self, seconds, processing_seconds, now, *, transport=False):
        # Network jitter and CPU overload both call for smaller frames. A full
        # send-to-display cycle includes RTT; a transport write is not labelled
        # as RTT and has its own more conservative recovery threshold.
        policy = self.policy
        processing_budget = policy.processing_budget
        if self._mode in ("auto", "quality"):
            processing_budget = max(processing_budget, .75 / self.target_fps)
        processing_fast = min(.10 if self._mode == "quality" else .04, policy.processing_budget / 2)
        if not transport and self._last_transport_duration is not None:
            # A stable long RTT is not evidence of a low-bandwidth link. Use
            # actual writer pressure and delay above the best observed cycle,
            # while a slow writer/decoder remains an absolute downshift signal.
            baseline = self._minimum_cycle if self._minimum_cycle is not None else seconds
            excess = max(0.0, seconds - baseline)
            write = self._last_transport_duration
            pressure_seconds = max(write, excess)
            slow = write > policy.write_slow or excess > policy.write_slow or processing_seconds > processing_budget
            moderate = write > policy.write_moderate or excess > policy.write_moderate
            fast = write < policy.write_fast and excess < policy.write_fast and processing_seconds < processing_fast
        else:
            # Older Peer implementations have no writer timing. Keep their
            # original conservative ACK/write-based adaptation.
            pressure_seconds = seconds
            slow = seconds > (policy.write_slow if transport else policy.write_slow + .10) or processing_seconds > processing_budget
            moderate = seconds > (policy.write_moderate if transport else policy.write_moderate + .10)
            fast = seconds < (policy.write_fast * .75 if transport else policy.write_fast + .06) and processing_seconds < processing_fast
        if slow:
            # A frame taking over a second still needs fewer pixels, even in
            # Quality. Ordinary pressure first reduces capture frequency.
            self._reduce(now, force_resolution=pressure_seconds > 1.0,
                         prefer_cadence=self._mode == "auto" and not moderate)
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
                if self.png_enabled and self._quality_cadence:
                    self._quality_cadence -= 1
                elif self._tier > 0:
                    self._tier -= 1
                elif self._quality_cadence:
                    self._quality_cadence -= 1
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

    def capture_finished(self, seconds, now, *, refinement=False):
        """Bound local capture/resize/JPEG CPU pressure without remote clocks."""
        budget = self.policy.processing_budget
        if self.png_enabled and self._mode in READABLE_STREAM_POLICIES:
            # A rare settled-detail encode must not throttle the motion path.
            if refinement:
                return
            budget = .08 if self._mode == "quality" else .06
        elif self._mode in ("auto", "quality"):
            budget = max(budget, 1.25 / self.target_fps)
        if _number(seconds) and seconds > budget:
            self._reduce(now, force_resolution=seconds > 1.0,
                         prefer_cadence=self._mode == "auto")

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
                self._reduce(now, force_resolution=True)
                self._busy_reduced = True
        else:
            self._busy_since = None
            self._busy_reduced = False
        duration, size = statistics.get("lastFrameDuration"), statistics.get("lastFrameBytes")
        if (_number(duration) and 0 <= duration <= 300 and type(size) is int and size > 0):
            self._last_transport_duration = duration
            self._last_transport_bytes = size
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
                self._reduce(now, force_resolution=True)
                self._probe_ready = True
            return True
        return False

    def frame_fields(self, now, frame_bytes=None, *, refinement=False):
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
        if refinement:
            # A final detail image never shares an in-flight window, even
            # when smaller previews previously earned the two-frame path.
            self._window_limit = 1
            self._window_fast_samples = 0
            if self._pending or size is None or size > self.fresh_refinement_png_budget(now):
                return None
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
        self._last_present_cycle = elapsed
        self._last_present_bytes = sample[2]
        self._last_present_processing = processing
        self._last_presented_frame_id = max(frame_id, self._last_presented_frame_id or 0)
        self._last_feedback_at = now
        self._cycle_samples.append(elapsed)
        self._delivery_samples.append((sample[2], elapsed, self._last_transport_duration))
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
    bgra = getattr(shot, "raw", None)
    if bgra is None:
        bgra = getattr(shot, "bgra", None)
    if bgra is not None:
        if len(bgra) != shot.size[0] * shot.size[1] * 4:
            raise ValueError("Buffer de captura BGRA incompleto.")
        return image_module.frombytes("RGB", shot.size, bgra, "raw", "BGRX")
    return image_module.frombytes("RGB", shot.size, shot.rgb)


class ScreenContentTracker:
    """Keep exact scene/fidelity digests, never a second desktop buffer.

    A candidate becomes remembered only after enqueueing succeeds. Refreshes
    JPEG previews refresh to collect capacity evidence. A native lossless image
    stays on the receiver until its pixels or capture context actually change:
    a slower ACK must not turn the same text back into a blurry preview. Peer
    negotiated unchanged-screen pulses maintain viewport health without a
    second PNG. An explicit recovery probe or resume can request a fresh image.
    """
    def __init__(self):
        self._published = None
        self._published_at = 0.0
        self._published_lossless = False
        self._published_retained = False
        self._observed = None
        self._changed_at = 0.0
        self._attempted = None
        self._attempt_budget = 0
        self._attempted_at = 0.0
        self._published_frame_id = None
        self._published_wire_bytes = None
        self._heartbeat_at = 0.0
        self._native_backoff_until = 0.0
        self._photo_sample = None

    def candidate(self, image, context, now, *, force=False, refinement_budget=0, pixels=None, bytes_per_pixel=3, fingerprint=None):
        # Exact row-band digests retain no desktop buffer. With MSS the caller
        # supplies its already-owned BGRX bytes, avoiding another 24MiB RGB copy.
        # Hash every byte: the tiny sample is only a conservative small-change
        # guard and never decides whether an image changed.
        pixels = memoryview(image.tobytes() if pixels is None else pixels)
        bands = self._bands(pixels, image.size, bytes_per_pixel) if fingerprint is None else fingerprint
        from PIL import Image
        sample = image.resize((min(64, image.width), min(36, image.height)), Image.Resampling.NEAREST)
        try:
            signature = (context, image.size, bands, sample.tobytes())
        finally:
            sample.close()
        if signature != self._observed:
            self._observed = signature
            self._changed_at = now
            self._attempted = None
            self._attempt_budget = 0
        if not force and signature == self._published:
            if self._published_lossless or self._published_retained:
                return None
            # A blinking caret commonly changes every half-second. Do not
            # defer the .35s detail opportunity until the .5s preview refresh.
            if (now - self._published_at < UNCHANGED_REFRESH_SECONDS
                    and not self._refinement_due(signature, refinement_budget, now)):
                return None
        return signature

    @staticmethod
    def _bands(pixels, size, bytes_per_pixel):
        pixels = memoryview(pixels)
        band_bytes = size[0] * bytes_per_pixel * 64
        return tuple(hashlib.sha256(pixels[start:start + band_bytes]).digest()
                     for start in range(0, len(pixels), band_bytes))

    def retained_capture_matches(self, pixels, size, context, now=None, refinement_budget=0, fingerprint_out=None):
        """Check an MSS capture before allocating/converting another RGB image."""
        keep = (self._published_lossless or self._published_retained or now is not None
                and now - self._published_at < UNCHANGED_REFRESH_SECONDS
                and not self._refinement_due(self._published, refinement_budget, now))
        if pixels is None or not keep or self._published is None or self._published[:2] != (context, size):
            return False
        bands = self._bands(pixels, size, 4)
        if fingerprint_out is not None:
            fingerprint_out.append(bands)
        return bands == self._published[2]

    def photo_candidate(self, image, context, now, *, force=False, pixels=None, bytes_per_pixel=3, fingerprint=None):
        """Avoid hashing moving photos; verify every byte once samples settle.

        The sample can only request more work. It never suppresses a change:
        two matching samples trigger the exact digest before a JPEG is retained.
        """
        from PIL import Image
        sample = image.resize((min(64, image.width), min(36, image.height)), Image.Resampling.NEAREST)
        try:
            key = (context, image.size, sample.tobytes())
        finally:
            sample.close()
        if key != self._photo_sample:
            self._photo_sample = key
            self._changed_at = now
            return None, True
        signature = self.candidate(image, context, now, force=force, pixels=pixels, bytes_per_pixel=bytes_per_pixel, fingerprint=fingerprint)
        return signature, signature is not None

    def _refinement_due(self, signature, wire_budget, now):
        if (signature is None or signature != self._observed or wire_budget <= 32 * 1024
                or now - self._changed_at < STATIC_REFINEMENT_SECONDS):
            return False
        if (self._attempted == signature and wire_budget < self._attempt_budget * 1.25
                and now - self._attempted_at < 5.0):
            return False
        return True

    def refinement_due(self, signature, wire_budget, now):
        if not self._refinement_due(signature, wire_budget, now):
            return False
        self._attempted, self._attempt_budget, self._attempted_at = signature, wire_budget, now
        return True

    def carry_native(self, signature, wire_budget):
        return (self._published_lossless and self._published is not None and signature is not None
                and signature[:2] == self._published[:2] and self._published_wire_bytes is not None
                and wire_budget >= self._published_wire_bytes
                and signature[3] == self._published[3]
                and sum(a != b for a, b in zip(signature[2], self._published[2])) <= 1)

    def moving_png_budget(self, budget, now):
        return budget if now >= self._native_backoff_until else 0

    def moving_png_failed(self, now):
        # A scene that does not fit must not pay for the same speculative PNG
        # at every animation tick. Idle refinement has its own bounded retry.
        self._native_backoff_until = now + 2.0

    def capture_fps(self, fps, now, *, active_at=None):
        if (self._published is not None and self._observed == self._published
                and now - self._changed_at >= IDLE_CAPTURE_SECONDS
                and not (active_at is not None and 0 <= now - active_at < IDLE_CAPTURE_SECONDS)):
            return min(fps, IDLE_CAPTURE_FPS)
        return fps

    @property
    def published_frame_id(self):
        return self._published_frame_id

    def unchanged_frame_id(self, now):
        if ((self._published_lossless or self._published_retained) and self._published_frame_id is not None
                and now - self._heartbeat_at >= UNCHANGED_HEARTBEAT_SECONDS):
            return self._published_frame_id
        return None

    def heartbeat_sent(self, now):
        self._heartbeat_at = now

    def published(self, signature, now, *, lossless_native=False, retained=False, frame_id=None, wire_bytes=None):
        self._published = signature
        self._published_at = now
        self._published_lossless = lossless_native is True
        self._published_retained = retained is True
        self._published_frame_id = frame_id
        self._published_wire_bytes = wire_bytes
        self._heartbeat_at = now
        if signature is None:
            self._observed = None


def screen_text_candidate(image, image_module):
    """Bounded C-side sample; avoid full PNG compression for photos/noise."""
    width, height = image.size
    sample = image.resize((min(128, width), min(72, height)), image_module.Resampling.NEAREST)
    try:
        # Stop in Pillow as soon as a photo exceeds this color bound. Asking
        # for all 9,216 colors and sorting them costs more than a JPEG on some
        # CPUs. A second tiny sample still recognizes a text window alongside
        # a small photo without paying for a full-color histogram.
        colors = sample.getcolors(512)
        color_pixels = sample.width * sample.height
        if colors is None:
            tiny = sample.resize((min(32, width), min(18, height)), image_module.Resampling.NEAREST)
            try:
                color_pixels = tiny.width * tiny.height
                colors = tiny.getcolors(color_pixels)
            finally:
                tiny.close()
            if colors is None:
                return False
        if len(colors) <= 32:
            return True
        # Text windows can coexist with a small image. Only try lossless when
        # a few repeated colors still occupy most sampled pixels.
        return sum(count for count, _ in sorted(colors, reverse=True)[:16]) >= color_pixels * .75
    finally:
        sample.close()


def _fast_rgb_png(image, byte_limit, *, compression_level=3, max_seconds=None):
    """Standard 8-bit RGB PNG, filter None and bounded zlib level 3.

    Desktop text compresses well without Pillow's per-row adaptive filtering.
    Avoiding that extra scan makes a native frame cheaper than repeatedly
    resizing it. PNG CRCs and the zlib checksum are emitted by stdlib code.
    Return None as soon as compressed output cannot fit the wire budget.
    """
    width, height = image.size
    if image.mode != "RGB" or max(width, height) > MAX_LOSSLESS_DIMENSION or width * height > MAX_LOSSLESS_PIXELS:
        return None
    started = time.perf_counter() if max_seconds is not None else None
    pixels = image.tobytes()
    compressor = zlib.compressobj(compression_level)
    pieces = []
    total = 0
    stride = width * 3
    for row in range(height):
        if started is not None and row % 32 == 0 and time.perf_counter() - started >= max_seconds:
            return None
        compressed = compressor.compress(b"\x00" + pixels[row * stride:(row + 1) * stride])
        total += len(compressed)
        if total + 57 > byte_limit:
            return None
        pieces.append(compressed)
    compressed = compressor.flush()
    total += len(compressed)
    if total + 57 > byte_limit:
        return None
    pieces.append(compressed)
    def chunk(kind, payload):
        return (struct.pack("!I", len(payload)) + kind + payload
                + struct.pack("!I", zlib.crc32(payload, zlib.crc32(kind)) & 0xffffffff))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", b"".join(pieces)) + chunk(b"IEND", b""))


def encode_lossless_refinement(image, wire_budget, *, max_seconds=.20):
    """Try a bounded original-pixel PNG after a scene settles.

    ``wire_budget`` includes base64 and a conservative JSON/metadata allowance.
    Filter None remains faster/smaller for the measured desktop workloads than
    Pillow's adaptive filters. Level 6 is an idle-only second attempt; level 9
    is deliberately excluded because it can stall a desktop capture thread.
    """
    if type(wire_budget) is not int or wire_budget <= 32 * 1024:
        return None
    raw_limit = ((min(wire_budget, MAX_REFINEMENT_FRAME_BYTES) - 32 * 1024) // 4) * 3
    started = time.perf_counter()
    for level in (3, 6):
        remaining = max_seconds - (time.perf_counter() - started)
        if remaining <= 0:
            break
        data = _fast_rgb_png(image, raw_limit, compression_level=level, max_seconds=remaining)
        if data is not None:
            return data
    return None


def encode_screen_image(image, profile, image_module, mode="auto", *, allow_png=False, native_png_budget=0, content_is_text=None, motion=False, wire_budget=None, try_png=True):
    """Encode within the wire budget while retaining readable source pixels.

    Legacy peers receive the exact JPEG path. New peers explicitly negotiate
    PNG before any lossless frame can be selected. Text/flat UI may use PNG;
    photos and noise retain JPEG without a full PNG compression attempt.
    Native lossless opportunism requires the Host's observed writer headroom.
    Every fallback starts from the intact capture, never a reduced candidate.
    """
    if type(mode) is not str or mode not in STREAM_MODES:
        raise ValueError("Prioridade de imagem inválida.")
    policy = STREAM_POLICIES[mode]
    readable = READABLE_STREAM_POLICIES.get(mode)
    if allow_png is True and readable is not None and profile in readable.profiles:
        policy = readable
    raw_limit = ((policy.frame_budget - 32 * 1024) // 4) * 3
    text_like = (allow_png is True and mode != "latency"
                 and (content_is_text if type(content_is_text) is bool else screen_text_candidate(image, image_module)))
    if motion and allow_png is True and readable is not None and profile in readable.profiles:
        if type(wire_budget) is int:
            policy = replace(policy, frame_budget=max(24 * 1024, min(wire_budget, policy.frame_budget)))
        return _encode_moving_screen_image(image, profile, image_module, mode, policy, text_like, native_png_budget, try_png)
    if text_like and type(native_png_budget) is int and native_png_budget > 32 * 1024:
        native_limit = ((min(native_png_budget, policy.frame_budget) - 32 * 1024) // 4) * 3
        native_data = _fast_rgb_png(image, native_limit)
        if native_data is not None:
            return native_data, profile, image.size
    for candidate in policy.profiles[policy.profiles.index(profile):]:
        resampling = (image_module.Resampling.LANCZOS if mode != "latency" else
                      getattr(image_module.Resampling, "BILINEAR", image_module.Resampling.LANCZOS))
        working = (image if image.size[0] <= candidate.width and image.size[1] <= candidate.height
                   else image.copy())
        try:
            if working is not image:
                working.thumbnail((candidate.width, candidate.height), resampling)
            size = working.size
            with BytesIO() as out:
                working.save(out, format="JPEG", quality=candidate.quality,
                             subsampling=candidate.subsampling, optimize=False)
                data = out.getvalue()
            if policy is readable and candidate is policy.profiles[-1]:
                # A native 720p noise image can be larger than a downsampled
                # 4K photo at the same raster size. Retain its readable raster
                # and lower only JPEG precision before treating complexity as
                # a capture failure. This branch never changes legacy bytes.
                for emergency_quality in (72, 56, 40):
                    if 4 * ((len(data) + 2) // 3) + 32 * 1024 <= policy.frame_budget:
                        break
                    with BytesIO() as out:
                        working.save(out, format="JPEG", quality=emergency_quality,
                                     subsampling=candidate.subsampling, optimize=False)
                        data = out.getvalue()
            if text_like and working is image:
                # A resampled text raster has already lost native details and
                # often compresses worse as PNG. Keep the exact fast JPEG
                # fallback rather than spend CPU trying to preserve that blur.
                png = _fast_rgb_png(working, min(raw_limit, len(data)))
                if png is not None and len(png) <= len(data):
                    return png, candidate, size
        finally:
            if working is not image:
                working.close()
        if 4 * ((len(data) + 2) // 3) + 32 * 1024 <= policy.frame_budget:
            return data, candidate, size
    raise ValueError("Imagem excede o orçamento de transmissão.")


def _encode_moving_screen_image(image, profile, image_module, mode, policy, text_like, native_png_budget, try_png):
    """One bounded PNG opportunity and fast, source-preserving JPEG previews.

    A changing desktop must not cascade through three Lanczos resizes. Preview
    resizes use Pillow's integer reduction + bilinear C path; an ACK-backed idle
    refinement independently restores all original pixels. Failed JPEG budgets
    first lower precision at the same raster, then use the floor directly.
    """
    # Screen metadata is under2KiB. The legacy32KiB reserve remains conservative
    # for normal frames; tiny slow-link previews need a proportional allowance.
    reserve = min(32 * 1024, policy.frame_budget // 8)
    raw_limit = ((policy.frame_budget - reserve) // 4) * 3
    within_profile = image.width <= profile.width and image.height <= profile.height
    if text_like and try_png and (within_profile or type(native_png_budget) is int and native_png_budget > 32 * 1024):
        native_budget = policy.frame_budget if within_profile else min(native_png_budget, policy.frame_budget)
        native_limit = ((native_budget - reserve) // 4) * 3
        data = _fast_rgb_png(image, native_limit, max_seconds=MOVING_PNG_SECONDS if mode == "auto" else .08)
        if data is not None:
            return data, profile, image.size
    # Photos do not need 4:4:4 text chroma, nor an initial speculative 4K preview.
    # Use the next known profile on Auto to avoid an expensive guaranteed retry.
    index = policy.profiles.index(profile)
    if not text_like and mode == "auto" and profile.width > 1920:
        index = max(index, 2)
    candidate = policy.profiles[index]
    choices = [candidate]
    if candidate is not policy.profiles[-1]:
        choices.append(policy.profiles[-1])
    for candidate in choices:
        width, height = image.size
        ratio = min(1.0, candidate.width / width, candidate.height / height)
        if policy.frame_budget <= 768 * 1024:
            cap = ((480, 270) if policy.frame_budget <= 48 * 1024 else
                   (640, 360) if policy.frame_budget <= 96 * 1024 else
                   (768, 432) if policy.frame_budget <= 128 * 1024 else
                   (1024, 576) if policy.frame_budget <= 192 * 1024 else
                   (1280, 720) if policy.frame_budget <= 384 * 1024 else (1920, 1080))
            ratio = min(ratio, cap[0] / width, cap[1] / height)
        if candidate is policy.profiles[-1]:
            cap = (320, 180) if policy.frame_budget <= 48 * 1024 else ((480, 270) if policy.frame_budget <= 96 * 1024
                   else ((640, 360) if policy.frame_budget <= 192 * 1024 else (candidate.width, candidate.height)))
            ratio = min(ratio, cap[0] / width, cap[1] / height)
            # Even a misclassified, adversarial RGB-noise frame must fit a
            # 24KiB slow-link budget. Bound the final raster before encoding;
            # q20 below is then comfortably under one byte per source pixel.
            ratio = min(ratio, math.sqrt(raw_limit / (width * height)))
        size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
        working = image if size == image.size else image.resize(size, image_module.Resampling.BILINEAR, reducing_gap=1.0)
        try:
            subsampling = candidate.subsampling if text_like else 2
            quality = candidate.quality
            for attempt in range(2):
                with BytesIO() as out:
                    working.save(out, format="JPEG", quality=quality, subsampling=subsampling, optimize=False)
                    data = out.getvalue()
                if len(data) <= raw_limit:
                    return data, candidate, size
                # No second resize just to shave a few bytes; actual encoded
                # complexity selects the correction, rather than retrying q92,
                # q90 and q86 at several nearly-identical raster sizes.
                quality = max(28 if candidate is policy.profiles[-1] or policy.frame_budget <= 192 * 1024 else 48,
                              min(quality - 12, int(quality * raw_limit / len(data) * .85)))
            if candidate is policy.profiles[-1]:
                # The bounded readable floor must always produce a frame, even
                # for worst-case random RGB pixels. JPEG q20 is an emergency
                # precision fallback, never the ordinary text setting.
                with BytesIO() as out:
                    working.save(out, format="JPEG", quality=20, subsampling=subsampling, optimize=False)
                    data = out.getvalue()
                if len(data) <= raw_limit:
                    return data, candidate, size
        finally:
            if working is not image:
                working.close()
    raise ValueError("Imagem excede o orçamento de transmissão.")
