"""Windows taskbar identity shared by the apps and their installed shortcuts.

Python remains the unmodified, official interpreter. The Shell receives a
stable application ID instead of identifying each app as the Python runtime.
No administrator privileges, registry policy changes or extra packages are used.
"""
from __future__ import annotations

import argparse
import ctypes
from pathlib import Path
import sys
import uuid


APP_IDS = {
    "controller": "AliancaDX.AcessoRemoto.Controller",
    "host": "AliancaDX.AcessoRemoto.Host",
    "connector": "AliancaDX.AcessoRemoto.Connector",
    "installer": "AliancaDX.AcessoRemoto.Installer",
    "updater": "AliancaDX.AcessoRemoto.Updater",
}


def configure_process_identity(role: str) -> bool:
    """Call before creating Tk, including a hidden owner or error dialog.

    IDs deliberately omit the version, so upgrades and multiple Controller
    windows remain grouped with the same pinned shortcut.
    """
    app_id = APP_IDS[role]
    if sys.platform != "win32":
        return False
    try:
        setter = ctypes.WinDLL("shell32", use_last_error=True).SetCurrentProcessExplicitAppUserModelID
        setter.argtypes = [ctypes.c_wchar_p]
        setter.restype = ctypes.c_int32
        return setter(app_id) >= 0
    except (AttributeError, OSError):
        # Identity is cosmetic; a damaged Shell must never prevent attendance.
        return False


class _GUID(ctypes.Structure):
    _fields_ = [("data", ctypes.c_ubyte * 16)]

    def __init__(self, value: str):
        super().__init__((ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le))


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", ctypes.c_uint32)]


class _VALUE(ctypes.Union):
    # PROPVARIANT's counted-array member is two pointer-sized slots on x64
    # (DWORD count, padding, pointer), and two 32-bit slots on x86.
    _fields_ = [("text", ctypes.c_void_p), ("storage", ctypes.c_void_p * 2)]


class _PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_uint16), ("reserved1", ctypes.c_uint16),
                ("reserved2", ctypes.c_uint16), ("reserved3", ctypes.c_uint16),
                ("value", _VALUE)]


_APP_ID_KEY = _PROPERTYKEY(_GUID("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5)
_PROPERTY_STORE_IID = _GUID("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")


def _checked(result: int, operation: str) -> None:
    if result < 0:
        raise OSError(f"{operation} falhou (HRESULT 0x{result & 0xffffffff:08x}).")


def set_shortcut_identity(path: Path, role: str) -> None:
    """Set and verify the AppID on one newly created .lnk, without changing it.

    The icon, executable, arguments and working directory remain those already
    created by the installer. The link and process must share the same AppID to
    avoid a second Python button when a user pins the application.
    """
    app_id = APP_IDS[role]
    path = Path(path)
    if sys.platform != "win32":
        raise OSError("A identidade dos atalhos requer Windows.")
    if path.suffix.lower() != ".lnk" or not path.is_file() or path.is_symlink():
        raise OSError("Atalho AliançaDX ausente ou inválido.")
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole.CoInitializeEx.restype = ctypes.c_int32
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    ole.PropVariantClear.argtypes = [ctypes.POINTER(_PROPVARIANT)]
    ole.PropVariantClear.restype = ctypes.c_int32
    initialize = ole.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    # The installer worker may already belong to another COM apartment.
    initialized_here = initialize >= 0
    if not initialized_here and initialize != -2147417850:  # RPC_E_CHANGED_MODE
        _checked(initialize, "Inicialização da identidade Windows")
    store = ctypes.c_void_p()
    release = None
    try:
        getter = shell.SHGetPropertyStoreFromParsingName
        getter.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32,
                           ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        getter.restype = ctypes.c_int32
        _checked(getter(str(path.resolve()), None, 2, ctypes.byref(_PROPERTY_STORE_IID), ctypes.byref(store)),
                 "Abertura das propriedades do atalho")  # GPS_READWRITE
        if not store.value:
            raise OSError("O Windows não retornou as propriedades do atalho.")
        vtable = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        factory = ctypes.WINFUNCTYPE
        release = factory(ctypes.c_uint32, ctypes.c_void_p)(vtable[2])
        get_value = factory(ctypes.c_int32, ctypes.c_void_p, ctypes.POINTER(_PROPERTYKEY),
                            ctypes.POINTER(_PROPVARIANT))(vtable[5])
        set_value = factory(ctypes.c_int32, ctypes.c_void_p, ctypes.POINTER(_PROPERTYKEY),
                            ctypes.POINTER(_PROPVARIANT))(vtable[6])
        commit = factory(ctypes.c_int32, ctypes.c_void_p)(vtable[7])
        text = ctypes.create_unicode_buffer(app_id)
        value = _PROPVARIANT()
        value.vt = 31  # VT_LPWSTR; Python owns text for the synchronous SetValue.
        value.value.text = ctypes.cast(text, ctypes.c_void_p).value
        _checked(set_value(store, ctypes.byref(_APP_ID_KEY), ctypes.byref(value)), "Identificação do atalho")
        _checked(commit(store), "Gravação da identidade do atalho")
        observed = _PROPVARIANT()
        try:
            _checked(get_value(store, ctypes.byref(_APP_ID_KEY), ctypes.byref(observed)), "Conferência do atalho")
            if observed.vt != 31 or not observed.value.text or ctypes.wstring_at(observed.value.text) != app_id:
                raise OSError("O Windows não confirmou a identidade do atalho.")
        finally:
            ole.PropVariantClear(ctypes.byref(observed))
    finally:
        if release is not None:
            release(store)
        if initialized_here:
            ole.CoUninitialize()


def main(argv=None) -> int:
    configure_process_identity("installer")
    parser = argparse.ArgumentParser(description="Identificar atalhos AliançaDX na barra de tarefas Windows")
    parser.add_argument("--role", required=True, choices=("controller", "host"))
    parser.add_argument("--shortcut", action="append", type=Path, default=[])
    parser.add_argument("--connector-shortcut", action="append", type=Path, default=[])
    args = parser.parse_args(argv)
    try:
        for path in args.shortcut:
            set_shortcut_identity(path, args.role)
        for path in args.connector_shortcut:
            set_shortcut_identity(path, "connector")
        print("Identidade e ícones AliançaDX dos atalhos verificados.")
        return 0
    except (OSError, ValueError, AttributeError) as error:
        print("Não foi possível identificar o atalho: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
