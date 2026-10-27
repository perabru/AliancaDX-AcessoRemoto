"""Portable boundary checks for the Windows identity; native tests are separate."""
from __future__ import annotations

import ctypes
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_identity


class ApplicationIdentityTests(unittest.TestCase):
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
