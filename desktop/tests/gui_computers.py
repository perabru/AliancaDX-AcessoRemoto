"""Actual Tk saved-computer UI smoke, with injected transport/presence.

Run directly on Linux with Xvfb installed. The test owns a TCP Xvfb child
in the same process namespace and uses only temporary settings/registries.
The separate gui_smoke.py validates the real Host/TLS/screen/file integration.
"""
import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
GUI_DEPS = PROJECT.parent / "native_build" / "gui_deps"
if GUI_DEPS.is_dir():
    sys.path.insert(0, str(GUI_DEPS))
from PIL import Image


def main():
    display = "127.0.0.1:113"
    xvfb = subprocess.Popen(["Xvfb", ":113", "-screen", "0", "1600x1000x24", "-nolisten", "unix",
        "-nolisten", "local", "-listen", "tcp", "-ac"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    os.environ["DISPLAY"] = display
    root = app = None
    try:
        import tkinter as tk
        from tkinter import messagebox
        deadline = time.monotonic() + 4
        while root is None:
            try:
                root = tk.Tk()
            except tk.TclError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Xvfb did not become available")
                threading.Event().wait(.03)
        from controller import Controller, Settings, pin_key
        from computer_book import ComputerBook
        import net
        import mss
        original_connect = net.connect
        with tempfile.TemporaryDirectory(prefix="minianydesk-computers-gui-") as directory:
            folder = Path(directory)
            errors, expected_errors, approvals, calls = [], [], [], []
            main_thread = threading.get_ident()
            root.report_callback_exception = lambda kind, error, traceback: errors.append(str(error))
            messagebox.showerror = lambda title, text, **kwargs: expected_errors.append((title, text))
            messagebox.showwarning = lambda *args, **kwargs: errors.append(str(args))
            def approve(title, text, **kwargs):
                approvals.append((title, text, kwargs.get("default")))
                return True
            messagebox.askyesno = approve
            availability = {"online.example": "online", "offline.example": "offline", "legacy.example": "unverified"}
            probe_threads = set()
            def probe(address, port, expected_fingerprint=None, timeout=3):
                probe_threads.add(threading.get_ident())
                calls.append((address, port, expected_fingerprint))
                return {"state": availability.get(address, "offline"), "detail": "Disponibilidade de teste",
                    "computer_name": "Nome anunciado", "fingerprint": "a" * 64, "busy": False, "version": "3.1.0"}
            settings = Settings(folder / "control.json")
            app = Controller(root, settings, probe=probe)
            root.geometry("1180x800+0+0")
            def wait(condition, reason, seconds=4):
                limit = time.monotonic() + seconds
                while time.monotonic() < limit:
                    root.update()
                    if condition(): return
                    threading.Event().wait(.01)
                raise AssertionError(reason + "; status=" + app.connection_var.get() + "; Tk errors=" + repr(errors))
            def select(entry):
                app.computers_tree.selection_set(entry["id"])
                root.update()
            def screenshot(name):
                destination = Path(os.environ.get("MINIANYDESK_GUI_OUTPUT", str(PROJECT.parent)))
                destination.mkdir(parents=True, exist_ok=True)
                target = destination / name
                root.update()
                with mss.MSS(display=display) as capture:
                    shot = capture.grab({"left": root.winfo_rootx(), "top": root.winfo_rooty(),
                        "width": root.winfo_width(), "height": root.winfo_height()})
                    Image.frombytes("RGB", shot.size, shot.rgb).save(target)
                print("SCREENSHOT: " + str(target))
            def add(name, address, port):
                app.add_computer_button.invoke(); root.update()
                dialog = app.computer_dialog
                for key, value in (("name", name), ("address", address), ("port", str(port))):
                    dialog.book_fields[key].set(value)
                dialog.save_button.invoke(); root.update()
                assert app.computer_dialog is None
                return app.book.find_endpoint(address, port)
            assert app.book.path == folder / "computers.json", "Injected settings must isolate the default registry"
            assert app.workspace.select() == str(app.internet_page), "First use must show automatic access"
            app._show_page("computers")
            assert app.workspace.select() == str(app.computers_page)
            app.add_computer_button.invoke(); root.update()
            dialog = app.computer_dialog
            dialog.book_fields["name"].set("Servidor da sala")
            dialog.book_fields["address"].set("online.example")
            dialog.book_fields["port"].set("invalid")
            dialog.save_button.invoke(); root.update()
            assert expected_errors and app.computer_dialog is dialog and not app.book.entries()
            dialog.book_fields["port"].set("64321"); dialog.save_button.invoke(); root.update()
            online = app.book.find_endpoint("online.example", 64321)
            offline = add("Notebook externo", "offline.example", 64322)
            legacy = add("Host antigo", "legacy.example", 64323)
            wait(lambda: all(entry["id"] in app.computer_presence for entry in (online, offline, legacy)), "Presence rows must update")
            assert app.computers_tree.item(online["id"], "tags") == ("online",)
            assert app.computers_tree.item(offline["id"], "tags") == ("offline",)
            assert app.computers_tree.item(legacy["id"], "tags") == ("unverified",)
            assert main_thread not in probe_threads and probe_threads
            assert not settings.data["pins"] and not approvals, "Polling must not trust a certificate or ask for login"
            assert app.book.find_endpoint("online.example", 64321)["last_seen"]
            select(online); app.search_var.set("Notebook externo"); root.update()
            assert app.computers_tree.get_children() == (offline["id"],), "Search must filter saved names"
            assert not app.computers_tree.selection(), "A filtered-out computer must not remain selected"
            app._refresh_computer_rows(); root.update()
            assert app.computers_tree.get_children() == (offline["id"],), "Presence updates must respect the current search"
            app.search_var.set("no-such-computer"); root.update()
            assert not app.computers_tree.get_children() and app.empty_state.winfo_ismapped()
            assert str(app.edit_computer_button["state"]) == "disabled"
            app.search_var.set(""); root.update()
            assert set(app.computers_tree.get_children()) == {online["id"], offline["id"], legacy["id"]}
            assert len(app.book.entries()) == 3, "Search must preserve the registry and cached status"
            select(online); app.edit_computer_button.invoke(); root.update()
            dialog = app.computer_dialog
            dialog.book_fields["name"].set("Sala renomeada")
            dialog.book_fields["address"].set("renamed.example")
            dialog.book_fields["port"].set("65000")
            dialog.save_button.invoke(); root.update()
            renamed = app.book.find_endpoint("renamed.example", 65000)
            assert renamed["id"] == online["id"] and renamed["name"] == "Sala renomeada"
            assert app.book.find_endpoint("online.example", 64321) is None
            reloaded = ComputerBook(folder / "computers.json")
            assert reloaded.find_endpoint("renamed.example", 65000)["name"] == "Sala renomeada"
            availability["offline.example"] = "online"
            app.refresh_computers_button.invoke()
            wait(lambda: app.computer_presence.get(offline["id"], {}).get("state") == "online", "Manual refresh must detect changed availability")
            availability["offline.example"] = "offline"
            app.refresh_computers_button.invoke()
            wait(lambda: app.computer_presence.get(offline["id"], {}).get("state") == "offline", "Manual refresh must detect an offline Host")
            select(legacy); app.delete_computer_button.invoke(); root.update()
            assert app.book.find_endpoint("legacy.example", 64323) is None
            assert not app.computers_tree.exists(legacy["id"])
            assert approvals[-1][0] == "Excluir computador" and approvals[-1][2] == "no"
            select(renamed); app.edit_computer_button.invoke(); root.update()
            dialog = app.computer_dialog
            saved_registry = app.book.path.read_bytes()
            app.book.path.write_bytes(b"{broken-json")
            app._refresh_computer_rows(); root.update()
            assert app.book is None and app.computer_dialog is dialog
            previous_errors = len(expected_errors)
            dialog.save_button.invoke(); root.update()
            assert len(expected_errors) > previous_errors and app.computer_dialog is dialog
            assert (folder / "computers.json").read_bytes() == b"{broken-json", "A failed edit must preserve the damaged registry"
            (folder / "computers.json").write_bytes(saved_registry)
            app.book = ComputerBook(folder / "computers.json"); app.book_error = ""
            dialog.save_button.invoke(); root.update()
            assert app.computer_dialog is None
            print("PASS: actual Tk add validation, persistent rename/endpoint edit/delete, online/offline/unverified rows and manual refresh")

            image = Image.new("RGB", (700, 253), "#2b6c8e")
            buffer = io.BytesIO(); image.save(buffer, "JPEG"); image.close()
            frame = {"type": "screen", "image": base64.b64encode(buffer.getvalue()).decode(),
                "width": 4480, "height": 1620, "left": -1920, "top": -180,
                "frameWidth": 700, "frameHeight": 253, "captureId": "gui-computers-frame", "monitorId": "all",
                "monitors": [{"id": "1", "left": 0, "top": 0, "width": 2560, "height": 1440}]}
            class Peer:
                def __init__(self, on_message, on_close):
                    self.closed = False; self.messages = []; self.computer_name = "Nome autenticado do Host"
                    self.on_message = on_message; self.on_close = on_close
                def send(self, message): self.messages.append(message)
                def close(self):
                    if self.closed: return
                    self.closed = True; self.on_close("Peer de teste encerrado")
            def connect(address, port, password, name, on_message, on_close, trust_callback,
                        *, cancel_event=None, deadline=None):
                assert password == "gui-local-password" and name == "Meu Controlador"
                assert cancel_event is not None and not cancel_event.is_set()
                assert trust_callback(address, port, "b" * 64)
                peer = Peer(on_message, on_close)
                on_message({"type": "state", "sharing": True, "localPaused": False, "computerName": peer.computer_name})
                on_message(frame)
                return peer
            net.connect = connect
            app.password_var.set("password-for-another-endpoint"); app.name_var.set("Meu Controlador")
            select(renamed)
            app._connect_computer()
            assert app.workspace.select() == str(app.connect_page)
            assert not app.peer and app.password_var.get() == "", "Selecting a different saved endpoint must not reuse a password or connect automatically"
            app.password_var.set("gui-local-password")
            app.connect_button.invoke()
            wait(lambda: app.peer and app.current_image, "Saved endpoint must connect through existing certificate approval")
            assert (app.address_var.get(), app.port_var.get()) == ("renamed.example", "65000")
            assert app.workspace.select() == str(app.remote_page)
            assert not app.computers_tree.winfo_ismapped(), "Computer list must not take remote viewer height"
            assert not app.files_panel.winfo_ismapped(), "The remote viewer starts with the file panel collapsed"
            app.files_toggle_button.invoke(); root.update()
            assert app.book.find_endpoint("renamed.example", 65000)["name"] == "Sala renomeada"
            assert settings.data["pins"][pin_key("renamed.example", 65000)] == "b" * 64
            assert any(title == "Conferir certificado do hospedeiro" and default == "no" for title, _, default in approvals)
            assert "password" not in json.loads(settings.path.read_text())
            before = len([call for call in calls if call[:2] == ("renamed.example", 65000)])
            app._refresh_computers(force=True); root.update()
            assert len([call for call in calls if call[:2] == ("renamed.example", 65000)]) == before
            for size in ("1180x800", "820x560"):
                root.geometry(size)
                wait(lambda: app.image_bounds[2] > 0 and app.image_bounds[0] + app.image_bounds[2] <= app.canvas.winfo_width()
                     and app.image_bounds[1] + app.image_bounds[3] <= app.canvas.winfo_height(), "Whole desktop must fit after resizing")
                root.update()
                assert app.canvas.itemcget(app.image_item, "state") == "normal"
                for button in (app.send_button, app.receive_button, app.cancel_button):
                    assert button.winfo_ismapped() and button.winfo_rooty() + button.winfo_height() <= root.winfo_rooty() + root.winfo_height(), "File actions must stay reachable at minimum height"
            app._release_input(); app.workspace.select(app.computers_page); root.update()
            assert app.peer.messages[-1] == {"type": "input", "action": "release"}
            for button in (app.add_computer_button, app.edit_computer_button, app.delete_computer_button,
                           app.refresh_computers_button, app.connect_computer_button):
                assert button.winfo_rootx() + button.winfo_width() <= root.winfo_rootx() + root.winfo_width(), "Computer actions must remain within the minimum window width"
                assert button.winfo_ismapped() and button.winfo_rooty() + button.winfo_height() <= root.winfo_rooty() + root.winfo_height(), "Computer actions must stay reachable at minimum height"
            screenshot("desktop-computers-minimum.png")
            app.disconnect(); wait(lambda: not app.peer, "Disconnect must close the session")
            app.address_var.set("new.example"); app.port_var.set("64321")
            assert app.password_var.get() == "", "Changing a manual endpoint must clear its previous password"
            app.password_var.set("gui-local-password"); app.connect()
            wait(lambda: app.peer and app.current_image, "Manual unknown endpoint must connect")
            auto = app.book.find_endpoint("new.example", 64321)
            assert auto and auto["name"] == "Nome autenticado do Host" and auto["last_seen"]
            root.geometry("1180x800")
            wait(lambda: app.canvas.winfo_width() > 800 and app.image_bounds[2] > 800, "Remote screenshot must contain the resized frame")
            screenshot("desktop-computers-remote.png")
            assert not errors, errors
            print("PASS: saved connection uses password/certificate approval, aliases survive authenticated names, remote tab keeps whole frame fitted, unknown connected Host auto-saves; zero Tk errors")
            net.connect = original_connect
            expected_names = {entry["id"]: entry["name"] for entry in app.book.entries()}
            expected_seen = {entry["id"]: entry["last_seen"] for entry in app.book.entries()}
            app.close(); app = root = None
            root = tk.Tk()
            root.report_callback_exception = lambda kind, error, traceback: errors.append(str(error))
            app = Controller(root, Settings(folder / "control.json"), probe=probe)
            root.update()
            assert app.workspace.select() == str(app.computers_page) and app.password_var.get() == ""
            assert {entry["id"]: entry["name"] for entry in app.computer_entries.values()} == expected_names
            for identity, last_seen in expected_seen.items():
                assert app.computer_entries[identity]["last_seen"] == last_seen
                assert app.computers_tree.item(identity, "values")[0] == expected_names[identity]
            assert not errors, errors
            print("PASS: restarting the actual Controller restores names/endpoints/last-seen rows with an empty password; corrupt registry edit surfaces an error and preserves bytes")
            app.close(); app = root = None
    finally:
        if app is not None:
            try: app.close()
            except Exception: pass
        elif root is not None:
            try: root.destroy()
            except Exception: pass
        xvfb.terminate()
        try: xvfb.wait(timeout=3)
        except subprocess.TimeoutExpired: xvfb.kill(); xvfb.wait()


if __name__ == "__main__":
    main()
