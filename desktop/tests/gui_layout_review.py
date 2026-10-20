"""Native Tk/Xvfb screenshot and layout review; isolated temporary user state.

This is a review helper, not an Android/Windows execution claim. It does not
initiate remote control or trust certificates, and counts no subchecks as tests.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("--scaling", type=float, default=1.333)
    parser.add_argument("--display", type=int, default=123)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    out = (args.output_dir or Path(__file__).resolve().parent) / ("screenshots-" + str(args.scaling).replace(".", "_"))
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(project))
    sys.path.insert(0, str(project.parent / "native_build" / "gui_deps"))
    display = f"127.0.0.1:{args.display}"
    xvfb = subprocess.Popen(["Xvfb", f":{args.display}", "-screen", "0", "1920x1080x24",
                            "-nolisten", "unix", "-nolisten", "local", "-listen", "tcp", "-ac"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    os.environ["DISPLAY"] = display
    root = app = None
    errors = []
    result = {"platform": "Linux/Xvfb", "scaling": args.scaling,
              "initiated_remote_sessions": 0, "screenshots": [], "geometry": [], "errors": errors}
    try:
        import tkinter as tk
        from tkinter import messagebox, font as tkfont, ttk
        from PIL import Image
        import mss
        deadline = time.monotonic() + 4
        while root is None:
            try:
                root = tk.Tk()
            except tk.TclError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Xvfb startup failed")
                time.sleep(.03)
        root.tk.call("tk", "scaling", args.scaling)
        from controller import Controller, Settings
        from host import HostApp, save_available_while_open
        messagebox.showerror = lambda *a, **kw: errors.append(str(a))
        messagebox.showwarning = lambda *a, **kw: errors.append(str(a))
        messagebox.askyesno = lambda *a, **kw: False
        def pump(seconds=.10):
            until = time.monotonic() + seconds
            while time.monotonic() < until:
                root.update()
                time.sleep(.01)
        def reveal(form, widgets):
            root.update_idletasks()
            top = min(widget.winfo_rooty() for widget in widgets) - form.content.winfo_rooty()
            bottom = max(widget.winfo_rooty() + widget.winfo_height() for widget in widgets) - form.content.winfo_rooty()
            if bottom - top > form.canvas.winfo_height():
                raise AssertionError("Critical group is taller than its scroll viewport")
            offset = top - max(0, (form.canvas.winfo_height() - (bottom - top)) / 2)
            form.canvas.yview_moveto(max(0, offset / max(1, form.content.winfo_height())))
            pump()
        def capture(name, critical=()):
            pump()
            origin = (root.winfo_rootx(), root.winfo_rooty())
            bounds = (origin[0], origin[1], root.winfo_width(), root.winfo_height())
            clipping = []
            for widget in critical:
                if not widget.winfo_ismapped():
                    clipping.append({"widget": str(widget), "reason": "not mapped"})
                    continue
                box = (widget.winfo_rootx(), widget.winfo_rooty(), widget.winfo_width(), widget.winfo_height())
                if box[0] < bounds[0] or box[1] < bounds[1] or box[0]+box[2] > bounds[0]+bounds[2] or box[1]+box[3] > bounds[1]+bounds[3]:
                    clipping.append({"widget": str(widget), "bounds": box})
                elif widget.winfo_class() == "TButton":
                    # TButton's default ten-character width is a preferred size,
                    # not a clipping threshold for short labels in a shared row.
                    font = ttk.Style(root).lookup(widget.cget("style") or "TButton", "font")
                    text_width = tkfont.Font(root=root, font=font).measure(widget.cget("text"))
                    if widget.winfo_width() < text_width + 8:
                        clipping.append({"widget": str(widget), "reason": "button text clipped", "width": widget.winfo_width(), "text_width": text_width})
                parent = widget.master
                while parent is not None and parent is not root:
                    if parent.winfo_class() == "Canvas":
                        view = (parent.winfo_rootx(), parent.winfo_rooty(), parent.winfo_width(), parent.winfo_height())
                        if box[0] < view[0] or box[1] < view[1] or box[0]+box[2] > view[0]+view[2] or box[1]+box[3] > view[1]+view[3]:
                            clipping.append({"widget": str(widget), "reason": "outside scroll viewport", "bounds": box, "viewport": view})
                    parent = parent.master
            target = out / (name + ".png")
            with mss.MSS(display=display) as grabber:
                shot = grabber.grab({"left": bounds[0], "top": bounds[1], "width": bounds[2], "height": bounds[3]})
                Image.frombytes("RGB", shot.size, shot.rgb).save(target)
            result["screenshots"].append(str(target))
            result["geometry"].append({"name": name, "window": bounds, "clipped_critical": clipping})
            print("SCREENSHOT", target)
            if clipping:
                print("CLIPPED", name, clipping)
            assert not clipping, (name, clipping)
        with tempfile.TemporaryDirectory(prefix="minianydesk32-ui-review-") as temp:
            folder = Path(temp)
            os.environ["LOCALAPPDATA"] = str(folder / "user")
            root.report_callback_exception = lambda kind, error, trace: errors.append(str(error))
            app = Controller(root, Settings(folder / "control.json"), probe=lambda *a, **kw: {"state": "offline", "detail": "QA local fixture"})
            root.geometry("1180x800+30+30")
            assert app.selected_page == "connect", "A new installation must show the guided connection first"
            pump()
            assert not app.mesh_panel.select_button.winfo_ismapped(), "The code stage must have one primary continuation action"
            capture("controller-first-use", (app.mesh_panel.code_entry, app.connect_button))
            app._show_page("computers")
            capture("controller-computers-empty", (app.add_computer_button, app.refresh_computers_button))
            for name, address, port in [("Servidor do laboratório", "laboratorio.example", 64321),
                                        ("Notebook da recepção", "recepcao.example", 64321),
                                        ("PC de desenvolvimento", "dev.example", 65000)]:
                app.book.add(name, address, port)
            app._refresh_computer_rows()
            capture("controller-computers", (app.add_computer_button, app.refresh_computers_button))
            root.geometry("820x560+30+30")
            capture("controller-computers-minimum", (app.add_computer_button, app.edit_computer_button,
                    app.delete_computer_button, app.refresh_computers_button, app.connect_computer_button))
            connection_page = getattr(app, "connection_page", None) or getattr(app, "connect_page", None)
            if connection_page is None:
                raise AssertionError("Controller connection_page/connect_page hook missing")
            app.workspace.select(connection_page)
            app._use_lan()
            reveal(app.connect_form, tuple(app.connection_entries[:2]))
            capture("controller-connect-minimum", tuple(app.connection_entries[:2]))
            reveal(app.connect_form, (app.password_entry, app.password_toggle_button))
            capture("controller-connect-password-minimum", (app.password_entry, app.password_toggle_button))
            app._toggle_connection_options()
            reveal(app.connect_form, (app.name_entry,))
            capture("controller-connect-name-minimum", (app.name_entry,))
            app.connect_form.canvas.yview_moveto(1.0)
            capture("controller-connect-minimum-scrolled", (app.connect_button, app.cancel_connect_button))
            app.connect_form.canvas.yview_moveto(0.0)
            root.geometry("1180x800+30+30")
            reveal(app.connect_form, tuple(app.connection_entries[:2]))
            capture("controller-connect", tuple(app.connection_entries[:2]) + (app.connect_button,))
            reveal(app.connect_form, (app.password_entry, app.password_toggle_button, app.name_entry))
            capture("controller-connect-options", (app.password_entry, app.password_toggle_button, app.name_entry, app.connect_button))
            app.workspace.select(app.remote_page)
            capture("controller-session")
            root.geometry("820x560+30+30")
            capture("controller-session-minimum")
            file_toggle = getattr(app, "files_toggle_button", None)
            if file_toggle is not None and str(file_toggle["state"]) != "disabled":
                file_toggle.invoke()
                capture("controller-session-files-minimum", (app.send_button, app.receive_button, app.cancel_button))
            control_toggle = getattr(app, "controls_toggle_button", None)
            if control_toggle is not None:
                control_toggle.invoke()
                capture("controller-session-controls-minimum", (app.monitor_combo, app.fit_button, app.pause_button, app.resume_button))
            app.close()
            app = root = None
            root = tk.Tk()
            root.tk.call("tk", "scaling", args.scaling)
            root.report_callback_exception = lambda kind, error, trace: errors.append(str(error))
            # This layout-only fixture keeps editing controls available. The
            # automatic-access journey separately verifies listener auto-start.
            save_available_while_open(folder / "user" / "MiniAnyDeskNativo" / "Host", False)
            app = HostApp(root)
            root.geometry("980x740+30+30")
            host_actions = (app.start_button, app.pause_button, app.stop_button, app.disconnect_button)
            capture("host-overview", host_actions)
            reveal(app.form, (app.password_entry, app.password_toggle_button, app.new_password_button))
            capture("host-password", host_actions + (app.password_entry, app.password_toggle_button, app.new_password_button))
            root.geometry("760x540+30+30")
            capture("host-overview-minimum", host_actions)
            positions = [(button.winfo_rootx(), button.winfo_rooty()) for button in host_actions]
            app.form.canvas.yview_moveto(1.0)
            pump()
            assert positions == [(button.winfo_rootx(), button.winfo_rooty()) for button in host_actions], "Host sharing actions must stay fixed while scrolling"
            capture("host-actions-minimum-scrolled", host_actions)
            app.nav_buttons["Meu computador"].invoke()
            capture("host-computer-name-minimum", host_actions + (app.save_name_button,))
            app.nav_buttons["Dados de conexão"].invoke()
            capture("host-connection-minimum", host_actions)
            if hasattr(app, "form"):
                reveal(app.form, (app.send_button, app.request_button, app.cancel_button))
                capture("host-overview-minimum-scrolled", (app.send_button, app.request_button, app.cancel_button))
                app.form.canvas.yview_moveto(0.0)
            if hasattr(app, "toggle_advanced"):
                app.toggle_advanced()
                root.geometry("980x740+30+30")
                if hasattr(app, "form"):
                    app.form.canvas.yview_moveto(1.0)
                capture("host-advanced")
            app.close()
            app = root = None
        if errors:
            raise AssertionError("Tk callback errors: " + repr(errors))
        (out / "layout-review.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
        print("PASS: native screenshot/layout review; no remote session initiated")
    finally:
        if app is not None:
            try: app.close()
            except Exception: pass
        elif root is not None:
            try: root.destroy()
            except Exception: pass
        if xvfb.poll() is None:
            xvfb.terminate()
            xvfb.wait(timeout=3)


if __name__ == "__main__":
    main()
