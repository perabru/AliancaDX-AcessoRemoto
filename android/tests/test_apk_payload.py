"""Regression checks for stale DEX packaging and false string-only evidence."""
import hashlib
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile
import zlib


TOOL = Path(__file__).resolve().parents[1] / "tools/apk_payload.py"
SPEC = importlib.util.spec_from_file_location("apk_payload", TOOL)
payload = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(payload)


def seal(data):
    struct.pack_into("<I", data, 32, len(data))
    data[12:32] = hashlib.sha1(data[32:]).digest()
    struct.pack_into("<I", data, 8, zlib.adler32(data[12:]) & 0xFFFFFFFF)
    return bytes(data)


def fixture():
    # Only Real is defined. OnlyReferenced exists solely in the string pool.
    data = bytearray(156)
    data[:8] = b"dex\n035\0"
    for offset, value in [(36, 112), (40, 0x12345678), (56, 2), (60, 112),
                          (64, 1), (68, 120), (96, 1), (100, 124)]:
        struct.pack_into("<I", data, offset, value)
    for index, descriptor in enumerate([b"Lexample/Real;", b"Lexample/OnlyReferenced;"]):
        struct.pack_into("<I", data, 112 + index * 4, len(data))
        data += bytes([len(descriptor)]) + descriptor + b"\0"
    return seal(data)


class PayloadTests(unittest.TestCase):
    def test_only_class_definitions_count(self):
        self.assertEqual(payload.defined_classes(fixture()), {"Lexample/Real;"})

    def test_referenced_string_cannot_satisfy_release_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.apk"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("classes.dex", fixture())
            result = payload.inspect_apk_payload(path, ["Real"], "example")
            self.assertEqual(result["required_defined_classes"], {"Real": True})
            with self.assertRaisesRegex(payload.PayloadError, "not defined.*OnlyReferenced"):
                payload.inspect_apk_payload(path, ["OnlyReferenced"], "example")

    def test_out_of_bounds_class_index_is_rejected(self):
        data = bytearray(fixture())
        struct.pack_into("<I", data, 124, 1)
        with self.assertRaisesRegex(payload.PayloadError, "class index"):
            payload.defined_classes(seal(data))

    def test_out_of_bounds_table_is_rejected(self):
        data = bytearray(fixture())
        struct.pack_into("<I", data, 96, 100)
        with self.assertRaisesRegex(payload.PayloadError, "table outside"):
            payload.defined_classes(seal(data))

    def test_checksum_tampering_is_rejected(self):
        data = bytearray(fixture())
        data[-2] ^= 1
        with self.assertRaisesRegex(payload.PayloadError, "checksum"):
            payload.defined_classes(bytes(data))

    def test_duplicate_multidex_class_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.apk"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("classes.dex", fixture())
                archive.writestr("classes2.dex", fixture())
            with self.assertRaisesRegex(payload.PayloadError, "Duplicate class across"):
                payload.inspect_apk_payload(path, ["Real"], "example")


if __name__ == "__main__":
    unittest.main()
