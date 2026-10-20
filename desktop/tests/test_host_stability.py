"""Host recovery and bounded UI delivery without Tk or desktop providers."""
from collections import deque
from pathlib import Path
import queue
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import host
from desktop_geometry import screen_metadata


MONITORS = [
    {"left": -800, "top": 0, "width": 1600, "height": 600},
    {"left": -800, "top": 0, "width": 800, "height": 600},
    {"left": 0, "top": 0, "width": 800, "height": 600},
]


class Value:
    def __init__(self, value=""):
        self.value = value
        self.history = []

    def set(self, value):
        self.value = value
        self.history.append(value)


class Peer:
    def __init__(self):
        self.messages = []
        self.closed = False

    def send(self, message):
        self.messages.append(message)

    def close(self):
        self.closed = True


class Inputs:
    def __init__(self):
        self.held_keys = set()
        self.held_buttons = set()
        self.events = []

    def move(self, x, y):
        self.events.append(("move", x, y))

    def key(self, name, down):
        self.events.append(("key", name, down))
        (self.held_keys.add if down else self.held_keys.discard)(name)

    def button(self, name, down):
        self.events.append(("button", name, down))
        (self.held_buttons.add if down else self.held_buttons.discard)(name)

    def release(self):
        self.events.append(("release",))
        self.held_keys.clear()
        self.held_buttons.clear()


def make_app(real_log=False):
    app = host.HostApp.__new__(host.HostApp)
    app.control_lock = threading.RLock()
    app.shutdown = threading.Event()
    app.ui_queue = queue.Queue()
    app.peer = Peer()
    app.server = object()
    app.transfers = None
    app.inputs = Inputs()
    app.sharing = True
    app.local_paused = False
    app.prompt_count = 0
    app.pending_consents = set()
    app.generation = 0
    app.monitor_id = "all"
    app.last_screen = screen_metadata(MONITORS, "all", MONITORS[0].copy(), (1600, 600))
    app.status = Value()
    app.transfer_status = Value()
    app.root = Mock()
    app.pause_button = Mock()
    for name in ("disconnect_button", "send_button", "request_button", "cancel_button"):
        setattr(app, name, Mock())
    if not real_log:
        app.log = Mock()
    return app


def drain(app):
    # A regression that endlessly reschedules itself should fail, not hang.
    for _ in range(1000):
        try:
            action = app.ui_queue.get_nowait()
        except queue.Empty:
            return
        action()
    raise AssertionError("UI queue did not drain within 1000 callbacks")


class WaitScript(threading.Event):
    """Advance capture iterations without sleeping, preserving Event stop semantics."""
    def __init__(self, on_wait):
        super().__init__()
        self.waits = []
        self.on_wait = on_wait

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if len(self.waits) > 30:
            self.set()
            raise AssertionError("capture loop exceeded deterministic iteration budget")
        self.on_wait(self, timeout)
        return self.is_set()


class Capture:
    monitors = MONITORS

    def __init__(self, grab_error=None, close_error=None):
        self.grab_error = grab_error
        self.close_error = close_error
        self.grabs = 0
        self.closes = 0

    def grab(self, rectangle):
        self.grabs += 1
        if self.grab_error is not None:
            raise self.grab_error
        return SimpleNamespace(size=(rectangle["width"], rectangle["height"]), rgb=b"")

    def close(self):
        self.closes += 1
        if self.close_error is not None:
            raise self.close_error


class Image:
    def __init__(self, save_error=None):
        self.size = (1600, 600)
        self.save_error = save_error
        self.closes = 0

    def thumbnail(self, dimensions, method):
        pass

    def save(self, output, **options):
        if self.save_error is not None:
            raise self.save_error
        output.write(b"test-jpeg")

    def close(self):
        self.closes += 1


def providers(factory, image_factory=Image):
    return patch.dict(sys.modules, {
        "mss": SimpleNamespace(mss=factory),
        "PIL": SimpleNamespace(Image=SimpleNamespace(
            frombytes=lambda *args: image_factory(),
            Resampling=SimpleNamespace(LANCZOS=1))),
    })


