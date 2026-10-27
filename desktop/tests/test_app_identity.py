"""Portable boundary checks for the Windows identity; native tests are separate."""
from __future__ import annotations

import ctypes
import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_identity
import desktop_ui


class ApplicationIdentityTests(unittest.TestCase):
    def test_shipped_ico_uses_native_dib_entries_with_unchanged_artwork(self):
        from PIL import Image
        root = Path(app_identity.__file__).resolve().parent
        source = root / "brand/aliancadx.ico"
        encoded = source.read_bytes()
        self.assertEqual(encoded, (root / "assets/brand/aliancadx.ico").read_bytes())
        self.assertEqual(struct.unpack_from("<HHH", encoded), (0, 1, 7))
        sizes = (16, 24, 32, 48, 64, 128, 256)
        with Image.open(source) as icon, Image.open(root / "brand/aliancadx-app-1024.png") as artwork:
            self.assertEqual(icon.ico.sizes(), {(size, size) for size in sizes})
            for index, size in enumerate(sizes):
                offset = struct.unpack_from("<I", encoded, 6 + index * 16 + 12)[0]
                self.assertEqual(struct.unpack_from("<I", encoded, offset)[0], 40)
                expected = artwork.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
                self.assertEqual(icon.ico.getimage((size, size)).convert("RGBA").tobytes(), expected.tobytes())

    def test_brand_generator_reproduces_the_shipped_native_icon(self):
        from PIL import Image
        root = Path(app_identity.__file__).resolve().parent
        spec = importlib.util.spec_from_file_location("identity_brand_generator", root / "brand/generate_brand_assets.py")
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        with tempfile.TemporaryDirectory() as temporary, Image.open(root / "brand/aliancadx-app-1024.png") as artwork:
            output = Path(temporary) / "reproduced.ico"
            generator.save_windows_icon(artwork, output)
            self.assertEqual(output.read_bytes(), (root / "assets/brand/aliancadx.ico").read_bytes())

    def test_windows_native_ico_does_not_get_replaced_by_large_photo(self):
        window = Mock(_aliancadx_icon_applied=False)
        window.winfo_toplevel.return_value = window
        icon = Path(desktop_ui.__file__).resolve().parent / "assets/brand/aliancadx.ico"
        with patch.object(desktop_ui.sys, "platform", "win32"), \
             patch.object(desktop_ui, "_brand_photo") as photo:
            desktop_ui._apply_window_icon(window)
        self.assertEqual(window.iconbitmap.call_args_list, [call(default=str(icon)), call(str(icon))])
        window.iconphoto.assert_not_called()
        photo.assert_not_called()
        self.assertTrue(window._aliancadx_icon_applied)

    def test_failed_windows_ico_keeps_photo_fallback(self):
        window = Mock(_aliancadx_icon_applied=False)
        window.winfo_toplevel.return_value = window
        window.iconbitmap.side_effect = desktop_ui.tk.TclError("Injected ICO failure")
        with patch.object(desktop_ui.sys, "platform", "win32"), \
             patch.object(desktop_ui, "_brand_photo", return_value="brand-photo"):
            desktop_ui._apply_window_icon(window)
        window.iconphoto.assert_called_once_with(True, "brand-photo")
        self.assertTrue(window._aliancadx_icon_applied)

    def test_non_windows_never_loads_shell_libraries(self):
        with patch.object(app_identity.sys, "platform", "linux"), \
             patch.object(app_identity.ctypes, "WinDLL", create=True) as library:
            self.assertFalse(app_identity.configure_process_identity("controller"))
            library.assert_not_called()

    def test_explicit_process_identity_uses_windows_hresult_width(self):
        native = Mock()
        native.SetCurrentProcessExplicitAppUserModelID.return_value = 0
        with patch.object(app_identity.sys, "platform", "win32"), \
             patch.object(app_identity.ctypes, "WinDLL", create=True, return_value=native):
            self.assertTrue(app_identity.configure_process_identity("host"))
        native.SetCurrentProcessExplicitAppUserModelID.assert_called_once_with("AliancaDX.AcessoRemoto.Host")
        self.assertIs(native.SetCurrentProcessExplicitAppUserModelID.restype, ctypes.c_int32)

    def test_failed_shell_identity_does_not_stop_attendance(self):
        native = Mock()
        native.SetCurrentProcessExplicitAppUserModelID.return_value = -2147467259
        with patch.object(app_identity.sys, "platform", "win32"), \
             patch.object(app_identity.ctypes, "WinDLL", create=True, return_value=native):
            self.assertFalse(app_identity.configure_process_identity("controller"))

    def test_invalid_shortcut_never_loads_com_or_mutates_files(self):
        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / "customer.txt"
            bad.write_text("unchanged", encoding="utf-8")
            with patch.object(app_identity.sys, "platform", "win32"), \
                 patch.object(app_identity.ctypes, "WinDLL", create=True) as library:
                with self.assertRaises(OSError):
                    app_identity.set_shortcut_identity(bad, "host")
                library.assert_not_called()
            self.assertEqual(bad.read_text(encoding="utf-8"), "unchanged")

    def test_hresult_and_propvariant_abi_match_native_x64_layout(self):
        self.assertEqual(ctypes.sizeof(app_identity._GUID), 16)
        self.assertEqual(ctypes.sizeof(app_identity._PROPERTYKEY), 20)
        self.assertEqual(app_identity._PROPERTYKEY.pid.offset, 16)
        self.assertEqual(app_identity._PROPVARIANT.value.offset, 8)
        self.assertEqual(ctypes.sizeof(app_identity._PROPVARIANT), 24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16)
        app_identity._checked(1, "S_FALSE is success")
        with self.assertRaisesRegex(OSError, "0x80004005"):
            app_identity._checked(-2147467259, "Injected failure")


if __name__ == "__main__":
    unittest.main()
