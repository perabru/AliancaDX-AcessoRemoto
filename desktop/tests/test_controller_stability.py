"""Headless regressions for controller backpressure and session boundaries.

The tests exercise the real queues, worker, UI pump and frame lifecycle.  Only
Tk widgets and the network endpoint are replaced with small observable stubs.
"""
from pathlib import Path
import queue
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import controller
from controller import Controller, _OutgoingCommands


class Value:
    def __init__(self, value=None): self.value = value
    def set(self, value): self.value = value
    def get(self): return self.value


class Widget:
    def __init__(self):
        self.options = {}
        self.items = {}
        self.grabbed = False
        self.focused = False
    def configure(self, **values): self.options.update(values)
    def itemconfigure(self, item, **values): self.items.setdefault(item, {}).update(values)
    def grab_set(self): self.grabbed = True
    def grab_release(self): self.grabbed = False
    def focus_set(self): self.focused = True


class Root:
    def __init__(self): self.jobs = []; self.cancelled = []
    def after(self, delay, callback):
        identity = f"job-{len(self.jobs) + 1}"
        self.jobs.append((identity, delay, callback))
        return identity
    def after_cancel(self, job): self.cancelled.append(job)


class Peer:
    def __init__(self):
        self.closed = False
        self.messages = []
        self.close_reasons = []
        self.thread_ids = []
    def send(self, message):
        self.messages.append(dict(message))
        self.thread_ids.append(threading.get_ident())
        return True
    def close(self, reason=None):
        self.closed = True
        self.close_reasons.append(reason)


def move(value, capture="screen-one"):
    return {"type": "input", "action": "move", "captureId": capture, "x": value, "y": .5}


def frame(capture="screen-one"):
    return {"type": "screen", "captureId": capture, "monitorId": "all", "monitors": [],
            "width": 100, "height": 50, "left": 0, "top": 0}


def app_stub():
    app = Controller.__new__(Controller)
    app.root = Root()
    app.peer = Peer()
    app.transfers = None
    app.generation = 7
    app.frame_epoch = 3
    app.connecting = False
    app.stopping = threading.Event()
    app.messages = queue.Queue()
    app.latest_lock = threading.Lock()
    app.latest_callbacks = {}
    app.queued_latest = set()
    app.pending_states = {}
    app.outgoing = _OutgoingCommands()
    app.send_failed_generation = None
    app.frame_lock = threading.Lock()
    app.frame_event = threading.Event()
    app.raw_frame = app.decoded_frame = None
    app.sharing = True
    app.local_paused = False
    app.local_prompt_active = app.remote_prompt_active = app.dialog_active = False
    app.capture_available = True
    app.network_suspended = app.frame_stalled = False
    app.last_frame_at = app.frame_wait_started = 100.0
    app.pending_monitor = None
    app.current_screen = frame()
    app.current_image = app.photo = None
    app.image_bounds = (0, 0, 100, 50)
    app.buttons_down, app.keys_down = set(), {}
    app.last_point, app.last_move = (.5, .5), 0.0
    app.pending_move = app.move_job = None
    app.wheel_remainder = 0.0
    app.canvas = Widget()
    app.image_item = "image"
    app.monitor_combo = Widget()
    app.monitor_ids = [0]
    app.selected_page = "session"
    app.fullscreen = False
    app.file_active = False
    app.file_var = Value()
    app.progress_var = Value()
    app.screen_var = Value()
    app.connection_var = Value()
    app.password_var = Value("old-secret")
    app.placeholders = []
    app._placeholder = lambda *values: app.placeholders.append(values)
    app._update_controls = Mock()
    app._set_monitors = Mock()
    app._render_frame = Mock(return_value=True)
    app._invalidate_presence_endpoint = Mock()
    app._set_password_visible = Mock()
    app._show_page = Mock()
    app.buffered_messages = []
    app.buffered_bytes = 0
    app.buffer_overflow_generation = None
    app.connected_endpoint = "example.test:64321"
    app.connecting_endpoint = None
    return app


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(): return
        threading.Event().wait(.005)
    raise AssertionError("The worker did not reach the expected state")