class CaptureRecoveryTests(unittest.TestCase):
    def test_state_defaults_to_available_for_existing_stub_and_disables_sharing_when_unavailable(self):
        app = make_app()
        app._emit_state()
        self.assertTrue(app.peer.messages[-1]["captureAvailable"])
        self.assertTrue(app.peer.messages[-1]["sharing"])
        app.capture_available = False
        app._emit_state()
        self.assertFalse(app.peer.messages[-1]["captureAvailable"])
        self.assertFalse(app.peer.messages[-1]["sharing"])
        self.assertFalse(app.peer.messages[-1]["localPaused"])

    def test_transient_grab_failure_invalidates_geometry_and_releases_then_recovers_before_frame(self):
        app = make_app()
        app.inputs.held_keys.add("ctrl")
        app.inputs.held_buttons.add("left")
        stale_id = app.last_screen["captureId"]
        failed, recovered = Capture(OSError("desktop temporarily unavailable")), Capture()
        captures = iter((failed, recovered))
        failed_snapshot = []

        def advance(event, timeout):
            if timeout == host.CAPTURE_RETRY_MIN:
                failed_snapshot.append((app.capture_available, app.last_screen,
                                        set(app.inputs.held_keys), set(app.inputs.held_buttons)))
                # Already queued input cannot operate against the invalid capture.
                app._message(app.peer, {"type": "input", "action": "move", "captureId": stale_id, "x": 1, "y": 1})
            else:
                event.set()

        app.shutdown = WaitScript(advance)
        with providers(lambda: next(captures)):
            app._capture_loop()
        self.assertEqual(failed_snapshot, [(False, None, set(), set())])
        self.assertFalse(any(event[0] == "move" for event in app.inputs.events))
        self.assertEqual([message["type"] for message in app.peer.messages], ["state", "state", "screen"])
        self.assertFalse(app.peer.messages[0]["sharing"])
        self.assertTrue(app.peer.messages[1]["captureAvailable"])
        self.assertTrue(app.peer.messages[1]["sharing"])
        self.assertIsNotNone(app.last_screen)
        self.assertFalse(app.peer.closed)
        self.assertEqual((failed.closes, recovered.closes), (1, 1))

    def test_provider_initialization_retry_is_exponential_capped_and_interruptible(self):
        app = make_app()
        attempts = []

        def unavailable():
            attempts.append(True)
            raise OSError("no desktop")

        app.shutdown = WaitScript(lambda event, delay: event.set() if len(event.waits) == 6 else None)
        with providers(unavailable):
            app._capture_loop()
        self.assertEqual(app.shutdown.waits, [.25, .5, 1., 2., 2., 2.])
        self.assertEqual(len(attempts), 6)
        # Repeated outages emit one transition, and do not disconnect the session.
        self.assertEqual(len(app.peer.messages), 1)
        self.assertFalse(app.peer.messages[0]["captureAvailable"])
        self.assertFalse(app.peer.closed)

    def test_jpeg_failure_closes_image_and_reopens_provider_before_retry(self):
        app = make_app()
        captures = [Capture(), Capture()]
        images = [Image(ValueError("encoder transient failure")), Image()]
        capture_iter, image_iter = iter(captures), iter(images)
        app.shutdown = WaitScript(lambda event, delay: event.set() if delay == 1. / host.FPS else None)
        with providers(lambda: next(capture_iter), lambda: next(image_iter)):
            app._capture_loop()
        self.assertEqual(app.shutdown.waits, [.25, 1. / host.FPS])
        self.assertEqual([capture.grabs for capture in captures], [1, 1])
        self.assertEqual([capture.closes for capture in captures], [1, 1])
        self.assertEqual([image.closes for image in images], [1, 1])
        self.assertEqual(sum(message["type"] == "screen" for message in app.peer.messages), 1)

    def test_provider_close_failure_cannot_prevent_recovery_or_clean_shutdown(self):
        app = make_app()
        captures = [Capture(OSError("capture lost"), OSError("close failed")),
                    Capture(close_error=OSError("final close failed"))]
        capture_iter = iter(captures)
        app.shutdown = WaitScript(lambda event, delay: event.set() if delay == 1. / host.FPS else None)
        with providers(lambda: next(capture_iter)):
            app._capture_loop()
        self.assertEqual([capture.closes for capture in captures], [1, 1])
        self.assertTrue(app.capture_available)
        self.assertEqual(sum(message["type"] == "screen" for message in app.peer.messages), 1)

    def test_periodic_refresh_still_replaces_provider_when_previous_close_raises(self):
        app = make_app()
        captures = [Capture(close_error=OSError("old handle already gone")), Capture()]
        capture_iter = iter(captures)
        now = [0.]

        def advance(event, delay):
            now[0] = 3.
            if len(event.waits) == 2:
                event.set()

        app.shutdown = WaitScript(advance)
        with providers(lambda: next(capture_iter)), patch.object(host.time, "monotonic", side_effect=lambda: now[0]):
            app._capture_loop()
        self.assertEqual([capture.grabs for capture in captures], [1, 1])
        self.assertEqual([capture.closes for capture in captures], [1, 1])
        self.assertEqual(sum(message["type"] == "screen" for message in app.peer.messages), 2)
        self.assertFalse(any(message["type"] == "state" and not message["captureAvailable"] for message in app.peer.messages))


class InputRecoveryTests(unittest.TestCase):
    def test_failed_press_releases_partially_held_input_and_preserves_authenticated_peer(self):
        app = make_app()
        peer, before = app.peer, app.generation
        app.inputs.held_buttons.add("left")
        original_key = app.inputs.key

        def rejected_press(name, down):
            original_key(name, down)
            raise OSError("input provider unavailable")

        app.inputs.key = rejected_press
        app._message(peer, {"type": "input", "action": "key", "key": "ctrl", "down": True,
                            "captureId": app.last_screen["captureId"]})
        self.assertIs(app.peer, peer)
        self.assertFalse(peer.closed)
        self.assertIsNone(app.last_screen)
        self.assertGreater(app.generation, before)
        self.assertEqual(app.inputs.held_keys, set())
        self.assertEqual(app.inputs.held_buttons, set())
        app.log.assert_called_once()


