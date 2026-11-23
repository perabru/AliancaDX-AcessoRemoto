"""Deterministic motion, capacity and idle regressions; no CPU timing thresholds."""
from io import BytesIO
import base64
import json
from pathlib import Path
import random
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

from PIL import Image, ImageDraw
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adaptive_stream as stream
import host
from test_host_stability import make_app, WaitScript


def desktop(size=(1280, 720)):
    image = Image.new("RGB", size, "#f5f7fa")
    draw = ImageDraw.Draw(image)
    for y in range(10, size[1], 22):
        draw.text((10, y), f"AliançaDX 0123456789 :: codigo = acesso.pc({y});", fill=(0, 70, 170))
    return image


def enabled(mode="auto"):
    result = stream.AdaptiveStream()
    result.enable(1, 2, True, ["jpeg", "png"], True)
    result.set_mode(1, mode, 0)
    return result


def ack(value, now, *, size=20000, cycle=.05, write=.001, processing=4):
    value.ready(now, {"pending": False, "writing": False, "lastFrameDuration": write, "lastFrameBytes": size})
    frame = value.frame_fields(now, size)
    assert frame is not None
    assert value.feedback({"frameId": frame["frameId"], "decodeMs": processing, "cumulative": True}, now + cycle)
    return now + cycle


class MotionEncodingTests(unittest.TestCase):
    def test_failed_native_png_is_attempted_only_once_then_jpeg(self):
        image = desktop()
        profile = stream.READABLE_PROFILES["auto"][1]
        with patch.object(stream, "_fast_rgb_png", return_value=None) as png:
            data, _, _ = stream.encode_screen_image(image, profile, Image, allow_png=True,
                native_png_budget=1024 * 1024, content_is_text=True, motion=True)
        self.assertEqual(png.call_count, 1)
        self.assertGreater(png.call_args.kwargs["max_seconds"], 0)
        self.assertTrue(data.startswith(b"\xff\xd8"))
        image.close()

    def test_negative_codec_backoff_and_carry_cannot_retry_png_in_same_frame(self):
        tracker = stream.ScreenContentTracker()
        tracker.moving_png_failed(10)
        self.assertEqual(tracker.moving_png_budget(100000, 11.99), 0)
        self.assertEqual(tracker.moving_png_budget(100000, 12), 100000)
        image = desktop()
        with patch.object(stream, "_fast_rgb_png", side_effect=AssertionError("no second PNG")):
            data, _, _ = stream.encode_screen_image(image, stream.READABLE_PROFILES["auto"][1], Image,
                allow_png=True, content_is_text=True, native_png_budget=1000000, motion=True, try_png=False)
        self.assertTrue(data.startswith(b"\xff\xd8"))
        image.close()

    def test_small_native_text_can_start_with_exact_png_inside_startup_budget(self):
        image = desktop((640, 360))
        data, _, size = stream.encode_screen_image(image, stream.READABLE_PROFILES["auto"][1], Image,
            allow_png=True, content_is_text=True, motion=True, wire_budget=128 * 1024)
        with Image.open(BytesIO(data)) as result:
            self.assertEqual(result.format, "PNG")
            self.assertEqual(result.tobytes(), image.tobytes())
        self.assertEqual(size, image.size)
        image.close()

    def test_motion_fallback_has_at_most_two_direct_bilinear_resizes(self):
        image = Image.frombytes("RGB", (1920, 1080), random.Random(771).randbytes(1920 * 1080 * 3))
        before = image.tobytes()
        calls = []
        resize = Image.Image.resize
        def record(source, size, resample=None, *args, **kwargs):
            calls.append((source is image, size, resample))
            return resize(source, size, resample, *args, **kwargs)
        with patch.object(Image.Image, "resize", record), patch.object(Image.Image, "copy", side_effect=AssertionError("no full source copy")):
            data, _, size = stream.encode_screen_image(image, stream.READABLE_PROFILES["quality"][0], Image, "quality",
                allow_png=True, content_is_text=False, motion=True, wire_budget=24 * 1024)
        self.assertLessEqual(len(calls), 2)
        self.assertTrue(all(row[0] and row[2] == Image.Resampling.BILINEAR for row in calls))
        self.assertEqual(image.tobytes(), before)
        self.assertLessEqual(max(size), 480)
        self.assertLess(len(data), 24 * 1024)
        image.close()

    def test_real_jpeg_packets_obey_dynamic_wire_caps_without_capture_failure(self):
        images = [desktop(), Image.frombytes("RGB", (1280, 720), random.Random(772).randbytes(1280 * 720 * 3))]
        for mode in ("auto", "quality"):
            for image in images:
                for budget in (24, 48, 96, 128, 192, 512, 1024):
                    with self.subTest(mode=mode, photo=image is images[1], budget=budget):
                        data, _, size = stream.encode_screen_image(image, stream.READABLE_PROFILES[mode][1], Image, mode,
                            allow_png=True, content_is_text=image is images[0], motion=True, wire_budget=budget * 1024, try_png=False)
                        packet = {"type": "screen", "image": base64.b64encode(data).decode(), "frameWidth": size[0],
                                  "frameHeight": size[1], "captureId": "performance-test", "metadataReserve": "x" * 1800}
                        wire = len(json.dumps(packet, separators=(",", ":")).encode()) + 4
                        self.assertLessEqual(wire, budget * 1024)
                        with Image.open(BytesIO(data)) as decoded:
                            self.assertEqual(decoded.size, size)
        for image in images: image.close()


