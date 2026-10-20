import base64
import io
import json
import queue
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from controller import Controller, Settings, fit_geometry, normalized_point, remote_key, monitor_identity, fingerprint_text, pin_key
from desktop_geometry import physical_point
from computer_book import ComputerBook


class Value:
    def __init__(self, value=None): self.value = value
    def set(self, value): self.value = value
    def get(self): return self.value


class Widget:
    def __init__(self): self.options = {}; self.grabbed = False
    def configure(self, **values): self.options.update(values)
    def grab_set(self): self.grabbed = True
    def grab_release(self): self.grabbed = False
    def focus_set(self): pass


class Peer:
    def __init__(self): self.closed = False; self.messages = []
    def send(self, message): self.messages.append(message)
    def close(self): self.closed = True


class Tree:
    def __init__(self): self.rows = {}; self.selected = ()
    def get_children(self): return tuple(self.rows)
    def exists(self, identity): return identity in self.rows
    def insert(self, _parent, _position, iid, **values): self.rows[iid] = values
    def item(self, identity, **values): self.rows[identity].update(values)
    def delete(self, identity): self.rows.pop(identity)
    def selection(self): return self.selected


def computers_app(folder):
    app = Controller.__new__(Controller)
    app.settings = Settings(Path(folder) / "control.json")
    app.book = ComputerBook(Path(folder) / "computers.json")
    app.peer = None; app.connecting = False
    app.connected_endpoint = None; app.connecting_endpoint = None
    app.buffered_messages = []
    app.computer_entries = {}; app.computer_revisions = {}; app.computer_presence = {}
    app.presence_pending = {}; app.presence_tasks = queue.Queue(maxsize=256)
    app.presence_refresh_requested = set()
    app.seen_persisted = {}; app.stopping = threading.Event()
    app.computers_tree = Tree(); app.computer_notice_var = Value()
    for name in ("add_computer_button", "edit_computer_button", "delete_computer_button",
                 "refresh_computers_button", "connect_computer_button"):
        setattr(app, name, Widget())
    app.address_var = Value("manual.example"); app.port_var = Value("64321")
    app.password_var = Value("password-stays-local"); app.name_var = Value("Controlador próprio")
    return app


def input_app():
    app = Controller.__new__(Controller)
    app.peer = Peer()
    app.sharing, app.local_paused = True, False
    app.dialog_active = app.local_prompt_active = app.remote_prompt_active = False
    app.pending_monitor = None
    app.current_screen = {"captureId": "geometry-one"}
    app.image_bounds = (10, 20, 301, 201)
    app.buttons_down, app.keys_down = set(), {}
    app.last_point, app.last_move = (0.5, 0.5), 0
    app.canvas = Widget()
    app.root = SimpleNamespace(focus_get=lambda: app.canvas, attributes=lambda *args: None)
    return app


class ControllerGeometryTests(unittest.TestCase):
    def test_fits_both_dimensions_for_wide_portrait_and_virtual_desktop(self):
        for natural in [(1920, 1080), (4480, 1620), (1080, 1920), (7680, 2160)]:
            bounds = fit_geometry(900, 500, *natural)
            self.assertGreater(bounds[2], 0); self.assertGreater(bounds[3], 0)
            self.assertGreaterEqual(bounds[0], 0); self.assertGreaterEqual(bounds[1], 0)
            self.assertLessEqual(bounds[0] + bounds[2], 900)
            self.assertLessEqual(bounds[1] + bounds[3], 500)
            self.assertAlmostEqual(bounds[2] / bounds[3], natural[0] / natural[1], delta=.015)

    def test_last_visible_pixel_reaches_entire_physical_desktop_corner(self):
        bounds = fit_geometry(900, 500, 4480, 1620)
        left, top, width, height = bounds
        point = normalized_point(left + width - 1, top + height - 1, bounds)
        self.assertEqual(point, (1.0, 1.0))
        screen = {"left": -1920, "top": -180, "width": 4480, "height": 1620}
        self.assertEqual(physical_point(screen, *point), (2559, 1439))
        self.assertEqual(normalized_point(left, top, bounds), (0.0, 0.0))
        self.assertIsNone(normalized_point(left - 1, top, bounds))

    def test_drag_outside_clamps_instead_of_leaving_held_input(self):
        self.assertEqual(normalized_point(-100, 1000, (20, 30, 100, 200), True), (0.0, 1.0))
        self.assertIsNone(normalized_point(0, 0, (0, 0, 0, 0)))

    def test_monitor_all_normalization_and_invalid_id(self):
        for value in (0, "0", "all"):
            self.assertEqual(monitor_identity(value), 0)
        self.assertEqual(monitor_identity("2"), 2)
        with self.assertRaises(ValueError): monitor_identity("invalid")

    def test_keys_use_pyautogui_names_and_avoid_unsupported_unicode(self):
        cases = [("Control_L", "", "ctrl"), ("Return", "", "enter"), ("Prior", "", "pageup"),
                 ("a", "\x01", "a"), ("A", "A", "a"), ("F12", "", "f12"), ("KP_3", "", "3")]
        for keysym, char, expected in cases:
            self.assertEqual(remote_key(keysym, char), expected)
        self.assertIsNone(remote_key("eacute", "é"))


