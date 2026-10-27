#!/usr/bin/env python3
"""Real Tk/TLS check for 4K received pixels, fit, native pan and remote input."""
from pathlib import Path
import argparse
import base64
import io
import json
import os
import sys
import tempfile
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFont, ImageGrab
import tkinter as tk
import controller
import net

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--report", type=Path, required=True)
parser.add_argument("--screenshots", type=Path, required=True)
args = parser.parse_args()
checks, errors, pictures, messages, peers = [], [], [], [], []
owner = manager = server = None


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    checks.append(name)


def pump(seconds=.08):
    stop = time.monotonic() + seconds
    while time.monotonic() < stop:
        owner.update(); time.sleep(.004)
    if errors:
        raise AssertionError(errors[-1])


def until(name, predicate, timeout=5):
    stop = time.monotonic() + timeout
    while time.monotonic() < stop:
        pump(.03)
        if predicate():
            checks.append(name); return
    raise AssertionError("Timed out: " + name)


def snapshot(name):
    args.screenshots.mkdir(parents=True, exist_ok=True)
    pump(.1)
    with ImageGrab.grab(xdisplay=os.environ["DISPLAY"]) as image:
        path = args.screenshots / (name + ".png")
        image.save(path); pictures.append(str(path))


try:
    with tempfile.TemporaryDirectory(prefix="aliancadx-native-view-") as folder:
        state = Path(folder)
        owner = tk.Tk(); owner.withdraw()
        owner.report_callback_exception = lambda *values: errors.append("".join(traceback.format_exception(*values)))
        def connected(peer, *_):
            peers.append(peer); peer.on_message = messages.append
        server = net.HostServer("127.0.0.1", 0, "fixture-native-password", lambda *_: True,
                                connected, lambda *_: None, state_dir=state / "tls")
        server.start()
        settings = controller.Settings(state / "controller.json")
        settings.data["pins"][controller.pin_key("127.0.0.1", server.port)] = server.fingerprint.lower()
        manager = controller.ControllerSessions(owner, settings=settings)
        app, other = manager.new_window(), manager.new_window()
        manager.focus_session(app.session_window_id)
        app._use_lan(); app.address_var.set("127.0.0.1"); app.port_var.set(str(server.port))
        app.password_var.set("fixture-native-password"); app.connect()
        until("localhost TLS authenticated", lambda: app.peer and not app.connecting and peers)
        image = Image.new("RGB", (3840, 2160), "#f4f8f5")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
        title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
        for x in (24, 2584):
            for y in (24, 1464):
                draw.rounded_rectangle((x-8, y-8, x+1218, y+660), radius=14, fill="white", outline="#d8e8df", width=2)
                draw.text((x+12, y+12), "AliançaDX | Texto e detalhes em 4K", font=title, fill="#073b2b")
                draw.text((x+12, y+58), "Fonte de 13 px recebida: letras, números, planilhas e código.", font=font, fill="#007a50")
                for row in range(24):
                    draw.text((x+12, y+92+row*21), f"{row+1:02d}  Cliente {row+101}   R$ 1.234,56   0123456789   ABCDEFG abcdefg  |  Acesso autorizado", font=font,
                              fill=("#162d23", "#0064a6", "#92346a")[row % 3])
        output = io.BytesIO(); image.save(output, "JPEG", quality=94, subsampling=0)
        image.close()
        payload = output.getvalue()
        with Image.open(io.BytesIO(payload)) as received:
            received.load(); received_pixels = received.convert("RGB").tobytes()
        frame = {"type": "screen", "captureId": "fixture-native", "monitorId": "all", "monitors": [],
            "width": 3840, "height": 2160, "left": -1920, "top": 0,
            "streamVersion": 1, "frameId": 1, "streamSettingsVersion": 1, "streamMode": "quality",
            "image": base64.b64encode(payload).decode()}
        check("synthetic 4K frame respects 2 MiB wire budget", len(json.dumps(frame).encode()) <= 2 * 1024 * 1024)
        peers[-1].send(frame)
        until("4K received frame shown", lambda: app.current_image is not None)
        check("fit stays inside viewport", app.image_bounds[0] >= 0 and app.image_bounds[1] >= 0 and app.image_bounds[2] <= app.canvas.winfo_width())
        check("fit uses smaller cached bitmap from received original", app.current_screen["_adxPresentation"][2].size != app.current_image.size)
        snapshot("windows-fit-4k")
        app.root.geometry(f"{app.root.minsize()[0]}x680"); pump(.15)
        actions = (app.controls_toggle_button, app.files_toggle_button, app.fullscreen_button,
                   app.image_settings_button, app.view_button, app.disconnect_button)
        check("view action wraps at narrow width without clipping toolbar actions", app._view_toolbar_wrapped and
              all(button.winfo_ismapped() and button.winfo_x() >= 0 and
                  button.winfo_x()+button.winfo_width() <= app.session_toolbar.winfo_width() for button in actions))
        snapshot("windows-narrow-toolbar")
        app.root.geometry("1180x680"); pump(.15)
        app.buttons_down.add("left"); app.keys_down[37] = "ctrl"
        app.view_button.invoke(); pump(.15)
        check("100% button releases held remote inputs", not app.buttons_down and not app.keys_down)
        check("100% reuses the exact received JPEG pixels", app.current_screen["_adxPresentation"][2] is app.current_image and app.current_image.tobytes() == received_pixels)
        check("100% is one received pixel per canvas pixel", app.image_bounds[2:] == (3840, 2160))
        check("both native navigation bars are visible", app.view_horizontal.winfo_ismapped() and app.view_vertical.winfo_ismapped())
        check("100% status distinguishes source and display", "Imagem 3840 × 2160" in app.screen_var.get() and "100% dos pixels recebidos" in app.screen_var.get())
        check("other window remains fitted independently", other.view_mode == "fit")
        snapshot("windows-native-13px-text")
        original_photo = app.photo
        app._pan_view(0, "moveto", "1"); app._pan_view(1, "moveto", "1"); pump()
        check("panning reuses Tk photo without a new resize or upload", app.photo is original_photo)
        width, height = app.canvas.winfo_width(), app.canvas.winfo_height()
        check("panned visible corner maps exactly to PC corner", controller.normalized_point(width-1, height-1, app.image_bounds) == (1.0, 1.0))
        app.canvas.event_generate("<ButtonPress-1>", x=width-1, y=height-1)
        app.canvas.event_generate("<ButtonRelease-1>", x=width-1, y=height-1)
        until("remote Host receives correct corner click", lambda: any(m.get("action") == "button" and m.get("down") and m.get("x") == 1.0 and m.get("y") == 1.0 for m in messages))
        snapshot("windows-native-panned")
        app._toggle_fullscreen(True); pump(.15)
        check("100% view action remains visible in fullscreen", app.fullscreen_view.winfo_ismapped())
        app._toggle_fullscreen_commands(); pump(.15)
        check("hide commands also hides native navigation bars", not app.view_horizontal.winfo_ismapped() and not app.view_vertical.winfo_ismapped())
        app._toggle_fullscreen_commands(); pump(.15)
        check("show commands restores navigation bars", app.view_horizontal.winfo_ismapped() and app.view_vertical.winfo_ismapped())
        snapshot("windows-native-fullscreen")
        app.fullscreen_view.invoke(); pump(.15)
        check("fullscreen action restores fit and removes navigation bars", app.view_mode == "fit" and not app.view_horizontal.winfo_ismapped() and not app.view_vertical.winfo_ismapped())
        app._toggle_fullscreen(False); pump()
        check("native and fit preserve quality gear", app.image_settings_button.winfo_ismapped() and app.stream_mode_applied == "quality")
        app._set_view_mode("native"); pump()
        app.canvas.event_generate("<ButtonPress-1>", x=800, y=400)
        until("native drag reaches Host before adaptive size change", lambda: "left" in app.buttons_down and
              any(m.get("action") == "button" and m.get("down") and .2 < m.get("x", 0) < .3 for m in messages))
        app.keys_down[37] = "ctrl"
        releases_before = len([m for m in messages if m.get("action") == "release"])
        with Image.open(io.BytesIO(payload)) as source:
            smaller = source.resize((1920, 1080), Image.Resampling.LANCZOS)
            smaller_bytes = io.BytesIO(); smaller.save(smaller_bytes, "JPEG", quality=94, subsampling=0); smaller.close()
        next_frame = dict(frame, frameId=2, image=base64.b64encode(smaller_bytes.getvalue()).decode())
        peers[-1].send(next_frame)
        until("same capture presents adaptive 1080p JPEG", lambda: app.current_image.size == (1920, 1080))
        until("adaptive native resize releases Host input before any continued drag", lambda:
              len([m for m in messages if m.get("action") == "release"]) > releases_before)
        check("adaptive native resize clears keys and buttons while preserving session", not app.buttons_down and not app.keys_down and
              app.view_mode == "native" and app.current_screen["captureId"] == frame["captureId"])
        app.canvas.event_generate("<Motion>", x=800, y=400); app.canvas.event_generate("<ButtonRelease-1>", x=800, y=400); pump()
        check("motion after adaptive resolution cannot resume the released drag", not app.buttons_down and not app.keys_down)
        check("TLS session stays active", app.peer and not app.peer.closed)
        check("no Tk callback errors", not errors)
        result = {"passed": True, "checks": checks, "checks_count": len(checks), "tk_errors": errors,
                  "screenshots": pictures, "tk_version": tk.TkVersion, "received_jpeg_bytes": len(payload),
                  "scope": "Tk with production localhost TLS; synthetic 4K JPEG Q94/4:4:4 and 13px text. Received pixel equality verified in 100% mode.",
                  "limitations": ["Linux Tk; no physical Windows DPI or internet network quality verification."]}
except BaseException as failure:
    result = {"passed": False, "error": str(failure), "traceback": traceback.format_exc(), "checks": checks, "tk_errors": errors}
finally:
    if manager:
        manager.close_all()
    if server:
        server.stop()
    if owner:
        try: owner.destroy()
        except tk.TclError: pass
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
if not result["passed"]:
    raise SystemExit(1)
