"""Read-only PE dependencies and embedded certificate-table metadata.

No executable is loaded. Certificate extraction does not verify Authenticode
digests, timestamps or Windows trust chains. Optional cryptography is used only
to decode the embedded PKCS7 certificate set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import warnings


def inspect_pe(path: Path) -> dict:
    data = path.read_bytes()
    nt = struct.unpack_from("<I", data, 0x3C)[0]
    if data[:2] != b"MZ" or data[nt:nt + 4] != b"PE\0\0":
        raise ValueError("Not a PE file")
    machine, count, _, _, _, optional_size, _ = struct.unpack_from("<HHIIIHH", data, nt + 4)
    optional = nt + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    if magic not in (0x10B, 0x20B):
        raise ValueError("Unsupported PE optional header")
    is64 = magic == 0x20B
    directories = optional + (112 if is64 else 96)
    sections = []
    for index in range(count):
        section = optional + optional_size + 40 * index
        virtual_size, virtual_address, raw_size, pointer = struct.unpack_from("<IIII", data, section + 8)
        sections.append((virtual_address, max(virtual_size, raw_size), pointer))

    def offset(rva: int) -> int:
        for address, size, pointer in sections:
            if address <= rva < address + size:
                return pointer + rva - address
        if rva < struct.unpack_from("<I", data, optional + 60)[0]:
            return rva
        raise ValueError("RVA outside sections")

    def cstring(position: int) -> str:
        return data[position:data.index(b"\0", position)].decode("ascii")

    imports = {}
    import_rva, _ = struct.unpack_from("<II", data, directories + 8)
    if import_rva:
        descriptor = offset(import_rva)
        while True:
            original, stamp, chain, name, thunk = struct.unpack_from("<IIIII", data, descriptor)
            descriptor += 20
            if not any((original, stamp, chain, name, thunk)):
                break
            position = offset(original or thunk)
            width, fmt = (8, "<Q") if is64 else (4, "<I")
            ordinal_bit = 1 << (width * 8 - 1)
            functions = []
            while True:
                value = struct.unpack_from(fmt, data, position)[0]
                position += width
                if not value:
                    break
                functions.append("#" + str(value & 0xFFFF) if value & ordinal_bit else cstring(offset(value) + 2))
            imports.setdefault(cstring(offset(name)), []).extend(functions)

    exports = []
    export_rva, _ = struct.unpack_from("<II", data, directories)
    if export_rva:
        table = offset(export_rva)
        name_count = struct.unpack_from("<I", data, table + 24)[0]
        names_rva = struct.unpack_from("<I", data, table + 32)[0]
        for index in range(name_count):
            exports.append(cstring(offset(struct.unpack_from("<I", data, offset(names_rva) + 4 * index)[0])))

    security_offset, security_size = struct.unpack_from("<II", data, directories + 4 * 8)
    delay_rva, delay_size = struct.unpack_from("<II", data, directories + 13 * 8)
    certificate_entries = []
    position = security_offset
    while position and position < security_offset + security_size:
        length, revision, kind = struct.unpack_from("<IHH", data, position)
        if length < 8 or position + length > security_offset + security_size:
            raise ValueError("Invalid WIN_CERTIFICATE bounds")
        entry = {"length": length, "revision": hex(revision), "type": kind}
        if kind == 2:
            try:
                from cryptography.hazmat.primitives.serialization import pkcs7
                with warnings.catch_warnings(record=True) as caught:
                    certificates = pkcs7.load_der_pkcs7_certificates(data[position + 8:position + length])
                entry["decoder_warnings"] = [str(w.message) for w in caught]
                entry["certificates"] = [{
                    "subject": cert.subject.rfc4514_string(),
                    "issuer": cert.issuer.rfc4514_string(),
                    "serial": format(cert.serial_number, "x"),
                    "not_before": str(cert.not_valid_before_utc),
                    "not_after": str(cert.not_valid_after_utc),
                } for cert in certificates]
            except ImportError:
                entry["certificate_decoder"] = "cryptography not installed"
        certificate_entries.append(entry)
        position += (length + 7) & ~7
    return {
        "filename": path.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        "machine": hex(machine), "ordinary_imports": imports, "named_exports": exports,
        "delay_import_directory": {"rva": delay_rva, "size": delay_size},
        "security_directory": {"file_offset": security_offset, "size": security_size},
        "embedded_certificates": certificate_entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = [inspect_pe(args.bundle / name) for name in ("easytier-core.exe", "Packet.dll")]
    core, packet = results
    required_packet = next((functions for dll, functions in core["ordinary_imports"].items()
                            if dll.casefold() == "packet.dll"), [])
    report = {
        "method": "Read-only PE headers, import/export tables and PKCS7 certificate extraction; no Windows execution",
        "authenticode_digest_or_windows_chain_verified": False,
        "binaries": results,
        "required_packet_exports_satisfied": set(required_packet).issubset(packet["named_exports"]),
        "core_imports_packet_at_load_time": bool(required_packet),
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"binary_count": len(results), "packet_ordinary_imports": len(required_packet),
                      "packet_exports_satisfied": report["required_packet_exports_satisfied"]}))


if __name__ == "__main__":
    main()
