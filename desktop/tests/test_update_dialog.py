"""Manual update actions, worker isolation and disposal without a display."""
from pathlib import Path
import queue
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import controller
import desktop_ui
import host
from updates import UpdateResult


URL = "https://github.com/perabru/AliancaDX-AcessoRemoto/releases/download/v7.5.0/AliancaDX_Controlador_Setup.exe"


def dialog_stub(checker=None):
    value = desktop_ui.UpdateDialog.__new__(desktop_ui.UpdateDialog)
    value.installed_version, value.role = "7.4.0", "controller"
    value.disposed = value.busy = False
    value.generation, value.poll_job, value.result = 0, None, None
    value.results = queue.Queue(maxsize=1)
    value.checker = checker or Mock(return_value=UpdateResult("current", "7.4.0", "7.4.0", "Atualizado"))
    value.downloader, value.installer = Mock(return_value=Path("/tmp/unused-update.exe")), Mock()
    value.is_idle, value.on_install_ready = Mock(return_value=False), Mock()
    value.can_close_connector, value.on_close_connector = None, None
    value.connector_button = Mock()
    value.cancelled, value.transfer_results = threading.Event(), queue.Queue(maxsize=1)
    value.transfer_finished = threading.Event()
    value.phase, value.prepared_path, value.transfer_job = None, None, None
    value._transfer_owned = False
    value._transfer_gate = None
    value._gate_lock = threading.Lock()
    value.download_progress = (0, 1)
    value.window, value.progress, value.close_button = Mock(), Mock(), Mock()
    value.title_var, value.message_var, value.latest_var, value.install_note_var = Mock(), Mock(), Mock(), Mock()
    value.install_note_label = Mock()
    value.status_label, value.download_button, value.retry_button = Mock(), Mock(), Mock()
    return value


