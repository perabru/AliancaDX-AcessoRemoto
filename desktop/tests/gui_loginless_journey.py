"""Real native Tk/TLS journey with an explicitly simulated overlay endpoint.

This checks application integration, not NAT traversal or a public relay. The
separate transport test must validate EasyTier across isolated networks.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--display", type=int, default=177)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    display = f"127.0.0.1:{args.display}"
    xvfb = subprocess.Popen(["Xvfb", f":{args.display}", "-screen", "0", "1920x1080x24", "-nolisten", "unix", "-nolisten", "local", "-listen", "tcp", "-ac"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    os.environ["DISPLAY"] = display
    root = host_app = control = None
    report = {"platform": "Linux Tk/Xvfb", "real_windows_installer": False, "real_public_relay": False,
              "transport": "real localhost TLS; simulated MeshManager endpoint", "checks": [], "screenshots": [], "errors": []}
    try:
        import tkinter as tk
        from tkinter import messagebox
        import mss
        from PIL import Image
        import controller
        import host
        import mesh
        from mesh_ui import pairing_address

        deadline = time.monotonic() + 4
        while root is None:
            try:
                root = tk.Tk()
            except tk.TclError:
                if time.monotonic() >= deadline:
                    raise
                threading.Event().wait(.02)
        root.report_callback_exception = lambda kind, error, trace: report["errors"].append(str(error))
        approvals = []
        def approve(title, text, **kwargs):
            approvals.append((title, kwargs.get("default")))
            return True

        class Manager:
            def __init__(self, role, config_dir, runtime_dir=None, on_status=None):
                self.role, self.on_status = role, on_status
                self.status = mesh.MeshStatus()
                self.starts, self.stops = 0, 0
            def snapshot(self):
                return self.status
            def start(self, pairing, port=None):
                self.starts += 1
                self.status = mesh.MeshStatus(state="ready" if self.role == "controller" else "connecting",
                    message="Pronto para conferir a senha e pedir aprovação." if self.role == "controller" else "Rede privada iniciada; aguardando Controlador.",
                    endpoint=("127.0.0.1", pairing.port) if self.role == "controller" else None)
                if self.on_status:
                    self.on_status(self.status)
            def wait_ready(self, timeout=45, cancel_event=None):
                if cancel_event and cancel_event.is_set():
                    raise mesh.MeshError("Conexão cancelada.")
                return self.status.endpoint
            def stop(self):
                self.stops += 1
                self.status = mesh.MeshStatus()
                if self.on_status:
                    self.on_status(self.status)
            close = stop

        def pump(predicate, label, timeout=12):
            until = time.monotonic() + timeout
            while time.monotonic() < until:
                root.update()
                if report["errors"]:
                    raise AssertionError(report["errors"])
                if predicate():
                    report["checks"].append(label)
                    return
                threading.Event().wait(.01)
            raise AssertionError(label + "; " + (control.connection_var.get() if control else ""))

        def capture(window, name):
            window.lift()
            for _ in range(8):
                root.update()
                threading.Event().wait(.02)
            target = args.output_dir / name
            with mss.MSS(display=display) as grabber:
                shot = grabber.grab({"left": window.winfo_rootx(), "top": window.winfo_rooty(), "width": window.winfo_width(), "height": window.winfo_height()})
                Image.frombytes("RGB", shot.size, shot.rgb).save(target)
            report["screenshots"].append(str(target))

        with tempfile.TemporaryDirectory(prefix="minianydesk-no-login-gui-") as directory, patch.dict(os.environ, {"LOCALAPPDATA": directory}), patch.object(mesh, "MeshManager", Manager), patch.object(messagebox, "askyesno", approve), patch.object(messagebox, "showerror", lambda title, text, **kwargs: report["errors"].append(f"{title}: {text}")), patch.object(messagebox, "showwarning", lambda title, text, **kwargs: report["errors"].append(f"{title}: {text}")):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            host_app = host.HostApp(root)
            host_app.port.set(str(port))
            host_app.computer_name.set("Computador do laboratório")
            host_app.save_computer_name()
            pump(lambda: host_app.server is not None and host_app.mesh_panel.pairing is not None, "Host opens with TLS and account-free network preparation")
            pair = host_app.mesh_panel.pairing
            assert pair.fingerprint == host_app.server.fingerprint
            assert host_app.mesh_panel.code_entry.cget("show") == "*"
            host_app._navigate("Internet sem login")
            capture(root, "host-internet.png")
            control_window = tk.Toplevel(root)
            control_window.report_callback_exception = root.report_callback_exception
            control = controller.Controller(control_window, controller.Settings(Path(directory) / "controller" / "control.json"))
            assert control.selected_page == "connect"
            assert not control.mesh_panel.remember_var.get()
            host_app.copy_connection()
            invitation = root.clipboard_get()
            assert invitation.startswith("MiniAnyDesk Host 6.1.0")
            assert "Código privado: " + pair.code in invitation
            assert control.mesh_panel.paste_code()
            assert control.mesh_panel.code_var.get() == pair.code
            assert control.password_var.get() == ""
            assert control._connect_primary()
            assert control.selected_page == "connect"
            assert control.connection_card.winfo_manager()
            report["checks"].append("Copy invitation -> Paste extracts code only -> Continue reveals password step")
            assert control.address_var.get() == pairing_address(pair)
            control.password_var.set(host_app.password.get())
            control.name_var.set("Bruno")
            capture(control_window, "controller-connect.png")
            control.connect()
            pump(lambda: control.peer is not None and host_app.peer is not None, "Private code binds Host TLS; password and local consent authorize session")
            pump(lambda: control.current_image is not None, "Real Host desktop frame displayed through existing TLS protocol")
            assert approvals == [("Autorizar acesso remoto?", "no")]
            key = controller.pin_key(pairing_address(pair), pair.port)
            assert control.settings.data["pins"][key] == pair.fingerprint.lower()
            assert "127.0.0.1" not in control.settings.path.read_text()
            assert pair.code not in control.settings.path.read_text()
            assert not (control.mesh_panel.config_dir / "remembered").exists()
            entries = control.book.entries()
            assert len(entries) == 1 and entries[0]["address"] == pairing_address(pair)
            control.book.update(entries[0]["id"], name="Meu computador remoto")
            control._refresh_computer_rows()
            assert control.book.entries()[0]["name"] == "Meu computador remoto"
            report["checks"].append("Pins and editable computer names use stable public Host identity; secret not persisted by default")
            capture(control_window, "controller-session.png")
            control.disconnect()
            pump(lambda: host_app.peer is None, "Controller End terminates TLS and its owned network")
            control.mesh_panel.code_var.set(pair.code)
            control.mesh_panel.remember_var.set(True)
            assert control.mesh_panel.select_code()
            assert control.mesh_panel.load_remembered(pairing_address(pair)) == pair
            report["checks"].append("Explicit remember option roundtrips through protected local code storage")
            before = host_app.mesh_panel.manager.starts
            host_app.stop_listener()
            for _ in range(30):
                root.update()
                threading.Event().wait(.01)
            host_app.mesh_panel.prepare_access()
            assert host_app.server is None and host_app.mesh_panel.manager.starts == before
            assert not host_app.available_while_open.get()
            report["checks"].append("Host End stops owned network and status updates do not restart it")
            host_app.start_listener()
            pump(lambda: host_app.server is not None and not host_app.starting, "Host can deliberately restart")
            host_app.mesh_panel.renew_code()
            assert host_app.mesh_panel.pairing.public_id != pair.public_id
            assert approvals[-1] == ("Revogar código privado", "no")
            report["checks"].append("Explicit local revocation creates another private network identity")
            control.close()
            control = None
            host_app.close()
            host_app = root = None
        report["result"] = "PASS"
    except Exception as error:
        report["result"] = "FAIL"
        report["errors"].append(str(error))
        raise
    finally:
        if control is not None:
            control.close()
        if host_app is not None:
            host_app.close()
        elif root is not None:
            root.destroy()
        xvfb.terminate()
        try:
            xvfb.wait(timeout=3)
        except subprocess.TimeoutExpired:
            xvfb.kill()
        (args.output_dir / "loginless-journey.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"result": report.get("result"), "checks": len(report["checks"]), "errors": report["errors"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
