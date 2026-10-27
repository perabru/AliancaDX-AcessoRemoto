"""Manual update actions, worker isolation and disposal without a display."""
from pathlib import Path
import queue
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

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
    value.browser_open = Mock(return_value=True)
    value.window, value.progress = Mock(), Mock()
    value.title_var, value.message_var, value.latest_var, value.install_note_var = Mock(), Mock(), Mock(), Mock()
    value.install_note_label = Mock()
    value.status_label, value.download_button, value.retry_button = Mock(), Mock(), Mock()
    return value


class UpdateDialogTests(unittest.TestCase):
    def test_only_explicit_download_action_opens_validated_installer(self):
        dialog = dialog_stub()
        dialog._show_result(UpdateResult("available", "7.4.0", "7.5.0", "Disponível", URL))
        dialog.browser_open.assert_not_called()
        dialog.download_button.pack.assert_called_once()
        self.assertTrue(dialog.download())
        dialog.browser_open.assert_called_once_with(URL)

    def test_current_offline_timeout_limit_and_error_have_no_download_action(self):
        for status in ("current", "ahead", "offline", "timeout", "limited", "error"):
            with self.subTest(status=status):
                dialog = dialog_stub()
                dialog._show_result(UpdateResult(status, "7.4.0", None, "Mensagem"))
                dialog.download_button.pack.assert_not_called()
                self.assertFalse(dialog.download())
                dialog.browser_open.assert_not_called()

    def test_untrusted_release_cannot_open_browser(self):
        for url in ("https://evil.example/setup.exe", URL.replace("Controlador", "Host"), URL + "?redirect=bad"):
            dialog = dialog_stub()
            dialog._show_result(UpdateResult("available", "7.4.0", "7.5.0", "Disponível", url))
            self.assertEqual(dialog.result.status, "error")
            self.assertFalse(dialog.download())
            dialog.browser_open.assert_not_called()

    def test_browser_failure_is_visible_and_retry_remains_possible(self):
        dialog = dialog_stub()
        dialog._show_result(UpdateResult("available", "7.4.0", "7.5.0", "Disponível", URL))
        dialog.browser_open.side_effect = OSError("browser")
        self.assertFalse(dialog.download())
        self.assertIn("Não foi possível abrir", dialog.message_var.set.call_args.args[0])
        dialog.browser_open.side_effect = None
        self.assertTrue(dialog.download())

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
            factory.assert_called_once_with(app.root, controller.VERSION, "controller")
            first.focus.assert_called_once()
        app._release_input.assert_called()
        app._close_stream_settings.assert_called_with(restore_focus=False)
        app.stopping.set()
        self.assertIsNone(app._open_updates())

    def test_host_open_releases_tracked_inputs_without_disconnecting(self):
        app = host.HostApp.__new__(host.HostApp)
        app.shutdown, app.root, app.control_lock = threading.Event(), object(), threading.RLock()
        app.inputs = SimpleNamespace(release=Mock())
        app.peer, app.sharing = object(), True
        peer = app.peer
        with patch.object(desktop_ui, "UpdateDialog") as factory:
            first = app._open_updates()
            self.assertIs(app._open_updates(), first)
            factory.assert_called_once_with(app.root, host.VERSION, "host")
        self.assertIs(app.peer, peer)
        self.assertTrue(app.sharing)
        app.inputs.release.assert_called()
        app.shutdown.set()
        self.assertIsNone(app._open_updates())


if __name__ == "__main__":
    unittest.main()
