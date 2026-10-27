#!/usr/bin/env python3
"""Real Tk controls and localhost TLS peers; synthetic screens only."""
from pathlib import Path
import argparse
import base64
import contextlib
import hashlib
import io
import json
import os
import platform
import sys
import tempfile
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageGrab
import tkinter as tk
import controller
import net

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--display")
parser.add_argument("--report", type=Path)
parser.add_argument("--screenshots", type=Path)
args = parser.parse_args()
if args.display:
    os.environ["DISPLAY"] = args.display
checks, errors, records, servers, pictures = [], [], [], [], []
owner = manager = None


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    checks.append(name)


def pump(seconds=.06):
    stop = time.monotonic() + seconds
    while time.monotonic() < stop:
        owner.update()
        time.sleep(.004)
    if errors:
        raise AssertionError(errors[-1])


def until(name, predicate, timeout=6):
    stop = time.monotonic() + timeout
    while time.monotonic() < stop:
        pump(.025)
        if predicate():
            checks.append(name)
            return
    raise AssertionError("Timed out: " + name)


def screen(record, mode=None):
    record["frame"] += 1
    with Image.new("RGB", (1280, 720), "#eaf4ef") as image:
        draw = ImageDraw.Draw(image)
        draw.text((35, 35), "PC demonstrativo — preferências de imagem", fill="#14513b")
        for row in range(6):
            draw.rounded_rectangle((35 + row * 145, 110, 150 + row * 145, 320), 14, fill="#c2ddd0")
        output = io.BytesIO(); image.save(output, "JPEG", quality=76, subsampling=0)
    message = {"type": "screen", "captureId": "fixture-" + record["name"], "monitorId": "all", "monitors": [],
               "width": 1280, "height": 720, "left": 0, "top": 0, "streamVersion": 1,
               "frameId": record["frame"], "image": base64.b64encode(output.getvalue()).decode()}
    if mode is not None:
        message.update(streamSettingsVersion=1, streamMode=mode)
    record["peers"][-1].send(message)


def snapshot(app, name):
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
        pump(.1)
        image = ImageGrab.grab(xdisplay=os.environ.get("DISPLAY"))
        path = args.screenshots / (name + ".png")
        image.save(path); image.close(); pictures.append(str(path))


def mode_commands(record):
    return [m for m in record["messages"] if m.get("type") == "stream_settings"]


