"""Host consent/input/capture regressions; desktop providers and Tk are stubbed."""
import base64
import hashlib
from pathlib import Path
import queue
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import host
from desktop_geometry import choose_monitor, physical_point, screen_metadata


MONITORS = [
    {"left": -1920, "top": -200, "width": 3840, "height": 1400},
    {"left": 0, "top": 0, "width": 1920, "height": 1080},
    {"left": -1920, "top": -200, "width": 1920, "height": 1200},
]


class RecordingInputs:
    def __init__(self):
        self.events = []
        self.held_buttons = set()
        self.held_keys = set()

    def move(self, *coordinates):
        self.events.append(("move", *coordinates))

    def key(self, name, down):
        self.events.append(("key", name, down))
        if down:
            self.held_keys.add(name)
        else:
            self.held_keys.discard(name)

    def button(self, name, down):
        self.events.append(("button", name, down))
        if down:
            self.held_buttons.add(name)
        else:
            self.held_buttons.discard(name)

    def wheel(self, delta):
        self.events.append(("wheel", delta))

    def release(self):
        self.events.append(("release",))
        self.held_buttons.clear()
        self.held_keys.clear()


class RecordingPeer:
    def __init__(self):
        self.messages = []
        self.closed = False

    def send(self, message):
        self.messages.append(message)

    def close(self):
        self.closed = True


class OneCaptureEvent(threading.Event):
    """Stop the capture loop at its first frame interval, without sleeping."""
    def wait(self, timeout=None):
        self.set()
        return True


def app_stub():
    app = host.HostApp.__new__(host.HostApp)
    app.control_lock = threading.RLock()
    app.shutdown = threading.Event()
    app.ui_queue = queue.Queue()
    app.peer = RecordingPeer()
    app.server = object()
    app.transfers = None
    app.inputs = RecordingInputs()
    app.sharing = True
    app.local_paused = False
    app.prompt_count = 0
    app.pending_consents = set()
    app.generation = 0
    app.monitor_id = "all"
    app.last_screen = screen_metadata(MONITORS, "all", MONITORS[0].copy(), (2560, 933))
    app.log = Mock()
    app.root = Mock()
    app.pause_button = Mock()
    return app