class ControllerStateTests(unittest.TestCase):
    def test_mouse_last_pixel_and_drag_release_are_exact(self):
        app = input_app()
        app._mouse_down(SimpleNamespace(num=1, x=310, y=220))
        self.assertTrue(app.canvas.grabbed)
        press = app.peer.messages[-1]
        self.assertEqual((press["x"], press["y"]), (1.0, 1.0))
        self.assertEqual(press["captureId"], "geometry-one")
        app._mouse_up(SimpleNamespace(num=1, x=900, y=-100))
        self.assertFalse(app.canvas.grabbed)
        self.assertEqual((app.peer.messages[-1]["x"], app.peer.messages[-1]["y"]), (1.0, 0.0))
        self.assertFalse(app.peer.messages[-1]["down"])

    def test_modifier_repeat_and_blur_release(self):
        app = input_app()
        event = SimpleNamespace(keysym="Control_L", char="", keycode=37)
        app._key_down(event); app._key_down(event)
        self.assertEqual(len(app.peer.messages), 1)
        app._release_input()
        self.assertEqual(app.peer.messages[-1], {"type": "input", "action": "release"})
        self.assertEqual(app.keys_down, {})

    def test_releases_remain_possible_after_pause(self):
        app = input_app(); app.buttons_down.add("left")
        app.local_paused = True; app.sharing = False; app.current_screen = None
        app._mouse_up(SimpleNamespace(num=1, x=10, y=20))
        self.assertFalse(app.peer.messages[-1]["down"])
        self.assertFalse(app._input_ready())

    def test_fullscreen_escape_is_local_and_sends_only_release(self):
        app = input_app(); app.fullscreen = True
        app.fullscreen_button = Widget(); app._resize = lambda: None
        self.assertEqual(app._key_down(SimpleNamespace(keysym="Escape", char="", keycode=9)), "break")
        self.assertFalse(app.fullscreen)
        self.assertEqual(app.peer.messages, [{"type": "input", "action": "release"}])

    def test_pending_all_monitor_accepts_string_all(self):
        app = input_app(); app.pending_monitor = 0; app.current_screen = None; app.current_image = None
        app.screen_var = Value(); app._set_monitors = lambda message: None
        app._render_frame = lambda: None; app._update_controls = lambda: None
        image = Image.new("RGB", (100, 50))
        message = {"monitorId": "all", "width": 4480, "height": 1620, "captureId": "all-geometry", "monitors": [{"id": "1"}]}
        app._show_frame(message, image)
        self.assertEqual(app.current_screen["captureId"], "all-geometry")
        self.assertIsNone(app.pending_monitor)
        image.close()

    def test_pending_individual_monitor_drops_old_all_frame(self):
        app = input_app(); app.pending_monitor = 1; app.current_screen = None; app.current_image = None
        image = Image.new("RGB", (100, 50))
        app._show_frame({"monitorId": "all", "monitors": [{"id": "1"}]}, image)
        self.assertIsNone(app.current_screen)
        self.assertEqual(app.pending_monitor, 1)

    def test_stale_connection_result_is_closed(self):
        app = input_app(); app.generation = 2; app.stopping = threading.Event()
        stale = Peer(); app._publish_connection(stale, 1)
        self.assertTrue(stale.closed)
        self.assertFalse(app.peer.closed)

    def test_transfer_fraction_is_rendered_as_percentage(self):
        app = Controller.__new__(Controller); app.file_var = Value(); app.progress_var = Value()
        app._update_controls = lambda: None
        app._file_status({"active": True, "message": "Recebendo", "progress": .5})
        self.assertEqual(app.progress_var.value, 50)
        self.assertTrue(app.file_active)

    def test_transfer_progress_respects_closed_panel_and_fullscreen(self):
        app = Controller.__new__(Controller)
        app.file_var = Value(); app.progress_var = Value(); app.files_panel = Widget()
        app.file_active = False; app.files_visible = False
        app.selected_page = "session"; app.fullscreen = False
        opened, released = [], []
        app._release_input = lambda: released.append(True)
        def toggle_files(value):
            app._release_input(); opened.append(value); app.files_visible = value
        app._toggle_files = toggle_files; app._update_controls = lambda: None
        app._file_status({"active": True, "message": "Enviando", "progress": .01})
        self.assertEqual(opened, [True])
        self.assertEqual(released, [True])
        app.files_visible = False  # The user closes Files while it continues.
        app._file_status({"active": True, "message": "Enviando", "progress": .50})
        app._file_status({"active": True, "message": "Enviando", "progress": .75})
        self.assertFalse(app.files_visible)
        self.assertEqual(opened, [True])
        self.assertEqual(released, [True])
        self.assertEqual(app.progress_var.get(), 75)
        app._file_status({"active": False, "message": "Concluído", "progress": 1})
        app.fullscreen = True
        app._file_status({"active": True, "message": "Recebendo", "progress": 0})
        self.assertEqual(opened, [True], "A transfer must not replace the fullscreen viewer")
        self.assertEqual(released, [True])

    def test_decoder_handles_scaled_jpeg_and_string_all(self):
        app = Controller.__new__(Controller)
        app.stopping = threading.Event(); app.frame_lock = threading.Lock(); app.frame_event = threading.Event()
        app.generation = app.frame_epoch = 1; app.decoded_frame = None; app.screen_var = Value()
        app._post = lambda callback, _generation=None: callback()
        buffer = io.BytesIO(); Image.new("RGB", (200, 100), "orange").save(buffer, "JPEG")
        message = {"image": base64.b64encode(buffer.getvalue()).decode(), "width": 2000, "height": 1000,
                   "left": -1000, "top": -200, "captureId": "geometry", "monitorId": "all", "monitors": []}
        app.raw_frame = (1, 1, message)
        thread = threading.Thread(target=app._decode_frames, daemon=True); thread.start(); app.frame_event.set()
        deadline = time.monotonic() + 2
        while app.decoded_frame is None and time.monotonic() < deadline: time.sleep(.01)
        app.stopping.set(); app.frame_event.set(); thread.join(1)
        self.assertIsNotNone(app.decoded_frame)
        self.assertEqual(app.decoded_frame[3].size, (200, 100))
        app.decoded_frame[3].close()


