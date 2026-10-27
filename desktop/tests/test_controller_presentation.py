"""Exercise real JPEG work, latest-frame ownership and Tk presentation boundaries."""
import base64
import io
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from PIL import Image, ImageDraw
import controller
from test_controller_stability import app_stub, frame, wait_for


def jpeg_frame(number, color):
    with Image.new("RGB", (100, 50), color) as image:
        stream = io.BytesIO()
        image.save(stream, "JPEG", quality=90, subsampling=0)
    return dict(frame(), streamVersion=1, frameId=number,
                image=base64.b64encode(stream.getvalue()).decode("ascii"))


def render_app(size=(64, 32)):
    app = app_stub()
    app.canvas.winfo_width = lambda: size[0]
    app.canvas.winfo_height = lambda: size[1]
    app.canvas.coords = Mock()
    app.placeholder_title, app.placeholder_detail = "title", "detail"
    app.current_image = Image.new("RGB", (100, 50), (30, 90, 140))
    app.current_screen = frame()
    return app


class ControllerPresentationTests(unittest.TestCase):
    def test_fit_enlarges_a_smaller_adaptive_frame_to_the_available_viewport(self):
        with Image.new("RGB", (120, 60), "#174b31") as image:
            prepared = controller.prepare_frame_image(image, (240, 180))
            self.addCleanup(prepared[2].close)
            self.assertIsNot(prepared[2], image)
            self.assertEqual(prepared[1], (0, 30, 240, 120))
            self.assertEqual(prepared[2].size, (240, 120))
            self.assertEqual(image.size, (120, 60))
            self.assertEqual(prepared[2].getpixel((239, 119)), (23, 75, 49))

    def test_fit_uses_decoded_frame_size_when_physical_pc_is_larger(self):
        app = render_app(size=(1440, 900))
        app.current_screen.update(width=3840, height=2160, frameWidth=100, frameHeight=50)
        self.addCleanup(lambda: controller.close_frame_image(app.current_screen, app.current_image))
        with patch.object(controller.ImageTk, "PhotoImage", return_value="tk-copy"):
            self.assertTrue(controller.Controller._render_frame(app))
        self.assertEqual(app.image_bounds, (0, 90, 1440, 720))
        self.assertEqual(controller.normalized_point(1439, 809, app.image_bounds), (1.0, 1.0))
        self.assertEqual(controller.normalized_point(0, 90, app.image_bounds), (0.0, 0.0))

    def test_fit_uses_lanczos_once_from_received_original(self):
        with Image.new("RGB", (180, 80), "white") as image:
            draw = ImageDraw.Draw(image)
            for x in range(0, 180, 4):
                draw.line((x, 0, x, 79), fill="black")
            prepared = controller.prepare_frame_image(image, (90, 40))
            with image.resize((90, 40), Image.Resampling.LANCZOS) as expected:
                self.assertEqual(prepared[2].tobytes(), expected.tobytes())
            prepared[2].close()

    def test_native_4k_never_resizes_or_copies_received_pixels(self):
        with Image.new("RGB", (3840, 2160), "#174b31") as image, \
                patch.object(Image.Image, "resize", side_effect=AssertionError("native pixel resize")):
            prepared = controller.prepare_frame_image(image, (900, 500), native=True)
            self.assertIs(prepared[2], image)
            self.assertEqual(prepared[1:], ((0, 0, 3840, 2160), image, "native"))

    def test_native_pan_maps_last_visible_pixel_to_remote_bottom_right(self):
        bounds = controller.native_geometry((900, 500), (3840, 2160), (1, 1))
        self.assertEqual(bounds, (-2940, -1660, 3840, 2160))
        self.assertEqual(controller.normalized_point(899, 499, bounds), (1.0, 1.0))
        self.assertEqual(controller.native_geometry((900, 500), (640, 480), (1, 1)), (130, 10, 640, 480))

    def test_native_pan_reuses_tk_bitmap_and_releases_remote_input(self):
        app = render_app(size=(60, 30))
        app._render_frame = lambda: controller.Controller._render_frame(app)
        app.view_mode, app.view_position = "native", (0.0, 0.0)
        app.buttons_down.add("left"); app.keys_down[37] = "ctrl"
        self.addCleanup(lambda: controller.close_frame_image(app.current_screen, app.current_image))
        with patch.object(controller.ImageTk, "PhotoImage", return_value="tk-copy") as photo, \
                patch.object(Image.Image, "resize", side_effect=AssertionError("native pixel resize")):
            self.assertTrue(app._render_frame())
            app._pan_view(0, "moveto", 1)
            app._pan_view(1, "scroll", 1, "pages")
            photo.assert_called_once()
        self.assertFalse(app.buttons_down or app.keys_down)
        self.assertEqual(app.image_bounds, (-40, -20, 100, 50))
        self.assertEqual(controller.normalized_point(59, 29, app.image_bounds), (1.0, 1.0))

    def test_adaptive_native_resolution_releases_drag_before_coordinates_change(self):
        app = render_app(size=(900, 500))
        app.current_image.close()
        app.current_image = Image.new("RGB", (3840, 2160))
        app.current_screen.update(width=3840, height=2160)
        app.view_mode, app.view_position = "native", (0.0, 0.0)
        app._render_frame = lambda: controller.Controller._render_frame(app)
        self.addCleanup(lambda: controller.close_frame_image(app.current_screen, app.current_image))
        event = SimpleNamespace(x=800, y=400, num=1)
        with patch.object(controller.ImageTk, "PhotoImage", return_value="tk-copy"):
            app._render_frame()
            app._mouse_down(event)
            self.assertIn("left", app.buttons_down)
            app.keys_down[37] = "ctrl"
            old_point = app.last_point
            while app.outgoing.get(0) is not None:
                pass
            message = dict(frame(), width=3840, height=2160)
            app._show_frame(message, Image.new("RGB", (1920, 1080)))
            self.assertEqual(app.outgoing.get(0)[2], {"type": "input", "action": "release"})
            self.assertFalse(app.buttons_down or app.keys_down)
            self.assertEqual((app.generation, app.frame_epoch, app.view_mode), (7, 3, "native"))
            self.assertEqual(app.current_screen["captureId"], message["captureId"])
            app._mouse_move(event)
            self.assertGreater(app.last_point[0] - old_point[0], .2)
            self.assertFalse(app.buttons_down, "Changed coordinates must not continue an existing drag")

    def test_mode_switch_closes_old_fitted_bitmap_but_preserves_original(self):
        app = render_app()
        app._render_frame = lambda: controller.Controller._render_frame(app)
        app.view_mode, app.view_position = "fit", (.5, .5)
        self.addCleanup(lambda: controller.close_frame_image(app.current_screen, app.current_image))
        with patch.object(controller.ImageTk, "PhotoImage", return_value="tk-copy"):
            app._render_frame()
            fitted = app.current_screen["_adxPresentation"][2]
            self.assertTrue(app._set_view_mode("native"))
            with self.assertRaises(ValueError):
                fitted.getpixel((0, 0))
            self.assertIs(app.current_screen["_adxPresentation"][2], app.current_image)
            self.assertEqual(app.current_image.getpixel((0, 0)), (30, 90, 140))
            self.assertEqual(app.view_position, (0.0, 0.0))
            self.assertIn("100% dos pixels recebidos", app.screen_var.get())
            self.assertTrue(app._set_view_mode("fit"))
            self.assertNotIn("100%", app.screen_var.get())

    def test_pan_and_view_choices_do_not_change_other_windows(self):
        first, second = render_app(), render_app()
        self.addCleanup(lambda: controller.close_frame_image(first.current_screen, first.current_image))
        self.addCleanup(lambda: controller.close_frame_image(second.current_screen, second.current_image))
        first._render_frame = Mock(return_value=True)
        second.view_mode, second.view_position = "fit", (0.0, 0.0)
        first._set_view_mode("native")
        self.assertEqual((second.view_mode, second.view_position), ("fit", (0.0, 0.0)))

    def test_native_size_preserves_every_pixel_without_another_bitmap(self):
        image = Image.new("RGB", (120, 60))
        self.addCleanup(image.close)
        with patch.object(Image.Image, "resize", side_effect=AssertionError("native-size resize")):
            prepared = controller.prepare_frame_image(image, (120, 60))
        self.assertIs(prepared[2], image)
        self.assertEqual(prepared[1], (0, 0, 120, 60))

    def test_reduced_presentation_keeps_native_image_for_future_window_size(self):
        image = Image.new("RGB", (120, 60), "red")
        self.addCleanup(image.close)
        prepared = controller.prepare_frame_image(image, (90, 90))
        self.addCleanup(prepared[2].close)
        self.assertEqual(prepared[2].size, (90, 45))
        self.assertEqual(prepared[1], (0, 22, 90, 45))
        self.assertEqual(image.size, (120, 60))
        self.assertEqual(image.getpixel((119, 59)), (255, 0, 0))

    def test_unavailable_or_invalid_viewport_does_not_allocate_a_presentation(self):
        with Image.new("RGB", (100, 50)) as image:
            for viewport in (None, [100, 50], (1, 50), (0, 1), (True, 2), (float("nan"), 50),
                             (100, 50, 1), (100000, 50)):
                with self.subTest(viewport=viewport):
                    self.assertIsNone(controller.prepare_frame_image(image, viewport))

    def test_prepared_frame_reaches_tk_without_another_resize(self):
        app = render_app()
        self.addCleanup(lambda: controller.close_frame_image(app.current_screen, app.current_image))
        prepared = controller.prepare_frame_image(app.current_image, (64, 32))
        app.current_screen["_adxPresentation"] = prepared
        with patch.object(Image.Image, "resize", side_effect=AssertionError("UI must not resize")), \
                patch.object(controller.ImageTk, "PhotoImage", return_value="tk-copy") as photo:
            self.assertTrue(controller.Controller._render_frame(app))
        photo.assert_called_once_with(prepared[2], master=app.root)
        self.assertEqual(app.image_bounds, (0, 0, 64, 32))

    def test_changed_viewport_uses_native_source_and_closes_old_preparation(self):
        app = render_app(size=(80, 40))
        self.addCleanup(lambda: controller.close_frame_image(app.current_screen, app.current_image))
        old = controller.prepare_frame_image(app.current_image, (40, 20))
        app.current_screen["_adxPresentation"] = old
        source = app.current_image
        real_resize = Image.Image.resize
        resized = []
        def record(image, size, *args, **kwargs):
            resized.append(image)
            return real_resize(image, size, *args, **kwargs)
        with patch.object(Image.Image, "resize", record), \
                patch.object(controller.ImageTk, "PhotoImage", return_value="tk-copy"):
            self.assertTrue(controller.Controller._render_frame(app))
        self.assertEqual(resized, [source])
        self.assertEqual(app.current_screen["_adxPresentation"][2].size, (80, 40))
        with self.assertRaises(ValueError):
            old[2].getpixel((0, 0))
        self.assertEqual(source.size, (100, 50))

    def test_clear_frame_closes_current_and_waiting_prepared_bitmaps(self):
        app = render_app()
        current, current_message = app.current_image, app.current_screen
        current_message["_adxPresentation"] = controller.prepare_frame_image(current, (64, 32))
        current_prepared = current_message["_adxPresentation"][2]
        waiting = Image.new("RGB", (100, 50))
        waiting_message = frame()
        waiting_message["_adxPresentation"] = controller.prepare_frame_image(waiting, (64, 32))
        waiting_prepared = waiting_message["_adxPresentation"][2]
        app.decoded_frame = (app.generation, app.frame_epoch, waiting_message, waiting)
        app._clear_frame()
        for image in (current, current_prepared, waiting, waiting_prepared):
            with self.assertRaises(ValueError):
                image.getpixel((0, 0))
        self.assertIsNone(app.decoded_frame)
        self.assertIsNone(app.current_image)

    def start_decoder(self, app):
        thread = threading.Thread(target=app._decode_frames, daemon=True)
        thread.start()
        def cleanup():
            app.stopping.set()
            app.frame_event.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            with app.frame_lock:
                decoded, app.decoded_frame = app.decoded_frame, None
            if decoded:
                controller.close_frame_image(decoded[2], decoded[3])
        self.addCleanup(cleanup)
        return thread

    def test_slow_decoder_keeps_current_and_latest_without_fifo_backlog(self):
        app = app_stub()
        app.decode_viewport = (64, 32)
        app.stream_ready_generation = app.generation
        app.last_stream_feedback_id = 0
        entered, unblock = threading.Event(), threading.Event()
        self.addCleanup(unblock.set)
        prepared_images = []
        real_prepare = controller.prepare_frame_image
        def prepare(image, viewport):
            prepared = real_prepare(image, viewport)
            prepared_images.append((image, prepared[2]))
            if len(prepared_images) == 1:
                entered.set()
                if not unblock.wait(2):
                    raise RuntimeError("test decoder gate timed out")
            return prepared
        with patch.object(controller, "prepare_frame_image", side_effect=prepare):
            self.start_decoder(app)
            app._network_message(jpeg_frame(1, "red"), app.generation)
            self.assertTrue(entered.wait(1))
            app._network_message(jpeg_frame(2, "green"), app.generation)
            app._network_message(jpeg_frame(3, "blue"), app.generation)
            unblock.set()
            wait_for(lambda: app.decoded_frame is not None and app.decoded_frame[2]["frameId"] == 3)
            self.assertEqual(len(prepared_images), 2, "The replaced middle JPEG must never be decoded")
            self.assertIsNone(app.raw_frame)
            self.assertEqual(app.messages.qsize(), 0, "Frames cannot create a growing Tk callback queue")
            for image in prepared_images[0]:
                with self.assertRaises(ValueError):
                    image.getpixel((0, 0))
            app._pump()
            self.assertEqual(app.current_screen["frameId"], 3)
            self.assertGreater(app.current_image.getpixel((0, 0))[2], 250)
            feedback = app.outgoing.get(0)[2]
            self.assertEqual((feedback["frameId"], feedback["cumulative"]), (3, True))
            app._clear_frame()

    def test_capture_change_while_preparing_discards_both_old_bitmaps(self):
        app = app_stub()
        app.decode_viewport = (64, 32)
        entered, unblock = threading.Event(), threading.Event()
        self.addCleanup(unblock.set)
        images = []
        real_prepare = controller.prepare_frame_image
        def prepare(image, viewport):
            prepared = real_prepare(image, viewport)
            images.extend((image, prepared[2]))
            entered.set()
            if not unblock.wait(2):
                raise RuntimeError("test decoder gate timed out")
            return prepared
        with patch.object(controller, "prepare_frame_image", side_effect=prepare):
            worker = self.start_decoder(app)
            app._network_message(jpeg_frame(1, "red"), app.generation)
            self.assertTrue(entered.wait(1))
            app._clear_frame()
            unblock.set()
            app.stopping.set()
            app.frame_event.set()
            worker.join(1)
            self.assertFalse(worker.is_alive())
        self.assertIsNone(app.decoded_frame)
        for image in images:
            with self.assertRaises(ValueError):
                image.getpixel((0, 0))

    def test_repeated_frames_do_not_reconfigure_unchanged_monitor_choices(self):
        app = app_stub()
        app.monitor_combo.configure = Mock()
        app.monitor_combo.current = Mock()
        controller.Controller._set_monitors(app, frame())
        controller.Controller._set_monitors(app, frame())
        self.assertEqual(app.monitor_combo.configure.call_count, 1)
        self.assertEqual(app.monitor_combo.current.call_count, 1)
        controller.Controller._set_monitors(app, dict(frame(), monitors=[{"id": 1, "width": 200, "height": 100}]))
        self.assertEqual(app.monitor_combo.configure.call_count, 2)


if __name__ == "__main__":
    unittest.main()
