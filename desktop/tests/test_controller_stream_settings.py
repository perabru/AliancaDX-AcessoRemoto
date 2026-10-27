"""Session-isolated image preferences and Host-confirmed application state."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from PIL import Image
import controller
from test_controller_stability import app_stub, frame, Peer, Value
from test_controller_network import network_app


def settings_app(settings=None):
    app = app_stub()
    app.settings = settings or SimpleNamespace(data={"stream_mode": "auto", "pins": {}}, save=Mock())
    app.stream_mode_preference = controller.valid_stream_mode(app.settings.data.get("stream_mode")) or "auto"
    app.stream_mode_var = Value(app.stream_mode_preference)
    app.stream_mode_status_var, app.stream_gear_var = Value(), Value()
    app.stream_settings_popup = None
    app._reset_stream_settings()
    return app


def shown_frame(app, mode="auto", version=1):
    image = Image.new("RGB", (100, 50))
    app._show_frame(dict(frame(), streamSettingsVersion=version, streamMode=mode), image)


def commands(app):
    result = []
    while (item := app.outgoing.get(0)) is not None:
        result.append(item[2])
    return result


class ControllerStreamSettingsTests(unittest.TestCase):
    def test_missing_and_invalid_persisted_preferences_default_to_auto(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "control.json"
            self.assertEqual(controller.Settings(path).data["stream_mode"], "auto")
            for mode in (None, "unsupported", True, [], 1, {}):
                with self.subTest(mode=mode):
                    path.write_text(json.dumps({"stream_mode": mode, "address": "office.example"}))
                    value = controller.Settings(path).data
                    self.assertEqual(value["stream_mode"], "auto")
                    self.assertEqual(value["address"], "office.example")

    def test_selected_preference_survives_a_new_settings_instance(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "control.json"
            settings = controller.Settings(path)
            settings.data["name"] = "Controlador do professor"
            app = settings_app(settings)
            app._choose_stream_mode("quality")
            loaded = controller.Settings(path).data
            self.assertEqual(loaded["stream_mode"], "quality")
            self.assertEqual(loaded["name"], "Controlador do professor")
            self.assertNotIn("password", loaded)

    def test_preference_before_first_frame_is_saved_without_sending_a_command(self):
        app = settings_app()
        self.assertTrue(app._choose_stream_mode("quality"))
        self.assertEqual(commands(app), [])
        self.assertIsNone(app.stream_mode_applied)
        self.assertEqual(app.stream_mode_preference, "quality")
        self.assertIn("Aguardando confirmação", app.stream_mode_status_var.get())
        self.assertEqual(app.stream_gear_var.get(), "Imagem")
        shown_frame(app, "auto")
        self.assertEqual(commands(app), [{"type": "stream_settings", "version": 1, "mode": "quality"}])
        app._clear_frame()

    def test_applied_mode_changes_only_after_a_rendered_host_confirmation(self):
        app = settings_app()
        shown_frame(app, "auto")
        app._choose_stream_mode("latency")
        self.assertEqual(app.stream_mode_applied, "auto")
        self.assertIn("Aplicando Resposta rápida", app.stream_mode_status_var.get())
        self.assertIn("Em uso: Automático", app.stream_mode_status_var.get())
        self.assertEqual(app.stream_gear_var.get(), "Auto…")
        commands(app)
        shown_frame(app, "latency")
        self.assertEqual(app.stream_mode_applied, "latency")
        self.assertEqual(app.stream_gear_var.get(), "Rápido")
        self.assertEqual(app.stream_mode_status_var.get(), "Em uso: Resposta rápida.")
        self.assertEqual(commands(app), [])
        app._clear_frame()

    def test_repeated_frames_do_not_repeat_pending_settings(self):
        app = settings_app()
        shown_frame(app)
        app._choose_stream_mode("quality")
        shown_frame(app)
        shown_frame(app)
        self.assertEqual(len(commands(app)), 1)
        app._clear_frame()

    def test_latest_preference_is_sent_once_after_capability_arrives(self):
        app = settings_app()
        for mode in ("quality", "latency", "auto", "quality"):
            app._choose_stream_mode(mode)
        self.assertEqual(commands(app), [])
        shown_frame(app)
        self.assertEqual(commands(app), [{"type": "stream_settings", "version": 1, "mode": "quality"}])
        app._clear_frame()

    def test_old_host_stays_connected_in_auto_and_does_not_receive_settings(self):
        app = settings_app()
        shown_frame(app, version=None)
        peer = app.peer
        app._choose_stream_mode("quality")
        self.assertEqual(commands(app), [])
        self.assertEqual(app.stream_mode_applied, "auto")
        self.assertEqual(app.stream_mode_preference, "quality")
        self.assertEqual(app.stream_gear_var.get(), "Auto")
        self.assertIn("Atualize o Hospedeiro", app.stream_mode_status_var.get())
        self.assertIs(app.peer, peer)
        self.assertFalse(peer.closed)
        app._clear_frame()

    def test_invalid_capability_metadata_never_enables_settings(self):
        for mode, version in (("auto", True), ("auto", 2), ("auto", "1"), (None, 1), ([], 1), ("unknown", 1)):
            with self.subTest(mode=mode, version=version):
                app = settings_app()
                shown_frame(app, mode, version)
                app._choose_stream_mode("quality")
                self.assertIsNone(app.stream_settings_generation)
                self.assertEqual(commands(app), [])
                app._clear_frame()

    def test_unrendered_frame_does_not_prove_host_support_or_apply_preference(self):
        app = settings_app()
        app._render_frame = Mock(return_value=False)
        app._choose_stream_mode("quality")
        shown_frame(app)
        self.assertFalse(app.stream_settings_checked)
        self.assertEqual(commands(app), [])
        app._clear_frame()

    def test_wrong_generation_cannot_send_using_previous_peer_capability(self):
        app = settings_app()
        shown_frame(app)
        app.generation += 1
        app._choose_stream_mode("quality")
        self.assertEqual(commands(app), [])
        app._clear_frame()

    def test_pause_keeps_preference_pending_until_fresh_display(self):
        app = settings_app()
        shown_frame(app)
        app.local_paused = True
        app._choose_stream_mode("quality")
        self.assertEqual(commands(app), [])
        self.assertIn("Aguardando a tela retomar", app.stream_mode_status_var.get())
        app.local_paused = False
        shown_frame(app)
        self.assertEqual(commands(app), [{"type": "stream_settings", "version": 1, "mode": "quality"}])
        app._clear_frame()

    def test_save_failure_keeps_session_choice_and_existing_settings(self):
        settings = SimpleNamespace(data={"stream_mode": "auto", "pins": {"known": "kept"}},
                                   save=Mock(side_effect=OSError("read only")))
        app = settings_app(settings)
        shown_frame(app)
        app._choose_stream_mode("quality")
        self.assertEqual(commands(app), [{"type": "stream_settings", "version": 1, "mode": "quality"}])
        self.assertEqual(app.stream_mode_preference, "quality")
        self.assertEqual(settings.data, {"stream_mode": "auto", "pins": {"known": "kept"}})
        self.assertIn("não pôde ser salva", app.stream_mode_status_var.get())
        shown_frame(app, "quality")
        self.assertEqual(app.stream_mode_applied, "quality")
        self.assertFalse(app.peer.closed)
        app._clear_frame()

    def test_shared_settings_do_not_change_other_open_window_selection(self):
        settings = SimpleNamespace(data={"stream_mode": "auto", "pins": {}}, save=Mock())
        first, second = settings_app(settings), settings_app(settings)
        shown_frame(first)
        shown_frame(second)
        first._choose_stream_mode("quality")
        self.assertEqual(second.stream_mode_preference, "auto")
        self.assertEqual(second.stream_mode_var.get(), "auto")
        self.assertEqual(second.stream_mode_applied, "auto")
        self.assertEqual(commands(second), [])
        third = settings_app(settings)
        self.assertEqual(third.stream_mode_preference, "quality")
        first._clear_frame(); second._clear_frame()

    def test_reconnect_replays_only_preference_after_new_host_capability(self):
        app = network_app()
        app.stream_mode_preference = "quality"
        app.stream_settings_generation = app.generation
        app.stream_settings_checked = True
        app.stream_mode_applied = "quality"
        app.stream_mode_last_sent = "quality"
        app._remember_connected_computer = Mock()
        app.workspace = SimpleNamespace(select=Mock())
        app.remote_page = "session"
        app.connecting = True
        with patch("transfer.FileTransfers", return_value=SimpleNamespace(close=Mock())):
            app._connected(Peer())
        sent = commands(app)
        self.assertEqual([m["type"] for m in sent], ["stream_ready"])
        self.assertIsNone(app.stream_settings_generation)
        self.assertIsNone(app.stream_mode_applied)
        self.assertEqual(app.stream_mode_preference, "quality")
        shown_frame(app)
        self.assertEqual(commands(app), [{"type": "stream_settings", "version": 1, "mode": "quality"}])
        app._clear_frame()

    def test_menu_blocks_remote_input_without_changing_sharing_or_peer(self):
        app = settings_app()
        self.assertTrue(app._input_ready())
        app.stream_settings_popup = object()
        self.assertFalse(app._input_ready())
        self.assertTrue(app.sharing)
        self.assertFalse(app.peer.closed)

    def test_invalid_user_choice_does_not_save_send_or_change_preference(self):
        app = settings_app()
        for mode in (None, "unknown", [], True):
            self.assertFalse(app._choose_stream_mode(mode))
        self.assertEqual(app.stream_mode_preference, "auto")
        self.assertEqual(commands(app), [])
        app.settings.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