class CapacityAndCadenceTests(unittest.TestCase):
    def test_initial_wire_budget_is_conservative_and_legacy_unchanged(self):
        self.assertEqual(enabled().preview_wire_budget(1), 128 * 1024)
        self.assertEqual(enabled("quality").preview_wire_budget(1), 192 * 1024)
        legacy = stream.AdaptiveStream(); legacy.enable(1)
        self.assertEqual(legacy.preview_wire_budget(1), stream.STREAM_POLICIES["auto"].frame_budget)

    def test_budget_can_grow_only_once_per_known_ack(self):
        value = enabled(); previous = value.preview_wire_budget(0)
        now = ack(value, 1, size=80000)
        current = value.preview_wire_budget(now)
        self.assertGreater(current, previous)
        self.assertLessEqual(current, previous * 2)
        for _ in range(20): self.assertEqual(value.preview_wire_budget(now), current)
        self.assertFalse(value.feedback({"frameId": 99999}, now))
        self.assertEqual(value.preview_wire_budget(now), current)

    def test_three_fast_presentations_can_probe_above_compressible_payload_ceiling(self):
        value = enabled(); budget = value.preview_wire_budget(0)
        budgets = [budget]
        for index in range(8):
            now = ack(value, index + 1, size=20000)
            budget = value.preview_wire_budget(now)
            self.assertLessEqual(budget, budgets[-1] * 2)
            budgets.append(budget)
        self.assertGreaterEqual(budgets[-1], 768 * 1024)
        self.assertEqual(value.max_in_flight, 1)

    def test_slow_or_unpresented_socket_samples_never_authorize_capacity_probe(self):
        for cycle, write in ((.4, .001), (.05, .2)):
            value = enabled(); value.preview_wire_budget(0)
            for index in range(5):
                now = ack(value, index + 1, size=20000, cycle=cycle, write=write)
                budget = value.preview_wire_budget(now)
            self.assertLessEqual(budget, 128 * 1024)
        value = enabled()
        for index in range(20):
            value.ready(index + 1, {"pending": False, "writing": False, "lastFrameDuration": .001, "lastFrameBytes": 2000000})
            self.assertEqual(value.preview_wire_budget(index + 1), 128 * 1024)

    def test_stale_generation_resume_and_mode_clear_capacity_proof(self):
        value = enabled()
        for index in range(5):
            now = ack(value, index + 1, size=20000)
            value.preview_wire_budget(now)
        self.assertEqual(value.preview_wire_budget(now + 2.01), 128 * 1024)
        value.synchronize(2)
        self.assertEqual(value.preview_wire_budget(now), 128 * 1024)
        self.assertFalse(value._stable_rtt_evidence())
        ack(value, 10)
        self.assertTrue(value.resume(1, 12))
        self.assertEqual(value.preview_wire_budget(12), 128 * 1024)
        self.assertFalse(value._stable_rtt_evidence())
        value.set_mode(1, "quality", 13)
        self.assertEqual(value.preview_wire_budget(13), 192 * 1024)

    def test_static_rtt_requires_varied_payload_and_stable_delivery(self):
        value = enabled()
        for index, size in enumerate((20000, 20000, 20000)):
            ack(value, index + 1, size=size, cycle=.35)
        self.assertFalse(value._stable_rtt_evidence())
        ack(value, 4, size=40000, cycle=.35)
        self.assertTrue(value._stable_rtt_evidence())
        ack(value, 5, size=80000, cycle=.8)
        self.assertFalse(value._stable_rtt_evidence())

    def test_readable_cpu_pressure_reduces_pixels_without_trapping_two_fps(self):
        for mode in ("auto", "quality"):
            value = enabled(mode); initial = value.profile
            value.capture_finished(.25, 1)
            self.assertLess(value.profile.width, initial.width)
            self.assertEqual(value._quality_cadence, 0)
            for index in range(10): value.capture_finished(.4, index + 2)
            self.assertGreaterEqual(value.target_fps, 6)
        legacy = stream.AdaptiveStream(); legacy.set_mode(1, "quality", 0)
        initial = legacy.profile
        legacy.capture_finished(.25, 1)
        self.assertEqual(legacy.profile, initial)
        self.assertEqual(legacy.target_fps, initial.fps // 2)

    def test_fast_recovery_restores_cadence_before_increasing_preview_pixels(self):
        value = enabled(); value._tier = 4; value._quality_cadence = 1
        initial = value.profile
        for index in range(12): ack(value, 1 + index * .5, size=30000)
        self.assertEqual(value._quality_cadence, 0)
        self.assertEqual(value.profile, initial)


class ExactIdleTests(unittest.TestCase):
    def native(self, image, now=0):
        tracker = stream.ScreenContentTracker()
        raw = bytearray(image.tobytes("raw", "BGRX"))
        signature = tracker.candidate(image, ("peer", "capture"), now, pixels=raw, bytes_per_pixel=4)
        tracker.published(signature, now, lossless_native=True, frame_id=1, wire_bytes=50000)
        return tracker, raw

    def test_unchanged_raw_capture_needs_no_rgb_conversion_or_sample(self):
        image = desktop((640, 360)); tracker, raw = self.native(image)
        self.assertTrue(tracker.retained_capture_matches(raw, image.size, ("peer", "capture")))
        raw[-4] ^= 1
        self.assertFalse(tracker.retained_capture_matches(raw, image.size, ("peer", "capture")))
        self.assertFalse(tracker.retained_capture_matches(raw, image.size, ("other", "capture")))
        image.close()

    def test_changed_raw_fingerprint_is_reused_once_and_not_across_mutated_captures(self):
        image = desktop((640, 360)); tracker, raw = self.native(image)
        context = ("peer", "capture")
        image.putpixel((1, 1), (1, 2, 3)); raw[:] = image.tobytes("raw", "BGRX")
        memo = []
        with patch.object(tracker, "_bands", wraps=tracker._bands) as bands:
            self.assertFalse(tracker.retained_capture_matches(raw, image.size, context, fingerprint_out=memo))
            first = tracker.candidate(image, context, .5, pixels=raw, bytes_per_pixel=4, fingerprint=memo[0])
            self.assertEqual(bands.call_count, 1)
            raw[-4] ^= 1
            second = tracker.candidate(image, context, .6, pixels=raw, bytes_per_pixel=4)
            self.assertEqual(bands.call_count, 2)
            self.assertNotEqual(first, second)
        image.close()

    def test_exact_small_caret_carries_native_but_full_scroll_or_band_change_does_not(self):
        image = Image.new("RGB", (640, 360), "white"); tracker, _ = self.native(image)
        image.putpixel((1, 1), (0, 0, 0))
        caret = tracker.candidate(image, ("peer", "capture"), .5, pixels=image.tobytes("raw", "BGRX"), bytes_per_pixel=4)
        self.assertTrue(tracker.carry_native(caret, 50000))
        ImageDraw.Draw(image).rectangle((0, 0, 639, 63), fill="red")
        band = tracker.candidate(image, ("peer", "capture"), .6, pixels=image.tobytes("raw", "BGRX"), bytes_per_pixel=4)
        self.assertFalse(tracker.carry_native(band, 50000))
        ImageDraw.Draw(image).rectangle((0, 64, 639, 359), fill="blue")
        scroll = tracker.candidate(image, ("peer", "capture"), .7, pixels=image.tobytes("raw", "BGRX"), bytes_per_pixel=4)
        self.assertFalse(tracker.carry_native(scroll, 50000))
        image.close()

    def test_settled_exact_jpeg_is_retained_and_unseen_one_pixel_change_is_not_lost(self):
        image = Image.frombytes("RGB", (640, 360), random.Random(773).randbytes(640 * 360 * 3))
        tracker = stream.ScreenContentTracker(); context = ("peer", "capture")
        self.assertEqual(tracker.photo_candidate(image, context, 0), (None, True))
        signature, changed = tracker.photo_candidate(image, context, .1)
        self.assertTrue(changed); self.assertIsNotNone(signature)
        tracker.published(signature, .1, retained=True, frame_id=7)
        self.assertEqual(tracker.photo_candidate(image, context, .2), (None, False))
        self.assertEqual(tracker.unchanged_frame_id(2.1), 7)
        image.putpixel((639, 359), (1, 2, 3))
        signature, changed = tracker.photo_candidate(image, context, .3)
        self.assertTrue(changed); self.assertIsNotNone(signature)
        image.close()

    def test_idle_capture_rate_is_bounded_and_accepted_input_wakes_it(self):
        image = desktop((640, 360)); tracker, _ = self.native(image)
        self.assertEqual(tracker.capture_fps(24, .9), 24)
        self.assertEqual(tracker.capture_fps(24, 1), 5)
        self.assertEqual(tracker.capture_fps(24, 3, active_at=2.5), 24)
        self.assertEqual(tracker.capture_fps(24, 4, active_at=2.5), 5)
        app = make_app(); app._capture_wake = threading.Event()
        app._message(app.peer, {"type": "input", "action": "move", "captureId": app.last_screen["captureId"], "x": .5, "y": .5})
        self.assertTrue(app._capture_wake.is_set())
        app._capture_wake.clear()
        app._message(app.peer, {"type": "input", "action": "move", "captureId": "stale", "x": .5, "y": .5})
        self.assertFalse(app._capture_wake.is_set())
        image.close()

    def test_high_frequency_input_cannot_interrupt_active_frame_period(self):
        app = make_app(); app.shutdown = Mock(); app._capture_wake = Mock()
        app._capture_wait(.02)
        app._capture_wait(1 / 6)
        self.assertEqual(app.shutdown.wait.call_args_list, [((.02,),), ((1 / 6,),)])
        app._capture_wake.wait.assert_not_called()
        app._capture_wait(.2, wake_input=True)
        app._capture_wake.wait.assert_called_once_with(.2)
        app._capture_wake.clear.assert_called_once_with()

    def test_actual_legacy_two_to_five_fps_capture_wait_is_never_treated_as_idle(self):
        image = Image.new("RGB", (64, 36), "white")
        rect = {"left": 0, "top": 0, "width": 64, "height": 36}
        class Capture:
            monitors = [rect.copy(), rect.copy()]
            def grab(self, rectangle): return SimpleNamespace(size=image.size, rgb=image.tobytes())
            def close(self): pass
        for mode, tier in (("auto", 3), ("auto", 4), ("quality", 3), ("quality", 4)):
            app = make_app(); app.stream = stream.AdaptiveStream(); app.stream.enable(1)
            app.stream.set_mode(1, mode, 0); app.stream._tier = tier
            self.assertLessEqual(app.stream.target_fps, 5)
            waits = []
            def stop(seconds, *, wake_input=False):
                waits.append(wake_input); app.shutdown.set()
            app._capture_wait = stop
            with patch.dict(sys.modules, {"mss": SimpleNamespace(mss=Capture)}):
                app._capture_loop()
            self.assertEqual(waits, [False], (mode, tier))
            self.assertTrue(any(m["type"] == "screen" for m in app.peer.messages))
        image.close()

    def test_idle_raw_preview_is_not_retained_across_refinement_or_context_barrier(self):
        image = desktop((640, 360)); tracker = stream.ScreenContentTracker()
        raw = image.tobytes("raw", "BGRX"); context = ("peer", "capture")
        signature = tracker.candidate(image, context, 0, pixels=raw, bytes_per_pixel=4)
        tracker.published(signature, 0)
        self.assertTrue(tracker.retained_capture_matches(raw, image.size, context, .1, 200000))
        self.assertFalse(tracker.retained_capture_matches(raw, image.size, context, .36, 200000))
        self.assertFalse(tracker.retained_capture_matches(raw, image.size, ("other", "capture"), .1, 200000))
        image.close()

    def photo_host(self, image, mode, cycle, write=.005):
        now = [10.]; app = make_app(); acked = set(); captures = []
        app._message(app.peer, {"type": "stream_ready", "version": 1, "maxInFlight": 2,
                               "ackCumulative": True, "imageFormats": ["jpeg", "png"], "unchangedFrames": True})
        app._message(app.peer, {"type": "stream_settings", "version": 1, "mode": mode})
        frames = lambda: [m for m in app.peer.messages if m.get("type") == "screen"]
        app.peer.stream_stats = lambda: {"pending": False, "writing": False, "lastFrameDuration": write,
            "lastFrameBytes": len(frames()[-1]["image"]) + 1000 if frames() else 30000}
        rect = {"left": 0, "top": 0, "width": image.width, "height": image.height}
        raw = bytearray(image.tobytes("raw", "BGRX"))
        class Capture:
            monitors = [rect.copy(), rect.copy()]
            def grab(self, rectangle):
                captures.append(now[0]); return SimpleNamespace(size=image.size, raw=raw)
            def close(self): pass
        def wait(event, delay):
            sent = frames()
            if sent and sent[-1]["frameId"] not in acked:
                frame = sent[-1]
                now[0] = max(now[0], app.stream._pending[-1][1] + cycle)
                app._message(app.peer, {"type": "stream_feedback", "frameId": frame["frameId"],
                                       "decodeMs": 2, "renderMs": 2, "cumulative": True})
                acked.add(frame["frameId"])
            now[0] += max(.1, delay)
            if len(event.waits) >= 28: event.set()
        app.shutdown = WaitScript(wait)
        with patch.dict(sys.modules, {"mss": SimpleNamespace(mss=Capture)}), patch.object(host.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(host, "encode_lossless_refinement", side_effect=AssertionError("photo must remain JPEG")):
            app._capture_loop()
        return app, frames()

    def test_static_4k_photo_finishes_progressive_quality_before_retaining_fast_or_high_rtt(self):
        image = Image.frombytes("RGB", (3840, 2160), random.Random(774).randbytes(3840 * 2160 * 3))
        for mode in ("auto", "quality"):
            for cycle in (.05, .35):
                with self.subTest(mode=mode, cycle=cycle):
                    app, frames = self.photo_host(image, mode, cycle)
                    self.assertGreaterEqual(frames[-1]["frameWidth"], 1920)
                    self.assertGreaterEqual(frames[-1]["frameHeight"], 1080)
                    self.assertLess(len(frames), 20, "a learned static JPEG stops repetitive encoding")
                    self.assertTrue(any(m["type"] == "screen_unchanged" for m in app.peer.messages))
                    self.assertTrue(all(base64.b64decode(m["image"]).startswith(b"\xff\xd8") for m in frames))
                    self.assertTrue(app.capture_available)
        image.close()

    def test_slow_link_photo_can_retain_its_actual_available_tier(self):
        image = Image.frombytes("RGB", (1920, 1080), random.Random(775).randbytes(1920 * 1080 * 3))
        app, frames = self.photo_host(image, "auto", .6, write=.4)
        self.assertLess(frames[-1]["frameWidth"], 1920)
        self.assertLess(len(frames), 20)
        self.assertTrue(any(m["type"] == "screen_unchanged" for m in app.peer.messages))
        self.assertTrue(app.capture_available)
        image.close()


if __name__ == "__main__": unittest.main()