class GeometryTests(unittest.TestCase):
    def test_virtual_desktop_maps_both_inclusive_edges_on_negative_monitors(self):
        app = app_stub()
        self.assertEqual(physical_point(app.last_screen, 0, 0), (-1920, -200))
        self.assertEqual(physical_point(app.last_screen, 1, 1), (1919, 1199))
        self.assertEqual(physical_point(app.last_screen, -1, 2), (-1920, 1199))

    def test_removed_monitor_falls_back_and_changes_geometry_identity(self):
        identifier, rectangle = choose_monitor(MONITORS, "2")
        first = screen_metadata(MONITORS, identifier, rectangle, (1920, 1200))
        identifier, rectangle = choose_monitor(MONITORS[:2], "2")
        second = screen_metadata(MONITORS[:2], identifier, rectangle, (2560, 933))
        self.assertEqual(identifier, "all")
        self.assertNotEqual(first["captureId"], second["captureId"])

    def test_nonfinite_coordinates_rejected(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                physical_point(app_stub().last_screen, value, 0)


class InputTests(unittest.TestCase):
    def setUp(self):
        self.app = app_stub()
        self.base = {"type": "input", "captureId": self.app.last_screen["captureId"], "x": 1, "y": 1}

    def send(self, **fields):
        self.app._message(self.app.peer, {**self.base, **fields})

    def test_current_capture_maps_to_physical_corner(self):
        self.send(action="move")
        self.assertEqual(self.app.inputs.events, [("move", 1919, 1199)])

    def test_stale_capture_and_different_peer_do_not_inject_input(self):
        self.send(action="move", captureId="stale")
        self.app._message(RecordingPeer(), {**self.base, "action": "move"})
        self.assertEqual(self.app.inputs.events, [])

    def test_input_requires_geometry_even_for_keyboard_down(self):
        self.app.last_screen = None
        self.send(action="key", key="ctrl", down=True)
        self.assertEqual(self.app.inputs.events, [])

    def test_prompt_synchronously_releases_and_blocks_until_last_modal_closes(self):
        self.send(action="key", key="ctrl", down=True)
        self.send(action="button", button="left", down=True)
        self.app._begin_prompt()
        self.app._begin_prompt()
        self.assertEqual(self.app.prompt_count, 2)
        self.assertEqual(self.app.inputs.held_keys, set())
        self.assertEqual(self.app.inputs.held_buttons, set())
        self.assertIsNone(self.app.last_screen)
        self.app.last_screen = app_stub().last_screen
        previous = list(self.app.inputs.events)
        self.send(action="key", key="enter", down=True)
        self.send(action="move")
        self.assertEqual(self.app.inputs.events, previous)
        self.app._end_prompt()
        self.assertEqual(self.app.prompt_count, 1)
        self.assertFalse(self.app.peer.messages[-1]["sharing"])
        self.assertTrue(self.app.peer.messages[-1]["promptActive"])
        self.app._end_prompt()
        self.assertTrue(self.app.peer.messages[-1]["sharing"])
        self.assertFalse(self.app.peer.messages[-1]["promptActive"])

    def test_remote_resume_cannot_cancel_local_pause(self):
        self.app.toggle_pause()
        self.app._message(self.app.peer, {"type": "sharing", "enabled": True})
        self.assertTrue(self.app.local_paused)
        self.assertFalse(self.app.sharing)
        self.send(action="key", key="enter", down=True)
        self.assertFalse(self.app.inputs.held_keys)
        self.assertTrue(self.app.peer.messages[-1]["localPaused"])

    def test_release_is_processed_during_pause_and_consent_without_capture_id(self):
        self.app.local_paused = True
        self.app.prompt_count = 1
        self.app.last_screen = None
        self.app.inputs.held_keys.add("ctrl")
        self.app.inputs.held_buttons.add("left")
        self.app._message(self.app.peer, {"type": "input", "action": "release"})
        self.assertEqual(self.app.inputs.held_keys, set())
        self.assertEqual(self.app.inputs.held_buttons, set())

    def test_individual_releases_never_press_or_release_local_unheld_keys(self):
        self.app.local_paused = True
        self.app.inputs.held_keys.add("ctrl")
        self.app._message(self.app.peer, {"type": "input", "action": "key", "key": "ctrl", "down": False})
        self.app._message(self.app.peer, {"type": "input", "action": "key", "key": "enter", "down": False})
        self.assertEqual(self.app.inputs.events, [("key", "ctrl", False)])

    def test_monitor_switch_releases_and_zero_selects_virtual_desktop(self):
        self.app.inputs.held_keys.add("shift")
        self.app._message(self.app.peer, {"type": "monitor", "id": 2})
        self.assertEqual(self.app.monitor_id, "2")
        self.assertFalse(self.app.inputs.held_keys)
        self.assertIsNone(self.app.last_screen)
        self.app._message(self.app.peer, {"type": "monitor", "id": 0})
        self.assertEqual(self.app.monitor_id, "all")

    def test_disconnect_releases_modifiers_and_closes_file_operations(self):
        self.app.inputs.held_keys.add("ctrl")
        self.app.inputs.held_buttons.add("left")
        transfer = Mock()
        self.app.transfers = transfer
        self.app._disconnected(self.app.peer, "network lost")
        self.assertIsNone(self.app.peer)
        self.assertIsNone(self.app.last_screen)
        self.assertFalse(self.app.inputs.held_keys)
        self.assertFalse(self.app.inputs.held_buttons)
        transfer.close.assert_called_once()


class WindowsInputTests(unittest.TestCase):
    def test_windows_mouse_uses_physical_coordinates_and_relative_button_wheel_flags(self):
        physical_cursor = Mock(return_value=1)
        sender = Mock(return_value=1)
        user32 = SimpleNamespace(SetPhysicalCursorPos=physical_cursor, SetCursorPos=Mock(return_value=1), SendInput=sender)
        inputs = host.InputController()
        with patch.object(host.sys, "platform", "win32"), patch.object(host.ctypes, "windll", SimpleNamespace(user32=user32), create=True):
            inputs.move(-1920, -200)
            inputs.button("left", True)
            inputs.wheel(3)
            inputs.release()
        physical_cursor.assert_called_once_with(-1920, -200)
        user32.SetCursorPos.assert_not_called()
        flags = [call.args[1]._obj.data.mi.dwFlags for call in sender.call_args_list]
        self.assertEqual(flags, [0x0002, 0x0800, 0x0004])
        self.assertEqual(sender.call_args_list[1].args[1]._obj.data.mi.mouseData, 360)
        self.assertIsNone(inputs._keyboard)
        self.assertFalse(inputs.held_buttons)

    def test_os_denied_input_raises_instead_of_attempting_security_bypass(self):
        sender = Mock(return_value=0)
        with patch.object(host.ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(SendInput=sender)), create=True):
            with self.assertRaises(OSError):
                host.InputController._send_mouse(0x0002)

    def test_keyboard_releases_held_modifiers_and_ignores_unknown_keys(self):
        keyboard = SimpleNamespace(KEYBOARD_KEYS=["ctrl", "enter"], keyDown=Mock(), keyUp=Mock())
        inputs = host.InputController()
        inputs._keyboard = keyboard
        inputs.key("not-a-key", True)
        inputs.key("ctrl", True)
        inputs.key("ctrl", True)
        inputs.release()
        keyboard.keyDown.assert_called_once_with("ctrl")
        keyboard.keyUp.assert_called_once_with("ctrl")
        self.assertFalse(inputs.held_keys)


