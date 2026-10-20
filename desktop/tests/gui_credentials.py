"""Actual Tk credential controls, clipboard, and eight-digit localhost TLS session.

Runs under an isolated Xvfb server with temporary application state. Screenshots
are QA artifacts saved only to the explicitly supplied output directory.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
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
    parser.add_argument("--display", type=int, default=125)
    parser.add_argument("--scaling", type=float, default=1.333)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    display = f"127.0.0.1:{args.display}"
    xvfb = subprocess.Popen(["Xvfb", f":{args.display}", "-screen", "0", "1920x1080x24",
        "-nolisten", "unix", "-nolisten", "local", "-listen", "tcp", "-ac"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    os.environ["DISPLAY"] = display
    root = app = peer = None
    errors = []
    approvals = []
    report = {"platform": "Linux/Xvfb", "scaling": args.scaling, "screenshots": [],
              "geometry": [], "checks": [], "errors": errors}
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk, font as tkfont
        from PIL import Image
        import mss
        import net
        import host
        from controller import Controller, Settings

        def new_root():
            deadline = time.monotonic() + 4
            while True:
                try:
                    value = tk.Tk()
                    value.tk.call("tk", "scaling", args.scaling)
                    value.report_callback_exception = lambda kind, error, trace: errors.append(str(error))
                    return value
                except tk.TclError:
                    if time.monotonic() >= deadline: raise
                    time.sleep(.03)

        def pump(seconds=.12):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                root.update()
                time.sleep(.01)

        def until(predicate, timeout=5):
            deadline = time.monotonic() + timeout
            while not predicate():
                if time.monotonic() >= deadline: raise AssertionError("UI condition timed out")
                pump(.03)

        def scroll_into_view(form, widgets):
            pump()
            top = min(widget.winfo_rooty() - form.content.winfo_rooty() for widget in widgets)
            bottom = max(widget.winfo_rooty() - form.content.winfo_rooty() + widget.winfo_height() for widget in widgets)
            height = form.canvas.winfo_height()
            assert bottom - top <= height, "Credential controls must fit together in the scroll viewport"
            total = form.canvas.bbox("all")[3]
            form.canvas.yview_moveto(max(0, (top - (height - (bottom - top)) / 2) / total))
            pump()

        def capture(name, critical=()):
            pump()
            box = (root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height())
            clipped = []
            for widget in critical:
                if not widget.winfo_ismapped():
                    clipped.append({"widget": str(widget), "reason": "not mapped"})
                    continue
                bounds = (widget.winfo_rootx(), widget.winfo_rooty(), widget.winfo_width(), widget.winfo_height())
                if bounds[0] < box[0] or bounds[1] < box[1] or bounds[0]+bounds[2] > box[0]+box[2] or bounds[1]+bounds[3] > box[1]+box[3]:
                    clipped.append({"widget": str(widget), "bounds": bounds})
                elif widget.winfo_class() == "TButton":
                    font = ttk.Style(root).lookup(widget.cget("style") or "TButton", "font")
                    width = tkfont.Font(root=root, font=font).measure(widget.cget("text"))
                    if bounds[2] < width + 8:
                        clipped.append({"widget": str(widget), "reason": "button text clipped"})
                parent = widget.master
                while parent is not None and parent is not root:
                    if parent.winfo_class() == "Canvas":
                        view = (parent.winfo_rootx(), parent.winfo_rooty(), parent.winfo_width(), parent.winfo_height())
                        if bounds[0] < view[0] or bounds[1] < view[1] or bounds[0]+bounds[2] > view[0]+view[2] or bounds[1]+bounds[3] > view[1]+view[3]:
                            clipped.append({"widget": str(widget), "reason": "outside scroll viewport"})
                    parent = parent.master
            path = args.output_dir / f"{name}.png"
            with mss.MSS(display=display) as grabber:
                shot = grabber.grab({"left": box[0], "top": box[1], "width": box[2], "height": box[3]})
                Image.frombytes("RGB", shot.size, shot.rgb).save(path)
            report["screenshots"].append(str(path))
            report["geometry"].append({"name": name, "window": box, "clipped_critical": clipped})
            assert not clipped, (name, clipped)

        messagebox.showerror = lambda *a, **kw: errors.append(str(a))
        messagebox.showwarning = lambda *a, **kw: errors.append(str(a))
        messagebox.askyesno = lambda title, text, **kw: approvals.append(title) or True
        with tempfile.TemporaryDirectory(prefix="minianydesk-password-qa-") as folder:
            os.environ["LOCALAPPDATA"] = folder
            # This fixture edits the code before manually starting TLS. The
            # automatic-access journey verifies the default available mode.
            host.save_available_while_open(Path(folder) / "MiniAnyDeskNativo" / "Host", False)
            root = new_root()
            app = host.HostApp(root)
            root.geometry("980x740+30+30")
            assert len(app.password.get()) == 8 and all(ch in "0123456789" for ch in app.password.get())
            assert app.password_entry.cget("show") == "*" and not app.show_password.get()
            app.nav_buttons["Dados de conexão"].invoke()
            scroll_into_view(app.form, (app.password_entry, app.password_toggle_button, app.new_password_button))
            capture("host-senha-oculta", (app.password_entry, app.password_toggle_button, app.new_password_button))
            app.password_toggle_button.invoke()
            assert app.show_password.get() and app.password_entry.cget("show") == ""
            with patch.object(host, "generate_session_password", return_value="00001234"):
                app.new_password_button.invoke()
            assert app.password.get() == "00001234" and app.password_entry.cget("show") == "*"
            app.password_toggle_button.invoke()
            scroll_into_view(app.form, (app.password_entry, app.password_toggle_button, app.new_password_button))
            capture("host-senha-visivel", (app.password_entry, app.password_toggle_button, app.new_password_button))
            app.copy_password()
            assert root.clipboard_get() == "00001234"
            app.copy_connection()
            assert "Senha: 00001234\n" in root.clipboard_get()
            report["checks"].append("host generation, visibility, renewal and exact clipboard")

            with socket.socket() as candidate:
                candidate.bind(("127.0.0.1", 0))
                port = candidate.getsockname()[1]
            app.port.set(str(port))
            app.start_listener()
            until(lambda: app.server is not None and not app.starting)
            assert str(app.password_entry.cget("state")) == "disabled"
            assert str(app.new_password_button.cget("state")) == "disabled"
            app.new_password_button.invoke()
            assert app.password.get() == "00001234"
            result = queue.Queue()
            def connect():
                try:
                    result.put(net.connect("127.0.0.1", port, "00001234", "QA de senha temporária",
                        lambda msg: None, lambda reason: None,
                        lambda address, remote_port, pin: pin == app.server.fingerprint))
                except Exception as error:
                    result.put(error)
            threading.Thread(target=connect, daemon=True).start()
            until(lambda: not result.empty())
            peer = result.get()
            if isinstance(peer, Exception): raise peer
            assert approvals and app.peer is not None
            peer.close(); peer = None
            until(lambda: app.peer is None)
            app.stop_listener()
            assert not app.show_password.get() and app.password_entry.cget("show") == "*"
            assert str(app.password_entry.cget("state")) == "normal"
            pump()
            assert "00001234" not in app.log_box.get("1.0", "end")
            report["checks"].append("eight-digit TLS login, certificate match, owner consent and immutable active credentials")
            root.geometry("760x540+30+30")
            sharing_buttons = (app.start_button, app.pause_button, app.stop_button, app.disconnect_button)
            scroll_into_view(app.form, (app.password_entry, app.password_toggle_button, app.new_password_button))
            capture("host-conexao-minima", (app.password_entry, app.password_toggle_button, app.new_password_button) + sharing_buttons)
            positions = [(button.winfo_rootx(), button.winfo_rooty()) for button in sharing_buttons]
            app.form.canvas.yview_moveto(1.0)
            pump()
            assert positions == [(button.winfo_rootx(), button.winfo_rooty()) for button in sharing_buttons]
            capture("host-conexao-minima-rolada", sharing_buttons)
            report["checks"].append("host sharing actions remain visible and stationary at minimum size while scrolling")
            # The application closes timers through their owning Tk widgets,
            # including the VPN panel, before destroying this interpreter.
            app.close(); app = root = None

            root = new_root()
            app = Controller(root, Settings(Path(folder) / "controller.json"),
                probe=lambda *a, **kw: {"state": "offline", "detail": "QA local"})
            app._show_page("connect")
            app._use_lan()
            root.geometry("1180x800+30+30")
            app.address_var.set("qa.example")
            app.password_var.set("00001234")
            assert not app.password_visible and app.password_entry.cget("show") == "*"
            scroll_into_view(app.connect_form, (app.password_entry, app.password_toggle_button))
            capture("controlador-senha-oculta", (app.password_entry, app.password_toggle_button, app.connect_button))
            app.password_toggle_button.invoke()
            assert app.password_visible and app.password_entry.cget("show") == ""
            scroll_into_view(app.connect_form, (app.password_entry, app.password_toggle_button))
            capture("controlador-senha-visivel", (app.password_entry, app.password_toggle_button, app.connect_button))
            app.address_var.set("other.example")
            assert app.password_var.get() == "" and not app.password_visible
            app.password_var.set("00001234")
            app.password_toggle_button.invoke()
            app._show_page("computers")
            assert not app.password_visible and app.password_entry.cget("show") == "*"
            app._show_page("connect")
            assert app.password_var.get() == "00001234"
            app.password_toggle_button.invoke()
            app._connection_closed("QA de encerramento")
            assert app.password_var.get() == "" and not app.password_visible
            app._show_page("connect")
            root.geometry("820x560+30+30")
            scroll_into_view(app.connect_form, (app.password_entry, app.password_toggle_button))
            connect_buttons = (app.connect_button, app.cancel_connect_button)
            capture("controlador-conexao-minima", (app.password_entry, app.password_toggle_button) + connect_buttons)
            positions = [(button.winfo_rootx(), button.winfo_rooty()) for button in connect_buttons]
            app.connect_form.canvas.yview_moveto(1.0)
            pump()
            assert positions == [(button.winfo_rootx(), button.winfo_rooty()) for button in connect_buttons]
            capture("controlador-conexao-minima-rolada", connect_buttons)
            report["checks"].append("controller toggle, destination change, page exit, disconnect and minimum geometry")
            app.settings.save()
            assert "password" not in json.loads(app.settings.path.read_text())
            app.close(); app = root = None
        assert not errors, errors
        (args.output_dir / "credentials-review.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print("PASS: actual Tk credential controls/clipboard, eight-digit TLS and owner consent; no clipped critical widgets or Tk errors")
    finally:
        if peer is not None:
            try: peer.close()
            except Exception: pass
        if app is not None:
            try: app.close()
            except Exception: pass
        elif root is not None:
            try: root.destroy()
            except Exception: pass
        xvfb.terminate()
        xvfb.wait(timeout=3)


if __name__ == "__main__":
    main()
