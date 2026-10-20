"""Compile shipped Kotlin transport/presence with real JVM TLS and one Build stub.

Usage: python3 tests/compile_presence_harness.py
Optional: gradle-lib json-jar output-jar (all three positional arguments).
The printed classpath can be supplied as MINIANYDESK_PRESENCE_CLASSPATH.
"""
from pathlib import Path
import os
import subprocess
import sys

VALIDATION = Path(__file__).resolve().parent
PROJECT = VALIDATION.parent
ROOT = PROJECT.parent
if not (ROOT / "android_build").is_dir():
    ROOT = ROOT.parent
if len(sys.argv) == 1:
    LIB = ROOT / "android_build/gradle/gradle-8.11.1/lib"
    JSON = ROOT / "android_build/json-20240303.jar"
    OUTPUT = ROOT / "android_validation_3_2/presence-tests-portable.jar"
elif len(sys.argv) == 4:
    LIB, JSON, OUTPUT = (Path(argument).resolve() for argument in sys.argv[1:])
else:
    raise SystemExit(__doc__)

stdlib = next(LIB.glob("kotlin-stdlib-[0-9]*.jar"))
annotations = next(LIB.glob("annotations-[0-9]*.jar"))
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
SOURCES = PROJECT / "app/src/main/java/br/com/perabru/minianydesk"
subprocess.run([
    "java", "-cp", str(LIB / "*"), "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
    "-no-stdlib", "-no-reflect", "-jvm-target", "17", "-classpath",
    os.pathsep.join(map(str, (stdlib, annotations, JSON))),
    str(SOURCES / "AndroidNet.kt"), str(SOURCES / "PresenceProbe.kt"),
    str(VALIDATION / "headless/android/os/Build.kt"),
    str(VALIDATION / "headless/android/annotation/TargetApi.kt"),
    str(VALIDATION / "PresenceHarness.kt"), "-d", str(OUTPUT)
], check=True)
print(os.pathsep.join(map(str, (OUTPUT, JSON, stdlib))))
