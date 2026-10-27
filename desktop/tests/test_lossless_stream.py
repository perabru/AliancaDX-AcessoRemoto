"""7.6 lossless text, writer headroom and unchanged-screen regression checks."""
from io import BytesIO
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adaptive_stream import (AdaptiveStream, PROFILES, STREAM_POLICIES, ScreenContentTracker,
                             _fast_rgb_png, encode_screen_image, screen_text_candidate)
import host
from test_host_stability import WaitScript, make_app


def text_desktop(size=(1920, 1080)):
    image = Image.new("RGB", size, "#f3f5f8")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSansMono.ttf", 13)
    except OSError:
        font = ImageFont.load_default(size=13)
    for row, y in enumerate(range(20, min(size[1] - 20, 1040), 22)):
        draw.text((20, y), f"{row:03} const conexao = await acesso.open({{ seguro: true }}); RGB 0123456789", font=font,
                  fill=("#a31515" if row % 2 else "#005b9b"))
    return image


def writer(stream, *, duration=.005, size=300_000, presented_cycle=.05):
    stream.ready(1, {"pending": False, "writing": False, "lastFrameDuration": duration, "lastFrameBytes": size})
    if presented_cycle is not None:
        frame = stream.frame_fields(1, size)
        stream.feedback({"frameId": frame["frameId"], "decodeMs": 2, "renderMs": 2, "cumulative": True}, 1 + presented_cycle)


