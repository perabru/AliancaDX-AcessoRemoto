"""Compile production AndroidNet and extra TCP/TLS/geometry tests.

Usage: python3 tests/compile_transport_audit.py /path/gradle/lib /path/json.jar output.jar
With no arguments, use the development workspace's android_build tools.
"""
from pathlib import Path
import subprocess
import sys
import os

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
if not (ROOT / "android_build").is_dir():
    ROOT = ROOT.parent
if len(sys.argv) == 1:
    LIB = ROOT / "android_build/gradle/gradle-8.11.1/lib"
    JSON = ROOT / "android_build/json-20240303.jar"
    OUTPUT = ROOT / "android_validation_3_2/transport-audit.jar"
elif len(sys.argv) == 4:
    LIB, JSON, OUTPUT = map(lambda argument: Path(argument).resolve(), sys.argv[1:])
else:
    raise SystemExit(__doc__)
stdlib = next(LIB.glob("kotlin-stdlib-[0-9]*.jar"))
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(["java", "-cp", str(LIB / "*"),
    "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler", "-no-stdlib", "-no-reflect",
    "-jvm-target", "17", "-classpath", os.pathsep.join(map(str, (stdlib, JSON))),
    str(PROJECT / "app/src/main/java/br/com/perabru/minianydesk/AndroidNet.kt"),
    str(PROJECT / "app/src/main/java/br/com/perabru/minianydesk/ScreenGeometry.kt"),
    str(PROJECT / "tests/headless/android/os/Build.kt"),
    str(PROJECT / "tests/headless/android/annotation/TargetApi.kt"),
    str(PROJECT / "tests/TransportAuditHarness.kt"),
    str(PROJECT / "tests/GeometryHarness.kt"),
    "-d", str(OUTPUT)], check=True)
print(os.pathsep.join(map(str, (OUTPUT, JSON, stdlib))))