class OutgoingQueueStabilityTests(unittest.TestCase):
    def test_pointer_coalescing_does_not_cross_button_release_or_wheel_barriers(self):
        commands = _OutgoingCommands()
        peer = Peer()
        click = {"type": "input", "action": "button", "button": "left", "down": True}
        release = {"type": "input", "action": "release"}
        wheel = {"type": "input", "action": "wheel", "delta": 1}
        for command in (move(.1), move(.2), click, move(.3), move(.4), wheel, release, move(.5)):
            self.assertTrue(commands.put(1, peer, command))
        actual = []
        while (item := commands.get(0)) is not None: actual.append(item[2])
        self.assertEqual(actual, [move(.2), click, move(.4), wheel, release, move(.5)])

    def test_pointer_coalescing_cannot_cross_peer_generation_or_capture_identity(self):
        first, second = Peer(), Peer()
        for previous, current in (
                ((1, first, move(.1)), (2, first, move(.2))),
                ((1, first, move(.1)), (1, second, move(.2))),
                ((1, first, move(.1, "old")), (1, first, move(.2, "new")))):
            with self.subTest(previous=previous[0::2], current=current[0::2]):
                commands = _OutgoingCommands()
                self.assertTrue(commands.put(*previous))
                self.assertTrue(commands.put(*current))
                self.assertEqual(commands.get(0), previous)
                self.assertEqual(commands.get(0), current)

    def test_full_queue_stays_bounded_but_can_replace_its_adjacent_pointer(self):
        commands = _OutgoingCommands(limit=2)
        peer = Peer()
        release = {"type": "input", "action": "release"}
        self.assertTrue(commands.put(1, peer, release))
        self.assertTrue(commands.put(1, peer, move(.1)))
        for index in range(1000): self.assertTrue(commands.put(1, peer, move(index / 1000)))
        self.assertFalse(commands.put(1, peer, {"type": "monitor", "id": 2}))
        self.assertEqual(commands.get(0)[2], release)
        self.assertEqual(commands.get(0)[2], move(.999))
        self.assertIsNone(commands.get(0))


class ControllerWorkerStabilityTests(unittest.TestCase):
    def start_sender(self, app):
        thread = threading.Thread(target=app._send_commands, daemon=True)
        thread.start()
        def stop():
            app.stopping.set()
            app.outgoing.clear()
            thread.join(1)
            self.assertFalse(thread.is_alive(), "The sender did not stop")
        self.addCleanup(stop)
        return thread

    def test_blocked_transport_does_not_block_tk_command_submission(self):
        app = app_stub()
        entered, release = threading.Event(), threading.Event()
        peer = app.peer
        def blocked_send(message):
            entered.set()
            if not release.wait(2): raise TimeoutError("test gate expired")
            return Peer.send(peer, message)
        peer.send = blocked_send
        self.addCleanup(release.set)
        self.assertTrue(app._send(move(.1)))
        self.assertFalse(entered.is_set(), "Tk called the transport directly")
        self.start_sender(app)
        self.assertTrue(entered.wait(1))
        self.assertTrue(app._send({"type": "input", "action": "release"}))
        self.assertEqual(peer.messages, [])
        release.set()
        wait_for(lambda: len(peer.messages) == 2)
        self.assertEqual([item["action"] for item in peer.messages], ["move", "release"])
        self.assertTrue(all(identity != threading.get_ident() for identity in peer.thread_ids))

    def test_discarded_move_is_not_a_connection_failure_and_release_still_arrives(self):
        app = app_stub()
        peer = app.peer
        original_send = peer.send
        def send(message):
            original_send(message)
            return message.get("action") != "move"
        peer.send = send
        app._connection_closed = Mock()
        app._send(move(.1))
        app._send({"type": "input", "action": "release"})
        self.start_sender(app)
        wait_for(lambda: len(peer.messages) == 2)
        self.assertFalse(peer.closed)
        self.assertTrue(app.messages.empty())
        app._connection_closed.assert_not_called()

    def test_rejected_reliable_command_closes_once_and_posts_one_session_failure(self):
        app = app_stub()
        peer = app.peer
        peer.send = Mock(return_value=False)
        app._connection_closed = Mock()
        app._send({"type": "input", "action": "release"})
        app._send({"type": "monitor", "id": 1})
        self.start_sender(app)
        wait_for(lambda: peer.closed and app.messages.qsize() == 1)
        self.assertEqual(len(peer.close_reasons), 1)
        generation, callback = app.messages.get_nowait()
        self.assertEqual(generation, app.generation)
        callback()
        app._connection_closed.assert_called_once()
        self.assertEqual(peer.send.call_count, 1)

    def test_commands_queued_for_previous_peer_or_generation_never_reach_new_session(self):
        app = app_stub()
        previous = Peer()
        app.outgoing.put(app.generation - 1, app.peer, move(.1))
        app.outgoing.put(app.generation, previous, {"type": "input", "action": "release"})
        app._send({"type": "monitor", "id": 2})
        self.start_sender(app)
        wait_for(lambda: bool(app.peer.messages))
        self.assertEqual(app.peer.messages, [{"type": "monitor", "id": 2}])
        self.assertEqual(previous.messages, [])

    def test_full_ui_queue_can_discard_move_without_disconnecting_but_not_release(self):
        app = app_stub()
        app.outgoing = _OutgoingCommands(limit=1)
        app._connection_closed = Mock()
        self.assertTrue(app._send({"type": "monitor", "id": 1}))
        self.assertFalse(app._send(move(.1)))
        app._connection_closed.assert_not_called()
        self.assertFalse(app._send({"type": "input", "action": "release"}))
        app._connection_closed.assert_called_once()


