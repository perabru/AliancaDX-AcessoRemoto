"""Run the synthetic Android native transport lab; requires an Android emulator, never a phone.

Start the emulator, install the Debug and androidTest APKs, then run this driver in
the same host/network namespace as the emulator and its ADB server. The Linux
EasyTier2.6.4 core is a separate official test dependency, not shipped in the APK.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import zipfile

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desktop", type=Path, required=True)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--adb", type=Path, required=True)
    parser.add_argument("--serial", default="emulator-5554")
    parser.add_argument("--target-package", default="br.com.perabru.minianydesk.controlador.debug",
                        help="Installed Debug application package")
    parser.add_argument("--test-package", help="Installed instrumentation package; defaults to TARGET_PACKAGE.test")
    parser.add_argument("--target-apk", type=Path,
                        default=PROJECT / "app/build/outputs/apk/controlador/debug/app-controlador-debug.apk",
                        help="Local Debug APK whose hash must match the installed target")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=280)
    parser.add_argument("--adb-reverse", action="store_true", help="Use a labelled ADB loopback TCP carrier for the local relay")
    args = parser.parse_args()
    test_package = args.test_package or args.target_package + ".test"
    for package in (args.target_package, test_package):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+", package):
            parser.error("Invalid Android package name")
    if not args.target_apk.is_file(): parser.error("Build or specify the local Debug target APK first")
    if not args.serial.startswith("emulator-"): parser.error("Only a synthetic Android emulator is accepted")
    adb = [str(args.adb), "-s", args.serial]
    qemu = subprocess.run([*adb, "shell", "getprop", "ro.kernel.qemu"], capture_output=True, text=True, timeout=15)
    if qemu.returncode or qemu.stdout.strip() != "1": parser.error("The selected target is not a running Android emulator")
    args.output.mkdir(parents=True, exist_ok=True)
    ready, stop, linux_report = [args.output / name for name in ("private-input.json", "stop", "linux-lab-report.json")]
    for path in (ready, stop, linux_report): path.unlink(missing_ok=True)
    env = os.environ.copy()
    env["MINIANYDESK_PYTHON_SOURCE"] = str(args.desktop.resolve())
    env["MINIANYDESK_LINUX_CORE"] = str(args.core.resolve())
    with (args.output / "linux-lab.log").open("w") as log:
        lab = subprocess.Popen([sys.executable, str(HERE / "native_lab.py"), "--ready", str(ready),
            "--stop", str(stop), "--report", str(linux_report)], stdout=log, stderr=subprocess.STDOUT, env=env)
    instrument = None
    reverse_rule = None
    try:
        deadline = time.monotonic() + 25
        while not ready.exists() and time.monotonic() < deadline:
            if lab.poll() is not None: raise RuntimeError("Linux lab exited; inspect linux-lab.log")
            time.sleep(.25)
        if not ready.exists(): raise RuntimeError("The local relay did not start")
        inputs = json.loads(ready.read_text())
        carrier = "emulator_guest_tcp"
        if args.adb_reverse:
            port = inputs["relay"].rsplit(":", 1)[1]
            require_port = int(port)
            if not 1 <= require_port <= 65535: raise RuntimeError("Invalid synthetic relay port")
            reverse_rule = "tcp:" + port
            subprocess.run([*adb, "reverse", reverse_rule, reverse_rule], capture_output=True, check=True, timeout=20)
            inputs["relay"] = "tcp://127.0.0.1:" + port
            carrier = "adb_reverse_local_tcp"
        instrumentation_log = args.output / "instrumentation.log"
        with instrumentation_log.open("w") as log:
            instrument = subprocess.Popen([*adb, "shell", "am", "instrument", "-w", "-r",
                "-e", "pairing", inputs["pairing"], "-e", "relay", inputs["relay"], "-e", "carrier", carrier,
                test_package + "/br.com.perabru.minianydesk.PortInteropInstrumentation"],
                stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + args.timeout
        observed = set()
        while instrument.poll() is None and time.monotonic() < deadline:
            for name in re.findall(r"^INSTRUMENTATION_STATUS: stage=(.+)$", instrumentation_log.read_text(errors="replace"), re.MULTILINE):
                if name not in observed: print(name, flush=True); observed.add(name)
            time.sleep(.5)
        if instrument.poll() is None:
            subprocess.run([*adb, "shell", "am", "force-stop", args.target_package], timeout=15)
            instrument.terminate()
            raise RuntimeError("Instrumentation exceeded its bounded deadline")
        output = instrumentation_log.read_text()
        match = re.search(r"^INSTRUMENTATION_RESULT: report=(\{.*\})$", output, re.MULTILINE)
        if not match: raise RuntimeError("Instrumentation returned no structured report; inspect instrumentation.log")
        report = json.loads(match.group(1))
    finally:
        stop.touch()
        try: lab.wait(12)
        except subprocess.TimeoutExpired: lab.terminate(); lab.wait(5)
        ready.unlink(missing_ok=True)
        if reverse_rule:
            subprocess.run([*adb, "reverse", "--remove", reverse_rule], capture_output=True, timeout=20)
    report["scenarios_passed"] = report.pop("passed", [])
    report["file_provider_scope"] = ("Real Android ContentResolver.wrap and file descriptors with a local synthetic ContentProvider; "
        "document-URI classification is fixture setup; no system-provider, Binder-discovery, or SAF-picker I/O")
    report["linux_host_and_relay"] = json.loads(linux_report.read_text())
    report["passed"] = report.get("result") == "passed" and all(report["linux_host_and_relay"].get(key) is True
        for key in ("commands_exact", "android_to_python_file_exact", "python_to_android_file_protocol_done"))
    report["source_sha256"] = {}
    for name in ("AndroidNet.kt", "FileTransfers.kt", "MeshPairing.kt", "MeshTransport.kt", "MeshCodeVault.kt"):
        path = PROJECT / "app/src/main/java/br/com/perabru/minianydesk" / name
        report["source_sha256"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    report["instrumentation_source_sha256"] = {name: hashlib.sha256((PROJECT /
        "app/src/androidTest/java/br/com/perabru/minianydesk" / name).read_bytes()).hexdigest()
        for name in ("PortInteropInstrumentation.kt", "PortTestDocuments.kt")}
    report["desktop_source_sha256"] = {name: hashlib.sha256((args.desktop / name).read_bytes()).hexdigest()
        for name in ("net.py", "host.py", "transfer.py", "mesh.py")}
    report["linux_core_sha256"] = hashlib.sha256(args.core.read_bytes()).hexdigest()
    report["prebuilt_input_native_sha256"] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (PROJECT / "app/src/main/jniLibs/x86_64").glob("*.so")}
    report["installed_apk_sha256"] = {}
    report["target_package"] = args.target_package
    report["test_package"] = test_package
    for package in (args.target_package, test_package):
        paths = subprocess.run([*adb, "shell", "pm", "path", package], capture_output=True, text=True, timeout=20)
        apk = next((line.removeprefix("package:").strip() for line in paths.stdout.splitlines()
                    if line.startswith("package:") and line.endswith("base.apk")), None)
        if apk:
            digest = subprocess.run([*adb, "shell", "sha256sum", apk], capture_output=True, text=True, timeout=30)
            value = digest.stdout.split()[0] if digest.stdout.split() else ""
            if re.fullmatch(r"[0-9a-f]{64}", value): report["installed_apk_sha256"][package] = value
    apk_path = args.target_apk
    report["verified_local_target_apk_sha256"] = hashlib.sha256(apk_path.read_bytes()).hexdigest()
    report["local_target_matches_installed"] = report["verified_local_target_apk_sha256"] == report["installed_apk_sha256"].get(
        args.target_package)
    if not report["local_target_matches_installed"]: report["passed"] = False
    if report["local_target_matches_installed"]:
        with zipfile.ZipFile(apk_path) as apk:
            report["packaged_native_sha256"] = {name.rsplit("/", 1)[-1]: hashlib.sha256(apk.read(name)).hexdigest()
                for name in apk.namelist() if name.startswith("lib/x86_64/") and name.endswith(".so")}
    (args.output / "native-runtime-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "scenarios": len(report["scenarios_passed"])}))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__": main()
