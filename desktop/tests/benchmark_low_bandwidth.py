#!/usr/bin/env python3
"""Reproducible JPEG fixture and finite-buffer link model, not a network benchmark.

Run from any directory with Python + Pillow. The old sender is modelled with
its existing current-packet/latest-frame slots, not an unlimited application
FIFO. The configurable write-ahead represents bytes already accepted by TCP.
The model uses supplied goodput (after any network loss), deterministic
propagation delay and fixed decode/draw costs. It does not implement TCP
congestion control, retransmissions, real TLS, screen capture CPU or input RTT.
Results describe only this generated image and these stated assumptions.
"""
from __future__ import annotations

import argparse
import base64
from collections import deque
from dataclasses import dataclass
import hashlib
import heapq
from io import BytesIO
import json
from pathlib import Path
import random
import statistics
import sys

from PIL import Image, ImageDraw, __version__ as PILLOW_VERSION

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adaptive_stream import AdaptiveStream, PROFILES
from net import _packet

STEP = .002
DECODE_SECONDS = .010
DRAW_SECONDS = .004


def fixture_packets():
    """A deterministic synthetic desktop: panels, text, chart, and a photo area."""
    image = Image.new("RGB", (2560, 1440), "#edf2f6")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 2560, 68), fill="#123a2a")
    draw.text((35, 25), "AliancaDX - synthetic desktop fixture", fill="white")
    for column in range(3):
        left = 30 + column * 843
        draw.rounded_rectangle((left, 100, left + 812, 1395), radius=14,
                               fill="white", outline="#cbd8d1", width=2)
        for row in range(38):
            top = 145 + row * 30
            draw.rectangle((left + 22, top, left + 32, top + 10), fill="#007a50")
            draw.text((left + 44, top),
                      f"{row:02d}  Sensor status / chart / remote support - 1234567890",
                      fill=(35 + row % 4 * 24, 75, 60))
    # Deterministic textured content makes compression costs visible without
    # pretending that a mostly blank desktop or a video is universal workload.
    texture = Image.frombytes("RGB", (760, 430), random.Random(6201).randbytes(760 * 430 * 3))
    image.paste(texture, (1750, 850))
    texture.close()
    draw = ImageDraw.Draw(image)
    for index in range(1, 70):
        x = 80 + index * 10
        draw.line((x, 1250, x + 7, 1250 - (index * 41) % 300),
                  fill="#007a50", width=5)
    profiles = {profile.name: profile for profile in PROFILES}
    profiles["legacy"] = type(PROFILES[0])("legacy", 2560, 1440, 65, 8)
    sizes = {}
    for name, profile in profiles.items():
        copy = image.copy()
        copy.thumbnail((profile.width, profile.height), Image.Resampling.LANCZOS)
        output = BytesIO()
        copy.save(output, format="JPEG", quality=profile.quality)
        jpeg = output.getvalue()
        packet = _packet({"type": "screen", "captureId": "fixture-geometry",
                          "width": 2560, "height": 1440,
                          "frameWidth": copy.width, "frameHeight": copy.height,
                          "image": base64.b64encode(jpeg).decode("ascii")})
        sizes[name] = {"jpegBytes": len(jpeg), "wireBytes": len(packet),
                       "width": copy.width, "height": copy.height,
                       "quality": profile.quality,
                       "jpegSha256": hashlib.sha256(jpeg).hexdigest()}
        copy.close()
    image.close()
    return sizes


@dataclass
class Frame:
    capture: float
    size: int
    profile: str
    identifier: int | None = None
    written: float = 0.0
    received: float = 0


def percentile(values, fraction):
    values = sorted(values)
    return values[min(len(values) - 1, int((len(values) - 1) * fraction))] if values else None


