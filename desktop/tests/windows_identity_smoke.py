"""Real Windows Shell/Tk checks; no installed app or user shortcuts are changed.

Run on a Windows runner: ``python desktop/tests/windows_identity_smoke.py``.
The temporary .lnk files are never placed on the Desktop or in the Start menu.
Explorer's visible taskbar and manual pinning still require a physical PC check.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


DESKTOP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESKTOP))
import app_identity


def rendered_icon(icon):
    """Read the actual HICON artwork through Win32, with owned GDI cleanup."""
    class IconInfo(ctypes.Structure):
        _fields_ = [("is_icon", ctypes.c_int32), ("hotspot_x", ctypes.c_uint32),
                    ("hotspot_y", ctypes.c_uint32), ("mask", ctypes.c_void_p), ("color", ctypes.c_void_p)]

    class Bitmap(ctypes.Structure):
        _fields_ = [("type", ctypes.c_int32), ("width", ctypes.c_int32), ("height", ctypes.c_int32),
                    ("row_bytes", ctypes.c_int32), ("planes", ctypes.c_uint16),
                    ("bits_per_pixel", ctypes.c_uint16), ("bits", ctypes.c_void_p)]

    class BitmapHeader(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint32), ("width", ctypes.c_int32), ("height", ctypes.c_int32),
                    ("planes", ctypes.c_uint16), ("bits_per_pixel", ctypes.c_uint16),
                    ("compression", ctypes.c_uint32), ("image_bytes", ctypes.c_uint32),
                    ("x_pixels_per_meter", ctypes.c_int32), ("y_pixels_per_meter", ctypes.c_int32),
                    ("colors_used", ctypes.c_uint32), ("colors_important", ctypes.c_uint32)]

    user = ctypes.WinDLL("user32", use_last_error=True)
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    user.GetIconInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(IconInfo)]
    user.GetIconInfo.restype = ctypes.c_int32
    user.DrawIconEx.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_int32, ctypes.c_void_p,
                               ctypes.c_int32, ctypes.c_int32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
    user.DrawIconEx.restype = ctypes.c_int32
    gdi.GetObjectW.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p]
    gdi.GetObjectW.restype = ctypes.c_int32
    gdi.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi.DeleteObject.restype = ctypes.c_int32
    gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.POINTER(BitmapHeader), ctypes.c_uint32,
                                   ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_uint32]
    gdi.CreateDIBSection.restype = ctypes.c_void_p
    gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi.SelectObject.restype = ctypes.c_void_p
    gdi.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi.DeleteDC.restype = ctypes.c_int32
    gdi.GdiFlush.argtypes = []
    gdi.GdiFlush.restype = ctypes.c_int32
    info = IconInfo()
    if not user.GetIconInfo(icon, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        bitmap = Bitmap()
        if not info.color or not gdi.GetObjectW(info.color, ctypes.sizeof(bitmap), ctypes.byref(bitmap)):
            raise OSError("The application HICON has no readable color artwork")
        width, height = bitmap.width, bitmap.height
        if not (1 <= width <= 256 and 1 <= height <= 256):
            raise OSError("Unexpected application icon dimensions")
    finally:
        for owned in (info.color, info.mask):
            if owned:
                gdi.DeleteObject(owned)
    dc = gdi.CreateCompatibleDC(None)
    if not dc:
        raise ctypes.WinError(ctypes.get_last_error())
    dib = previous = None
    try:
        header = BitmapHeader()
        header.size, header.width, header.height = ctypes.sizeof(header), width, -height
        header.planes, header.bits_per_pixel = 1, 32
        pixels = ctypes.c_void_p()
        dib = gdi.CreateDIBSection(dc, ctypes.byref(header), 0, ctypes.byref(pixels), None, 0)
        if not dib or not pixels.value:
            raise ctypes.WinError(ctypes.get_last_error())
        previous = gdi.SelectObject(dc, dib)
        ctypes.memset(pixels, 255, width * height * 4)
        if not user.DrawIconEx(dc, 0, 0, icon, width, height, 0, None, 3):  # DI_NORMAL
            raise ctypes.WinError(ctypes.get_last_error())
        if not gdi.GdiFlush():
            raise ctypes.WinError(ctypes.get_last_error())
        return width, height, ctypes.string_at(pixels, width * height * 4)
    finally:
        if previous:
            gdi.SelectObject(dc, previous)
        if dib:
            gdi.DeleteObject(dib)
        gdi.DeleteDC(dc)


def read_app_id(path):
    """Independent read-only Shell property access after the writer released it."""
    shell = ctypes.WinDLL("shell32")
    ole = ctypes.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole.CoInitializeEx.restype = ctypes.c_int32
    result = ole.CoInitializeEx(None, 2)
    if result < 0 and result != -2147417850:
        raise OSError(result)
    pointer = ctypes.c_void_p()
    getter = shell.SHGetPropertyStoreFromParsingName
    getter.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32,
                       ctypes.POINTER(app_identity._GUID), ctypes.POINTER(ctypes.c_void_p)]
    getter.restype = ctypes.c_int32
    app_identity._checked(getter(str(path), None, 0, ctypes.byref(app_identity._PROPERTY_STORE_IID), ctypes.byref(pointer)),
                          "Leitura independente do atalho")
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    release = ctypes.WINFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p)(table[2])
    read = ctypes.WINFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.POINTER(app_identity._PROPERTYKEY),
                              ctypes.POINTER(app_identity._PROPVARIANT))(table[5])
    value = app_identity._PROPVARIANT()
    try:
        app_identity._checked(read(pointer, ctypes.byref(app_identity._APP_ID_KEY), ctypes.byref(value)),
                              "Conferência independente do atalho")
        if value.vt != 31 or not value.value.text:
            raise OSError("Atalho sem AppID Windows")
        return ctypes.wstring_at(value.value.text)
    finally:
        ole.PropVariantClear.argtypes = [ctypes.POINTER(app_identity._PROPVARIANT)]
        ole.PropVariantClear(ctypes.byref(value))
        release(pointer)
        if result >= 0:
            ole.CoUninitialize()


@unittest.skipUnless(sys.platform == "win32", "Real Windows Shell/Tk is required")
class WindowsIdentitySmoke(unittest.TestCase):
    def test_official_interpreter_has_own_stable_application_identity(self):
        shell = ctypes.WinDLL("shell32")
        ole = ctypes.WinDLL("ole32")
        getter = shell.GetCurrentProcessExplicitAppUserModelID
        getter.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        getter.restype = ctypes.c_int32
        ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        for role, expected in app_identity.APP_IDS.items():
            with self.subTest(role=role):
                self.assertTrue(app_identity.configure_process_identity(role))
                value = ctypes.c_void_p()
                self.assertEqual(getter(ctypes.byref(value)), 0)
                try:
                    self.assertEqual(ctypes.wstring_at(value), expected)
                finally:
                    ole.CoTaskMemFree(value)

    def test_installed_links_keep_target_and_brand_icon_with_verified_app_id(self):
        create = ("$ErrorActionPreference='Stop'; $s=New-Object -ComObject WScript.Shell; "
                  "$l=$s.CreateShortcut($env:ALIANCADX_TEST_LINK); "
                  "$l.TargetPath=$env:ALIANCADX_TEST_PYTHON; "
                  "$l.Arguments=$env:ALIANCADX_TEST_ARGUMENTS; "
                  "$l.WorkingDirectory=$env:ALIANCADX_TEST_ROOT; "
                  "$l.IconLocation=$env:ALIANCADX_TEST_ICON; $l.Save()")
        inspect = ("$ErrorActionPreference='Stop'; $s=New-Object -ComObject WScript.Shell; "
                   "$l=$s.CreateShortcut($env:ALIANCADX_TEST_LINK); "
                   "@{target=$l.TargetPath;arguments=$l.Arguments;icon=$l.IconLocation;directory=$l.WorkingDirectory}"
                   "|ConvertTo-Json -Compress")
        icon = DESKTOP / "assets/brand/aliancadx.ico"
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        with tempfile.TemporaryDirectory(prefix="AliancaDX-ícones-") as temporary:
            for role in ("controller", "host", "connector"):
                entry = "ios_connector.py" if role == "connector" else role + ".py"
                link = Path(temporary) / f"AliançaDX {role}.lnk"
                arguments = '"' + str(DESKTOP / entry) + '"'
                env = {**os.environ, "ALIANCADX_TEST_LINK": str(link), "ALIANCADX_TEST_PYTHON": str(pythonw),
                       "ALIANCADX_TEST_ARGUMENTS": arguments, "ALIANCADX_TEST_ROOT": str(DESKTOP),
                       "ALIANCADX_TEST_ICON": str(icon) + ",0"}
                subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", create],
                               env=env, check=True, capture_output=True, timeout=45)
                app_identity.set_shortcut_identity(link, role)
                self.assertEqual(read_app_id(link), app_identity.APP_IDS[role])
                # A second update of the same shortcut must remain safe.
                flag = "--connector-shortcut" if role == "connector" else "--shortcut"
                subprocess.run([sys.executable, str(DESKTOP / "app_identity.py"),
                                "--role", role if role != "connector" else "host", flag, str(link)],
                               check=True, capture_output=True, timeout=45)
                self.assertEqual(read_app_id(link), app_identity.APP_IDS[role])
                data = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", inspect],
                                      env=env, check=True, capture_output=True, text=True, timeout=45)
                details = json.loads(data.stdout)
                self.assertEqual(Path(details["target"]), pythonw)
                self.assertEqual(details["arguments"], arguments)
                self.assertEqual(details["icon"], str(icon) + ",0")
                self.assertEqual(Path(details["directory"]), DESKTOP)

    def test_real_tk_owner_and_multiple_windows_have_brand_icons(self):
        import tkinter as tk
        from desktop_ui import _apply_window_icon
        app_identity.configure_process_identity("controller")
        owner = tk.Tk()
        owner.withdraw()
        try:
            _apply_window_icon(owner)
            windows = [tk.Toplevel(owner), tk.Toplevel(owner)]
            user = ctypes.WinDLL("user32")
            user.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            user.GetAncestor.restype = ctypes.c_void_p
            user.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t, ctypes.c_ssize_t]
            user.SendMessageW.restype = ctypes.c_ssize_t
            user.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32,
                                       ctypes.c_int32, ctypes.c_int32, ctypes.c_uint32]
            user.LoadImageW.restype = ctypes.c_void_p
            user.DestroyIcon.argtypes = [ctypes.c_void_p]
            user.DestroyIcon.restype = ctypes.c_int32
            for window in windows:
                _apply_window_icon(window)
                window.update_idletasks()
                self.assertTrue(window._aliancadx_icon_applied)
                hwnd = user.GetAncestor(window.winfo_id(), 2)  # GA_ROOT, Tk wrapper HWND
                self.assertTrue(hwnd)
                # Tk's iconbitmap getter reads its X bitmap, not its native
                # Windows ICO (tkWinWm.c WmIconbitmapCmd). Read and render the
                # actual small/large HICONs; a Python/Tk default icon must fail.
                for size in (0, 1):
                    actual = user.SendMessageW(hwnd, 0x007F, size, 0)  # WM_GETICON
                    self.assertTrue(actual)
                    width, height, observed = rendered_icon(actual)
                    expected = user.LoadImageW(None, str(DESKTOP / "assets/brand/aliancadx.ico"),
                                               1, width, height, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
                    self.assertTrue(expected)
                    try:
                        _, _, reference = rendered_icon(expected)
                        self.assertTrue(any(g > 100 and g > r * 1.3 and g > b * 1.3
                                            for b, g, r, _a in zip(reference[0::4], reference[1::4],
                                                                   reference[2::4], reference[3::4])),
                                        "Reference HICON contains no AliançaDX green artwork")
                        self.assertEqual(hashlib.sha256(observed).hexdigest(), hashlib.sha256(reference).hexdigest(),
                                         f"Actual {width}x{height} HICON differs from AliançaDX artwork")
                    finally:
                        user.DestroyIcon(expected)
        finally:
            owner.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
