"""Concurrent window ownership, focus release and command routing regressions."""
from pathlib import Path
import queue
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from controller import Controller, ControllerSessions, Settings, _OutgoingCommands


class Value:
    def __init__(self, value=""):
        self.value = value
        self.callbacks = {}
        self.next_id = 0

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        for callback in tuple(self.callbacks.values()):
            callback()

    def trace_add(self, _mode, callback):
        self.next_id += 1
        identity = str(self.next_id)
        self.callbacks[identity] = callback
        return identity

    def trace_remove(self, _mode, identity):
        del self.callbacks[identity]


class Window:
    def __init__(self, owner=None):
        self.owner = owner
        self.destroy_count = 0
        self.focus_count = 0
        self.last_title = ""
        self.cancelled = []

    def deiconify(self):
        pass

    def lift(self):
        pass

    def focus_force(self):
        self.focus_count += 1

    def title(self, value):
        self.last_title = value

    def destroy(self):
        self.destroy_count += 1

    def after_cancel(self, identity):
        self.cancelled.append(identity)


class Peer:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class Session:
    """Small UI facade around the production input queue and close lifecycle."""
    close = Controller.close
    _release_input = Controller._release_input
    _drop_pending_move = Controller._drop_pending_move
    _send = Controller._send
    _flush_pending_move = Controller._flush_pending_move
    _input_ready = Controller._input_ready
    _point_message = Controller._point_message
    _mouse_down = Controller._mouse_down

    def __init__(self, window, *, settings, book, session_manager, session_id):
        self.root = window
        self.settings = settings
        self.book = book if book is not None else object()
        self.session_manager = session_manager
        self.session_window_id = session_id
        self.connection_var = Value("Desconectado")
        self.stopping = threading.Event()
        self.peer = Peer()
        self.mesh_panel = SimpleNamespace(close=lambda: setattr(self, "mesh_closed", True))
        self.mesh_closed = False
        self.mesh_start_lock = threading.RLock()
        self.frame_event = threading.Event()
        self.pump_job = "pump"; self.presence_job = "presence"; self.resize_job = None
        self.move_job = None; self.pending_move = None
        self.buttons_down = set(); self.keys_down = {}
        self.wheel_remainder = 0
        self.last_point = (.5, .5)
        self.generation = 1
        self.outgoing = _OutgoingCommands()
        self.connecting = False
        self.connecting_endpoint = None; self.connected_endpoint = None
        self.retry_job = None; self.retry_credentials = None; self.retry_attempting = False
        self.mesh_target_name = ""
        self.computer_entries = {}
        self.selected_page = "session"
        self.canvas = SimpleNamespace(focus_set=lambda: None, grab_release=lambda: None,
                                      grab_set=lambda: None)
        self.current_screen = {"captureId": f"screen-{session_id}"}
        self.image_bounds = (0, 0, 100, 100)
        self.sharing = True; self.local_paused = False
        self.pending_monitor = None
        self.dialog_active = self.local_prompt_active = self.remote_prompt_active = False
        self.rows = []
        self.prepared = None
        self.code_setup_open = False

    def _set_session_windows(self, rows):
        if self.root.destroy_count:
            raise AssertionError("A destroyed session received a UI refresh")
        self.rows = rows

    def _prepare_computer_connection(self, entry):
        self.prepared = entry

    def _open_mesh_setup(self):
        self.code_setup_open = True

    def disconnect(self):
        self.peer.close()
        self.outgoing.clear()


def make_manager(folder, **kwargs):
    return ControllerSessions(Window(), Settings(Path(folder) / "control.json"),
                              window_factory=Window, controller_factory=Session, **kwargs)


def commands(session):
    result = []
    while session.outgoing.items:
        result.append(session.outgoing.get(timeout=0))
    return result


