"""Exercise real JPEG work, latest-frame ownership and Tk presentation boundaries."""
import base64
import io
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from PIL import Image
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
