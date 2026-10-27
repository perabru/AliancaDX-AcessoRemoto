"""Durable saved-code/list linkage and explicit update/cancel boundaries."""
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import controller
from controller import Controller, Settings, pin_key
from computer_book import ComputerBook
import mesh
from mesh_ui import MeshPanel, pairing_address


class Value:
    def __init__(self, value=""): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


def panel(directory, book=None):
    view = MeshPanel.__new__(MeshPanel)
    view.config_dir = Path(directory) / "mesh-controller"
    view.closed = False
    view.code_var = Value(); view.saved_name_var = Value("Notebook de teste")
    view.remember_var = Value(True); view.status_var = Value()
    view.code_entry = Mock(); view.hide_code = Mock(); view.winfo_toplevel = lambda: None
    view.on_select = Mock(return_value=True)
    view.on_save = (lambda pairing: book.remember_pairing(pairing, view.config_dir,
                          name=view.saved_name_var.get())) if book is not None else None
    return view


def prepared_app(directory):
    app = Controller.__new__(Controller)
    app.book = ComputerBook(Path(directory) / "computers.json")
    app.mesh_pairing = None
    app.mesh_panel = panel(directory)
    app.mesh_panel.select_pairing = Mock(return_value=True)
    app.address_var = Value(); app.port_var = Value("64321"); app.password_var = Value()
    app._open_mesh_setup = Mock(); app._computer_error = Mock()
    app._updating_computer_id = None
    app.root = Mock()
    return app


class SavedPairingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        self.book = ComputerBook(self.path / "computers.json")
        self.config = self.path / "mesh-controller"
        self.pairing = mesh.generate_pairing(fingerprint="A" * 64)

    def save(self, pairing=None, **kwargs):
        return self.book.remember_pairing(pairing or self.pairing, self.config, **kwargs)

    def test_save_before_first_authentication_survives_fresh_instances(self):
        view = panel(self.path, self.book); view.code_var.set(self.pairing.code)
        view.on_select.side_effect = lambda pairing: self.assertEqual(len(ComputerBook(self.book.path).entries()), 1) is None
        self.assertTrue(view.select_code())
        fresh = ComputerBook(self.book.path)
        record = fresh.entries()[0]
        self.assertEqual(record["name"], "Notebook de teste")
        self.assertIsNone(record["last_seen"])
        self.assertEqual(panel(self.path).load_remembered(record["address"]), self.pairing)
        self.assertNotIn(self.pairing.code, self.book.path.read_text())
        self.assertNotIn(self.pairing.network_secret, self.book.path.read_text())
        self.assertEqual(Settings(self.path / "control.json").data["pins"], {})

    def test_full_invitation_saves_only_the_code_and_public_metadata(self):
        view = panel(self.path, self.book)
        password = "not-saved-test-password"
        view.code_var.set(f"AliançaDX Host 7.2.0\nCódigo privado: {self.pairing.code}\nSenha: {password}\n")
        self.assertTrue(view.select_code())
        self.assertEqual(view.pairing, self.pairing)
        for path in self.path.rglob("*"):
            if path.is_file(): self.assertNotIn(password.encode(), path.read_bytes())

    def test_using_code_only_in_memory_never_deletes_a_previously_saved_code(self):
        record = self.save()
        updated = mesh.HostPairing(self.pairing.token, self.pairing.port, "B" * 64)
        view = panel(self.path, self.book); view.code_var.set(updated.code); view.remember_var.set(False)
        self.assertTrue(view.select_code())
        self.assertEqual(view.pairing, updated)
        self.assertEqual(view.load_remembered(record["address"]), self.pairing)
        self.assertEqual(len(self.book.entries()), 1)

    def test_vault_write_failure_never_creates_a_list_entry_or_selects_target(self):
        view = panel(self.path, self.book); view.code_var.set(self.pairing.code)
        with patch.object(mesh, "save_pairing_code", side_effect=mesh.MeshError("Disco indisponível")), patch("mesh_ui.messagebox.showerror"):
            self.assertFalse(view.select_code())
        view.on_select.assert_not_called()
        self.assertEqual(ComputerBook(self.book.path).entries(), [])
        self.assertIn("Não foi possível salvar", view.status_var.get())

    def test_silently_missing_vault_write_never_creates_a_saved_row(self):
        with patch.object(mesh, "save_pairing_code"):
            with self.assertRaises(OSError): self.save()
        self.assertEqual(ComputerBook(self.book.path).entries(), [])

    def test_registry_write_failure_removes_new_orphan_vault(self):
        with patch.object(self.book, "_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError): self.save()
        self.assertFalse((self.config / "remembered" / f"{self.pairing.public_id}.bin").exists())
        self.assertEqual(ComputerBook(self.book.path).entries(), [])

    def test_registry_write_failure_restores_existing_certificate_code(self):
        record = self.save(name="Casa")
        updated = mesh.HostPairing(self.pairing.token, self.pairing.port, "B" * 64)
        with patch.object(self.book, "_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError): self.save(updated, identifier=record["id"], name="Novo nome")
        self.assertEqual(panel(self.path).load_remembered(record["address"]), self.pairing)
        self.assertEqual(ComputerBook(self.book.path).get(record["id"])["name"], "Casa")

    def test_vault_failure_after_atomic_replace_also_restores_old_code(self):
        record = self.save(name="Casa")
        updated = mesh.HostPairing(self.pairing.token, self.pairing.port, "B" * 64)
        real_save = mesh.save_pairing_code
        def partially_written(path, code):
            real_save(path, code)
            raise mesh.MeshError("Falha ao confirmar proteção do arquivo")
        with patch.object(mesh, "save_pairing_code", side_effect=partially_written):
            with self.assertRaises(mesh.MeshError): self.save(updated, identifier=record["id"])
        self.assertEqual(panel(self.path).load_remembered(record["address"]), self.pairing)
        self.assertEqual(self.book.get(record["id"]), record)

    def test_registry_failure_after_atomic_replace_restores_both_original_files(self):
        record = self.save(name="Casa")
        old_registry = self.book.path.read_bytes()
        vault = self.config / "remembered" / f"{self.pairing.public_id}.bin"
        old_vault = vault.read_bytes()
        updated = mesh.HostPairing(self.pairing.token, self.pairing.port, "B" * 64)
        real_write = self.book._write
        def partially_written(records):
            real_write(records)
            raise OSError("Injected failure after registry replacement")
        with patch.object(self.book, "_write", side_effect=partially_written):
            with self.assertRaises(OSError): self.save(updated, identifier=record["id"], name="Changed")
        self.assertEqual(self.book.path.read_bytes(), old_registry)
        self.assertEqual(vault.read_bytes(), old_vault)
        self.assertEqual(ComputerBook(self.book.path).get(record["id"]), record)

    def test_first_registry_failure_after_replace_removes_both_new_files(self):
        real_write = self.book._write
        def partially_written(records):
            real_write(records)
            raise OSError("Injected failure after registry replacement")
        with patch.object(self.book, "_write", side_effect=partially_written):
            with self.assertRaises(OSError): self.save()
        self.assertFalse(self.book.path.exists())
        self.assertFalse((self.config / "remembered" / f"{self.pairing.public_id}.bin").exists())
        self.assertEqual(ComputerBook(self.book.path).entries(), [])

    def test_rotated_invitation_keeps_stable_id_name_and_history(self):
        record = self.save(name="Escritório")
        self.book.mark_seen(record["id"])
        before = self.book.get(record["id"])
        rotated = mesh.generate_pairing(port=64322, fingerprint="B" * 64)
        after = self.save(rotated, identifier=record["id"])
        for field in ("id", "name", "created_at", "last_seen"):
            self.assertEqual(after[field], before[field])
        self.assertEqual(after["address"], pairing_address(rotated))
        self.assertEqual(after["port"], 64322)
        self.assertEqual(len(ComputerBook(self.book.path).entries()), 1)
        self.assertEqual(panel(self.path).load_remembered(after["address"]), rotated)

    def test_two_fresh_book_instances_serialize_duplicate_save(self):
        failures = []; barrier = threading.Barrier(2)
        def save(name):
            try:
                book = ComputerBook(self.book.path); barrier.wait(2)
                book.remember_pairing(self.pairing, self.config, name=name)
            except BaseException as error: failures.append(type(error).__name__)
        threads = [threading.Thread(target=save, args=(name,)) for name in ("A", "B")]
        for thread in threads: thread.start()
        for thread in threads: thread.join(3)
        self.assertFalse(failures)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(ComputerBook(self.book.path).entries()), 1)
        self.assertEqual(panel(self.path).load_remembered(pairing_address(self.pairing)), self.pairing)

    def test_same_host_new_port_updates_one_saved_row_and_keeps_alias(self):
        record = self.save(name="Casa")
        changed = mesh.HostPairing(self.pairing.token, 64322, self.pairing.fingerprint)
        updated = self.save(changed)
        self.assertEqual(updated["id"], record["id"])
        self.assertEqual(updated["name"], "Casa")
        self.assertEqual(updated["port"], 64322)
        self.assertEqual(len(self.book.entries()), 1)

    def test_legacy_ambiguous_same_host_ports_require_an_explicit_saved_selection(self):
        address = pairing_address(self.pairing)
        self.book.add("A", address, 64321); self.book.add("B", address, 64322)
        with self.assertRaises(ValueError): self.save()
        self.assertEqual(len(self.book.entries()), 2)

    def test_update_cannot_overwrite_another_saved_computer_or_recreate_deleted_id(self):
        first = self.save(name="Primeiro")
        other = mesh.generate_pairing(fingerprint="B" * 64)
        self.save(other, name="Segundo")
        with self.assertRaises(ValueError): self.save(other, identifier=first["id"])
        self.book.remove(first["id"])
        with self.assertRaises(ValueError): self.save(identifier=first["id"])
        self.assertEqual(len(self.book.entries()), 1)

    def test_saved_selection_reloads_stale_row_and_prefers_new_vault_over_memory(self):
        old = self.save(name="Casa")
        updated = mesh.HostPairing(self.pairing.token, self.pairing.port, "B" * 64)
        app = prepared_app(self.path); app.mesh_pairing = self.pairing
        self.save(updated, identifier=old["id"])
        self.assertTrue(app._prepare_computer_connection(old))
        app.mesh_panel.select_pairing.assert_called_once_with(updated, remembered=True)
        rotated = mesh.generate_pairing(fingerprint="C" * 64)
        self.save(rotated, identifier=old["id"])
        self.assertTrue(app._prepare_computer_connection(old))
        app.mesh_panel.select_pairing.assert_called_with(rotated, remembered=True)

    def test_missing_vault_offers_update_without_deleting_the_saved_row(self):
        record = self.book.add("Casa", pairing_address(self.pairing), self.pairing.port)
        app = prepared_app(self.path)
        self.assertFalse(app._prepare_computer_connection(record))
        self.assertEqual(app._updating_computer_id, record["id"])
        self.assertTrue(app.mesh_panel.remember_var.get())
        self.assertEqual(self.book.get(record["id"]), record)
        app.mesh_panel.select_pairing.assert_not_called()

    def test_corrupt_vault_never_falls_back_to_stale_code_in_memory(self):
        record = self.save(name="Casa")
        path = self.config / "remembered" / f"{self.pairing.public_id}.bin"
        path.write_bytes(b"invalid vault")
        app = prepared_app(self.path); app.mesh_pairing = self.pairing
        self.assertFalse(app._prepare_computer_connection(record))
        app.mesh_panel.select_pairing.assert_not_called()
        self.assertIn("cofre", app.mesh_panel.status_var.get())
        self.assertEqual(self.book.get(record["id"]), record)

    def test_reconnect_uses_stable_saved_id_after_another_window_rotates_address(self):
        record = self.save(name="Casa")
        rotated = mesh.generate_pairing(fingerprint="B" * 64)
        fresh = self.save(rotated, identifier=record["id"])
        app = prepared_app(self.path); app._saved_computer_id = record["id"]
        app.peer = None; app.connecting = False; app.password_var.set("previous-memory-password")
        app._set_password_visible = Mock(); app._set_reconnect_available = Mock(); app._show_page = Mock()
        app._prepare_computer_connection = Mock(return_value=True)
        app._prepare_reconnect()
        app._prepare_computer_connection.assert_called_once_with(fresh)
        self.assertEqual(app.password_var.get(), "")

    def test_late_authentication_does_not_recreate_old_address_after_saved_code_rotation(self):
        record = self.save(name="Casa")
        app = prepared_app(self.path); app._saved_computer_id = record["id"]
        app.connecting_endpoint = (record["address"], record["port"])
        app._mark_computer_seen = Mock(); app._refresh_computer_rows = Mock(); app.computer_presence = {}
        rotated = mesh.generate_pairing(fingerprint="B" * 64)
        self.save(rotated, identifier=record["id"])
        app._remember_connected_computer(SimpleNamespace())
        self.assertEqual(len(self.book.entries()), 1)
        self.assertEqual(self.book.get(record["id"])["address"], pairing_address(rotated))

    def test_cancel_update_preserves_saved_code_and_clears_pending_replacement(self):
        record = self.save(name="Casa")
        app = prepared_app(self.path); app._updating_computer_id = record["id"]
        app.mesh_panel.code_var.set(mesh.generate_pairing(fingerprint="B" * 64).code)
        app._mesh_endpoint_busy = Mock(return_value=False); app._show_computers = Mock(); app._update_controls = Mock()
        app._cancel_connection_action()
        self.assertIsNone(app._updating_computer_id)
        self.assertEqual(app.mesh_panel.code_var.get(), "")
        self.assertEqual(self.book.get(record["id"]), record)
        self.assertEqual(panel(self.path).load_remembered(record["address"]), self.pairing)

    def test_changed_certificate_clears_memory_password_and_keeps_old_pin_for_confirmation(self):
        app = prepared_app(self.path); app.mesh_pairing = self.pairing
        app.stopping = threading.Event(); app._mesh_endpoint_busy = Mock(return_value=False)
        app.address_var.set(pairing_address(self.pairing)); app.password_var.set("temporary-password")
        app.mesh_panel.stop = Mock(); app._set_endpoint_details = Mock(); app._set_password_visible = Mock()
        app._update_address_hint = Mock(); app._show_page = Mock()
        app.connect_form = SimpleNamespace(canvas=Mock()); app.password_entry = Mock()
        updated = mesh.HostPairing(self.pairing.token, self.pairing.port, "B" * 64)
        self.assertTrue(app._select_mesh_pairing(updated))
        self.assertEqual(app.password_var.get(), "")
        app.mesh_panel.stop.assert_called_once()
        key = pin_key(pairing_address(updated), updated.port)
        app.settings = SimpleNamespace(data={"pins": {key: "a" * 64}})
        app.generation = 2; app._trust = Mock(return_value=False)
        self.assertFalse(app._trust_pairing(pairing_address(updated), updated.port, "B" * 64, 2, updated))
        app._trust.assert_called_once()
        self.assertEqual(app.settings.data["pins"][key], "a" * 64)

    def test_busy_or_cancelled_retry_cannot_replace_saved_code(self):
        app = prepared_app(self.path); app.stopping = threading.Event()
        app.retry_job = "pending"; app.peer = None; app.connecting = False
        app.book.remember_pairing = Mock()
        with patch.object(controller.messagebox, "showinfo"):
            self.assertFalse(app._save_selected_pairing(self.pairing))
        app.book.remember_pairing.assert_not_called()


if __name__ == "__main__": unittest.main()