class ControllerSettingsTests(unittest.TestCase):
    def test_settings_keep_pins_but_never_password(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.json"
            path.write_text(json.dumps({"password": "secret123", "pins": {"pc:64321": "A" * 64}, "port": 64321}))
            settings = Settings(path); settings.save()
            saved = json.loads(path.read_text())
            self.assertNotIn("password", saved)
            self.assertEqual(saved["pins"]["pc:64321"], "a" * 64)

    def test_fingerprint_and_address_are_canonical(self):
        self.assertEqual(fingerprint_text(":".join(["AB"] * 32)), "ab" * 32)
        self.assertEqual(pin_key("PC.EXAMPLE.", 64321), "pc.example:64321")
        self.assertEqual(pin_key("::1", 64321), "[::1]:64321")
        self.assertEqual(pin_key("[0:0:0:0:0:0:0:1]", 64321), "[::1]:64321")
        self.assertEqual(pin_key("BÜCHER.example.", 64321), "xn--bcher-kva.example:64321")
        self.assertEqual(pin_key("fe80::1%EthZ", 64321), "[fe80::1%EthZ]:64321")
        with self.assertRaises(ValueError): fingerprint_text("not a SHA256 fingerprint")

    def test_legacy_unicode_certificate_pin_migrates_without_changing_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.json"
            path.write_text(json.dumps({"pins": {"bücher.example:64321": "A" * 64}}))
            settings = Settings(path)
            self.assertEqual(settings.data["pins"], {"xn--bcher-kva.example:64321": "a" * 64})
            settings.save()
            self.assertEqual(json.loads(path.read_text())["pins"], settings.data["pins"])

    def test_conflicting_legacy_alias_pins_require_fresh_manual_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.json"
            path.write_text(json.dumps({"pins": {"bücher.example:64321": "a" * 64,
                "xn--bcher-kva.example:64321": "b" * 64, "BÜCHER.EXAMPLE.:64321": "a" * 64}}))
            self.assertEqual(Settings(path).data["pins"], {})


class ControllerComputersTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = computers_app(self.directory.name)

    def saved(self, name="Escritório", address="office.example", port=64321):
        entry = self.app.book.add(name, address, port)
        self.app._refresh_computer_rows()
        return entry

    def token(self, entry):
        self.app._refresh_computers(force=True)
        return self.app.presence_pending[entry["id"]]

    def test_saved_endpoint_opens_connection_without_reusing_another_password(self):
        entry = self.saved(); self.app.computers_tree.selected = (entry["id"],)
        called = []; self.app.connect = lambda: called.append(True)
        pages = []; self.app._show_page = lambda page: pages.append(page)
        self.app._connect_computer()
        self.assertEqual((self.app.address_var.get(), self.app.port_var.get()), ("office.example", "64321"))
        self.assertEqual(self.app.password_var.get(), "")
        self.assertEqual(self.app.name_var.get(), "Controlador próprio")
        self.assertEqual(called, [])
        self.assertEqual(pages, ["connect"])
        self.app.password_var.set("password-for-this-endpoint")
        self.app._connect_computer()
        self.assertEqual(self.app.password_var.get(), "password-for-this-endpoint")
        self.assertEqual(called, [])

    def test_probe_snapshots_pin_without_password_and_does_not_overlap(self):
        entry = self.saved(); token = self.token(entry)
        self.app._refresh_computers(force=True)
        self.assertEqual(self.app.presence_tasks.qsize(), 1)
        task = self.app.presence_tasks.get_nowait()
        self.assertEqual(task, (token, "office.example", 64321, None))
        self.assertNotIn("password", repr(task))

    def test_online_result_preserves_alias_and_marks_seen_without_pinning(self):
        entry = self.saved(); token = self.token(entry)
        self.app._presence_result(token, {"state": "online", "computer_name": "Other name", "detail": "Disponível", "fingerprint": "a" * 64})
        current = self.app.book.entries()[0]
        self.assertEqual(current["name"], "Escritório")
        self.assertTrue(current["last_seen"])
        self.assertEqual(self.app.settings.data["pins"], {})
        self.assertEqual(self.app.computers_tree.rows[entry["id"]]["tags"], ("online",))
        self.app._refresh_computers()
        self.assertNotIn(entry["id"], self.app.presence_pending)

    def test_manual_refresh_while_pending_runs_once_after_current_probe(self):
        entry = self.saved(); token = self.token(entry)
        self.app._refresh_computers(force=True)
        self.app._refresh_computers(force=True)
        self.assertEqual(self.app.presence_tasks.qsize(), 1)
        self.app.presence_tasks.get_nowait()
        self.app._presence_result(token, {"state": "offline"})
        self.assertIn(entry["id"], self.app.presence_pending)
        self.assertEqual(self.app.presence_tasks.qsize(), 1)
        self.assertFalse(self.app.presence_refresh_requested)

    def test_rename_and_endpoint_edit_discard_old_probe_and_queue_current_snapshot(self):
        entry = self.saved(); old = self.token(entry)
        self.app.book.update(entry["id"], "Casa", "home.example", 65000)
        self.app._presence_result(old, {"state": "online", "detail": "Old host"})
        self.assertNotIn(entry["id"], self.app.computer_presence)
        new = self.app.presence_pending[entry["id"]]
        self.assertNotEqual(new, old)
        self.assertEqual(new[2], "home.example:65000")
        self.assertIsNone(self.app.book.entries()[0]["last_seen"])
        self.assertEqual(self.app.computers_tree.rows[entry["id"]]["values"][0], "Casa")

    def test_deleted_computer_cannot_be_restored_by_late_probe(self):
        entry = self.saved(); old = self.token(entry)
        self.app.book.remove(entry["id"])
        self.app._presence_result(old, {"state": "online"})
        self.assertFalse(self.app.computer_entries)
        self.assertFalse(self.app.computers_tree.rows)
        self.assertFalse(self.app.presence_pending)

    def test_changed_pin_discards_probe_captured_before_certificate_change(self):
        entry = self.saved(); old = self.token(entry)
        self.app.settings.data["pins"]["office.example:64321"] = "b" * 64
        self.app._presence_result(old, {"state": "online"})
        self.assertNotIn(entry["id"], self.app.computer_presence)
        self.assertEqual(self.app.presence_pending[entry["id"]][3], "b" * 64)

    def test_connected_computer_is_online_without_network_probe(self):
        entry = self.saved(); self.app.peer = Peer(); self.app.connected_endpoint = "office.example:64321"
        self.app._refresh_computers(force=True)
        self.assertTrue(self.app.presence_tasks.empty())
        self.assertEqual(self.app.computer_presence[entry["id"]]["state"], "online")
        self.assertEqual(self.app.computers_tree.rows[entry["id"]]["values"][3], "Online · conectado")

    def test_probe_before_session_cannot_publish_stale_busy_status_after_disconnect(self):
        entry = self.saved(); token = self.token(entry)
        self.app.connected_endpoint = "office.example:64321"; self.app.peer = Peer()
        self.app._invalidate_presence_endpoint(self.app.connected_endpoint)
        self.app._refresh_computers(force=True)
        self.app._invalidate_presence_endpoint(self.app.connected_endpoint)
        self.app.peer = None; self.app.connected_endpoint = None
        self.app._refresh_computers(force=True)
        self.assertEqual(self.app.presence_pending[entry["id"]], token)
        self.assertEqual(self.app.presence_tasks.qsize(), 1)
        self.app.presence_tasks.get_nowait()
        self.app._presence_result(token, {"state": "online", "busy": True})
        self.assertNotIn(entry["id"], self.app.computer_presence)
        fresh = self.app.presence_pending[entry["id"]]
        self.assertNotEqual(token, fresh)
        self.assertEqual(self.app.presence_tasks.qsize(), 1)
        self.app._presence_result(token, {"state": "online", "busy": True})
        self.assertEqual(self.app.presence_pending[entry["id"]], fresh)
        self.assertNotIn(entry["id"], self.app.computer_presence)

    def test_authenticated_name_applies_only_when_creating_automatic_entry(self):
        self.app.connecting_endpoint = ("manual.example", 64321)
        peer = Peer(); peer.computer_name = "Computador da sala"
        self.app._remember_connected_computer(peer)
        self.assertEqual(self.app.book.entries()[0]["name"], "Computador da sala")
        peer.computer_name = "Sobrescrever alias"
        self.app._remember_connected_computer(peer)
        self.assertEqual(self.app.book.entries()[0]["name"], "Computador da sala")

    def test_unicode_connected_endpoint_matches_canonical_registry_and_pin(self):
        entry = self.saved("Livros", "bücher.example")
        self.app.connecting_endpoint = ("bücher.example", 64321)
        self.app.peer = Peer()
        self.app.settings.data["pins"][pin_key("bücher.example", 64321)] = "c" * 64
        self.app._remember_connected_computer(self.app.peer)
        self.assertEqual(self.app.connected_endpoint, "xn--bcher-kva.example:64321")
        self.assertEqual(self.app.computers_tree.rows[entry["id"]]["values"][3], "Online · conectado")
        self.app._refresh_computers(force=True)
        self.assertTrue(self.app.presence_tasks.empty())

    def test_user_rename_is_preserved_when_peer_later_reports_another_name(self):
        self.app.connecting_endpoint = ("manual.example", 64321)
        self.app._remember_connected_computer(Peer())
        entry = self.app.book.entries()[0]
        self.app.book.update(entry["id"], "Meu nome escolhido")
        peer = Peer(); peer.computer_name = "Nome do host"
        self.app._remember_connected_computer(peer)
        self.assertEqual(self.app.book.entries()[0]["name"], "Meu nome escolhido")

    def test_four_workers_bound_concurrency_and_never_process_ui_on_workers(self):
        for index in range(10): self.saved(f"PC {index}", f"pc{index}.example")
        lock = threading.Lock(); gate = threading.Event(); active = [0]; maximum = [0]
        threads_seen = set(); callbacks = queue.Queue()
        def probe(address, port, expected_fingerprint=None, timeout=3):
            with lock:
                active[0] += 1; maximum[0] = max(maximum[0], active[0]); threads_seen.add(threading.get_ident())
            gate.wait(2)
            with lock: active[0] -= 1
            return {"state": "offline", "detail": "Test"}
        self.app.probe = probe
        self.app._post = lambda callback, generation=None: callbacks.put(callback)
        workers = [threading.Thread(target=self.app._presence_worker, daemon=True) for _ in range(4)]
        for worker in workers: worker.start()
        self.app._refresh_computers(force=True)
        deadline = time.monotonic() + 2
        while maximum[0] < 4 and time.monotonic() < deadline: time.sleep(.005)
        self.assertEqual(maximum[0], 4)
        self.assertNotIn(threading.get_ident(), threads_seen)
        self.app._refresh_computers(force=True)
        self.assertEqual(len(self.app.presence_pending), 10)
        gate.set(); self.app.presence_tasks.join()
        self.app.stopping.set()
        for worker in workers: worker.join(1)
        self.assertEqual(callbacks.qsize(), 10)
        self.assertFalse(self.app.computer_presence)


if __name__ == "__main__":
    unittest.main()
