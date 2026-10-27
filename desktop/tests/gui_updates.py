#!/usr/bin/env python3
"""Real Tk update dialogs with local TLS and generated screens; no internet."""
from pathlib import Path
import argparse
import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import traceback
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageGrab
import tkinter as tk
import controller
import desktop_ui
import host
import net
from updates import UpdateResult

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--report", type=Path, required=True)
parser.add_argument("--screenshots", type=Path, required=True)
args = parser.parse_args()
checks, errors, pictures, messages, peers = [], [], [], [], []
owner = manager = server = host_app = None


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    checks.append(name)


def pump(seconds=.08):
    stop = time.monotonic() + seconds
    while time.monotonic() < stop:
        owner.update()
        time.sleep(.004)
    if errors:
        raise AssertionError(errors[-1])


def until(name, predicate, timeout=4):
    stop = time.monotonic() + timeout
    while time.monotonic() < stop:
        pump(.025)
        if predicate():
            checks.append(name)
            return
    raise AssertionError("Timed out: " + name)


def screen(frame_id):
    with Image.new("RGB", (1280, 720), "#eaf4ef") as image:
        output = io.BytesIO(); image.save(output, "JPEG", quality=76, subsampling=0)
    peers[-1].send({"type": "screen", "captureId": "fixture-updates", "monitorId": "all", "monitors": [],
        "width": 1280, "height": 720, "left": 0, "top": 0, "streamVersion": 1, "frameId": frame_id,
        "image": base64.b64encode(output.getvalue()).decode()})


def snapshot(name):
    args.screenshots.mkdir(parents=True, exist_ok=True)
    pump(.1)
    with ImageGrab.grab(xdisplay=os.environ["DISPLAY"]) as image:
        path = args.screenshots / (name + ".png")
        image.save(path); pictures.append(str(path))


