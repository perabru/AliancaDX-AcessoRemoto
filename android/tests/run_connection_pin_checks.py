"""Compile and run production pin migration and actual ComputerBook normalization.

Usage: python3 tests/run_connection_pin_checks.py GRADLE_LIB ANDROID_JAR JSON_JAR OUTPUT_JAR
With no arguments, use the development workspace's installed Kotlin/Android toolchain.
Android's jar provides types only; these checks call no framework methods.
"""
from pathlib import Path
import os
import subprocess
import sys

project = Path(__file__).resolve().parents[1]
if len(sys.argv) == 1:
    workspace = project.parent
    if not (workspace / "android_build").is_dir():
        workspace = workspace.parent
    build = workspace / "android_build"
    lib = build / "gradle/gradle-8.11.1/lib"
    android = build / "sdk/platforms/android-35/android.jar"
    json_jar = build / "json-20240303.jar"
    output = build / "connection-pin-checks.jar"
elif len(sys.argv) == 5:
    lib, android, json_jar, output = (Path(value).resolve() for value in sys.argv[1:])
else:
    raise SystemExit(__doc__)

stdlib = next(lib.glob("kotlin-stdlib-[0-9]*.jar"))
classpath = os.pathsep.join(map(str, (stdlib, json_jar, android)))
source = project / "app/src/main/java/br/com/perabru/minianydesk"
output.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(["java", "-cp", str(lib / "*"), "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
    "-no-stdlib", "-no-reflect", "-jvm-target", "17", "-classpath", classpath,
    str(source / "ComputerBook.kt"), str(source / "ConnectionPins.kt"),
    str(project / "tests/ConnectionPinsHarness.kt"), "-d", str(output)], check=True)
subprocess.run(["java", "-cp", os.pathsep.join((str(output), classpath)),
    "br.com.perabru.minianydesk.ConnectionPinsHarness"], check=True)