class ConsentTests(unittest.TestCase):
    def transfer_for(self, app):
        from transfer import FileTransfers
        transfer = FileTransfers(app.peer, app.dispatch, app._ask_receive, Mock(),
            on_request_file=app._pick_requested_file,
            prompt_guard=lambda active: app._begin_prompt() if active else app._end_prompt())
        app.transfers = transfer
        self.addCleanup(transfer.close)
        return transfer

    @staticmethod
    def drain(app):
        while True:
            try:
                app.ui_queue.get_nowait()()
            except queue.Empty:
                break

    @staticmethod
    def offer():
        return {"type": "file_offer", "id": str(uuid.uuid4()), "name": "example.txt",
                "size": 0, "sha256": hashlib.sha256(b"").hexdigest()}

    def test_guard_is_active_before_connection_question_is_dispatched(self):
        app = app_stub()
        app.peer = None
        result = []
        worker = threading.Thread(target=lambda: result.append(app._connection_request("Alice", "127.0.0.1")))
        with patch.object(host.messagebox, "askyesno", return_value=True) as question:
            worker.start()
            prompt = app.ui_queue.get(timeout=2)
            self.assertEqual(app.prompt_count, 1)
            self.assertIsNone(app.last_screen)
            question.assert_not_called()
            prompt()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [True])
        self.assertEqual(app.prompt_count, 0)

    def test_local_question_denial_does_not_authorize(self):
        app = app_stub()
        app.peer = None
        result = []
        worker = threading.Thread(target=lambda: result.append(app._connection_request("Alice", "127.0.0.1")))
        with patch.object(host.messagebox, "askyesno", return_value=False):
            worker.start()
            app.ui_queue.get(timeout=2)()
            worker.join(2)
        self.assertEqual(result, [False])

    def test_expired_connection_question_is_not_shown_after_network_denial(self):
        app = app_stub()
        app.peer = None
        expired = threading.Event()
        with patch.object(host.threading, "Event", return_value=expired), patch.object(expired, "wait", return_value=False):
            accepted = app._connection_request("Alice", "127.0.0.1")
        self.assertFalse(accepted)
        self.assertTrue(expired.is_set())
        self.assertEqual(app.prompt_count, 1)
        with patch.object(host.messagebox, "askyesno") as question:
            self.drain(app)
            question.assert_not_called()
        self.assertEqual(app.prompt_count, 0)

    def test_real_transfer_offer_blocks_remote_input_before_tk_dispatch(self):
        app = app_stub()
        self.transfer_for(app)
        app.inputs.held_keys.add("ctrl")
        with patch.object(host.messagebox, "askyesno", return_value=False) as question:
            app._message(app.peer, self.offer())
            self.assertEqual(app.prompt_count, 1)
            self.assertFalse(app.inputs.held_keys)
            self.assertIsNone(app.last_screen)
            question.assert_not_called()
            previous = list(app.inputs.events)
            app._message(app.peer, {"type": "input", "action": "key", "key": "enter", "down": True})
            self.assertEqual(app.inputs.events, previous)
            self.drain(app)
            question.assert_called_once()
        self.assertEqual(app.prompt_count, 0)

    def test_transfer_closed_before_dispatch_clears_guard_without_showing_dialog(self):
        app = app_stub()
        transfer = self.transfer_for(app)
        with patch.object(host.messagebox, "askyesno", return_value=False) as question:
            app._message(app.peer, self.offer())
            self.assertEqual(app.prompt_count, 1)
            transfer.close()
            self.drain(app)
            question.assert_not_called()
        self.assertEqual(app.prompt_count, 0)

    def test_file_request_denial_does_not_open_local_file_picker(self):
        app = app_stub()
        self.transfer_for(app)
        with patch.object(host.messagebox, "askyesno", return_value=False), patch.object(host.filedialog, "askopenfilename") as picker:
            app._message(app.peer, {"type": "file_request", "id": str(uuid.uuid4())})
            self.assertEqual(app.prompt_count, 1)
            self.drain(app)
            picker.assert_not_called()
        self.assertEqual(app.prompt_count, 0)


