"""Audit the shipped Windows runtime without loading a PE or making network requests."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

from pe_static_audit import inspect_pe


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def protocol_nodes(source: bytes) -> dict[str, str]:
    tree = ast.parse(source)
    names = {"_port", "_fingerprint", "_derive", "HostPairing", "generate_pairing",
             "parse_pairing", "pairing_public_id", "build_config"}
    found = {node.name: ast.dump(node, include_attributes=False)
             for node in tree.body if getattr(node, "name", None) in names}
    manager = next(node for node in tree.body if getattr(node, "name", None) == "MeshManager")
    observe = next(node for node in manager.body if getattr(node, "name", None) == "_observe")
    found["MeshManager._observe"] = ast.dump(observe, include_attributes=False)
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--original-project-zip", type=Path)
    parser.add_argument("--official-runtime-zip", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project = args.project.resolve()
    bundle = project / "easytier_bundle"
    encoded = (bundle / "manifest.json").read_bytes()
    manifest = json.loads(encoded)
    files = {}
    for item in manifest["files"]:
        data = (bundle / item["filename"]).read_bytes()
        files[item["filename"]] = len(data) == item["size"] and digest(data) == item["sha256"]
    assert all(files.values()), "Runtime hash mismatch"
    assert set(files) | {"manifest.json"} == {p.name for p in bundle.iterdir()}, "Unlisted runtime payload"
    assert encoded == (project / "installer/easytier_bundle_manifest.json").read_bytes()
    assert "easytier-cli.exe" not in files
    core = inspect_pe(bundle / "easytier-core.exe")
    packet = inspect_pe(bundle / "Packet.dll")
    required = next(v for k, v in core["ordinary_imports"].items() if k.casefold() == "packet.dll")
    assert len(required) == 11 and set(required).issubset(packet["named_exports"])
    assert core["delay_import_directory"] == {"rva": 0, "size": 0}
    provenance = json.loads((bundle / "PROVENANCE.json").read_bytes())
    assert core["sha256"] == provenance["runtime_upstream"]["unmodified_executables"]["easytier-core.exe"]
    assert packet["sha256"] == provenance["packet_dll"]["replacement_sha256"]
    report = {
        "scope": "Static PE and local pinned payload inspection; Python unit tests; no Windows execution or antivirus scan",
        "network_requests_by_this_runner": False,
        "all_10_manifest_entries_verified": len(files) == 10 and all(files.values()),
        "manifest_copies_identical": True,
        "manifest_sha256": digest(encoded),
        "file_integrity": files,
        "unused_cli_omitted": True,
        "omitted_executable_bytes": 9973248,
        "core_sha256": core["sha256"],
        "packet_sha256": packet["sha256"],
        "ordinary_packet_imports": required,
        "packet_exports_satisfy_all_imports": True,
        "core_delay_import_directory_empty": True,
        "core_embedded_authenticode_table_present": bool(core["security_directory"]["size"]),
        "packet_embedded_certificate_table_present": bool(packet["security_directory"]["size"]),
        "authenticode_digest_or_windows_trust_chain_verified": False,
        "packet_reextracted_from_official_installer": False,
        "protocol_source_sha256": digest((project / "mesh.py").read_bytes()),
    }
    if args.original_project_zip:
        with zipfile.ZipFile(args.original_project_zip) as original:
            name = next(n for n in original.namelist() if n.endswith("/mesh.py") or n == "mesh.py")
            old = protocol_nodes(original.read(name))
        new = protocol_nodes((project / "mesh.py").read_bytes())
        unchanged = {name: new.get(name) == value for name, value in old.items()}
        assert len(unchanged) == 9 and all(unchanged.values())
        report["protocol_nodes_unchanged_from_6_1_0"] = unchanged
    if args.official_runtime_zip:
        upstream = provenance["runtime_upstream"]
        assert digest(args.official_runtime_zip.read_bytes()) == upstream["sha256"]
        with zipfile.ZipFile(args.official_runtime_zip) as official:
            name = next(n for n in official.namelist() if n.endswith("/easytier-core.exe") or n == "easytier-core.exe")
            assert digest(official.read(name)) == core["sha256"]
        report["official_zip_sha256_verified"] = upstream["sha256"]
        report["core_byte_identical_to_official_zip"] = True
    checks = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_mesh.py", "-v"],
                            cwd=project, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    log_path = args.output.with_suffix(".log")
    log_path.write_text(checks.stdout)
    report["mesh_unit_tests_exit_code"] = checks.returncode
    report["mesh_unit_tests_log"] = log_path.name
    report["passed"] = checks.returncode == 0
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "manifest_entries": len(files), "packet_imports": len(required)}))
    raise SystemExit(checks.returncode)


if __name__ == "__main__":
    main()
