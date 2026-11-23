"""Actual Tk/PhotoImage quality checks for the Windows publication gate.

Only synthetic images and local in-memory Peer test doubles are used. No PC
connection, screen capture, installed settings or user files are accessed.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import MethodType
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont, ImageTk

DESKTOP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESKTOP))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import controller
from test_controller_stability import app_stub

OUTPUT = Path(os.environ.get("ALIANCADX_QUALITY_QA_OUTPUT", str(Path(tempfile.gettempdir()) / "aliancadx-quality-7.7.0")))


@unittest.skipUnless(sys.platform == "win32" or os.environ.get("DISPLAY"), "Actual Windows/Tk or a private X display is required")
class WindowsQualitySmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tkinter as tk
        import app_identity
        app_identity.configure_process_identity("controller")
        controller.enable_dpi_awareness()
        cls.tk = tk
        cls.owner = tk.Tk()
        cls.owner.withdraw()
        cls.errors, cls.report = [], []
        cls.owner.report_callback_exception = lambda kind, error, traceback: cls.errors.append(f"{kind.__name__}: {error}")
        OUTPUT.mkdir(parents=True, exist_ok=True)
        cls.source = Image.new("RGB", (3840, 2160), "#f6faf8")
        draw = ImageDraw.Draw(cls.source)
        font = ImageFont.load_default(size=18)
        draw.rectangle((0, 0, 3839, 79), fill="#073b2b")
        draw.text((24, 26), "AliançaDX · teste local de imagem 3840 x 2160", font=font, fill="white")
        for row in range(70):
            y = 90 + row * 28
            color = ("#173e2b", "#b41558", "#205aa5")[row % 3]
            draw.text((25, y), f"{row + 1:02d}  Texto original: 0123456789 ABCDefgh · bordas finas e cores preservadas", font=font, fill=color)
            draw.line((24, y + 24, 2200, y + 24), fill="#aec9bc", width=1)
        for x in range(2500, 3500, 2):
            draw.line((x, 90, x, 2089), fill="#152f25", width=1)
        stream = io.BytesIO(); cls.source.save(stream, "PNG")
        cls.png = stream.getvalue()
        reduced = cls.source.resize((960, 540), Image.Resampling.LANCZOS)
        stream = io.BytesIO(); reduced.save(stream, "JPEG", quality=65, subsampling=0)
        cls.jpeg = stream.getvalue(); reduced.close()
        cls.source_sha = hashlib.sha256(cls.source.tobytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.owner.update_idletasks()
            native_result = getattr(cls, "native_result", None)
            result = {"all_pass": not cls.errors and bool(native_result and native_result.wasSuccessful()) and len(cls.report) == 5, "scope": "Actual production Controller decoder/render/status on native Tk, synthetic local images, no remote PC or installer run", "tests_run": native_result.testsRun if native_result else 0, "source_pixels": [3840, 2160], "source_RGB_sha256": cls.source_sha, "checks": cls.report, "Tk_callback_errors": cls.errors}
            (OUTPUT / "windows-quality-verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({"native_Tk_quality": result, "output_directory": str(OUTPUT)}, ensure_ascii=False))
        finally:
            cls.source.close()
            cls.owner.destroy()

    def setUp(self):
        type(self).native_result = self._outcome.result
        self.apps = []

    def tearDown(self):
        for app, worker, window in self.apps:
            app.stopping.set(); app.frame_event.set(); worker.join(3)
            self.assertFalse(worker.is_alive())
            app._clear_frame()
            window.destroy()
        self.assertEqual(self.errors, [])

    def make_app(self, title="AliançaDX · qualidade"):
        tk = self.tk
        window = tk.Toplevel(self.owner)
        window.title(title); window.geometry("1000x680+10+10")
        app = app_stub()
        app.root = window
        app.canvas = tk.Canvas(window, width=1000, height=590, background="#073b2b", highlightthickness=0)
        app.canvas.pack(fill="both", expand=True)
        app.image_item = app.canvas.create_image(0, 0, anchor="nw")
        app.placeholder_title = app.canvas.create_text(100, 50, text="Aguardando imagem")
        app.placeholder_detail = app.canvas.create_text(100, 80, text="Teste local")
        app.screen_var = tk.StringVar(window)
        app.stream_mode_status_var = tk.StringVar(window)
        tk.Label(window, textvariable=app.screen_var, wraplength=960, anchor="w").pack(fill="x")
        tk.Label(window, textvariable=app.stream_mode_status_var, wraplength=960, anchor="w").pack(fill="x")
        app.view_mode_var = tk.StringVar(window)
        app.stream_gear_var = tk.StringVar(window)
        app.view_mode, app.view_position = "fit", (0.0, 0.0)
        app.photo_bitmap = None
        app.stream_settings_popup = None
        app.stream_settings_checked = True
        app.stream_settings_generation = app.generation
        app.stream_mode_preference = app.stream_mode_applied = "auto"
        app.stream_mode_last_sent = None
        app.stream_ready_generation = app.stream_active_generation = app.generation
        app.last_stream_feedback_id = 0
        app.stream_resume_needed = False
        app.latest_stream_frame = None
        app._render_frame = MethodType(controller.Controller._render_frame, app)
        # Native Windows mapping/WM_SIZE messages are not idle tasks. A real
        # app runs mainloop before it receives its first screen; this fixture
        # must likewise wait for a mapped, usable canvas before the decoder
        # snapshots its viewport. A 1x1 startup canvas correctly cannot ACK.
        window.deiconify()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.owner.update()
            if window.winfo_ismapped() and app.canvas.winfo_viewable() and app.canvas.winfo_width() >= 100 and app.canvas.winfo_height() >= 100:
                break
            time.sleep(.01)
        self.assertTrue(window.winfo_ismapped(), "Native test window never mapped")
        self.assertTrue(app.canvas.winfo_viewable(), "Native test canvas never became visible")
        self.assertGreaterEqual(app.canvas.winfo_width(), 100)
        self.assertGreaterEqual(app.canvas.winfo_height(), 100)
        app.decode_viewport = (app.canvas.winfo_width(), app.canvas.winfo_height())
        app.decode_native = False
        worker = threading.Thread(target=app._decode_frames, daemon=True)
        worker.start(); self.apps.append((app, worker, window))
        return app

    def display(self, app, data, number=1, capture="synthetic-screen"):
        with Image.open(io.BytesIO(data)) as encoded:
            encoded_size = encoded.size
        message = {"type": "screen", "captureId": capture, "monitorId": 1, "monitors": [], "left": 0, "top": 0,
                   "width": 3840, "height": 2160, "frameWidth": encoded_size[0], "frameHeight": encoded_size[1],
                   "image": base64.b64encode(data).decode("ascii"), "streamVersion": 1, "frameId": number,
                   "streamSettingsVersion": 1, "streamMode": "auto", "_adxImageFormat": "untrusted wire hint"}
        app._network_message(message, app.generation)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with app.frame_lock:
                ready = app.decoded_frame
                if ready:
                    app.decoded_frame = None
                    break
            self.owner.update()
            time.sleep(.005)
        else:
            self.fail("Production decoder did not finish a synthetic image")
        _, _, decoded_message, image = ready
        app._show_frame(decoded_message, image)
        self.assertFalse(app.frame_stalled)
        diagnostic = {"window_mapped": bool(app.root.winfo_ismapped()), "canvas_viewable": bool(app.canvas.winfo_viewable()),
                      "canvas_size": [app.canvas.winfo_width(), app.canvas.winfo_height()],
                      "decoded_size": list(image.size), "has_Tk_photo": app.photo is not None,
                      "prepared_viewport": decoded_message.get("_adxPresentation", (None,))[0],
                      "generation": app.generation, "ready_generation": app.stream_ready_generation,
                      "frame_id": controller.stream_frame_id(decoded_message), "ack_id": app.last_stream_feedback_id}
        print(json.dumps({"native_Tk_display": diagnostic}), flush=True)
        self.assertEqual(app.last_stream_feedback_id, number, json.dumps(diagnostic))
        self.owner.update()
        return image

    def tk_pixels(self, app):
        return ImageTk.getimage(app.photo).convert("RGB")

    def test_native_png_reaches_tk_pixel_exact_and_fit_always_uses_the_received_original(self):
        app = self.make_app()
        image = self.display(app, self.png)
        self.assertEqual(image.size, (3840, 2160))
        self.assertEqual(hashlib.sha256(image.tobytes()).hexdigest(), self.source_sha)
        self.assertIn("Resolução original · sem perdas", app.stream_mode_status_var.get())
        for scale in (1.0, 1.5, 2.0):
            self.owner.tk.call("tk", "scaling", scale * 4 / 3)
            app.root.geometry("1000x680"); self.owner.update_idletasks()
            app._set_view_mode("fit")
            viewport = (app.canvas.winfo_width(), app.canvas.winfo_height())
            expected = controller.fit_geometry(*viewport, 3840, 2160)
            self.assertEqual(app.image_bounds, expected)
            with self.source.resize(expected[2:], Image.Resampling.LANCZOS) as target, self.tk_pixels(app) as actual:
                self.assertEqual(actual.tobytes(), target.tobytes())
                if scale == 1.0: actual.save(OUTPUT / "tk-fit-4k.png")
            self.assertEqual(hashlib.sha256(image.tobytes()).hexdigest(), self.source_sha)
            app._set_view_mode("native")
            self.assertIs(app.current_screen["_adxPresentation"][2], image)
            with self.tk_pixels(app) as actual:
                self.assertEqual(actual.size, (3840, 2160))
                self.assertEqual(hashlib.sha256(actual.tobytes()).hexdigest(), self.source_sha)
                if scale == 1.0: actual.save(OUTPUT / "tk-native-original-4k.png")
            self.report.append(f"Tk scaling {int(scale * 100)}%: full decoded PNG retained; Fit one Lanczos transform; native PhotoImage exact RGB")
        self.assertEqual(app.last_stream_feedback_id, 1)

    def test_native_and_reduced_quality_labels_remain_isolated_between_two_real_windows(self):
        first, second = self.make_app("AliançaDX · original"), self.make_app("AliançaDX · adaptada")
        self.display(first, self.png, capture="screen-original")
        self.display(second, self.jpeg, capture="screen-adapted")
        first_image = first.current_image
        self.assertIn("Resolução original · sem perdas", first.stream_mode_status_var.get())
        self.assertIn("960 × 540 · Resolução adaptada", second.stream_mode_status_var.get())
        self.assertNotIn("sem perdas", second.stream_mode_status_var.get())
        self.assertEqual((first.current_screen["captureId"], second.current_screen["captureId"]), ("screen-original", "screen-adapted"))
        self.display(second, self.png, number=2, capture="screen-adapted")
        self.assertIn("Resolução original · sem perdas", second.stream_mode_status_var.get())
        self.assertIs(first.current_image, first_image)
        self.assertEqual(first.last_stream_feedback_id, 1)
        self.assertEqual(second.last_stream_feedback_id, 2)
        self.report.append("Two real Tk windows: adapted JPEG to original PNG refinement, codec truth and frame/capture isolation")

    def test_unchanged_confirmations_keep_native_image_without_repaint_or_ack_and_respect_pause_and_deadlines(self):
        app = self.make_app()
        image = self.display(app, self.png)
        bitmap = app.photo
        initial = app.last_frame_at
        unchanged = {"type": "screen_unchanged", "streamVersion": 1, "frameId": 1, "captureId": "synthetic-screen"}
        for step in range(2, 21, 2):
            now = initial + step
            with patch.object(controller.time, "monotonic", return_value=now): self.assertTrue(app._screen_unchanged(unchanged))
            app._check_frame_health(now=now + 1.9)
            self.assertFalse(app.frame_stalled)
            self.assertIs(app.current_image, image)
            self.assertIs(app.photo, bitmap)
            self.assertEqual(app.last_stream_feedback_id, 1)
        app.local_paused = True
        self.assertFalse(app._screen_unchanged(unchanged))
        app.local_paused = False
        with patch.object(controller.time, "monotonic", return_value=app.last_frame_at + 6.001): self.assertFalse(app._screen_unchanged(unchanged))
        app._clear_frame()
        self.assertFalse(app._screen_unchanged(unchanged))
        self.assertIsNone(app.current_image)
        self.report.append("Twenty seconds of unchanged native image: no redraw/ACK; pause, late confirmation and cleared-frame recovery remain closed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