try:
    with contextlib.ExitStack() as stack:
        state = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="aliancadx-updates-")))
        owner = tk.Tk(); owner.withdraw()
        owner.report_callback_exception = lambda *values: errors.append("".join(traceback.format_exception(*values)))
        def connected(peer, *_):
            peers.append(peer); peer.on_message = messages.append
        server = net.HostServer("127.0.0.1", 0, "fixture-update-password", lambda *_: True,
                                connected, lambda *_: None, state_dir=state / "tls")
        server.start(); stack.callback(server.stop)
        settings = controller.Settings(state / "controller.json")
        settings.data["pins"][controller.pin_key("127.0.0.1", server.port)] = server.fingerprint.lower()
        manager = controller.ControllerSessions(owner, settings=settings)
        app = manager.new_window()
        app._use_lan(); app.address_var.set("127.0.0.1"); app.port_var.set(str(server.port))
        app.password_var.set("fixture-update-password"); app.connect()
        until("TLS session authenticated", lambda: app.peer and not app.connecting and peers)
        screen(1); until("generated screen presented", lambda: app.current_image)
        app._show_page("internet"); pump()
        check("Controller update action is visible in Como usar", app.update_button.winfo_ismapped())
        entered, release = threading.Event(), threading.Event()
        browser = Mock(return_value=True)
        real_dialog = desktop_ui.UpdateDialog
        def blocked(installed, role):
            entered.set(); release.wait(3)
            return UpdateResult("available", installed, "7.5.0", "Uma nova versão está disponível.",
                "https://github.com/perabru/AliancaDX-AcessoRemoto/releases/download/v7.5.0/AliancaDX_Controlador_Setup.exe")
        def factory(parent, installed, role):
            return real_dialog(parent, installed, role, checker=blocked, browser_open=browser)
        app.buttons_down.add("left"); app.keys_down[37] = "ctrl"
        with patch.object(desktop_ui, "UpdateDialog", side_effect=factory):
            app.update_button.invoke(); dialog = app.update_dialog
            check("opening update dialog releases held remote inputs", not app.buttons_down and not app.keys_down)
            until("release reaches authenticated Host", lambda: any(item.get("action") == "release" for item in messages))
            check("update worker started", entered.wait(1))
            check("dialog is modeless", dialog.window.grab_current() is None)
            check("pending check does not expose installer action", not dialog.download_button.winfo_manager())
            ticks = []
            owner.after(20, lambda: ticks.append(True)); pump(.12)
            check("Tk heartbeat continues while lookup is blocked", ticks)
            screen(2); until("TLS frames continue during lookup", lambda: app.current_screen.get("frameId") == 2)
            dialog.close(); pump()
            check("closing pending lookup removes poll safely", dialog.disposed and dialog.poll_job is None)
            release.set(); pump(.15)
            check("late worker result produces no callback errors", not errors)
            app.update_button.invoke(); dialog = app.update_dialog
            until("retry after reopening presents available release", lambda: not dialog.busy)
        check("latest release and installed version are distinct", dialog.installed_version == "7.4.0" and dialog.latest_var.get() == "7.5.0")
        check("download action appears only for available release", dialog.download_button.winfo_ismapped())
        browser.assert_not_called(); checks.append("lookup never opens browser automatically")
        dialog.download_button.invoke(); browser.assert_called_once(); checks.append("explicit download action opens validated browser URL")
        dialog.close_button.focus_force(); pump()
        before = len([m for m in messages if m.get("type") == "input" and m.get("action") != "release"])
        dialog.close_button.event_generate("<KeyPress-a>", keycode=38)
        dialog.close_button.event_generate("<KeyRelease-a>", keycode=38)
        dialog.window.event_generate("<ButtonPress-1>", x=20, y=20)
        dialog.window.event_generate("<ButtonRelease-1>", x=20, y=20)
        pump()
        after = len([m for m in messages if m.get("type") == "input" and m.get("action") != "release"])
        check("dialog keys and clicks never reach remote PC", before == after)
        check("available dialog fits display", 0 <= dialog.window.winfo_rootx() and 0 <= dialog.window.winfo_rooty() and
            dialog.window.winfo_rootx()+dialog.window.winfo_width() <= owner.winfo_screenwidth() and
            dialog.window.winfo_rooty()+dialog.window.winfo_height() <= owner.winfo_screenheight())
        snapshot("windows-update-available")
        for status, title in (("current", "atualizado"), ("ahead", "mais recente"), ("offline", "Sem conexão"),
                              ("timeout", "demorou"), ("limited", "Limite"), ("error", "verificar")):
            dialog._show_result(UpdateResult(status, "7.4.0", "7.4.0" if status == "current" else None,
                                            "Confira sua conexão e tente novamente."))
            pump()
            check(status + " state is readable with retry and no download", title in dialog.title_var.get() and
                  not dialog.download_button.winfo_manager() and "disabled" not in dialog.retry_button.state())
        snapshot("windows-update-error")
        dialog.window.event_generate("<Escape>"); pump()
        check("Escape closes update dialog without disconnecting session", not dialog.is_open and app.peer and not app.peer.closed)

        with patch.dict(os.environ, {"LOCALAPPDATA": str(state)}), \
             patch.object(host.HostApp, "_poll_addresses"), patch.object(host.HostApp, "_capture_loop"), \
             patch.object(host.HostApp, "_start_available_while_open"):
            host_app = host.HostApp(tk.Toplevel(owner))
            host_app._navigate("Rede e registros"); pump()
            check("Host update action is visible in Ajuda e detalhes", host_app.update_button.winfo_ismapped())
            with patch.object(desktop_ui, "UpdateDialog", side_effect=lambda parent, installed, role:
                real_dialog(parent, installed, role, checker=lambda installed, role:
                    UpdateResult("current", installed, installed, "Você está usando a versão pública mais recente."), browser_open=browser)):
                host_app.update_button.invoke(); host_dialog = host_app.update_dialog
                until("Host update result presented", lambda: not host_dialog.busy)
            check("Host dialog uses Host role", host_dialog.role == "host")
            snapshot("windows-host-update-current")
            host_app.close(); host_app = None; pump()
            check("closing Host also disposes its update dialog", host_dialog.disposed)
        check("Controller TLS session remains active after all dialogs", app.peer and not app.peer.closed)
        check("no Tk callback exceptions", not errors)
        result = {"passed": True, "checks": checks, "checks_count": len(checks), "tk_errors": errors,
                  "screenshots": pictures, "tk_version": tk.TkVersion,
                  "scope": "Real Tk widgets and production localhost TLS with generated screens; injected update responses, no GitHub requests or browser launches.",
                  "limitations": ["Linux Tk display; no physical Windows installer verification."]}
except BaseException as failure:
    result = {"passed": False, "error": str(failure), "traceback": traceback.format_exc(), "checks": checks, "tk_errors": errors}
finally:
    if host_app:
        host_app.close()
    if manager:
        manager.close_all()
    if server:
        server.stop()
    if owner:
        try:
            owner.destroy()
        except tk.TclError:
            pass
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
if not result["passed"]:
    raise SystemExit(1)