class CaptureTests(unittest.TestCase):
    def capture_once(self, app, during_grab=lambda: None):
        app.shutdown = OneCaptureEvent()
        class Capture:
            monitors = MONITORS
            closed = False
            def grab(self, rectangle):
                during_grab()
                return SimpleNamespace(size=(rectangle["width"], rectangle["height"]), rgb=b"")
            def close(self):
                self.closed = True
        capture = Capture()
        class Image:
            size = (3840, 1400)
            def thumbnail(self, dimensions, method):
                self.size = (2560, 933)
            def save(self, buffer, **options):
                buffer.write(b"test-jpeg")
        image_module = SimpleNamespace(Image=SimpleNamespace(frombytes=lambda *args: Image(), Resampling=SimpleNamespace(LANCZOS=1)))
        mss_module = SimpleNamespace(mss=lambda: capture)
        with patch.dict(sys.modules, {"mss": mss_module, "PIL": image_module}):
            app._capture_loop()
        self.assertTrue(capture.closed)

    def test_complete_frame_retains_physical_bounds_and_downscaled_frame_dimensions(self):
        app = app_stub()
        self.capture_once(app)
        frames = [message for message in app.peer.messages if message["type"] == "screen"]
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertEqual((frame["left"], frame["top"], frame["width"], frame["height"]), (-1920, -200, 3840, 1400))
        self.assertEqual((frame["frameWidth"], frame["frameHeight"]), (2560, 933))
        self.assertEqual(base64.b64decode(frame["image"]), b"test-jpeg")
        self.assertEqual(len(frame["monitors"]), 2)

    def test_monitor_switch_during_capture_never_publishes_old_geometry(self):
        app = app_stub()
        self.capture_once(app, lambda: app._message(app.peer, {"type": "monitor", "id": 2}))
        self.assertFalse(any(message["type"] == "screen" for message in app.peer.messages))
        self.assertIsNone(app.last_screen)

    def test_local_pause_during_capture_never_publishes_queued_pixels(self):
        app = app_stub()
        self.capture_once(app, app.toggle_pause)
        self.assertFalse(any(message["type"] == "screen" for message in app.peer.messages))
        self.assertTrue(app.local_paused)

    def test_module_import_does_not_load_desktop_providers(self):
        folder = str(Path(__file__).resolve().parents[1])
        code = f"import sys; sys.path.insert(0,{folder!r}); import host; assert not {{'mss','pyautogui','PIL'}} & set(sys.modules)"
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
