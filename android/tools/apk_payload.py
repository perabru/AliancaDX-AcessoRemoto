#!/usr/bin/env python3
"""Confirm that required Android classes are defined in an APK's DEX payload.

This reads class_defs, rather than searching strings which can also name absent,
referenced classes. It is a release packaging check, not an Android verifier or
an installation test. No signing key or third-party Python package is required.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
import re
import struct
import zipfile
import zlib


class PayloadError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PayloadError(message)


def defined_classes(data: bytes) -> set[str]:
    """Return descriptors from standard DEX 035/037–040 class definitions."""
    require(len(data) >= 112 and data[:8] in
            {b"dex\n035\0", b"dex\n037\0", b"dex\n038\0", b"dex\n039\0", b"dex\n040\0"},
            "Unsupported or truncated DEX header")

    def uint(offset: int) -> int:
        require(0 <= offset <= len(data) - 4, "DEX integer outside payload")
        return struct.unpack_from("<I", data, offset)[0]

    require(uint(32) == len(data) and uint(36) == 112 and uint(40) == 0x12345678,
            "Invalid DEX size, header, or byte order")
    require(data[12:32] == hashlib.sha1(data[32:]).digest()
            and uint(8) == (zlib.adler32(data[12:]) & 0xFFFFFFFF),
            "DEX signature or checksum mismatch")

    def table(size_offset: int, width: int) -> tuple[int, int]:
        count, offset = uint(size_offset), uint(size_offset + 4)
        require((count == 0 and offset == 0) or
                (offset >= 112 and offset % 4 == 0 and count <= (len(data) - offset) // width),
                "DEX table outside payload")
        return count, offset

    string_count, strings = table(56, 4)
    type_count, types = table(64, 4)
    class_count, classes = table(96, 32)

    def string(index: int) -> str:
        require(index < string_count, "DEX string index outside table")
        offset = uint(strings + index * 4)
        units = 0
        for shift in range(0, 35, 7):
            require(offset < len(data), "Truncated DEX string length")
            byte = data[offset]
            offset += 1
            require(shift < 28 or byte <= 0x0F, "Invalid DEX string length")
            units |= (byte & 0x7F) << shift
            if byte < 0x80:
                break
        else:
            raise PayloadError("Invalid DEX string length")
        end = data.find(b"\0", offset)
        require(end >= offset, "Unterminated DEX string")
        try:
            text = data[offset:end].replace(b"\xc0\x80", b"\0").decode("utf-8", "surrogatepass")
            encoded = text.encode("utf-16-le", "surrogatepass")
            require(len(encoded) // 2 == units, "DEX string UTF-16 length mismatch")
            return encoded.decode("utf-16-le", "surrogatepass")
        except UnicodeError as error:
            raise PayloadError("Invalid DEX modified UTF-8 string") from error

    result = set()
    for index in range(class_count):
        class_index = uint(classes + index * 32)
        require(class_index < type_count, "DEX class index outside type table")
        descriptor = string(uint(types + class_index * 4))
        require(descriptor.startswith("L") and descriptor.endswith(";") and len(descriptor) > 2
                and not any(char in descriptor[1:-1] for char in ".;[()\0"),
                "Invalid defined-class descriptor")
        require(descriptor not in result, "Duplicate DEX class definition")
        result.add(descriptor)
    return result


def inspect_apk_payload(path: Path, required: list[str],
                        package: str = "br/com/perabru/minianydesk") -> dict:
    """Require the named, actually defined classes in the APK's root DEX files."""
    all_classes: set[str] = set()
    inventory = {}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)) and archive.testzip() is None,
                "APK duplicate entries or CRC failure")
        dex_names = [name for name in names if re.fullmatch(r"classes(?:[2-9]|[1-9][0-9]+)?\.dex", name)]
        require("classes.dex" in dex_names, "APK has no root DEX payload")
        for name in sorted(dex_names):
            classes = defined_classes(archive.read(name))
            require(not all_classes.intersection(classes), "Duplicate class across APK DEX files")
            all_classes.update(classes)
            inventory[name] = len(classes)
    require(len(required) == len(set(required)), "Duplicate required class name")
    results = {name: "L" + package + "/" + name + ";" in all_classes for name in required}
    missing = sorted(name for name, present in results.items() if not present)
    require(not missing, "Required classes are not defined in APK DEX: " + ", ".join(missing))
    return {"method": "DEX class_defs/type_ids/string_ids parser",
            "dex_defined_class_counts": inventory, "required_defined_classes": results}


def verify_required_classes(apk: Path, required: Iterable[str]) -> dict:
    """Read-only release API for the AliançaDX Android application namespace."""
    return inspect_apk_payload(apk, list(required))
