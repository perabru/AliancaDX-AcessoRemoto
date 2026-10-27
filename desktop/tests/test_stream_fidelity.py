"""Real pixels, source-preserving fallback and readable-quality adaptation."""
from io import BytesIO
from pathlib import Path
import random
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adaptive_stream import AdaptiveStream, PROFILES, STREAM_POLICIES, encode_screen_image
from desktop_geometry import physical_point
import host
from test_host_stability import WaitScript, make_app


def idle(write=.01):
    return {"pending": False, "writing": False,
            "lastFrameBytes": 100_000, "lastFrameDuration": write}


class FidelityTests(unittest.TestCase):
    def test_native_4k_quality_retains_every_source_pixel_position(self):
        source = Image.new("RGB", (3840, 2160), "white")
        ImageDraw.Draw(source).text((20, 20), "AliançaDX 0123456789 texto RGB", fill="red")
        original = source.tobytes()
        data, actual, size = encode_screen_image(source, STREAM_POLICIES["quality"].profiles[0], Image, "quality")
        with Image.open(BytesIO(data)) as decoded:
            self.assertEqual(decoded.size, source.size)
            self.assertEqual(decoded.size, size)
        self.assertEqual(actual.quality, 94)
        self.assertEqual(actual.subsampling, 0)
        self.assertEqual(source.tobytes(), original)
        self.assertLessEqual(4 * ((len(data) + 2) // 3) + 32768, 2 * 1024 * 1024)
        source.close()

    def test_fallback_jpeg_equals_one_resize_of_original_not_successive_reductions(self):
        source = Image.frombytes("RGB", (2560, 1440), random.Random(75).randbytes(2560 * 1440 * 3))
        original = source.tobytes()
        data, actual, size = encode_screen_image(source, PROFILES[0], Image)
        self.assertNotEqual(actual, PROFILES[0])
        direct = source.copy()
        direct.thumbnail((actual.width, actual.height), Image.Resampling.LANCZOS)
        with BytesIO() as output:
            direct.save(output, "JPEG", quality=actual.quality, subsampling=actual.subsampling, optimize=False)
            self.assertEqual(data, output.getvalue())
        self.assertEqual(size, direct.size)
        self.assertEqual(source.tobytes(), original)
        direct.close(); source.close()

    def test_no_mode_upscales_a_smaller_capture(self):
        source = Image.new("RGB", (640, 360), "white")
        for mode, policy in STREAM_POLICIES.items():
            data, actual, size = encode_screen_image(source, policy.profiles[0], Image, mode)
            self.assertEqual(size, source.size)
            with Image.open(BytesIO(data)) as decoded:
                self.assertEqual(decoded.size, size)
        source.close()

    def test_each_fallback_uses_a_temporary_buffer_and_releases_it_on_save_error(self):
        source = Image.new("RGB", (3000, 2000), "white")
        original = source.tobytes()
        temporary = []
        original_copy = Image.Image.copy
        def track_copy(image):
            result = original_copy(image)
            temporary.append(result)
            return result
        with patch.object(Image.Image, "copy", track_copy), patch.object(Image.Image, "save", side_effect=ValueError("encoding failed")):
            with self.assertRaisesRegex(ValueError, "encoding failed"):
                encode_screen_image(source, PROFILES[0], Image)
        self.assertEqual(len(temporary), 1)
        with self.assertRaises(ValueError):
            temporary[0].getpixel((0, 0))
        self.assertEqual(source.tobytes(), original)
        source.close()

    def test_quality_prioritizes_pixels_by_reducing_rate_before_resolution(self):
        stream = AdaptiveStream(); stream.enable(1); stream.set_mode(1, "quality", 0)
        initial = stream.profile
        for i in range(2):
            now = 1. + i
            self.assertTrue(stream.ready(now, idle(.5)))
            frame = stream.frame_fields(now, 100_000)
            self.assertTrue(stream.feedback({"frameId": frame["frameId"]}, now + .6))
            self.assertEqual(stream.profile, initial)
            self.assertEqual(stream.target_fps, max(2, initial.fps // (2 ** (i + 1))))
        self.assertTrue(stream.ready(3, idle(.5)))
        frame = stream.frame_fields(3, 100_000)
        stream.feedback({"frameId": frame["frameId"]}, 3.6)
        self.assertLess(stream.profile.width, initial.width)

    def test_severely_slow_quality_writer_still_reduces_pixels_immediately(self):
        stream = AdaptiveStream(); stream.enable(1); stream.set_mode(1, "quality", 0)
        initial = stream.profile
        stream.ready(1, idle(1.5)); frame = stream.frame_fields(1, 100_000)
        stream.feedback({"frameId": frame["frameId"]}, 2.6)
        self.assertLess(stream.profile.width, initial.width)
        self.assertEqual(stream.max_in_flight, 1)

    def test_stable_high_rtt_does_not_force_quality_down_on_fast_writer(self):
        stream = AdaptiveStream(); stream.enable(1, 2, True); stream.set_mode(1, "quality", 0)
        for i in range(60):
            now = i * .6 + 1
            self.assertTrue(stream.ready(now, idle(.01)))
            frame = stream.frame_fields(now, 500_000)
            stream.feedback({"frameId": frame["frameId"], "decodeMs": 30, "renderMs": 20}, now + .5)
        self.assertEqual(stream.profile, STREAM_POLICIES["quality"].profiles[0])
        self.assertEqual(stream.max_in_flight, 1)

    def test_quality_cpu_throttle_respects_available_frame_period(self):
        stream = AdaptiveStream(); stream.set_mode(1, "quality", 0)
        initial = stream.profile
        stream.capture_finished(.25, 1)
        self.assertEqual(stream.profile, initial)
        self.assertEqual(stream.target_fps, initial.fps // 2)
        for now in (2, 3, 4):
            stream.capture_finished(.25, now)
        self.assertEqual(stream.profile, initial)
        self.assertEqual(stream.target_fps, initial.fps // 2)

    def test_quality_recovers_native_4k_despite_expensive_intermediate_resizing(self):
        stream = AdaptiveStream(); stream.enable(1); stream.set_mode(1, "quality", 0)
        for i in range(100):
            now = 1. + i * .7
            self.assertTrue(stream.ready(now, idle(.02)))
            # Resizing a 4K source to 1440p can cost more than encoding native.
            stream.capture_finished(.30 if stream.profile.width < 3840 else .08, now)
            frame = stream.frame_fields(now, 500_000)
            stream.feedback({"frameId": frame["frameId"], "decodeMs": 35, "renderMs": 20}, now + .35)
        self.assertEqual(stream.profile.width, 3840)
        self.assertEqual(stream.target_fps, stream.profile.fps)

    def test_automatic_cpu_throttle_recovers_with_sustained_fast_feedback(self):
        stream = AdaptiveStream(); stream.enable(1)
        stream.capture_finished(.3, 1)
        self.assertLess(stream.target_fps, stream.profile.fps)
        for i in range(100):
            now = i * .3 + 2
            stream.ready(now, idle(.005)); frame = stream.frame_fields(now, 100_000)
            stream.feedback({"frameId": frame["frameId"], "decodeMs": 2, "renderMs": 2}, now + .04)
        self.assertEqual(stream.profile, PROFILES[0])
        self.assertEqual(stream.target_fps, stream.profile.fps)

    def test_auto_slow_receiver_uses_lower_rate_before_sacrificing_text_pixels(self):
        stream = AdaptiveStream(); stream.enable(1)
        initial = stream.profile
        for i in range(20):
            now = 1. + i * .5
            stream.ready(now, idle(.01)); frame = stream.frame_fields(now, 200_000)
            stream.feedback({"frameId": frame["frameId"], "decodeMs": 100, "renderMs": 20}, now + .35)
            self.assertEqual(stream.profile, initial)
        self.assertEqual(stream.target_fps, initial.fps // 2)
        # Writer pressure remains a signal to shrink a single slow frame;
        # lowering capture rate cannot make that large write complete sooner.
        stream.ready(20, idle(.35)); frame = stream.frame_fields(20, 200_000)
        stream.feedback({"frameId": frame["frameId"], "decodeMs": 100, "renderMs": 20}, 20.9)
        self.assertLess(stream.profile.width, initial.width)

    def test_quality_missing_ack_probe_is_smaller_and_still_single(self):
        stream = AdaptiveStream(); stream.enable(1); stream.set_mode(1, "quality", 0)
        initial = stream.profile; stream.frame_fields(0, 500_000)
        self.assertTrue(stream.ready(2, idle()))
        self.assertLess(stream.profile.width, initial.width)
        probe = stream.frame_fields(2, 100_000)
        self.assertFalse(stream.ready(20, idle()))
        self.assertEqual(stream.outstanding_frame_id, probe["frameId"])


class EncodedGeometryTests(unittest.TestCase):
    def test_host_keeps_physical_geometry_and_reports_real_jpeg_dimensions(self):
        rectangle = {"left": -3840, "top": 200, "width": 3840, "height": 2160}
        source = Image.new("RGB", (3840, 2160), "white")
        class Capture:
            monitors = [rectangle.copy(), rectangle.copy()]
            def grab(self, rectangle):
                return SimpleNamespace(size=source.size, rgb=source.tobytes())
            def close(self): pass
        app = make_app(); app._message(app.peer, {"type": "stream_settings", "version": 1, "mode": "quality"})
        app.shutdown = WaitScript(lambda event, delay: event.set())
        with patch.dict(sys.modules, {"mss": SimpleNamespace(mss=Capture)}):
            app._capture_loop()
        frames = [message for message in app.peer.messages if message["type"] == "screen"]
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertEqual((frame["width"], frame["height"]), (3840, 2160))
        self.assertEqual((frame["frameWidth"], frame["frameHeight"]), (2560, 1440))
        self.assertEqual(physical_point(app.last_screen, 1, 1), (-1, 2359))
        self.assertEqual(frame["streamMode"], "quality")
        self.assertEqual(source.size, (3840, 2160)); source.close()


if __name__ == "__main__":
    unittest.main()