class ControllerPumpStabilityTests(unittest.TestCase):
    def test_thousands_of_transfer_updates_queue_only_latest_status(self):
        app = app_stub()
        app._file_status = Mock()
        for index in range(10000):
            app._transfer_status({"active": True, "message": str(index), "progress": index / 10000}, app.generation)
        self.assertEqual(app.messages.qsize(), 1)
        app._check_frame_health = Mock()
        app._pump()
        app._file_status.assert_called_once_with({"active": True, "message": "9999", "progress": .9999})
        self.assertEqual(app.latest_callbacks, {})
        self.assertEqual(app.queued_latest, set())

    def test_old_generation_status_does_not_replace_new_generation_status(self):
        app = app_stub()
        shown = []
        app._post_latest("status", lambda: shown.append("old"), app.generation - 1)
        app._post_latest("status", lambda: shown.append("new"), app.generation)
        app._check_frame_health = Mock()
        app._pump()
        self.assertEqual(shown, ["new"])

    def test_pump_budget_yields_with_backlog_and_reschedules_after_callback_error(self):
        app = app_stub()
        app._check_frame_health = Mock()
        clock = [100.0]
        done = []
        def failing():
            clock[0] += .009
            raise ValueError("invalid one-off update")
        app._post(failing)
        app._post(lambda: done.append("next"))
        with patch.object(controller.time, "monotonic", side_effect=lambda: clock[0]):
            app._pump()
        self.assertEqual(done, [])
        self.assertEqual(app.messages.qsize(), 1)
        self.assertEqual(app.root.jobs[-1][1], 1)
        self.assertIn("invalid one-off update", app.connection_var.get())
        app._pump()
        self.assertEqual(done, ["next"])
        self.assertEqual(app.root.jobs[-1][1], 30)

    def test_blocked_state_behind_ui_backlog_never_publishes_a_decoded_old_frame(self):
        app = app_stub()
        app._show_frame = Mock()
        app._check_frame_health = Mock()
        image = Image.new("RGB", (10, 5))
        self.addCleanup(image.close)
        app.decoded_frame = (app.generation, app.frame_epoch, frame(), image)
        for _ in range(129): app._post(lambda: None)
        app._queue_state({"type": "state", "sharing": False}, app.generation)
        self.assertTrue(app.network_suspended)
        self.assertFalse(app._input_ready())
        app._pump()
        app._show_frame.assert_not_called()

    def test_pause_then_resume_coalescing_keeps_the_input_and_frame_barrier(self):
        app = app_stub()
        old_image = Image.new("RGB", (10, 5))
        app.current_image = old_image
        app.buttons_down.add("left")
        app.keys_down[37] = "ctrl"
        app._queue_state({"type": "state", "sharing": False, "localPaused": True}, app.generation)
        app._queue_state({"type": "state", "sharing": True, "localPaused": False}, app.generation)
        self.assertEqual(app.messages.qsize(), 1)
        self.assertFalse(app._input_ready())
        app._check_frame_health = Mock()
        app._pump()
        self.assertTrue(app.sharing)
        self.assertFalse(app.network_suspended)
        self.assertIsNone(app.current_screen)
        self.assertIsNone(app.current_image)
        self.assertFalse(app.buttons_down)
        self.assertFalse(app.keys_down)
        with self.assertRaises(ValueError): old_image.getpixel((0, 0))
        queued = []
        while (item := app.outgoing.get(0)) is not None: queued.append(item[2])
        self.assertIn({"type": "input", "action": "release"}, queued)

    def test_preconnection_messages_have_count_and_byte_limits_and_one_failure_callback(self):
        for payload, count in (("small", 1000), ("z" * (3 * 1024 * 1024), 4)):
            with self.subTest(payload_bytes=len(payload)):
                app = app_stub()
                app.peer = None
                app.connecting = True
                for _ in range(count):
                    app._buffer_message({"type": "file_offer", "name": payload}, app.generation)
                self.assertLessEqual(len(app.buffered_messages), 128)
                self.assertLessEqual(app.buffered_bytes, 8 * 1024 * 1024)
                self.assertEqual(app.buffer_overflow_generation, app.generation)
                self.assertEqual(app.messages.qsize(), 1)
                peer = Peer()
                app._connection_closed = Mock()
                app._connected(peer)
                self.assertTrue(peer.closed)
                app._connection_closed.assert_called_once()