class BoundedUiTests(unittest.TestCase):
    def test_closed_peer_cannot_attach_after_disconnect_callback_arrives_before_connect(self):
        app = make_app()
        app.peer = None
        peer = Peer()
        peer.close()
        # HostServer.stop can close the reserved peer before on_connect enters
        # HostApp. The early disconnect is then correctly a no-op for the app.
        app._disconnected(peer, "listener stopped before connect callback")
        with patch("transfer.FileTransfers") as transfers:
            app._connected(peer, "late visitor", "127.0.0.1")
        self.assertIsNone(app.peer)
        self.assertIsNone(app.transfers)
        transfers.assert_not_called()
        self.assertTrue(app.ui_queue.empty())
        self.assertEqual(app.status.history, [])
        self.assertEqual(peer.messages, [])

    def test_ten_thousand_logs_keep_newest_eighty_and_schedule_one_callback(self):
        app = make_app(real_log=True)
        delivered = []
        app._write_log = delivered.append
        for index in range(10000):
            app.log(f"{index}:\r\n" + "x" * 2000)
        self.assertEqual(app.ui_queue.qsize(), 1)
        self.assertEqual(len(app._pending_logs), 80)
        self.assertIsInstance(app._pending_logs, deque)
        drain(app)
        self.assertEqual(len(delivered), 80)
        self.assertTrue(delivered[0].startswith("9920:"))
        self.assertTrue(delivered[-1].startswith("9999:"))
        self.assertTrue(all(len(message) <= 1024 and "\r" not in message and "\n" not in message for message in delivered))
        app.log("after flush")
        self.assertEqual(app.ui_queue.qsize(), 1)
        drain(app)
        self.assertEqual(delivered[-1], "after flush")

    def test_diagnostics_limit_each_category_for_five_seconds_without_suppressing_others(self):
        app = make_app()
        with patch.object(host.time, "monotonic", side_effect=[10., 10., 14.999, 15., 15.1]):
            app._diagnostic("capture", "capture first")
            app._diagnostic("input", "input first")
            app._diagnostic("capture", "capture throttled")
            app._diagnostic("capture", "capture later")
            app._diagnostic("input", "input later")
        self.assertEqual([call.args[0] for call in app.log.call_args_list],
                         ["capture first", "input first", "capture later", "input later"])

    def test_ten_thousand_progress_updates_deliver_only_latest_with_one_callback(self):
        app = make_app()
        for index in range(10000):
            app._transfer_status({"active": True, "message": f"transfer {index}", "progress": index / 9999})
        self.assertEqual(app.ui_queue.qsize(), 1)
        self.assertEqual(app.transfer_status.history, [])
        drain(app)
        self.assertEqual(app.transfer_status.history, ["transfer 9999 · 100%"])
        app.log.assert_not_called()

    def test_disconnect_invalidates_queued_progress_before_ui_drain(self):
        app = make_app()
        app._transfer_status({"active": True, "message": "obsolete progress", "progress": .5})
        app._disconnected(app.peer, "connection lost")
        drain(app)
        self.assertNotIn("obsolete progress · 50%", app.transfer_status.history)
        self.assertEqual(app.transfer_status.value, "Transferências encerradas")

    def test_late_old_transfer_callback_cannot_replace_new_session_status(self):
        app = make_app()
        app.peer = None
        callbacks = []

        def transfers(peer, dispatch, ask_receive, status, **kwargs):
            callbacks.append(status)
            return SimpleNamespace(close=Mock())

        old_peer, new_peer = Peer(), Peer()
        with patch("transfer.FileTransfers", side_effect=transfers):
            app._connected(old_peer, "old visitor", "127.0.0.1")
            app._disconnected(old_peer, "closed")
            app._connected(new_peer, "new visitor", "127.0.0.1")
        drain(app)
        callbacks[1]({"active": True, "message": "new session", "progress": .25})
        callbacks[0]({"active": True, "message": "obsolete old session", "progress": .8})
        drain(app)
        self.assertEqual(app.transfer_status.value, "new session · 25%")
        self.assertFalse(any("obsolete" in value for value in app.transfer_status.history))
        self.assertIs(app.peer, new_peer)

    def test_rejected_ui_dispatch_drops_pending_buffers_and_resets_coalescing_flags(self):
        app = make_app(real_log=True)
        app.dispatch = Mock(return_value=False)
        app.log("not deliverable")
        app._transfer_status({"active": True, "message": "not deliverable", "progress": .5})
        self.assertEqual(list(app._pending_logs), [])
        self.assertFalse(app._log_queued)
        self.assertIsNone(app._pending_transfer_status)
        self.assertFalse(app._transfer_status_queued)


if __name__ == "__main__":
    unittest.main()
