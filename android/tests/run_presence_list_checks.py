"""Compile and run the saved-device polling state checks without Android.

Usage: python3 tests/run_presence_list_checks.py /path/to/gradle/lib output.jar
With no arguments, use the development workspace's installed Kotlin toolchain.
"""
from pathlib import Path
import os
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
if len(sys.argv) == 1:
    workspace = PROJECT.parent
    if not (workspace / "android_build").is_dir():
        workspace = workspace.parent
    lib = workspace / "android_build/gradle/gradle-8.11.1/lib"
    output = workspace / "android_build/presence-list-checks.jar"
elif len(sys.argv) == 3:
    lib, output = (Path(value).resolve() for value in sys.argv[1:])
else:
    raise SystemExit(__doc__)

stdlib = next(lib.glob("kotlin-stdlib-[0-9]*.jar"))
output.parent.mkdir(parents=True, exist_ok=True)
subprocess.run([
    "java", "-cp", str(lib / "*"), "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
    "-no-stdlib", "-no-reflect", "-jvm-target", "17", "-classpath", str(stdlib),
    str(PROJECT / "app/src/main/java/br/com/perabru/minianydesk/PresenceListModel.kt"),
    str(PROJECT / "tests/PresenceListModelHarness.kt"), "-d", str(output),
], check=True)
subprocess.run([
    "java", "-cp", os.pathsep.join(map(str, (output, stdlib))),
    "br.com.perabru.minianydesk.PresenceListModelHarness",
], check=True)