class ControllerFrameLifecycleStabilityTests(unittest.TestCase):
    def test_capture_unavailable_keeps_connection_and_blocks_resume(self):
        app = app_stub()
        peer = app.peer
        app._handle_message({"type": "state", "sharing": True, "captureAvailable": False})
        self.assertIs(app.peer, peer)
        self.assertFalse(peer.closed)
        self.assertFalse(app.sharing)
        self.assertFalse(app.capture_available)
        self.assertFalse(app._input_ready())
        self.assertIsNone(app.current_screen)
        app.outgoing.clear()
        app._request_sharing(True)
        self.assertIsNone(app.outgoing.get(0), "Capture recovery must not send a remote Resume request")
        self.assertIn("captura", app.screen_var.get())
        app._handle_message({"type": "state", "sharing": True, "captureAvailable": True})
        image = Image.new("RGB", (10, 5))
        self.addCleanup(image.close)
        app._show_frame(frame("recovered"), image)
        self.assertTrue(app._input_ready())
        self.assertIs(app.peer, peer)

    def test_capture_unavailable_disables_resume_but_keeps_file_transfer_controls(self):
        app = app_stub()
        app.sharing = app.capture_available = False
        app.connection_entries = []
        for name in ("connect_button", "disconnect_button", "fit_button", "pause_button",
                     "resume_button", "fullscreen_button", "send_button", "receive_button", "cancel_button"):
            setattr(app, name, Widget())
        Controller._update_controls(app)
        self.assertEqual(app.resume_button.options["state"], "disabled")
        self.assertEqual(app.pause_button.options["state"], "disabled")
        self.assertEqual(app.send_button.options["state"], "normal")
        self.assertEqual(app.receive_button.options["state"], "normal")

    def test_stalled_image_releases_input_without_closing_tls_and_next_frame_recovers(self):
        app = app_stub()
        peer = app.peer
        app.current_image = Image.new("RGB", (10, 5))
        app.buttons_down.add("left")
        app.keys_down[37] = "ctrl"
        app._check_frame_health(now=105.9)
        self.assertFalse(app.frame_stalled)
        self.assertIsNotNone(app.current_screen)
        app._check_frame_health(now=106.1)
        self.assertTrue(app.frame_stalled)
        self.assertIsNone(app.current_screen)
        self.assertFalse(app.buttons_down)
        self.assertFalse(app.keys_down)
        self.assertIs(app.peer, peer)
        self.assertFalse(peer.closed)
        image = Image.new("RGB", (10, 5))
        self.addCleanup(image.close)
        with patch.object(controller.time, "monotonic", return_value=107.0):
            app._show_frame(frame("new-image"), image)
        self.assertFalse(app.frame_stalled)
        self.assertEqual(app.last_frame_at, 107.0)
        self.assertTrue(app._input_ready())

    def test_render_failure_never_marks_frame_healthy_and_pump_keeps_running(self):
        app = app_stub()
        app._render_frame = Mock(return_value=False)
        app.frame_stalled = True
        image = Image.new("RGB", (10, 5))
        self.addCleanup(image.close)
        app._show_frame(frame(), image)
        self.assertTrue(app.frame_stalled)
        self.assertFalse(app._input_ready())
        app._show_frame = Mock(side_effect=ValueError("bad display surface"))
        next_image = Image.new("RGB", (10, 5))
        app.decoded_frame = (app.generation, app.frame_epoch, frame(), next_image)
        app._pump()
        self.assertTrue(app.frame_stalled)
        self.assertTrue(app.root.jobs)
        self.assertIn("bad display surface", app.screen_var.get())
        self.assertFalse(app.peer.closed)
        with self.assertRaises(ValueError): next_image.getpixel((0, 0))

    def test_manual_reconnect_opens_connection_form_and_clears_password_without_connecting(self):
        app = app_stub()
        app.peer = None
        app.reconnect_available = True
        app.password_entry = Widget()
        app.connect = Mock()
        app._prepare_reconnect()
        self.assertEqual(app.password_var.get(), "")
        self.assertFalse(app.reconnect_available)
        app._set_password_visible.assert_called_once_with(False)
        app._show_page.assert_called_once_with("connect")
        self.assertTrue(app.password_entry.focused)
        app.connect.assert_not_called()

    def test_unexpected_disconnect_clears_session_work_and_offers_manual_reconnect(self):
        app = app_stub()
        peer, generation = app.peer, app.generation
        transfers = app.transfers = SimpleNamespace(close=Mock())
        app.current_image = Image.new("RGB", (10, 5))
        decoded_image = Image.new("RGB", (10, 5))
        app.decoded_frame = (generation, app.frame_epoch, frame(), decoded_image)
        app.raw_frame = (generation, app.frame_epoch, frame())
        app.buttons_down.add("left")
        app.keys_down[37] = "ctrl"
        app._send(move(.1))
        app._post_latest("status", Mock())
        app._buffer_message({"type": "file_offer", "name": "pending"}, generation)
        app._connection_closed("wire interrupted")
        self.assertTrue(peer.closed)
        transfers.close.assert_called_once()
        self.assertIsNone(app.peer)
        self.assertIsNone(app.transfers)
        self.assertEqual(app.generation, generation + 1)
        self.assertTrue(app.reconnect_available)
        self.assertEqual(app.password_var.get(), "")
        self.assertIsNone(app.outgoing.get(0))
        self.assertEqual(app.latest_callbacks, {})
        self.assertEqual(app.pending_states, {})
        self.assertEqual(app.buffered_messages, [])
        self.assertEqual(app.buffered_bytes, 0)
        self.assertIsNone(app.raw_frame)
        self.assertIsNone(app.decoded_frame)
        self.assertFalse(app.buttons_down)
        self.assertFalse(app.keys_down)
        with self.assertRaises(ValueError): decoded_image.getpixel((0, 0))

    def test_user_disconnect_does_not_offer_a_reconnect_banner(self):
        app = app_stub()
        peer = app.peer
        app.disconnect()
        self.assertTrue(peer.closed)
        self.assertFalse(app.reconnect_available)
        self.assertFalse(app.manual_disconnect)
        self.assertEqual(app.password_var.get(), "")


if __name__ == "__main__": unittest.main()