class LinkModel:
    def __init__(self, sizes, adaptive, buffer_bytes, link, duration=50, hidden=None):
        self.sizes, self.adaptive = sizes, adaptive
        self.buffer_bytes, self.link, self.duration = buffer_bytes, link, duration
        self.hidden = hidden
        self.stream = AdaptiveStream()
        self.stream.synchronize(1)
        if adaptive:
            assert self.stream.enable(1)
        self.current = self.latest = None
        self.kernel = deque()
        self.queued = 0.0
        self.events = []
        self.event_sequence = 0
        self.next_capture = 0.0
        self.last_stats = {"lastFrameBytes": 0, "lastFrameDuration": 0}
        self.delays = []
        self.samples = []
        self.tiers = []
        self.displayed = self.accepted_acks = self.stale_acks = self.captured = 0
        self.max_kernel = self.max_current_latest = self.max_in_transit = 0
        self.in_transit = 0
        self.bytes_sent = 0.0
        self.resumed = False

    def event(self, when, kind, frame):
        self.event_sequence += 1
        heapq.heappush(self.events, (when, self.event_sequence, kind, frame))

    def pump(self, now):
        while self.current is not None and self.queued < self.buffer_bytes:
            frame = self.current
            amount = min(frame.size - frame.written, self.buffer_bytes - self.queued)
            if amount <= 0:
                break
            frame.written += amount
            self.kernel.append([frame, amount])
            self.queued += amount
            if frame.written == frame.size:
                self.last_stats = {"lastFrameBytes": frame.size,
                                   "lastFrameDuration": now - frame.capture}
                self.current, self.latest = self.latest, None
        self.max_kernel = max(self.max_kernel, self.queued)

    def drain(self, now, rate, rtt):
        credit = rate * STEP
        while credit > 0 and self.kernel:
            frame, remaining = self.kernel[0]
            amount = min(credit, remaining)
            self.kernel[0][1] -= amount
            self.queued -= amount
            self.bytes_sent += amount
            frame.received += amount
            credit -= amount
            if self.kernel[0][1] <= .00001:
                self.kernel.popleft()
            if frame.size - frame.received <= .00001:
                self.event(now + rtt / 2 + DECODE_SECONDS + DRAW_SECONDS, "draw", frame)

    def consume_events(self, now, rtt):
        while self.events and self.events[0][0] <= now:
            when, _, kind, frame = heapq.heappop(self.events)
            if kind == "draw":
                if self.hidden and self.hidden[0] <= when < self.hidden[1]:
                    self.in_transit -= 1
                    continue  # Explicitly discarded viewport; no false ACK.
                delay = when - frame.capture
                self.delays.append(delay)
                self.samples.append((when, delay, frame.profile))
                self.displayed += 1
                if self.adaptive:
                    self.event(when + rtt / 2, "ack", frame)
                else:
                    self.in_transit -= 1
            else:
                accepted = self.stream.feedback({"frameId": frame.identifier,
                    "decodeMs": DECODE_SECONDS * 1000,
                    "renderMs": DRAW_SECONDS * 1000}, when)
                self.accepted_acks += int(accepted)
                self.stale_acks += int(not accepted)
                self.in_transit -= 1

    def run(self):
        for tick in range(round(self.duration / STEP)):
            now = tick * STEP
            rate, rtt = self.link(now)
            self.consume_events(now, rtt)
            if self.hidden and now >= self.hidden[1] and not self.resumed:
                assert self.stream.resume(1, now)
                self.resumed = True
            pressure = {**self.last_stats, "pending": self.latest is not None,
                        "writing": self.current is not None}
            # New host polls pressure even when its capture period has not
            # elapsed; the real host does this at 10ms while flow-controlled.
            ready = self.stream.ready(now, pressure) if self.adaptive else True
            if now + 1e-9 >= self.next_capture and ready:
                profile = self.stream.profile if self.adaptive else None
                name = profile.name if profile else "legacy"
                fields = self.stream.frame_fields(now) if self.adaptive else {}
                frame = Frame(now, self.sizes[name]["wireBytes"], name, fields.get("frameId"))
                self.captured += 1
                if self.current is None:
                    self.current = frame
                    self.in_transit += 1
                else:
                    if self.latest is None:
                        self.in_transit += 1
                    self.latest = frame
                self.next_capture = now + 1.0 / (profile.fps if profile else 8)
                self.tiers.append((now, name))
                self.max_in_transit = max(self.max_in_transit, self.in_transit)
                self.max_current_latest = max(self.max_current_latest,
                    int(self.current is not None) + int(self.latest is not None))
            self.pump(now)
            self.drain(now, rate, rtt)
            self.pump(now + STEP)
        recent = [delay for when, delay, _ in self.samples if when >= self.duration - 15]
        return {"adaptive": self.adaptive, "writeAheadBytes": self.buffer_bytes,
                "captured": self.captured, "displayed": self.displayed,
                "meanDisplayMs": round(statistics.mean(self.delays) * 1000, 2),
                "p95DisplayMs": round(percentile(self.delays, .95) * 1000, 2),
                "last15sP95DisplayMs": round(percentile(recent, .95) * 1000, 2),
                "maxKernelBytes": round(self.max_kernel),
                "maxCurrentAndLatestPackets": self.max_current_latest,
                "maxFramesAwaitingDisplayOrAck": self.max_in_transit,
                "wireBytesDrained": round(self.bytes_sent),
                "acceptedAcks": self.accepted_acks, "staleAcks": self.stale_acks,
                "firstProfile": self.tiers[0][1], "lastProfile": self.tiers[-1][1],
                "profilesUsed": list(dict.fromkeys(name for _, name in self.tiers)),
                "viewportResumed": self.resumed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write the JSON evidence report")
    args = parser.parse_args()
    sizes = fixture_packets()
    initial_reduction = 1 - sizes["balanced"]["wireBytes"] / sizes["legacy"]["wireBytes"]
    assert initial_reduction > .5, "Fixture no longer demonstrates smaller initial frames"
    weak_link = lambda _: (512_000 / 8, .120)
    legacy = [LinkModel(sizes, False, amount, weak_link).run()
              for amount in (64 * 1024, 256 * 1024, 1024 * 1024)]
    adaptive_model = LinkModel(sizes, True, 64 * 1024, weak_link)
    adaptive = adaptive_model.run()
    assert all(case["maxKernelBytes"] <= case["writeAheadBytes"] for case in legacy + [adaptive])
    assert adaptive["maxFramesAwaitingDisplayOrAck"] <= 2
    assert adaptive["maxCurrentAndLatestPackets"] <= 1
    assert adaptive["acceptedAcks"] > 50
    assert all(adaptive["last15sP95DisplayMs"] < case["last15sP95DisplayMs"] for case in legacy)
    probe = LinkModel(sizes, True, 64 * 1024,
                      lambda _: (256_000 / 8, .180), duration=30).run()
    assert probe["maxFramesAwaitingDisplayOrAck"] == 2, "Slow first frame did not exercise the recovery probe"
    assert probe["staleAcks"] >= 1, "Superseded original ACK was not exercised"
    assert probe["acceptedAcks"] > 20, "Probe ACK did not restore progress"
    step_link = lambda now: (512_000 / 8, .120) if 8 <= now < 28 else (8_000_000 / 8, .040)
    step_model = LinkModel(sizes, True, 64 * 1024, step_link, duration=60)
    step = step_model.run()
    assert step["maxFramesAwaitingDisplayOrAck"] <= 2
    assert any(8 < when < 28 and name in ("low", "minimum") for when, name in step_model.tiers)
    assert step["lastProfile"] in ("balanced", "detail"), "Detail did not recover after goodput returned"
    hidden_model = LinkModel(sizes, True, 64 * 1024, weak_link, duration=35, hidden=(10, 18))
    hidden = hidden_model.run()
    assert hidden["viewportResumed"]
    assert sum(when > 18 for when, _, _ in hidden_model.samples) > 20, "Viewer did not recover after returning"
    report = {
        "kind": "deterministic_link_model_not_hardware_benchmark",
        "pillowVersion": PILLOW_VERSION,
        "assumptions": {"fixture": "generated 2560x1440 panels/text/chart/texture",
            "clockStepMs": STEP * 1000, "weakGoodputBitsPerSecond": 512000,
            "weakRttMs": 120, "goodGoodputBitsPerSecond": 8000000, "goodRttMs": 40,
            "decodeMs": DECODE_SECONDS * 1000, "drawMs": DRAW_SECONDS * 1000,
            "oldCaptureFps": 8, "oldApplicationQueue": "current packet + replaceable latest frame",
            "legacyWriteAheadSensitivityBytes": [65536, 262144, 1048576],
            "newWriteAheadBytes": 65536,
            "limits": "Supplied goodput, no TCP congestion/retransmission/TLS/capture CPU/input-delay simulation; not a promise about user networks."},
        "fixturePackets": sizes,
        "initialWireByteReductionPercent": round(initial_reduction * 100, 2),
        "constantWeakLink": {"legacy": legacy, "adaptive": adaptive},
        "slowerLink256kbit180msExercisesSingleProbe": probe,
        "stepDownAt8sRecoveryAt28s": step,
        "hiddenViewport10to18sThenExplicitResume": hidden,
        "assertions": "PASS: finite buffers, <=2 adaptive display/ACK-flight frames, congestion downshift, good-link recovery, viewport resume, lower steady fixture delay for every legacy buffer assumption"}
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
