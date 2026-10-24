"""Run pure production traffic-window and hide/recovery policy checks on JVM.

Usage: python3 tests/run_session_presentation_checks.py KOTLIN_LIB OUTPUT_JAR
No network speed test, Android rendering, emulator or physical-device claim.
"""
from pathlib import Path
import os
import subprocess
import sys

if len(sys.argv) != 3:
    raise SystemExit(__doc__)
project = Path(__file__).resolve().parents[1]
lib, output = (Path(value).resolve() for value in sys.argv[1:])
stdlib = next(lib.glob("kotlin-stdlib-[0-9]*.jar"))
output.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(["java", "-cp", str(lib / "*"), "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
    "-no-stdlib", "-no-reflect", "-jvm-target", "17", "-classpath", str(stdlib),
    str(project / "app/src/main/java/br/com/perabru/minianydesk/SessionPresentation.kt"),
    str(project / "tests/SessionPresentationHarness.kt"), "-d", str(output)], check=True)
subprocess.run(["java", "-cp", os.pathsep.join(map(str, (output, stdlib))),
    "br.com.perabru.minianydesk.SessionPresentationHarness"], check=True)