class UpdateDialogTests(unittest.TestCase):
    def setUp(self):
        gate = patch.object(desktop_ui, "_UPDATE_DOWNLOAD_GATE", threading.BoundedSemaphore(1))
        gate.start(); self.addCleanup(gate.stop)

    def available(self, dialog):
        dialog._show_result(UpdateResult("available", "7.4.0", "7.5.0", "Disponível", URL, 1000, "a"*64))

    def transfer_poll(self, dialog):
        item = dialog.transfer_results.get(timeout=1)
        dialog.transfer_results.put(item)
        dialog._poll_transfer()

    def test_explicit_update_downloads_in_app_and_waits_for_all_sessions(self):
        dialog = dialog_stub()
        self.available(dialog)
        dialog.downloader.assert_not_called()
        dialog.installer.assert_not_called()
        with patch("update_install.install_target"):
            self.assertTrue(dialog.download())
        self.transfer_poll(dialog)
        self.assertEqual(dialog.phase, "waiting")
        dialog.installer.assert_not_called()
        self.assertIn("Encerre todas as conexões", dialog.message_var.set.call_args.args[0])
        dialog.is_idle.return_value = True
        dialog._poll_transfer()
        self.transfer_poll(dialog)
        dialog.installer.assert_called_once_with(Path("/tmp/unused-update.exe"), dialog.result, "controller")
        dialog.on_install_ready.assert_called_once_with()
        self.assertEqual(dialog.phase, "handed_off")

    def test_current_offline_timeout_limit_and_error_have_no_install_action(self):
        for status in ("current", "ahead", "offline", "timeout", "limited", "error"):
            with self.subTest(status=status):
                dialog = dialog_stub()
                dialog._show_result(UpdateResult(status, "7.4.0", None, "Mensagem"))
                dialog.download_button.pack.assert_not_called()
                self.assertFalse(dialog.download())
                dialog.downloader.assert_not_called()

    def test_untrusted_release_or_missing_digest_cannot_start_download(self):
        for url in ("https://evil.example/setup.exe", URL.replace("Controlador", "Host"), URL + "?redirect=bad"):
            dialog = dialog_stub()
            dialog._show_result(UpdateResult("available", "7.4.0", "7.5.0", "Disponível", url, 1000, "a"*64))
            self.assertEqual(dialog.result.status, "error")
            self.assertFalse(dialog.download())
            dialog.downloader.assert_not_called()
        dialog = dialog_stub()
        dialog._show_result(UpdateResult("available", "7.4.0", "7.5.0", "Disponível", URL))
        self.assertFalse(dialog.download())
        dialog.download_button.configure.assert_called_with(text="Atualizar agora · 7.5.0", state="disabled")

    def test_download_failure_is_visible_and_retry_remains_possible(self):
        dialog = dialog_stub(); self.available(dialog)
        dialog.downloader.side_effect = OSError("Conexão interrompida")
        with patch("update_install.install_target"):
            self.assertTrue(dialog.download())
        self.transfer_poll(dialog)
        self.assertIsNone(dialog.phase)
        self.assertIn("Conexão interrompida", dialog.message_var.set.call_args.args[0])
        dialog.retry_button.configure.assert_called_with(state="normal")
        dialog.installer.assert_not_called()

    def test_only_one_update_download_can_run_across_session_windows(self):
        first, second = dialog_stub(), dialog_stub()
        self.available(first); self.available(second)
        with patch("update_install.install_target"):
            self.assertTrue(first.download())
            self.assertFalse(second.download())
            second.downloader.assert_not_called()
            self.assertIn("outra janela", second.message_var.set.call_args.args[0])
            self.assertTrue(first.transfer_finished.wait(1))
            first.close()
            self.assertTrue(second.download())
            second.close()

    def test_cancelled_worker_holds_global_gate_until_network_returns(self):
        first, second = dialog_stub(), dialog_stub()
        self.available(first); self.available(second)
        entered, release = threading.Event(), threading.Event()
        def blocked(*args):
            entered.set(); release.wait(2)
            from update_install import UpdateCancelled
            raise UpdateCancelled()
        first.downloader = blocked
        with patch("update_install.install_target"):
            self.assertTrue(first.download())
            self.assertTrue(entered.wait(1))
            first.close()
            self.assertFalse(second.download())
            release.set()
            self.assertTrue(first.transfer_finished.wait(1))
            first.download_worker.join(timeout=1)
            self.assertFalse(first.download_worker.is_alive())
            self.assertTrue(second.download())
            self.assertTrue(second.transfer_finished.wait(1))
            second.close()

    def test_source_or_non_windows_cannot_start_installer(self):
        dialog = dialog_stub(); self.available(dialog)
        with patch("update_install.install_target", side_effect=ValueError("Use o aplicativo instalado")):
            self.assertFalse(dialog.download())
        dialog.downloader.assert_not_called()
        self.assertIn("aplicativo instalado", dialog.message_var.set.call_args.args[0])

    def test_close_cancels_download_and_never_starts_installation(self):
        dialog = dialog_stub(); self.available(dialog)
        entered, release = threading.Event(), threading.Event()
        def blocking(*args):
            entered.set(); release.wait(2)
            from update_install import UpdateCancelled
            if dialog.cancelled.is_set():
                raise UpdateCancelled()
            return Path("/tmp/unused-update.exe")
        dialog.downloader = blocking
        with patch("update_install.install_target"):
            dialog.download()
        self.assertTrue(entered.wait(1))
        dialog.close(); release.set()
        self.assertTrue(dialog.disposed)
        self.assertTrue(dialog.cancelled.is_set())
        dialog.installer.assert_not_called()

    def test_install_failure_keeps_app_open_and_reports_error(self):
        dialog = dialog_stub(); self.available(dialog)
        dialog.is_idle.return_value = True
        dialog.installer.side_effect = ValueError("Instalação bloqueada")
        with patch("update_install.install_target"):
            dialog.download()
        self.transfer_poll(dialog)
        self.transfer_poll(dialog)
        dialog.on_install_ready.assert_not_called()
        self.assertIn("Instalação bloqueada", dialog.message_var.set.call_args.args[0])
        self.assertIsNone(dialog.phase)

    def test_new_session_during_handoff_aborts_without_closing_app(self):
        dialog = dialog_stub(); self.available(dialog)
        dialog.is_idle.side_effect = [True, False]
        with patch("update_install.install_target"):
            dialog.download()
        self.transfer_poll(dialog)
        with patch("update_install.cancel_handoff", return_value=True) as cancel:
            self.transfer_poll(dialog)
            self.transfer_poll(dialog)
        cancel.assert_called_once()
        dialog.on_install_ready.assert_not_called()
        self.assertIn("Uma conexão foi aberta", dialog.message_var.set.call_args.args[0])

    def test_connector_button_requires_explicit_confirmation_and_waiting_phase(self):
        dialog = dialog_stub(); self.available(dialog)
        dialog.phase = "waiting"
        dialog.can_close_connector = Mock(return_value=True)
        dialog.on_close_connector = Mock(return_value=True)
        with patch("tkinter.messagebox.askyesno", return_value=False) as ask:
            self.assertFalse(dialog.stop_connector())
            dialog.on_close_connector.assert_not_called()
            self.assertIn("qualquer atendimento", ask.call_args.args[1])
        with patch("tkinter.messagebox.askyesno", return_value=True):
            self.assertTrue(dialog.stop_connector())
        dialog.on_close_connector.assert_called_once()
        self.assertIn("assim que o processo terminar", dialog.message_var.set.call_args.args[0])
        dialog.phase = "downloading"
        with patch("tkinter.messagebox.askyesno") as ask:
            self.assertFalse(dialog.stop_connector())
            ask.assert_not_called()

    def test_connector_cannot_be_stopped_while_other_connections_are_active(self):
        dialog = dialog_stub(); self.available(dialog); dialog.phase = "waiting"
        dialog.can_close_connector = Mock(return_value=False)
        dialog.on_close_connector = Mock(return_value=True)
        with patch("tkinter.messagebox.askyesno") as ask:
            self.assertFalse(dialog.stop_connector())
        ask.assert_not_called(); dialog.on_close_connector.assert_not_called()
        dialog.can_close_connector.return_value = True
        dialog.on_close_connector.return_value = False
        with patch("tkinter.messagebox.askyesno",return_value=True):
            self.assertFalse(dialog.stop_connector())
        self.assertIn("Uma conexão foi aberta", dialog.message_var.set.call_args.args[0])

    def test_connector_action_becomes_visible_only_when_it_is_the_remaining_blocker(self):
        dialog = dialog_stub(); self.available(dialog); dialog.phase = "waiting"
        dialog.can_close_connector = Mock(return_value=True)
        dialog._poll_transfer()
        dialog.connector_button.pack.assert_called_once()
        self.assertIn("mesmo que a aba já tenha sido fechada", dialog.message_var.set.call_args.args[0])
        dialog.can_close_connector.return_value = False
        dialog.connector_button.reset_mock(); dialog._poll_transfer()
        dialog.connector_button.pack.assert_not_called()
        dialog.installer.assert_not_called()

    def test_controller_waits_for_connector_process_to_exit_after_explicit_stop(self):
        app = controller.Controller.__new__(controller.Controller)
        app.peer, app.connecting, app.retry_job, app.retry_attempting, app.file_active = None, False, None, False, False
        app.session_manager = None
        process=Mock(); process.poll.return_value=None
        app._ios_connector_process=process
        self.assertFalse(app._update_idle()); self.assertTrue(app._update_can_close_connector())
        self.assertTrue(app._update_close_connector()); process.terminate.assert_called_once()
        self.assertIsNone(app._ios_connector_process)
        self.assertFalse(app._update_idle(), "requested stop must not be mistaken for a stopped process")
        process.poll.return_value=0
        self.assertTrue(app._update_idle())
        self.assertEqual(app._update_ios_stopping,[])

    def test_controller_connector_stop_covers_all_windows_and_refuses_active_peer(self):
        app = controller.Controller.__new__(controller.Controller)
        other=SimpleNamespace(peer=None,connecting=False,retry_job=None,retry_attempting=False,file_active=False)
        app.peer,app.connecting,app.retry_job,app.retry_attempting,app.file_active=None,False,None,False,False
        app.session_manager=SimpleNamespace(controllers={1:app,2:other})
        process=Mock();process.poll.return_value=None;other._ios_connector_process=process
        app.peer=SimpleNamespace(closed=False)
        self.assertFalse(app._update_close_connector());process.terminate.assert_not_called()
        app.peer=None
        self.assertTrue(app._update_close_connector());process.terminate.assert_called_once()
        self.assertFalse(app._update_idle());process.poll.return_value=0
        self.assertTrue(app._update_idle())

    def test_host_connector_stop_uses_idle_guard_and_confirms_exit_before_install(self):
        app=host.HostApp.__new__(host.HostApp)
        app.shutdown,app.control_lock=threading.Event(),threading.RLock()
        app.starting=False;app.pending_consents=set();app.prompt_count=0
        app.peer=SimpleNamespace(closed=False)
        process=Mock();process.poll.return_value=None;app._ios_connector_process=process
        self.assertFalse(app._update_close_connector());process.terminate.assert_not_called()
        app.peer=None
        self.assertTrue(app._update_can_close_connector())
        self.assertTrue(app._update_close_connector());process.terminate.assert_called_once()
        self.assertFalse(app._update_idle())
        process.poll.return_value=0
        self.assertTrue(app._update_idle())

    def test_invalid_or_wrong_installation_result_becomes_readable_error(self):
        for result in (None, UpdateResult("strange", "7.4.0", None, ""), UpdateResult("current", "1.0.0", None, "")):
            dialog = dialog_stub()
            dialog._show_result(result)
            self.assertEqual(dialog.result.status, "error")
            self.assertIn("validada", dialog.message_var.set.call_args.args[0])

    def test_check_runs_on_worker_and_polling_alone_updates_tk(self):
        main = threading.get_ident()
        finished = threading.Event()
        def checker(*_):
            self.assertNotEqual(threading.get_ident(), main)
            finished.set()
            return UpdateResult("offline", "7.4.0", None, "Sem conexão")
        dialog = dialog_stub(checker)
        with patch.object(desktop_ui, "_UPDATE_LOOKUP_GATE", threading.BoundedSemaphore(1)):
            self.assertTrue(dialog.start())
            self.assertFalse(dialog.start())
            self.assertTrue(finished.wait(1))
            # Queue.get blocks only this test; production uses a nonblocking Tk poll.
            item = dialog.results.get(timeout=1)
            dialog.results.put(item)
            self.assertIsNone(dialog.result)
            dialog._poll()
        self.assertEqual(dialog.result.status, "offline")
        self.assertFalse(dialog.busy)
        dialog.retry_button.configure.assert_called_with(state="normal")

    def test_close_during_check_cancels_poll_and_late_result_never_touches_tk(self):
        entered, release = threading.Event(), threading.Event()
        def checker(*_):
            entered.set(); release.wait(2)
            return UpdateResult("current", "7.4.0", "7.4.0", "Atualizado")
        dialog = dialog_stub(checker)
        with patch.object(desktop_ui, "_UPDATE_LOOKUP_GATE", threading.BoundedSemaphore(1)):
            dialog.start(); self.assertTrue(entered.wait(1))
            dialog.close()
            self.assertTrue(dialog.disposed)
            dialog.window.after_cancel.assert_called_once()
            release.set()
            dialog.results.get(timeout=1)
            dialog.window.reset_mock(); dialog.title_var.reset_mock()
            dialog._poll()
            dialog.window.assert_not_called()
            dialog.title_var.set.assert_not_called()
            self.assertFalse(dialog.start())

    def test_closing_dialog_does_not_start_parallel_network_checks(self):
        gate = threading.BoundedSemaphore(1)
        gate.acquire()
        dialog = dialog_stub()
        with patch.object(desktop_ui, "_UPDATE_LOOKUP_GATE", gate):
            dialog.start()
            item = dialog.results.get(timeout=1); dialog.results.put(item)
            dialog._poll()
            dialog.checker.assert_not_called()
            self.assertEqual(dialog.result.status, "error")
            self.assertIn("busca em andamento", dialog.message_var.set.call_args.args[0])
        gate.release()

    def test_worker_exception_is_retryable(self):
        dialog = dialog_stub(Mock(side_effect=RuntimeError("failure")))
        with patch.object(desktop_ui, "_UPDATE_LOOKUP_GATE", threading.BoundedSemaphore(1)):
            dialog.start()
            item = dialog.results.get(timeout=1); dialog.results.put(item); dialog._poll()
            self.assertEqual(dialog.result.status, "error")
            dialog.checker = Mock(return_value=UpdateResult("current", "7.4.0", "7.4.0", "Atualizado"))
            dialog.start()
            item = dialog.results.get(timeout=1); dialog.results.put(item); dialog._poll()
            self.assertEqual(dialog.result.status, "current")

    def test_controller_open_releases_input_closes_gear_and_reuses_one_dialog(self):
        app = controller.Controller.__new__(controller.Controller)
        app.stopping, app.root = threading.Event(), object()
        app._close_stream_settings, app._release_input = Mock(), Mock()
        with patch.object(desktop_ui, "UpdateDialog") as factory:
            first = app._open_updates()
            self.assertIs(app._open_updates(), first)
            factory.assert_called_once_with(app.root, controller.VERSION, "controller",
                is_idle=app._update_idle, on_install_ready=app._close_for_update,
                can_close_connector=app._update_can_close_connector, on_close_connector=app._update_close_connector)
            first.focus.assert_called_once()
        app._release_input.assert_called()
        app._close_stream_settings.assert_called_with(restore_focus=False)
        app.stopping.set()
        self.assertIsNone(app._open_updates())

    def test_controller_idle_checks_every_window_reconnect_transfer_and_connector(self):
        app = controller.Controller.__new__(controller.Controller)
        other = SimpleNamespace(peer=None, connecting=False, retry_job=None, retry_attempting=False, file_active=False)
        app.session_manager = SimpleNamespace(controllers={1: app, 2: other})
        app.peer, app.connecting, app.retry_job, app.retry_attempting, app.file_active = None, False, None, False, False
        self.assertTrue(app._update_idle())
        for name, value in (("peer", SimpleNamespace(closed=False)), ("connecting", True), ("retry_job", "timer"),
                            ("retry_attempting", True), ("file_active", True), ("_ios_connector_process", Mock(poll=Mock(return_value=None)))):
            previous = getattr(other, name, None)
            setattr(other, name, value)
            self.assertFalse(app._update_idle(), name)
            setattr(other, name, previous)
        other.peer = SimpleNamespace(closed=True)
        self.assertTrue(app._update_idle())

    def test_host_open_releases_tracked_inputs_without_disconnecting(self):
        app = host.HostApp.__new__(host.HostApp)
        app.shutdown, app.root, app.control_lock = threading.Event(), object(), threading.RLock()
        app.inputs = SimpleNamespace(release=Mock())
        app.peer, app.sharing = object(), True
        peer = app.peer
        with patch.object(desktop_ui, "UpdateDialog") as factory:
            first = app._open_updates()
            self.assertIs(app._open_updates(), first)
            factory.assert_called_once_with(app.root, host.VERSION, "host",
                is_idle=app._update_idle, on_install_ready=app._close_for_update,
                can_close_connector=app._update_can_close_connector, on_close_connector=app._update_close_connector)
        self.assertIs(app.peer, peer)
        self.assertTrue(app.sharing)
        app.inputs.release.assert_called()
        app.shutdown.set()
        self.assertIsNone(app._open_updates())


if __name__ == "__main__":
    unittest.main()
