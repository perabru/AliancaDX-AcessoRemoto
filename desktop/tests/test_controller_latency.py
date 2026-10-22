"""Presentation feedback and low-latency scheduling without a Tk display."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from PIL import Image
import controller
from test_controller_stability import app_stub, frame, move, Peer
from test_controller_network import network_app


def ready_app():
    app = app_stub()
    app.stream_ready_generation = app.generation
    app.last_stream_feedback_id = 0
    app.stream_active_generation = app.generation
    app.last_stream_resume_at = None
    app.stream_resume_needed = False
    return app


def stream_frame(frame_id=1):
    return dict(frame(), streamVersion=1, frameId=frame_id, _adxDecodeMs=4.25)


def queued_commands(app):
    commands = []
    while (item := app.outgoing.get(0)) is not None:
        commands.append(item[2])
    return commands


class ControllerLatencyTests(unittest.TestCase):
    def test_opt_in_is_sent_for_fresh_connected_peer_and_feedback_state_resets(self):
        app = network_app()
        app.last_stream_feedback_id = 99
        app.connecting = True
        app.connecting_endpoint = ("office.example", 64321)
        app._remember_connected_computer = Mock()
        app.workspace = SimpleNamespace(select=Mock())
        app.remote_page = "session"
        peer = Peer()
        with patch("transfer.FileTransfers", return_value=SimpleNamespace(close=Mock())):
            app._connected(peer)
        self.assertEqual(queued_commands(app), [{"type": "stream_ready", "version": 1}])
        self.assertEqual(app.stream_ready_generation, app.generation)
        self.assertEqual(app.last_stream_feedback_id, 0)

    def test_feedback_follows_successful_presentation_and_is_sent_only_once(self):
        app = ready_app()
        observed = []
        app._render_frame = lambda: observed.append("rendered") or True
        original_send = app._send
        def send(message):
            observed.append("feedback")
            return original_send(message)
        app._send = send
        image = Image.new("RGB", (100, 50))
        self.addCleanup(image.close)
        app._show_frame(stream_frame(3), image)
        app._ack_presented_frame(stream_frame(3), 7)
        app._ack_presented_frame(stream_frame(2), 7)
        self.assertEqual(observed, ["rendered", "feedback"])
        commands = queued_commands(app)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["type"], "stream_feedback")
        self.assertEqual(commands[0]["frameId"], 3)
        self.assertEqual(commands[0]["decodeMs"], 4.25)
        self.assertGreaterEqual(commands[0]["renderMs"], 0)

    def test_failed_or_unavailable_render_does_not_acknowledge_display(self):
        for result in (False, None):
            with self.subTest(render=result):
                app = ready_app()
                app._render_frame = Mock(return_value=result)
                image = Image.new("RGB", (100, 50))
                self.addCleanup(image.close)
                app._show_frame(stream_frame(), image)
                self.assertEqual(queued_commands(app), [])
                self.assertEqual(app.last_frame_at, 100.0)

    def test_pending_monitor_drop_is_not_reported_as_display(self):
        app = ready_app()
        app.pending_monitor = 1
        image = Image.new("RGB", (100, 50))
        self.addCleanup(image.close)
        app._show_frame(dict(stream_frame(), monitors=[{"id": "1"}]), image)
        app._render_frame.assert_not_called()
        self.assertEqual(queued_commands(app), [])

    def test_legacy_invalid_and_previous_session_frames_do_not_emit_feedback(self):
        app = ready_app()
        for message in (frame(), dict(stream_frame(), streamVersion=True),
                        dict(stream_frame(), streamVersion=2),
                        dict(stream_frame(), frameId=True), dict(stream_frame(), frameId=0),
                        dict(stream_frame(), frameId=2 ** 53), dict(stream_frame(), frameId="1")):
            app._ack_presented_frame(message, 1)
        app.stream_ready_generation = app.generation - 1
        app._ack_presented_frame(stream_frame(), 1)
        self.assertEqual(queued_commands(app), [])

    def test_feedback_is_bounded_and_does_not_send_peer_supplied_invalid_timing(self):
        app = ready_app()
        for index, value in enumerate((float("nan"), float("inf"), -1, 30_001, True, "5"), 1):
            app._ack_presented_frame(dict(stream_frame(index), _adxDecodeMs=value), value)
        commands = queued_commands(app)
        self.assertEqual(len(commands), 6)
        self.assertTrue(all(set(item) == {"type", "frameId"} for item in commands))

    def test_feedback_preserves_keyboard_and_mouse_order(self):
        app = ready_app()
        press = {"type": "input", "action": "button", "button": "left", "down": True}
        release = {"type": "input", "action": "release"}
        for command in (move(.1), move(.2), press):
            app._send(command)
        app._ack_presented_frame(stream_frame(), 1)
        for command in (move(.3), move(.4), release):
            app._send(command)
        commands = queued_commands(app)
        self.assertEqual(commands[:2], [move(.2), press])
        self.assertEqual(commands[2]["type"], "stream_feedback")
        self.assertEqual(commands[3:], [move(.4), release])

    def test_active_pump_uses_short_interval_and_disconnected_pump_stays_idle(self):
        app = app_stub()
        app._check_frame_health = Mock()
        app._pump()
        self.assertEqual(app.root.jobs[-1][1], 10)
        app.peer = None
        app._pump()
        self.assertEqual(app.root.jobs[-1][1], 30)

    def test_pointer_batches_the_latest_position_until_the_sixtieth_second(self):
        app = app_stub()
        app.last_move = 10.0
        with patch.object(controller.time, "monotonic", return_value=10.005):
            app._mouse_move(SimpleNamespace(x=30, y=15))
            app._mouse_move(SimpleNamespace(x=80, y=40))
        self.assertEqual(len(app.root.jobs), 1)
        self.assertLessEqual(app.root.jobs[0][1], 12)
        self.assertEqual(queued_commands(app), [])
        with patch.object(controller.time, "monotonic", return_value=10.017):
            app.root.jobs[0][2]()
        commands = queued_commands(app)
        self.assertEqual(len(commands), 1)
        self.assertAlmostEqual(commands[0]["x"], 80 / 99)
        self.assertAlmostEqual(commands[0]["y"], 40 / 49)

    def test_consecutive_bad_frames_request_bounded_recovery_and_fresh_frame_recovers(self):
        app = ready_app()
        with patch.object(controller.time, "monotonic", return_value=100.0):
            app._frame_failed("first bad JPEG")
        first = queued_commands(app)
        self.assertEqual([item for item in first if item["type"] == "stream_resume"],
                         [{"type": "stream_resume", "version": 1}])
        with patch.object(controller.time, "monotonic", return_value=100.1):
            app._frame_failed("second bad JPEG")
        self.assertFalse(any(item["type"] == "stream_resume" for item in queued_commands(app)))
        # A failed recovery probe is a new event, not a timer or a busy retry.
        with patch.object(controller.time, "monotonic", return_value=102.2):
            app._frame_failed("probe failed before display")
        self.assertEqual(sum(item["type"] == "stream_resume" for item in queued_commands(app)), 1)
        image = Image.new("RGB", (100, 50))
        self.addCleanup(image.close)
        app._show_frame(stream_frame(3), image)
        self.assertFalse(app.frame_stalled)
        self.assertFalse(app.stream_resume_needed)
        self.assertEqual(queued_commands(app)[-1]["frameId"], 3)
        self.assertFalse(app._resume_stream())
        self.assertEqual(app.root.jobs, [], "Recovery must not create a timer loop")

    def test_stale_frame_requests_a_fresh_stream_without_disconnecting_or_enabling_input(self):
        app = ready_app()
        app.buttons_down.add("left")
        with patch.object(controller.time, "monotonic", return_value=107.0):
            app._check_frame_health()
        self.assertTrue(app.frame_stalled)
        self.assertFalse(app.peer.closed)
        self.assertFalse(app._input_ready())
        self.assertEqual(app.buttons_down, set())
        self.assertEqual(queued_commands(app)[-1], {"type": "stream_resume", "version": 1})

    def test_resume_does_not_override_pause_prompt_or_another_connection(self):
        for gate in ("local_paused", "remote_prompt_active", "local_prompt_active", "dialog_active",
                     "network_suspended"):
            with self.subTest(gate=gate):
                app = ready_app()
                setattr(app, gate, True)
                self.assertFalse(app._resume_stream(needed=True))
                self.assertEqual(queued_commands(app), [])
        app = ready_app()
        app.sharing = False
        self.assertFalse(app._resume_stream(needed=True))
        app.sharing = True
        app.generation += 1
        self.assertFalse(app._resume_stream(needed=True))
        self.assertEqual(queued_commands(app), [])

    def test_resume_is_deferred_during_local_prompt_and_consumed_once_after_it(self):
        app = ready_app()
        app.local_prompt_active = True
        self.assertFalse(app._resume_stream(needed=True))
        self.assertTrue(app.stream_resume_needed)
        app._local_prompt_guard(False)
        self.assertEqual(queued_commands(app), [{"type": "stream_resume", "version": 1}])
        app._local_prompt_guard(False)
        self.assertEqual(queued_commands(app), [])

    def test_resume_is_not_sent_to_legacy_host_before_a_v1_frame(self):
        app = ready_app()
        app.stream_active_generation = None
        self.assertFalse(app._resume_stream(needed=True))
        self.assertEqual(queued_commands(app), [])

    def test_returning_to_viewer_with_an_unpresented_candidate_requests_new_frame(self):
        app = ready_app()
        app.workspace = SimpleNamespace(page_name=lambda page: page, show=Mock())
        app.page_buttons = {}
        app._resize = Mock()
        app.current_screen = stream_frame(2)
        app.current_image = Image.new("RGB", (100, 50))
        self.addCleanup(app.current_image.close)
        app.last_stream_feedback_id = 1
        controller.Controller._show_page(app, "session")
        self.assertEqual(queued_commands(app)[-1], {"type": "stream_resume", "version": 1})

    def test_successful_display_after_loss_consumes_deferred_resume(self):
        app = ready_app()
        app.stream_resume_needed = True
        image = Image.new("RGB", (100, 50))
        self.addCleanup(image.close)
        app._show_frame(stream_frame(), image)
        self.assertFalse(app.stream_resume_needed)
        self.assertEqual(queued_commands(app)[-1]["type"], "stream_feedback")


if __name__ == "__main__":
    unittest.main()
