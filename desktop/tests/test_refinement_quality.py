"""7.7 original-pixel refinement, fidelity retention and bounded health pulses."""
from io import BytesIO
import base64
import importlib.util
import json
import random
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adaptive_stream as adaptive
import host
import ios_connector as ios
import net
from test_host_stability import WaitScript, make_app
from test_lossless_stream import text_desktop


def dense_desktop():
    image = Image.new("RGB", (3840, 2160), "#f6f7fa")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("DejaVuSansMono.ttf", 13)
    for column in range(0, 3840, 960):
        for line, y in enumerate(range(8, 2150, 18)):
            color = ("#135b89", "#a31515", "#25384c")[line % 3]
            draw.text((column + 8, y), f"{line:03d} const acesso = await pc.open('AliançaDX'); // 0123456789", font=font, fill=color)
    return image


class RefinementBudgetTests(unittest.TestCase):
    def evidence(self, stream, *, wire_bytes=700_000, elapsed=.05, write=.005, processing=4):
        stream.ready(1, {"pending": False, "writing": False,
                         "lastFrameDuration": write, "lastFrameBytes": wire_bytes})
        frame = stream.frame_fields(1, wire_bytes)
        self.assertTrue(stream.feedback({"frameId": frame["frameId"], "decodeMs": processing,
                                         "cumulative": True}, 1 + elapsed))
        return 1 + elapsed

    def test_only_png_peers_use_readable_color_and_resolution_ladders(self):
        legacy = adaptive.AdaptiveStream(); legacy.enable(1)
        current = adaptive.AdaptiveStream(); current.enable(1, 2, True, ["jpeg", "png"])
        self.assertIs(legacy.profiles, adaptive.PROFILES)
        self.assertGreater(current.profile.width, legacy.profile.width)
        self.assertTrue(all(p.subsampling == 0 and p.quality >= 74 for p in current.profiles))
        current.set_mode(1, "quality", 1)
        self.assertGreaterEqual(current.profiles[-1].width, 1280)
        current.set_mode(1, "latency", 2)
        self.assertIs(current.profiles, adaptive.LATENCY_PROFILES)

    def test_larger_static_cap_uses_actual_ack_and_writer_projections(self):
        stream = adaptive.AdaptiveStream(); stream.enable(1, 2, True, ["jpeg", "png"], True)
        self.assertEqual(stream.refinement_png_budget, 0)
        now = self.evidence(stream)
        self.assertEqual(stream.fresh_refinement_png_budget(now), 3 * 1024 * 1024)
        self.assertEqual(stream.fresh_refinement_png_budget(now + 2.01), 0)
        stream.set_mode(1, "auto", now)
        now = self.evidence(stream, wire_bytes=300_000, elapsed=.35)
        self.assertLessEqual(stream.refinement_png_budget, int(300_000 * .45 / .35) + 1)
        self.assertGreater(stream.refinement_png_budget, 300_000)
        now = self.evidence(stream, wire_bytes=100_000, elapsed=1.5, write=.001)
        self.assertLessEqual(stream.refinement_png_budget, 30_001)

    def test_pending_probe_decoder_pressure_and_new_context_cannot_authorize_refinement(self):
        for restriction in ("pending", "probe", "decoder", "generation", "mode", "resume", "unknown_size"):
            stream = adaptive.AdaptiveStream(); stream.enable(1, 2, True, ["jpeg", "png"], True)
            now = self.evidence(stream, processing=150 if restriction == "decoder" else 4)
            if restriction == "pending": stream.frame_fields(now + .01, 100_000)
            elif restriction == "probe": stream._probe_ready = True
            elif restriction == "generation": stream.synchronize("new")
            elif restriction == "mode": stream.set_mode(1, "quality", now + .01)
            elif restriction == "resume": stream.resume(1, now + .01)
            elif restriction == "unknown_size": stream._last_present_bytes = None
            self.assertEqual(stream.refinement_png_budget, 0, restriction)

    def test_refinement_reservation_is_alone_and_rechecks_current_budget(self):
        stream = adaptive.AdaptiveStream(); stream.enable(1, 2, True, ["jpeg", "png"], True)
        now = self.evidence(stream)
        stream._window_limit = 2
        frame = stream.frame_fields(now + .1, 2_500_000, refinement=True)
        self.assertIsNotNone(frame); self.assertEqual(stream.max_in_flight, 1)
        self.assertIsNone(stream.frame_fields(now + .11, 100_000, refinement=True))
        self.assertEqual(stream.outstanding_count, 1)

    def test_late_valid_ack_cannot_move_retained_presented_identity_backwards(self):
        stream = adaptive.AdaptiveStream(); stream.enable(1, 2, True, ["jpeg", "png"], True)
        stream._window_limit = 2
        first = stream.frame_fields(1, 100_000); second = stream.frame_fields(1.01, 100_000)
        self.assertTrue(stream.feedback({"frameId": second["frameId"]}, 1.05))
        self.assertTrue(stream.feedback({"frameId": first["frameId"]}, 1.06))
        self.assertEqual(stream.last_presented_frame_id, second["frameId"])

    def test_unchanged_is_strict_first_negotiation_optin(self):
        for value in (False, None, 1, "true", [], {}):
            stream = adaptive.AdaptiveStream(); stream.enable(1, 2, True, ["jpeg", "png"], value)
            self.assertFalse(stream.unchanged_enabled)
        stream = adaptive.AdaptiveStream(); stream.enable(1, 2, True, ["jpeg"], True)
        self.assertFalse(stream.unchanged_enabled)
        stream = adaptive.AdaptiveStream(); stream.enable(1, 2, True, ["jpeg", "png"], True)
        self.assertTrue(stream.unchanged_enabled)

    def test_native_high_entropy_picture_fits_the_readable_floor_without_capture_failure(self):
        for mode in ("auto", "quality"):
            policy = adaptive.READABLE_STREAM_POLICIES[mode]
            profile = policy.profiles[-1]
            image = Image.frombytes("RGB", (profile.width, profile.height),
                                    random.Random(7701).randbytes(profile.width * profile.height * 3))
            data, actual, size = adaptive.encode_screen_image(image, profile, Image, mode,
                                                              allow_png=True, content_is_text=False)
            self.assertEqual(size, image.size)
            self.assertLessEqual(4 * ((len(data) + 2) // 3) + 32768, policy.frame_budget)
            with Image.open(BytesIO(data)) as decoded:
                self.assertEqual(decoded.format, "JPEG")
            image.close()


class StaticImageTests(unittest.TestCase):
    def test_native_lossless_never_regresses_on_same_scene_or_slow_ack(self):
        image = text_desktop((640, 360)); tracker = adaptive.ScreenContentTracker()
        signature = tracker.candidate(image, ("peer", "capture", "auto"), 0)
        tracker.published(signature, 0, lossless_native=True, frame_id=9, wire_bytes=50_000)
        for now in (.5, 2, 5, 20, 100):
            self.assertIsNone(tracker.candidate(image, ("peer", "capture", "auto"), now))
        self.assertEqual(tracker.unchanged_frame_id(2), 9)
        tracker.heartbeat_sent(2)
        self.assertIsNone(tracker.unchanged_frame_id(3.99))
        image.putpixel((1, 1), (2, 3, 4))
        changed = tracker.candidate(image, ("peer", "capture", "auto"), 2.01)
        self.assertIsNotNone(changed)
        self.assertTrue(tracker.carry_native(changed, 50_000))
        self.assertFalse(tracker.carry_native(changed, 49_999))
        self.assertIsNotNone(tracker.candidate(image, ("peer", "different capture", "auto"), 2.02))
        image.close()

    def test_refinement_does_not_miss_half_second_blinking_caret(self):
        image = text_desktop((640, 360)); tracker = adaptive.ScreenContentTracker()
        context = ("peer", "capture", "auto")
        signature = tracker.candidate(image, context, 0)
        tracker.published(signature, 0)
        self.assertIsNone(tracker.candidate(image, context, .34, refinement_budget=300_000))
        self.assertIsNotNone(tracker.candidate(image, context, .36, refinement_budget=300_000))
        self.assertTrue(tracker.refinement_due(signature, 300_000, .36))
        self.assertIsNone(tracker.candidate(image, context, .37, refinement_budget=300_000))
        image.close()

    def test_native_refinement_is_exact_and_wire_bounded(self):
        image = dense_desktop()
        data = adaptive.encode_lossless_refinement(image, 3 * 1024 * 1024)
        self.assertIsNotNone(data)
        self.assertLessEqual(4 * ((len(data) + 2) // 3) + 32768, 3 * 1024 * 1024)
        with Image.open(BytesIO(data)) as decoded:
            self.assertEqual(decoded.tobytes(), image.tobytes())
        self.assertIsNone(adaptive.encode_lossless_refinement(image, 32768))
        image.close()


class HostRefinementTests(unittest.TestCase):
    def capture(self, image, *, elapsed=.05, loops=30, mutate=None, optin=True):
        now = [10.0]; app = make_app(); acked = set(); encoded_frames = []
        app._message(app.peer, {"type": "stream_ready", "version": 1, "maxInFlight": 2,
                               "ackCumulative": True, "imageFormats": ["jpeg", "png"], "unchangedFrames": optin})
        app.peer.stream_stats = lambda: {"pending": False, "writing": False,
                                        "lastFrameDuration": .005, "lastFrameBytes":
                                        len(encoded_frames[-1]["image"]) + 32768 if encoded_frames else 300_000}
        rectangle = {"left": -image.width, "top": 37, "width": image.width, "height": image.height}
        class Capture:
            monitors = [rectangle.copy(), rectangle.copy()]
            def grab(self, rectangle): return SimpleNamespace(size=image.size, rgb=image.tobytes())
            def close(self): pass
        def wait(event, delay):
            frames = [m for m in app.peer.messages if m.get("type") == "screen"]
            encoded_frames[:] = frames
            if frames and frames[-1]["frameId"] not in acked:
                frame = frames[-1]
                now[0] = max(now[0], app.stream._pending[-1][1] + elapsed)
                app._message(app.peer, {"type": "stream_feedback", "frameId": frame["frameId"],
                                       "decodeMs": 2, "renderMs": 2, "cumulative": True})
                acked.add(frame["frameId"])
            if mutate: mutate(app, len(event.waits), image)
            now[0] += .1
            if len(event.waits) >= loops: event.set()
        app.shutdown = WaitScript(wait)
        with patch.dict(sys.modules, {"mss": SimpleNamespace(mss=Capture)}), patch.object(host.time, "monotonic", side_effect=lambda: now[0]):
            app._capture_loop()
        return app, [m for m in app.peer.messages if m.get("type") == "screen"]

    def test_default_auto_dense_4k_eventually_matches_every_original_pixel(self):
        image = dense_desktop(); app, frames = self.capture(image)
        self.assertTrue(frames[0]["image"])
        self.assertGreaterEqual(len(frames), 2)
        self.assertEqual((frames[-1]["frameWidth"], frames[-1]["frameHeight"]), image.size)
        encoded = base64.b64decode(frames[-1]["image"])
        with Image.open(BytesIO(encoded)) as decoded:
            self.assertEqual(decoded.format, "PNG"); self.assertEqual(decoded.tobytes(), image.tobytes())
        self.assertTrue(any(m["type"] == "screen_unchanged" for m in app.peer.messages))
        self.assertEqual(app.stream.max_in_flight, 1)
        image.close()

    def test_350ms_rtt_does_not_force_same_original_text_back_to_preview(self):
        image = text_desktop((3840, 2160)); app, frames = self.capture(image, elapsed=.35, loops=20)
        self.assertGreaterEqual(len(frames), 2)
        self.assertEqual((frames[-1]["frameWidth"], frames[-1]["frameHeight"]), image.size)
        self.assertTrue(base64.b64decode(frames[-1]["image"]).startswith(b"\x89PNG"))
        self.assertLessEqual(len(frames), 7, "bounded startup previews establish capacity before native refinement")
        first_native = next(i for i, frame in enumerate(frames) if base64.b64decode(frame["image"]).startswith(b"\x89PNG"))
        self.assertEqual(len(frames[first_native:]), 1, "native pixels remain retained after their slower ACK")
        image.close()

    def test_old_png_receiver_still_gets_actual_screen_refreshes(self):
        image = text_desktop((640, 360)); app, frames = self.capture(image, loops=12, optin=False)
        self.assertGreaterEqual(len(frames), 2)
        self.assertFalse(any(m["type"] == "screen_unchanged" for m in app.peer.messages))
        image.close()

    def test_blinking_caret_does_not_alternate_original_image_with_blurry_previews(self):
        image = dense_desktop()
        def blink(app, iteration, image):
            if iteration % 5 == 0:
                image.putpixel((3800, 2000), (0, 0, 0) if iteration % 10 else (255, 255, 255))
        app, frames = self.capture(image, loops=25, mutate=blink)
        native = [i for i, frame in enumerate(frames) if base64.b64decode(frame["image"]).startswith(b"\x89PNG")]
        self.assertTrue(native)
        for frame in frames[native[0]:]:
            self.assertTrue(base64.b64decode(frame["image"]).startswith(b"\x89PNG"))
            self.assertEqual((frame["frameWidth"], frame["frameHeight"]), image.size)
        self.assertGreaterEqual(len(frames[native[0]:]), 3)
        image.close()


class QueueTests(unittest.TestCase):
    def peer(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close); self.addCleanup(b.close)
        return net.Peer(a)

    def pulse(self, frame_id=1):
        return {"type": "screen_unchanged", "streamVersion": 1, "frameId": frame_id, "captureId": "same"}

    def test_peer_pulse_is_one_reserved_slot_and_never_reorders_reliable_fifo(self):
        peer = self.peer()
        for i in range(20): peer.send({"type": "file_chunk", "n": i})
        for i in range(1000): peer.send(self.pulse(i))
        self.assertEqual(len(peer._out), 20)
        self.assertEqual(json.loads(peer._next_packet()[4:])["frameId"], 999)
        self.assertEqual([json.loads(peer._next_packet()[4:])["n"] for _ in range(20)], list(range(20)))
        self.assertIsNone(peer._next_packet())

    def test_peer_new_capture_pause_and_close_drop_old_pulse(self):
        for barrier in ("screen", "state", "close"):
            peer = self.peer(); peer.send(self.pulse())
            if barrier == "close": peer.close()
            else: peer.send({"type": barrier, "sharing": False})
            self.assertIsNone(peer._unchanged_frame)
        peer = self.peer()
        with self.assertRaises(ValueError): peer.send({**self.pulse(), "padding": "x" * 2000})
        self.assertIsNone(peer._unchanged_frame)

    def test_bridge_pulse_coalesces_and_has_bounded_reserve_before_bulk_files(self):
        queue = ios.BoundedEvents(max_messages=2, max_bytes=300)
        queue.put({"type": "file_chunk", "n": 1}); queue.put({"type": "file_chunk", "n": 2})
        for i in range(1000):
            self.assertTrue(queue.put({"type": "bridge_packet", "generation": 1, "message": self.pulse(i)}))
        self.assertLessEqual(queue.bytes, 1324)
        reader = queue.subscribe()
        self.assertEqual(json.loads(queue.next(reader))["message"]["frameId"], 999)
        self.assertEqual([json.loads(queue.next(reader))["n"] for _ in range(2)], [1, 2])
        self.assertEqual(queue.bytes, 0)

    def test_bridge_capture_barrier_clears_queued_pulse(self):
        queue = ios.BoundedEvents()
        pulse = {"type": "bridge_packet", "generation": 1, "message": self.pulse()}
        queue.put(pulse); queue.discard_frame(); self.assertIsNone(queue.unchanged)
        queue.put(pulse); queue.put({"type": "screen", "frame": 2}, frame=True)
        self.assertIsNone(queue.unchanged)
        queue.put(pulse); queue.reset({"type": "bridge_state"}); self.assertIsNone(queue.unchanged)
        self.assertFalse(queue.put({**pulse, "padding": "x" * 2000}))


if __name__ == "__main__": unittest.main()
