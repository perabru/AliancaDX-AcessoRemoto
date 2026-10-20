"""Compile and run production recovery rules with Kotlin/JVM, without Android.

Usage: python3 tests/run_recovery_checks.py [TOOLS_DIRECTORY]
TOOLS_DIRECTORY follows tests/viewport/run_viewport_checks.py and contains
Gradle 8.11.1 and optionally jdk-17. Evidence is written under tests/recovery/.
This checks pure recovery rules, not device reconnection or a real VPN.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

project = Path(__file__).resolve().parents[1]
tools = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else project.parents[1] / "android_port_tools"
if len(sys.argv) > 2:
    raise SystemExit(__doc__)
lib = tools / "gradle-8.11.1/lib"
stdlib = next(lib.glob("kotlin-stdlib-[0-9]*.jar"), None)
annotations = next(lib.glob("annotations-[0-9]*.jar"), None)
if stdlib is None or annotations is None:
    raise SystemExit("Kotlin compiler dependencies were not found: " + str(lib))
java = tools / "jdk-17/bin/java"
java_cmd = str(java) if java.is_file() else "java"
production = project / "app/src/main/java/br/com/perabru/minianydesk/ConnectionRecovery.kt"
harness = project / "tests/RecoveryHarness.kt"
output = project / "tests/recovery"
output.mkdir(parents=True, exist_ok=True)
classpath = os.pathsep.join(map(str, (stdlib, annotations)))
report = {
    "scope": "Production ConnectionRecovery rules and existing RecoveryHarness on a real JVM; no Android or network",
    "passed": False,
    "harnesses": 1,
    "check_expressions": len(re.findall(r"\bcheck\s*\(", harness.read_text())),
    "android_runtime_tested": False,
    "real_reconnection_tested": False,
    "source_sha256": {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (production, harness)},
}
logs = []
try:
    with tempfile.TemporaryDirectory(prefix="recovery-checks-") as temporary:
        jar = Path(temporary) / "recovery.jar"
        commands = [
            [java_cmd, "-Xmx256m", "-cp", str(lib / "*"), "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
             "-no-stdlib", "-no-reflect", "-jvm-target", "17", "-classpath", classpath,
             str(production), str(harness), "-d", str(jar)],
            [java_cmd, "-Xmx128m", "-cp", os.pathsep.join((str(jar), classpath)),
             "br.com.perabru.minianydesk.RecoveryHarnessKt"],
        ]
        for command in commands:
            result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
            logs.append(result.stdout)
            if result.returncode:
                report["failed_command"] = "compiler" if command == commands[0] else "harness"
                report["returncode"] = result.returncode
                break
        else:
            report["passed"] = True
except (OSError, subprocess.TimeoutExpired) as error:
    report["error_type"] = type(error).__name__
finally:
    (output / "checks.log").write_text("".join(logs))
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
raise SystemExit(0 if report["passed"] else 1)
