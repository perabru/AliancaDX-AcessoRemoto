"""Portable controller-only regressions against an sibling Desktop6.2 checkout.

python tests/port_6_1/run_controller_interop.py --desktop ../desktop \
    --kotlin-lib /path/gradle-8.11.1/lib --json-jar /path/json-20240303.jar --output /tmp/port-qa

Requires JDK17+, Python3.11+, Pillow and cryptography. Uses local TCP/TLS sockets and
JVM Android-provider doubles. It does not test an Android device or a public relay.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import unittest

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
TESTS = PROJECT / "tests"
SOURCES = PROJECT / "app/src/main/java/br/com/perabru/minianydesk"
EXCLUDED_HOST_ROLE = {
    "test_python_controller_android_host_with_busy_denial_and_reconnect",
    "test_android_host_stop_restart_reuses_port_and_drops_peer",
    "test_python_probe_android_host_name_no_auth_and_failed_counter_preserved",
    "test_python_probe_android_active_session_live_rename_and_echo",
    "test_python_probe_android_pending_approval_stays_busy",
    "test_python_probe_api26_android_host_is_legacy_without_auth",
}


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite): yield from flatten(item)
        else: yield item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desktop", default=PROJECT.parent / "desktop", type=Path)
    parser.add_argument("--kotlin-lib", required=True, type=Path)
    parser.add_argument("--json-jar", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--java", default="java")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name in ("net.py", "transfer.py", "host.py", "mesh.py"):
        if not (args.desktop / name).is_file(): parser.error(f"Missing Desktop source: {name}")
    gradle = (PROJECT / "app/build.gradle.kts").read_text()
    android_version = re.search(r'versionName\s*=\s*"([^\"]+)"', gradle).group(1)
    desktop_versions = {}
    for name in ("host.py", "controller.py", "mesh.py"):
        module = ast.parse((args.desktop / name).read_text())
        assignment = next(node for node in module.body if isinstance(node, ast.Assign) and
                          any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets))
        desktop_versions[name] = ast.literal_eval(assignment.value)
    if any(version != android_version for version in desktop_versions.values()):
        parser.error(f"Application versions must match for this current-release run: Android={android_version}, Desktop={desktop_versions}")
    stdlib = next(args.kotlin_lib.glob("kotlin-stdlib-[0-9]*.jar"))
    common = [TESTS / "headless/android/os/Build.kt", TESTS / "headless/android/annotation/TargetApi.kt"]
    android_stubs = sorted((TESTS / "headless/android").rglob("*.kt"))
    android_net = SOURCES / "AndroidNet.kt"
    builds = {
        "TRANSPORT": [android_net, *common, TESTS / "TransportHarness.kt"],
        "TRANSFER": [android_net, SOURCES / "FileTransfers.kt", *sorted((TESTS / "headless").rglob("*.kt"))],
        "PEER_STABILITY": [android_net, *common, TESTS / "PeerStabilityHarness.kt", TESTS / "ConnectionDeadlineHarness.kt"],
        "PRESENCE": [android_net, SOURCES / "PresenceProbe.kt", *common, TESTS / "PresenceHarness.kt"],
        "PORT": [android_net, SOURCES / "FileTransfers.kt", SOURCES / "ScreenGeometry.kt", *android_stubs, HERE / "DesktopPortHarness.kt"],
        "PAIRING": [SOURCES / "MeshPairing.kt", HERE / "MeshPairingHarness.kt"],
    }
    for key, files in builds.items():
        output = args.output / (key.lower() + ".jar")
        subprocess.run([args.java, "-Xmx512m", "-cp", str(args.kotlin_lib / "*"),
            "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler", "-no-stdlib", "-no-reflect", "-jvm-target", "17",
            "-classpath", os.pathsep.join(map(str, (stdlib, args.json_jar))), "-d", str(output), *map(str, files)], check=True)
        os.environ[f"MINIANYDESK_{key}_CLASSPATH"] = os.pathsep.join(map(str, (output, args.json_jar, stdlib)))
    os.environ["MINIANYDESK_PYTHON_SOURCE"] = str(args.desktop.resolve())
    os.environ["MINIANYDESK_PRESENCE_REFERENCE"] = str(args.desktop.resolve())
    os.environ["MINIANYDESK_EXPECTED_APP_VERSION"] = android_version
    sys.path.insert(0, str(args.desktop.resolve()))
    files = [TESTS / name for name in ("test_android_transport.py", "test_transfer_interop.py",
             "test_peer_stability.py", "test_presence_interop.py")]
    files += [HERE / "test_desktop61_port.py", HERE / "test_pairing_vectors.py"]
    suite = unittest.TestSuite()
    for index, path in enumerate(files):
        spec = importlib.util.spec_from_file_location(f"controller_interop_{index}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for test in flatten(unittest.defaultTestLoader.loadTestsFromModule(module)):
            if test.id().rsplit(".", 1)[-1] not in EXCLUDED_HOST_ROLE: suite.addTest(test)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    log = stream.getvalue()
    (args.output / "controller-interop.log").write_text(log)
    print(log)
    production_sources = sorted({path for files in builds.values() for path in files if path.parent == SOURCES})
    evidence_files = [*production_sources, *(args.desktop / name for name in ("net.py", "transfer.py", "host.py", "mesh.py"))]
    hashes = {str(path.relative_to(PROJECT)) if path.is_relative_to(PROJECT) else "Desktop/" + path.name:
              hashlib.sha256(path.read_bytes()).hexdigest() for path in evidence_files}
    report = {"scope": f"Current Android Controller {android_version} production Kotlin + Python Desktop {android_version}; real local TLS; JVM Android doubles",
        "android_app_version": android_version, "desktop_app_versions": desktop_versions,
        "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
        "passed": result.wasSuccessful() and not result.skipped, "excluded_legacy_android_host_role_tests": len(EXCLUDED_HOST_ROLE),
        "android_runtime_tested": False, "physical_device_tested": False, "public_relays_tested": False, "cross_isp_tested": False,
        "source_sha256": hashes}
    (args.output / "controller-interop-report.json").write_text(json.dumps(report, indent=2) + "\n")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__": main()