class LosslessImageTests(unittest.TestCase):
    def test_standard_fast_png_is_exact_rgb_including_1_pixel_edges_and_crc(self):
        source = text_desktop((1366, 768))
        source.putpixel((1365, 767), (17, 93, 251))
        encoded = _fast_rgb_png(source, 1024 * 1024)
        self.assertIsNotNone(encoded)
        with Image.open(BytesIO(encoded)) as decoded:
            decoded.verify()
        with Image.open(BytesIO(encoded)) as decoded:
            self.assertEqual(decoded.format, "PNG")
            self.assertEqual(decoded.mode, "RGB")
            self.assertEqual(decoded.size, source.size)
            self.assertEqual(decoded.tobytes(), source.tobytes())
        source.close()

    def test_native_lossless_4k_does_not_resize_or_encode_a_jpeg(self):
        source = text_desktop((3840, 2160))
        before = source.tobytes()
        with patch.object(Image.Image, "save", side_effect=AssertionError("native PNG must not encode JPEG")):
            data, actual, size = encode_screen_image(source, PROFILES[1], Image, allow_png=True,
                                                     native_png_budget=STREAM_POLICIES["auto"].frame_budget)
        self.assertIs(actual, PROFILES[1])
        self.assertEqual(size, (3840, 2160))
        self.assertLessEqual(4 * ((len(data) + 2) // 3) + 32768, STREAM_POLICIES["auto"].frame_budget)
        with Image.open(BytesIO(data)) as decoded:
            self.assertEqual(decoded.tobytes(), before)
        self.assertEqual(source.tobytes(), before)
        source.close()

    def test_selected_profile_lossless_is_no_larger_than_real_jpeg(self):
        source = text_desktop()
        legacy, _, legacy_size = encode_screen_image(source, PROFILES[1], Image)
        lossless, _, size = encode_screen_image(source, PROFILES[1], Image, allow_png=True)
        self.assertEqual(size, legacy_size)
        self.assertLessEqual(len(lossless), len(legacy))
        with Image.open(BytesIO(lossless)) as decoded:
            self.assertEqual(decoded.format, "PNG")
            self.assertEqual(decoded.tobytes(), source.tobytes())
        source.close()

    def test_photo_noise_avoids_full_png_work_and_retains_legacy_jpeg(self):
        source = Image.frombytes("RGB", (1280, 720), random.Random(7600).randbytes(1280 * 720 * 3))
        legacy = encode_screen_image(source, PROFILES[1], Image)
        with patch("adaptive_stream._fast_rgb_png", side_effect=AssertionError("photo should use JPEG")):
            result = encode_screen_image(source, PROFILES[1], Image, allow_png=True, native_png_budget=1024 * 1024)
        self.assertEqual(result, legacy)
        source.close()
        grayscale = Image.effect_noise((1280, 720), 60).convert("RGB")
        with patch("adaptive_stream._fast_rgb_png", side_effect=AssertionError("grayscale photo should use JPEG")):
            data, _, _ = encode_screen_image(grayscale, PROFILES[1], Image, allow_png=True)
        self.assertTrue(data.startswith(b"\xff\xd8"))
        grayscale.close()

    def test_classifier_handles_text_ui_with_a_twenty_percent_photo_region(self):
        source = text_desktop()
        photo = Image.frombytes("RGB", (384, 1080), random.Random(7630).randbytes(384 * 1080 * 3))
        source.paste(photo, (1536, 0))
        self.assertTrue(screen_text_candidate(source, Image))
        self.assertFalse(screen_text_candidate(photo, Image))
        source.close(); photo.close()

    def test_insufficient_native_budget_falls_back_without_oversized_write(self):
        source = text_desktop((3840, 2160))
        data, actual, size = encode_screen_image(source, PROFILES[1], Image, allow_png=True, native_png_budget=32769)
        self.assertLessEqual(size[0], actual.width)
        self.assertLessEqual(size[1], actual.height)
        self.assertLessEqual(4 * ((len(data) + 2) // 3) + 32768, STREAM_POLICIES["auto"].frame_budget)
        source.close()

    def test_latency_mode_remains_jpeg_with_same_fast_low_byte_policy(self):
        source = text_desktop()
        policy = STREAM_POLICIES["latency"]
        with patch("adaptive_stream._fast_rgb_png", side_effect=AssertionError("latency remains JPEG")):
            data, actual, size = encode_screen_image(source, policy.profiles[1], Image, "latency",
                                                     allow_png=True, native_png_budget=1024 * 1024)
        with Image.open(BytesIO(data)) as decoded:
            self.assertEqual(decoded.format, "JPEG")
        self.assertLessEqual(4 * ((len(data) + 2) // 3) + 32768, policy.frame_budget)
        source.close()

    def test_fast_png_rejects_over_4k_or_non_rgb_source_and_aborts_budget(self):
        for size, mode in (((3841, 2160), "RGB"), ((3840, 2161), "RGB"), ((10, 10), "RGBA")):
            source = Image.new(mode, size)
            self.assertIsNone(_fast_rgb_png(source, 1024 * 1024))
            source.close()
        source = text_desktop((640, 360))
        self.assertIsNone(_fast_rgb_png(source, 32))
        source.close()


class CapabilityAndBudgetTests(unittest.TestCase):
    def test_png_requires_valid_explicit_first_negotiation(self):
        for formats in (None, "png", ["png"], ["jpeg"], ["jpeg", True, "png"], ["jpeg", "PNG"], ["jpeg", "png", "a", "b", "c"]):
            stream = AdaptiveStream()
            self.assertTrue(stream.enable(1, 2, True, formats))
            self.assertFalse(stream.png_enabled, formats)
            stream.enable(1, 2, True, ["jpeg", "png"])
            self.assertFalse(stream.png_enabled, "repeated negotiation cannot replace a negotiated format set")
        stream = AdaptiveStream()
        self.assertFalse(stream.enable(True, 2, True, ["jpeg", "png"]))
        self.assertFalse(stream.png_enabled)
        self.assertTrue(stream.enable(1, 2, True, ["jpeg", "png"]))
        self.assertTrue(stream.png_enabled)

    def test_native_budget_requires_observed_capacity_and_is_mode_bounded(self):
        stream = AdaptiveStream(); stream.enable(1, 2, True, ["jpeg", "png"])
        self.assertEqual(stream.native_png_budget, 0)
        writer(stream)
        self.assertEqual(stream.native_png_budget, STREAM_POLICIES["auto"].frame_budget)
        writer(stream, duration=.03, size=90_000)
        self.assertEqual(stream.native_png_budget, 120_000)
        writer(stream, duration=.05)
        self.assertEqual(stream.native_png_budget, 0)

    def test_native_opportunity_never_bypasses_cpu_congestion_probe_or_latency_mode(self):
        for restricted in ("cpu", "tier", "probe", "latency", "legacy"):
            stream = AdaptiveStream(); stream.enable(1, 2, True, ["jpeg", "png"] if restricted != "legacy" else None)
            writer(stream)
            if restricted == "cpu": stream._quality_cadence = 2
            elif restricted == "tier": stream._tier = 2
            elif restricted == "probe": stream._probe_ready = True
            elif restricted == "latency": stream.set_mode(1, "latency", 2)
            self.assertEqual(stream.native_png_budget, 0, restricted)

    def test_one_encoder_throttle_does_not_trap_native_text_behind_slow_resizing(self):
        stream = AdaptiveStream(); stream.enable(1, 2, True, ["jpeg", "png"])
        self.assertEqual(stream.native_png_budget, 0)
        stream.capture_finished(.25, 1)
        self.assertEqual(stream.target_fps, 6)
        writer(stream)
        self.assertGreater(stream.native_png_budget, 0)
        # The measured native path can complete inside the lowered cadence's
        # budget while keeping its pixels; a second real overload disables it.
        stream.capture_finished(.12, 2)
        self.assertEqual(stream._quality_cadence, 1)
        self.assertGreater(stream.native_png_budget, 0)
        stream.capture_finished(.5, 3)
        self.assertEqual(stream._quality_cadence, 2)
        self.assertEqual(stream.native_png_budget, 0)

    def test_socket_buffer_speed_cannot_replace_actual_presented_frame_evidence(self):
        stream = AdaptiveStream(); stream.enable(1, 2, True, ["jpeg", "png"])
        writer(stream, duration=.001, size=304_320, presented_cycle=None)
        self.assertEqual(stream.native_png_budget, 0, "no presentation ACK means no native expansion")
        initial = stream.profile
        writer(stream, duration=.001, size=304_320, presented_cycle=1.5)
        self.assertEqual(stream.native_png_budget, 0, "fast kernel writes must not expand a slow presented frame")
        self.assertEqual(stream.profile, initial, "stable RTT may disable opportunism without degrading existing resolution")
        writer(stream, duration=.001, size=304_320, presented_cycle=.05)
        self.assertGreaterEqual(stream.native_png_budget, 681_640)

    def test_ack_projection_bounds_expansion_even_below_the_cycle_threshold(self):
        stream = AdaptiveStream(); stream.enable(1, 2, True, ["jpeg", "png"])
        writer(stream, duration=.001, size=300_000, presented_cycle=.20)
        self.assertEqual(stream.native_png_budget, 330_000)
        self.assertLess(stream.native_png_budget, 681_640)
        stream.set_mode(1, "quality", 2)
        self.assertEqual(stream.native_png_budget, 0, "new mode requires fresh presentation evidence")
        writer(stream, duration=.001, size=300_000, presented_cycle=.30)
        self.assertGreaterEqual(stream.native_png_budget, 319_999)
        self.assertLessEqual(stream.native_png_budget, 320_000)
        writer(stream, duration=.001, size=300_000, presented_cycle=.33)
        self.assertEqual(stream.native_png_budget, 0)

    def test_pause_resume_and_unknown_ack_cannot_reuse_past_native_headroom(self):
        stream = AdaptiveStream(); stream.synchronize(1); stream.enable(1, 2, True, ["jpeg", "png"])
        writer(stream)
        self.assertGreater(stream.native_png_budget, 0)
        stream.synchronize(2)
        self.assertEqual(stream.native_png_budget, 0)
        self.assertFalse(stream.feedback({"frameId": 999_999}, 2))
        self.assertEqual(stream.native_png_budget, 0)
        writer(stream)
        self.assertGreater(stream.native_png_budget, 0)
        self.assertTrue(stream.resume(1, 4))
        self.assertEqual(stream.native_png_budget, 0)
        frame = stream.frame_fields(4)  # No known wire bytes: not enough evidence.
        self.assertTrue(stream.feedback({"frameId": frame["frameId"]}, 4.05))
        self.assertEqual(stream.native_png_budget, 0)

    def test_context_revision_changes_on_pause_mode_and_resume_but_ids_are_monotonic(self):
        stream = AdaptiveStream(); stream.enable(1, 2, True, ["jpeg", "png"])
        stream.synchronize(1); first_revision = stream.content_revision
        first = stream.frame_fields(1)
        stream.set_mode(1, "quality", 2)
        self.assertGreater(stream.content_revision, first_revision)
        revision = stream.content_revision
        stream.synchronize(2)
        self.assertGreater(stream.content_revision, revision)
        revision = stream.content_revision
        self.assertTrue(stream.resume(1, 4))
        self.assertGreater(stream.content_revision, revision)
        self.assertGreater(stream.frame_fields(4)["frameId"], first["frameId"])


class UnchangedSceneTests(unittest.TestCase):
    def test_unchanged_capture_skips_until_bounded_refresh_and_not_before_publish(self):
        tracker = ScreenContentTracker(); source = text_desktop((640, 360))
        signature = tracker.candidate(source, ("peer", 1), 10)
        self.assertIsNotNone(signature)
        self.assertIsNotNone(tracker.candidate(source, ("peer", 1), 10.1))
        tracker.published(signature, 10.1)
        self.assertIsNone(tracker.candidate(source, ("peer", 1), 10.59))
        self.assertIsNotNone(tracker.candidate(source, ("peer", 1), 10.6))
        source.close()

    def test_one_pixel_or_geometry_context_change_is_never_suppressed(self):
        tracker = ScreenContentTracker(); source = text_desktop((640, 360))
        tracker.published(tracker.candidate(source, ("peer", 1), 10), 10)
        self.assertIsNotNone(tracker.candidate(source, ("peer", 2), 10.01))
        source.putpixel((639, 359), (1, 2, 3))
        self.assertIsNotNone(tracker.candidate(source, ("peer", 1), 10.01))
        source.close()


class HostLosslessTests(unittest.TestCase):
    def capture(self, source, *, action=None, iterations=8, writer_headroom=True):
        now = [10.0]
        app = make_app()
        app._message(app.peer, {"type": "stream_ready", "version": 1, "maxInFlight": 2,
                              "ackCumulative": True, "imageFormats": ["jpeg", "png"]})
        app.peer.stream_stats = lambda: ({"pending": False, "writing": False,
                                         "lastFrameBytes": 300_000, "lastFrameDuration": .005}
                                        if writer_headroom else {"pending": False, "writing": False})
        monitor = {"left": -source.width, "top": 37, "width": source.width, "height": source.height}
        captures = []
        class Capture:
            monitors = [monitor.copy(), monitor.copy()]
            def grab(self, rectangle):
                captures.append(1)
                return SimpleNamespace(size=source.size, rgb=source.tobytes())
            def close(self): pass
        app._test_capture_monitors = Capture.monitors
        def wait(event, delay):
            frames = [frame for frame in app.peer.messages if frame.get("type") == "screen"]
            if frames:
                app._message(app.peer, {"type": "stream_feedback", "frameId": frames[-1]["frameId"],
                                       "decodeMs": 2, "renderMs": 2, "cumulative": True})
            if action is not None: action(app, len(event.waits), source)
            now[0] += .1
            if len(event.waits) >= iterations: event.set()
        app.shutdown = WaitScript(wait)
        with patch.dict(sys.modules, {"mss": SimpleNamespace(mss=Capture)}), patch.object(host.time, "monotonic", side_effect=lambda: now[0]):
            app._capture_loop()
        return app, captures, [frame for frame in app.peer.messages if frame.get("type") == "screen"]

    def test_host_native_png_reports_actual_dimensions_and_physical_coordinates(self):
        source = text_desktop((3840, 2160))
        app, captures, frames = self.capture(source, iterations=2)
        self.assertEqual(len(frames), 2)
        frame = frames[-1]
        self.assertEqual((frame["frameWidth"], frame["frameHeight"]), source.size)
        self.assertEqual((frame["left"], frame["top"], frame["width"], frame["height"]), (-3840, 37, 3840, 2160))
        import base64
        with Image.open(BytesIO(base64.b64decode(frame["image"]))) as decoded:
            self.assertEqual(decoded.format, "PNG")
            self.assertEqual(decoded.tobytes(), source.tobytes())
        self.assertEqual(app.stream.max_in_flight, 1)
        source.close()

    def test_real_host_first_scaled_frame_and_feedback_can_reach_native_lossless(self):
        source = text_desktop((3840, 2160)); now = [10.0]
        app = make_app(); writes = []
        app._message(app.peer, {"type": "stream_ready", "version": 1, "maxInFlight": 2,
                               "ackCumulative": True, "imageFormats": ["jpeg", "png"]})
        app.peer.stream_stats = lambda: ({"pending": False, "writing": False} if not writes else
                                        {"pending": False, "writing": False, "lastFrameBytes": 300_000, "lastFrameDuration": .005})
        rectangle = {"left": 0, "top": 0, "width": 3840, "height": 2160}
        class Capture:
            monitors = [rectangle.copy(), rectangle.copy()]
            def grab(self, rectangle): return SimpleNamespace(size=source.size, rgb=source.tobytes())
            def close(self): pass
        def encode(*args, **kwargs):
            value = encode_screen_image(*args, **kwargs)
            # Reproduce the first resize CPU pressure without a slow test clock.
            now[0] += .25 if not writes else .08
            writes.append(value)
            return value
        def wait(event, delay):
            frame = [frame for frame in app.peer.messages if frame.get("type") == "screen"][-1]
            app._message(app.peer, {"type": "stream_feedback", "frameId": frame["frameId"],
                                    "decodeMs": 2, "renderMs": 2, "cumulative": True})
            now[0] += .1
            if len(event.waits) >= 2: event.set()
        app.shutdown = WaitScript(wait)
        with patch.dict(sys.modules, {"mss": SimpleNamespace(mss=Capture)}), patch.object(host.time, "monotonic", side_effect=lambda: now[0]), patch.object(host, "encode_screen_image", side_effect=encode):
            app._capture_loop()
        frames = [frame for frame in app.peer.messages if frame.get("type") == "screen"]
        self.assertEqual([(frame["frameWidth"], frame["frameHeight"]) for frame in frames], [(1920, 1080), (3840, 2160)])
        self.assertTrue(writes[0][0].startswith(b"\xff\xd8"))
        self.assertTrue(writes[1][0].startswith(b"\x89PNG"))
        self.assertEqual(app.stream._quality_cadence, 1)
        source.close()

    def test_host_suppresses_unchanged_compression_and_network_until_refresh(self):
        source = text_desktop((640, 360))
        app, captures, frames = self.capture(source)
        self.assertEqual(len(captures), 8)
        self.assertEqual(len(frames), 2)
        self.assertGreater(frames[1]["frameId"], frames[0]["frameId"])
        self.assertTrue(app.capture_available)
        source.close()

    def test_host_photo_video_does_not_copy_full_rgb_to_hash_each_frame(self):
        source = Image.frombytes("RGB", (1280, 720), random.Random(7620).randbytes(1280 * 720 * 3))
        with patch.object(ScreenContentTracker, "candidate", side_effect=AssertionError("video must not pay full digest cost")):
            app, captures, frames = self.capture(source, iterations=3)
        self.assertEqual(len(frames), 3)
        self.assertEqual(len(captures), 3)
        source.close()

    def test_host_scaled_fallback_does_not_hash_full_4k_when_native_is_unaffordable(self):
        source = text_desktop((3840, 2160))
        with patch.object(ScreenContentTracker, "candidate", side_effect=AssertionError("scaled fallback must not pay full digest cost")):
            app, captures, frames = self.capture(source, iterations=2, writer_headroom=False)
        self.assertEqual(len(frames), 2)
        self.assertEqual([(frame["frameWidth"], frame["frameHeight"]) for frame in frames], [(1920, 1080)] * 2)
        source.close()

    def test_physical_monitor_origin_change_never_hides_behind_unchanged_pixels(self):
        source = text_desktop((640, 360))
        def action(app, count, image):
            if count == 1:
                app._test_capture_monitors[0]["left"] += 50
                app._test_capture_monitors[1]["left"] += 50
        app, captures, frames = self.capture(source, action=action, iterations=2)
        self.assertEqual(len(frames), 2)
        self.assertNotEqual(frames[0]["captureId"], frames[1]["captureId"])
        self.assertEqual(frames[1]["left"], frames[0]["left"] + 50)
        source.close()

    def test_new_mode_and_single_changed_pixel_force_immediate_fresh_frame(self):
        source = text_desktop((640, 360))
        def action(app, count, image):
            if count == 1:
                app._message(app.peer, {"type": "stream_settings", "version": 1, "mode": "quality"})
            elif count == 2:
                image.putpixel((639, 359), (1, 2, 3))
        app, captures, frames = self.capture(source, action=action, iterations=3)
        self.assertEqual(len(frames), 3)
        self.assertEqual([frame["streamMode"] for frame in frames], ["auto", "quality", "quality"])
        source.close()


if __name__ == "__main__":
    unittest.main()
