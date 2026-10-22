"""Host integration: fresh capture waits for wire/presentation capacity."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import host
from test_host_stability import Capture, Peer, WaitScript, make_app, providers


class HostStreamTests(unittest.TestCase):
    def test_frame_rate_budget_includes_capture_and_encoding_work(self):
        app = make_app()
        now = [0.]
        app.peer.stream_stats = lambda: {"pending": False, "writing": False}
        capture = Capture()
        original_grab = capture.grab

        def grab(rectangle):
            now[0] += .04
            return original_grab(rectangle)

        capture.grab = grab
        app.shutdown = WaitScript(lambda event, delay: event.set())
        with providers(lambda: capture), patch.object(host.time, "monotonic", side_effect=lambda: now[0]):
            app._capture_loop()
        self.assertEqual(capture.grabs, 1)
        self.assertAlmostEqual(app.shutdown.waits[0], 1.0 / host.FPS - .04)

    def test_capture_waits_for_socket_and_display_then_captures_fresh(self):
        app = make_app()
        now, busy = [0.], [True]
        app.peer.stream_stats = lambda: {"pending": busy[0], "writing": False}
        app._message(app.peer, {"type": "stream_ready", "version": 1})
        capture = Capture()
        first_id = []

        def advance(event, delay):
            frames = [message for message in app.peer.messages if message["type"] == "screen"]
            if len(event.waits) == 1:
                self.assertEqual(capture.grabs, 0)
                self.assertEqual(frames, [])
                busy[0], now[0] = False, .1
            elif len(event.waits) == 2:
                self.assertEqual(capture.grabs, 1)
                self.assertEqual(len(frames), 1)
                self.assertEqual(frames[0]["streamVersion"], 1)
                first_id.append(frames[0]["frameId"])
                now[0] = .2
            elif len(event.waits) == 3:
                self.assertEqual(capture.grabs, 1)
                self.assertEqual(len(frames), 1)
                app._message(app.peer, {"type": "stream_feedback", "frameId": first_id[0],
                                       "decodeMs": 4, "renderMs": 5})
                now[0] = .3
            else:
                self.assertEqual(capture.grabs, 2)
                self.assertEqual(len(frames), 2)
                self.assertGreater(frames[-1]["frameId"], first_id[0])
                event.set()

        app.shutdown = WaitScript(advance)
        with providers(lambda: capture), patch.object(host.time, "monotonic", side_effect=lambda: now[0]):
            app._capture_loop()
        self.assertEqual(len(app.shutdown.waits), 4)
        self.assertEqual(capture.closes, 1)

    def test_only_current_authorized_peer_can_enable_or_acknowledge_stream(self):
        app = make_app()
        app._message(Peer(), {"type": "stream_ready", "version": 1})
        self.assertIsNone(getattr(app, "stream", None))
        app._message(app.peer, {"type": "stream_ready", "version": True})
        self.assertFalse(app.stream.negotiated)
        app._message(app.peer, {"type": "stream_ready", "version": 1})
        self.assertTrue(app.stream.negotiated)
        fields = app.stream.frame_fields(10)
        app._message(Peer(), {"type": "stream_feedback", "frameId": fields["frameId"]})
        self.assertEqual(app.stream.outstanding_frame_id, fields["frameId"])
        with patch.object(host.time, "monotonic", return_value=10.1):
            app._message(app.peer, {"type": "stream_feedback", "frameId": fields["frameId"]})
        self.assertIsNone(app.stream.outstanding_frame_id)

    def test_pause_generation_ignores_old_feedback_and_preserves_local_control_guard(self):
        app = make_app()
        app._message(app.peer, {"type": "stream_ready", "version": 1})
        fields = app.stream.frame_fields(10)
        app.inputs.held_keys.add("ctrl")
        app.toggle_pause()
        app._message(app.peer, {"type": "stream_feedback", "frameId": fields["frameId"]})
        self.assertIsNone(app.stream.outstanding_frame_id)
        self.assertTrue(app.local_paused)
        self.assertFalse(app.sharing)
        self.assertEqual(app.inputs.held_keys, set())
        self.assertIsNone(app.last_screen)
        self.assertTrue(app.peer.messages[-1]["localPaused"])

    def test_only_current_peer_can_resume_a_viewport_and_cannot_bypass_local_pause(self):
        app = make_app()
        app._message(app.peer, {"type": "stream_ready", "version": 1})
        original = app.stream.frame_fields(10)
        app._message(Peer(), {"type": "stream_resume", "version": 1})
        self.assertEqual(app.stream.outstanding_frame_id, original["frameId"])
        with patch.object(host.time, "monotonic", return_value=10.1):
            app._message(app.peer, {"type": "stream_resume", "version": 1})
        self.assertIsNone(app.stream.outstanding_frame_id)
        app.toggle_pause()
        with patch.object(host.time, "monotonic", return_value=12):
            app._message(app.peer, {"type": "stream_resume", "version": 1})
        self.assertTrue(app.local_paused)
        self.assertFalse(app.sharing)
        self.assertIsNone(app.last_screen)


if __name__ == "__main__":
    unittest.main()