try:
    with contextlib.ExitStack() as stack:
        state = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="aliancadx-image-modes-")))
        owner = tk.Tk(); owner.withdraw()
        owner.report_callback_exception = lambda *values: errors.append("".join(traceback.format_exception(*values)))
        settings = controller.Settings(state / "controller.json")
        for index in range(2):
            record = {"name": chr(65 + index), "frame": 0, "messages": [], "peers": []}
            def connected(peer, name, address, record=record):
                record["peers"].append(peer)
                peer.on_message = record["messages"].append
            server = net.HostServer("127.0.0.1", 0, "fixture-mode-password", lambda *_: True,
                                    connected, lambda *_: None, state_dir=state / str(index))
            server.start(); stack.callback(server.stop)
            settings.data["pins"][controller.pin_key("127.0.0.1", server.port)] = server.fingerprint.lower()
            records.append(record); servers.append(server)
        manager = controller.ControllerSessions(owner, settings=settings)
        stack.callback(manager.close_all)
        a, b = manager.new_window(), manager.new_window()
        for app, server in zip((a, b), servers):
            app._use_lan(); app.address_var.set("127.0.0.1"); app.port_var.set(str(server.port))
            app.password_var.set("fixture-mode-password"); app.connect()
        until("both TLS sessions authenticated", lambda: a.peer and b.peer and not a.connecting and not b.connecting)
        screen(records[0], "auto"); screen(records[1])
        until("new and legacy Host frames presented", lambda: a.current_image and b.current_image)
        check("session gear buttons visible", a.image_settings_button.winfo_ismapped() and b.image_settings_button.winfo_ismapped())
        check("gear icons do not depend on font glyph support", bool(a.image_settings_button.cget("image")) and bool(a.fullscreen_image_settings.cget("image")))
        check("initial mode automatic", a.stream_mode_preference == b.stream_mode_preference == "auto")
        manager.focus_session(a.session_window_id); pump()
        a.buttons_down.add("left"); a.keys_down[37] = "ctrl"
        a.image_settings_button.invoke(); pump()
        check("opening gear releases remote input", not a.buttons_down and not a.keys_down and not a._input_ready())
        until("release reaches authenticated Host", lambda: any(m.get("action") == "release" for m in records[0]["messages"]))
        check("three native radio choices", [w.cget("value") for w in a.stream_mode_radio_buttons] == ["auto", "latency", "quality"])
        a.stream_mode_radio_buttons[2].invoke()
        until("quality request reaches only selected Host", lambda: mode_commands(records[0]))
        check("request does not fake applied mode", a.stream_mode_applied == "auto" and "Aplicando" in a.stream_mode_status_var.get())
        check("pending confirmation text fits popover", a.stream_settings_popup.winfo_height() >= a.stream_settings_popup.winfo_reqheight())
        check("other window retains automatic preference", b.stream_mode_preference == "auto" and not mode_commands(records[1]))
        screen(records[0], "quality")
        until("applied quality confirmed by displayed frame", lambda: a.stream_mode_applied == "quality")
        popup = a.stream_settings_popup
        check("popover adapts after status changes", popup.winfo_height() >= popup.winfo_reqheight())
        check("popover lies within screen", 0 <= popup.winfo_rootx() and 0 <= popup.winfo_rooty() and popup.winfo_rootx() + popup.winfo_width() <= a.root.winfo_screenwidth() and popup.winfo_rooty() + popup.winfo_height() <= a.root.winfo_screenheight())
        snapshot(a, "quality-confirmed")
        popup.event_generate("<Escape>"); pump()
        check("Escape closes popover", a.stream_settings_popup is None)
        a._toggle_stream_settings(a.image_settings_button); pump()
        popup = a.stream_settings_popup
        popup.event_generate("<ButtonPress-1>", x=-8, y=-8, rootx=popup.winfo_rootx()-8, rooty=popup.winfo_rooty()-8)
        pump(); check("outside click dismisses without controlling PC", a.stream_settings_popup is None)
        a._toggle_fullscreen(True); pump()
        check("fullscreen gear visible", a.fullscreen_image_settings.winfo_ismapped())
        a.fullscreen_image_settings.invoke(); pump()
        snapshot(a, "fullscreen-quality-menu")
        a.stream_settings_popup.event_generate("<Escape>"); pump()
        check("Escape closes menu before exiting fullscreen", a.fullscreen and a.stream_settings_popup is None)
        a._escape(); pump()
        manager.focus_session(b.session_window_id); pump()
        b.image_settings_button.invoke(); pump()
        b.stream_mode_radio_buttons[1].invoke(); pump()
        check("legacy Host is visibly unsupported", "Atualize o Hospedeiro" in b.stream_mode_status_var.get())
        check("legacy Host remains automatic without settings commands", b.stream_mode_applied == "auto" and not mode_commands(records[1]) and b.peer and not b.peer.closed)
        snapshot(b, "legacy-host-message")
        b._close_stream_settings(); pump()
        check("saved preference does not leak to open session", a.stream_mode_preference == "quality" and b.stream_mode_preference == "latency")
        loaded = controller.Settings(settings.path)
        check("last preference survives settings reload", loaded.data["stream_mode"] == "latency")
        a.disconnect(); pump()
        check("disconnect clears applied capability only", a.stream_mode_preference == "quality" and a.stream_mode_applied is None and a.stream_settings_generation is None)
        a._use_lan(); a.address_var.set("127.0.0.1"); a.port_var.set(str(servers[0].port)); a.password_var.set("fixture-mode-password"); a.connect()
        until("session reconnects with fresh TLS authorization", lambda: a.peer and not a.connecting and len(records[0]["peers"]) == 2)
        request_count = len(mode_commands(records[0]))
        check("reconnect does not replay settings before capability", a.stream_mode_last_sent is None)
        screen(records[0], "auto")
        until("only local preference reapplied after new capability", lambda: len(mode_commands(records[0])) == request_count + 1)
        check("reapplied request is quality", mode_commands(records[0])[-1]["mode"] == "quality")
        screen(records[0], "quality"); until("reconnected applied mode confirmed", lambda: a.stream_mode_applied == "quality")
        check("other session remains alive", b.peer and not b.peer.closed and b.stream_mode_applied == "auto")
        check("no Tk callback exceptions", not errors)
        result = {"passed": True, "checks": checks, "checks_count": len(checks), "screenshots": pictures,
                  "scope": "Real Tk controls, ImageTk presentation, and authenticated localhost production TLS peers; generated screens and temporary identities only.",
                  "limitations": ["No physical Windows installation or real PC capture.", "The fixture confirms UI protocol behavior, not quality/latency performance of Host profiles."], "tk_errors": errors}
except BaseException as error:
    result = {"passed": False, "checks": checks, "checks_count": len(checks), "error": str(error),
              "traceback": traceback.format_exc(), "tk_errors": errors}
finally:
    try:
        if manager:
            manager.close_all()
    except BaseException:
        pass
    for server in servers:
        server.stop()
    result["tk_version"] = tk.TkVersion
    result["operating_system"] = platform.system()
    result["controller_sha256"] = hashlib.sha256((Path(__file__).resolve().parents[1] / "controller.py").read_bytes()).hexdigest()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
if not result["passed"]:
    raise SystemExit(1)
