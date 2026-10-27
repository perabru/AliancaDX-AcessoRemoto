"""Congestion/presentation flow control without sockets, clocks or a desktop."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adaptive_stream import ACK_PROBE_SECONDS, AdaptiveStream, PROFILES


IDLE = {"pending": False, "writing": False}


class DisplayWindowTests(unittest.TestCase):
    def stream(self):
        stream = AdaptiveStream()
        stream.synchronize(1)
        self.assertTrue(stream.enable(1))
        return stream

    def test_only_presented_current_frame_opens_the_window(self):
        stream = self.stream()
        self.assertTrue(stream.ready(10, IDLE))
        fields = stream.frame_fields(10)
        self.assertEqual(fields["streamVersion"], 1)
        self.assertFalse(stream.ready(10.1, IDLE))
        self.assertFalse(stream.feedback({"frameId": fields["frameId"] + 1}, 10.2))
        self.assertFalse(stream.ready(10.2, IDLE))
        self.assertTrue(stream.feedback({"frameId": fields["frameId"], "decodeMs": 3, "renderMs": 4}, 10.21))
        self.assertTrue(stream.ready(10.21, IDLE))
        next_frame = stream.frame_fields(10.21)
        self.assertGreater(next_frame["frameId"], fields["frameId"])
        self.assertFalse(stream.feedback({"frameId": fields["frameId"]}, 10.22))
        self.assertFalse(stream.ready(10.22, IDLE))

    def test_never_enqueues_another_image_while_previous_frame_is_on_wire(self):
        stream = self.stream()
        stream.frame_fields(0)
        for now in (0.5, 2.1, 10.0):
            self.assertFalse(stream.ready(now, {"pending": True, "writing": False}))
            self.assertFalse(stream.ready(now, {"pending": False, "writing": True}))
        self.assertTrue(stream.ready(10.01, IDLE))

    def test_failed_presentation_gets_exactly_one_small_probe_not_a_retry_flood(self):
        stream = self.stream()
        first = stream.frame_fields(0)
        first_width = stream.profile.width
        self.assertFalse(stream.ready(ACK_PROBE_SECONDS - .001, IDLE))
        self.assertTrue(stream.ready(ACK_PROBE_SECONDS, IDLE))
        self.assertLess(stream.profile.width, first_width)
        probe = stream.frame_fields(ACK_PROBE_SECONDS)
        for now in (4, 30, 120, 3600):
            self.assertFalse(stream.ready(now, IDLE))
            stream.enable(1)  # Repeated negotiation must not bypass the bound.
            self.assertFalse(stream.ready(now, IDLE))
        self.assertFalse(stream.feedback({"frameId": first["frameId"]}, 3600))
        self.assertTrue(stream.feedback({"frameId": probe["frameId"]}, 3600))
        self.assertTrue(stream.ready(3600, IDLE))

    def test_monitor_pause_resume_barrier_reopens_without_recycling_ids(self):
        stream = self.stream()
        first = stream.frame_fields(10)
        stream.synchronize(2)
        self.assertTrue(stream.ready(10.1, IDLE))
        second = stream.frame_fields(10.1)
        self.assertGreater(second["frameId"], first["frameId"])
        self.assertFalse(stream.feedback({"frameId": first["frameId"]}, 10.2))
        self.assertFalse(stream.ready(10.2, IDLE))

    def test_returning_viewport_recovers_after_both_frames_were_discarded(self):
        stream = self.stream()
        first = stream.frame_fields(0)
        self.assertTrue(stream.ready(2, IDLE))
        probe = stream.frame_fields(2)
        self.assertFalse(stream.ready(8, IDLE))
        self.assertTrue(stream.resume(1, 8))
        # A resume cannot cut ahead of bytes already being written.
        self.assertFalse(stream.ready(8, {"pending": False, "writing": True}))
        self.assertTrue(stream.ready(8.1, IDLE))
        returned = stream.frame_fields(8.1)
        self.assertGreater(returned["frameId"], probe["frameId"])
        for old in (first, probe):
            self.assertFalse(stream.feedback({"frameId": old["frameId"]}, 8.2))
        self.assertTrue(stream.feedback({"frameId": returned["frameId"]}, 8.3))

    def test_repeated_or_invalid_resume_cannot_continuously_open_the_window(self):
        stream = self.stream()
        stream.frame_fields(0)
        for version in (True, 0, 2, 1.0, "1"):
            self.assertFalse(stream.resume(version, 1))
        self.assertTrue(stream.resume(1, 1))
        pending = stream.frame_fields(1)
        for now in (1.01, 1.25, 1.999):
            self.assertFalse(stream.resume(1, now))
            self.assertEqual(stream.outstanding_frame_id, pending["frameId"])
        self.assertTrue(stream.resume(1, 2))
        legacy = AdaptiveStream()
        self.assertFalse(legacy.resume(1, 1))

    def test_invalid_ids_do_not_unlock_or_change_quality(self):
        stream = self.stream()
        stream.frame_fields(10)
        before = stream.profile
        for value in (True, 1.0, 0, -1, "1", None, float("nan"), 1 << 53):
            self.assertFalse(stream.feedback({"frameId": value}, 10.1))
        self.assertEqual(stream.profile, before)
        self.assertFalse(stream.ready(10.1, IDLE))


class AdaptationTests(unittest.TestCase):
    def acknowledge(self, stream, now, cycle, **fields):
        frame = stream.frame_fields(now)
        self.assertTrue(stream.feedback({"frameId": frame["frameId"], **fields}, now + cycle))
        return now + cycle

    def test_bad_connection_quickly_reduces_bytes_pixels_and_rate_then_recovers_slowly(self):
        stream = AdaptiveStream()
        stream.enable(1)
        initial = stream.profile
        now = self.acknowledge(stream, 10, .9)
        congested = stream.profile
        self.assertLess(congested.width * congested.height, initial.width * initial.height)
        self.assertLess(congested.quality, initial.quality)
        self.assertLess(congested.fps, initial.fps)
        # One fast reply is not enough to oscillate back to a large image.
        now = self.acknowledge(stream, now + .1, .04)
        self.assertEqual(stream.profile, congested)
        for _ in range(50):
            now = self.acknowledge(stream, now + .2, .04)
        self.assertGreater(stream.profile.width, congested.width)
        self.assertLessEqual(stream.profile.width, PROFILES[0].width)

    def test_slow_decoder_reduces_rate_before_pixels_on_a_fast_network(self):
        stream = AdaptiveStream()
        stream.enable(1)
        before = stream.profile
        self.acknowledge(stream, 1, .12, decodeMs=100, renderMs=10)
        self.assertEqual(stream.profile, before)
        self.assertLess(stream.target_fps, before.fps)

    def test_invalid_processing_metrics_do_not_crash_or_unlock_wrong_frame(self):
        stream = AdaptiveStream()
        stream.enable(1)
        frame = stream.frame_fields(1)
        self.assertTrue(stream.feedback({"frameId": frame["frameId"], "decodeMs": float("nan"),
                                         "renderMs": "fast"}, 1.1))
        self.assertTrue(stream.ready(1.1, IDLE))

    def test_legacy_controller_still_receives_frames_without_optional_ack(self):
        stream = AdaptiveStream()
        for version in (True, "1", 1.0, 0, 2, None):
            self.assertFalse(stream.enable(version))
        for now in range(20):
            self.assertTrue(stream.ready(now, IDLE))
            frame = stream.frame_fields(now)
            self.assertNotIn("frameId", frame)
            self.assertNotIn("streamVersion", frame)

    def test_legacy_slow_writer_applies_backpressure_and_reduces_next_jpeg(self):
        stream = AdaptiveStream()
        initial = stream.profile
        self.assertFalse(stream.ready(0, {"pending": True, "writing": False}))
        self.assertFalse(stream.ready(.5, {"pending": False, "writing": True}))
        self.assertLess(stream.profile.width, initial.width)
        self.assertTrue(stream.ready(1, {**IDLE, "lastFrameBytes": 100_000, "lastFrameDuration": .8}))
        self.assertLess(stream.profile.width, initial.width)
        self.assertNotIn("frameId", stream.frame_fields(1))

    def test_repeated_snapshot_does_not_count_as_many_completed_frames(self):
        stream = AdaptiveStream()
        snapshot = {**IDLE, "lastFrameBytes": 100_000, "lastFrameDuration": .8}
        stream.ready(1, snapshot)
        before = stream.profile
        for now in range(2, 50):
            self.assertTrue(stream.ready(now, snapshot))
        self.assertEqual(stream.profile, before)

    def test_no_transport_metrics_keeps_legacy_peers_compatible(self):
        stream = AdaptiveStream()
        for statistics in (None, object(), {"lastFrameDuration": float("inf"), "lastFrameBytes": 50}):
            self.assertTrue(stream.ready(10, statistics))


if __name__ == "__main__":
    unittest.main()