class ControllerSessionsTests(unittest.TestCase):
    def test_windows_share_saved_pins_but_not_live_transport_or_input_state(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = make_manager(folder)
            first = manager.new_window()
            second = manager.new_window(connect=True)
            self.assertIs(first.settings, second.settings)
            self.assertIs(first.book, second.book)
            first.settings.data["pins"]["first.example:64321"] = "1" * 64
            second.settings.data["pins"]["second.example:64321"] = "2" * 64
            first.settings.save()
            self.assertEqual(len(Settings(first.settings.path).data["pins"]), 2)
            for name in ("root", "peer", "outgoing", "buttons_down", "keys_down", "mesh_panel", "stopping"):
                self.assertIsNot(getattr(first, name), getattr(second, name), name)
            self.assertEqual([first.session_window_id, second.session_window_id], [1, 2])
            self.assertTrue(second.code_setup_open)
            self.assertEqual(len(first.rows), 2)

    def test_switch_releases_held_inputs_to_original_peer_before_new_mouse_input(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = make_manager(folder)
            first = manager.new_window(); second = manager.new_window()
            commands(first); commands(second)
            first.buttons_down.add("left"); first.keys_down["Control_L"] = "ctrl"
            first.pending_move = {"type": "input", "action": "move", "captureId": "screen-1"}
            manager.focus_session(second.session_window_id)
            second._mouse_down(SimpleNamespace(num=3, x=80, y=20))
            original = commands(first); active = commands(second)
            self.assertFalse(first.buttons_down); self.assertFalse(first.keys_down)
            self.assertIsNone(first.pending_move)
            self.assertEqual([message["action"] for _generation, _peer, message in original], ["release"])
            self.assertTrue(all(peer is first.peer for _generation, peer, _message in original))
            self.assertEqual([message["action"] for _generation, _peer, message in active], ["move", "button"])
            self.assertTrue(all(peer is second.peer for _generation, peer, _message in active))
            self.assertTrue(all(message["captureId"] == "screen-2" for _generation, _peer, message in active))

    def test_each_session_queues_file_messages_for_its_own_peer_and_generation(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = make_manager(folder)
            first = manager.new_window(); second = manager.new_window()
            commands(first); commands(second)
            first._send({"type": "file_offer", "id": "first-file"})
            second.generation = 5
            second._send({"type": "file_request", "id": "second-file"})
            self.assertEqual(commands(first), [(1, first.peer, {"type": "file_offer", "id": "first-file"})])
            self.assertEqual(commands(second), [(5, second.peer, {"type": "file_request", "id": "second-file"})])

    def test_close_one_keeps_other_transport_and_hidden_owner_alive(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = make_manager(folder)
            first = manager.new_window(); second = manager.new_window()
            first.close()
            self.assertTrue(first.peer.closed); self.assertTrue(first.mesh_closed)
            self.assertFalse(second.peer.closed); self.assertFalse(second.mesh_closed)
            self.assertEqual(manager.owner.destroy_count, 0)
            self.assertEqual(set(manager.controllers), {second.session_window_id})
            self.assertEqual(first.root.destroy_count, 1)
            self.assertFalse(first.connection_var.callbacks)
            # A previously queued status update after unregister cannot refresh dead widgets.
            first.connection_var.set("stale first window")
            second.connection_var.set("second remains connected")
            self.assertEqual(len(second.rows), 1)
            second.close()
            self.assertEqual(manager.owner.destroy_count, 1)
            second.close()
            self.assertEqual(manager.owner.destroy_count, 1)

    def test_existing_connecting_and_retry_endpoint_routes_to_original_window(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = make_manager(folder)
            first = manager.new_window(); second = manager.new_window()
            first.connected_endpoint = "pc-a.example:64321"
            second.connecting_endpoint = ("PC-B.EXAMPLE", 12345)
            self.assertEqual(manager.find_endpoint("PC-A.EXAMPLE", 64321), first.session_window_id)
            self.assertEqual(manager.find_endpoint("pc-b.example", 12345), second.session_window_id)
            second.connecting_endpoint = None
            second.retry_job = "retry"
            second.retry_credentials = ("pc-b.example", 12345, "memory-only-password", "Controller")
            self.assertEqual(manager.find_endpoint("pc-b.example", 12345), second.session_window_id)
            second.retry_job = None
            self.assertIsNone(manager.find_endpoint("pc-b.example", 12345))

    def test_saved_pc_selection_from_busy_window_opens_an_independent_connection(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = make_manager(folder)
            first = manager.new_window()
            original_peer = first.peer
            entry = {"name": "Second PC", "address": "second.example", "port": 64321}
            first._selected_computer = lambda: entry
            first._mesh_endpoint_busy = lambda: True
            created = Controller._connect_computer(first)
            self.assertEqual(len(manager.controllers), 2)
            self.assertIs(created, manager.controllers[2])
            self.assertEqual(created.prepared, entry)
            self.assertIs(first.peer, original_peer)
            self.assertFalse(original_peer.closed)

    def test_saved_pc_already_open_focuses_its_window_without_a_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = make_manager(folder)
            first = manager.new_window(); second = manager.new_window()
            second.connected_endpoint = "second.example:64321"
            entry = {"name": "Second PC", "address": "SECOND.EXAMPLE", "port": 64321}
            first._selected_computer = lambda: entry
            first._mesh_endpoint_busy = lambda: True
            prior_focus = second.root.focus_count
            Controller._connect_computer(first)
            self.assertEqual(len(manager.controllers), 2)
            self.assertEqual(second.root.focus_count, prior_focus + 1)
            self.assertFalse(first.peer.closed or second.peer.closed)

    def test_limit_does_not_replace_or_close_an_existing_session(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = make_manager(folder, limit=2)
            first = manager.new_window(); second = manager.new_window()
            with patch("controller.messagebox.showinfo") as notice:
                self.assertIsNone(manager.new_window(parent=first.root))
                notice.assert_called_once()
            self.assertEqual(len(manager.controllers), 2)
            self.assertFalse(first.peer.closed or second.peer.closed)
            self.assertEqual(manager.owner.destroy_count, 0)

    def test_constructor_failure_preserves_existing_windows(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = make_manager(folder)
            first = manager.new_window()
            created = []
            manager.window_factory = lambda owner: created.append(Window(owner)) or created[-1]
            manager.controller_factory = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("construction failed"))
            with self.assertRaises(RuntimeError):
                manager.new_window()
            self.assertEqual(created[0].destroy_count, 1)
            self.assertEqual(list(manager.controllers.values()), [first])
            self.assertFalse(first.peer.closed)
            self.assertEqual(manager.owner.destroy_count, 0)

    def test_saved_entry_is_copied_and_prepared_in_new_window(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = make_manager(folder)
            entry = {"name": "Office", "address": "office.example", "port": 64321}
            first = manager.new_window(entry=entry)
            self.assertEqual(first.prepared, entry)
            self.assertIsNot(first.prepared, entry)

    def test_stale_unregister_cannot_remove_another_session(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = make_manager(folder)
            first = manager.new_window()
            impostor = SimpleNamespace(session_window_id=first.session_window_id)
            manager.unregister(impostor)
            self.assertIs(manager.controllers[first.session_window_id], first)
            self.assertEqual(manager.owner.destroy_count, 0)
            self.assertFalse(manager.focus_session(999))

    def test_close_all_closes_each_owned_session_once(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = make_manager(folder)
            first = manager.new_window(); second = manager.new_window()
            manager.close_all()
            self.assertFalse(manager.controllers)
            self.assertTrue(first.peer.closed and second.peer.closed)
            self.assertTrue(first.mesh_closed and second.mesh_closed)
            self.assertEqual(first.root.destroy_count, 1)
            self.assertEqual(second.root.destroy_count, 1)
            self.assertEqual(manager.owner.destroy_count, 1)
            self.assertIsNone(manager.new_window())


if __name__ == "__main__":
    unittest.main()
