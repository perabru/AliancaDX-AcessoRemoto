"""Compile and exercise production pacing feedback on a JVM; no Android/device claim.

Usage: python3 tests/run_stream_feedback_checks.py KOTLIN_LIB JSON_JAR OUTPUT_JAR
"""
from pathlib import Path
import os
import subprocess
import sys

if len(sys.argv) != 4:
    raise SystemExit(__doc__)
project = Path(__file__).resolve().parents[1]
lib, json_jar, output = (Path(value).resolve() for value in sys.argv[1:])
stdlib = next(lib.glob("kotlin-stdlib-[0-9]*.jar"))
output.parent.mkdir(parents=True, exist_ok=True)
classpath = os.pathsep.join(map(str, (stdlib, json_jar)))
subprocess.run(["java", "-cp", str(lib / "*"), "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
    "-no-stdlib", "-no-reflect", "-jvm-target", "17", "-classpath", classpath,
    str(project / "app/src/main/java/br/com/perabru/minianydesk/StreamFeedback.kt"),
    str(project / "tests/StreamFeedbackHarness.kt"), "-d", str(output)], check=True)
subprocess.run(["java", "-cp", os.pathsep.join((str(output), classpath)),
    "br.com.perabru.minianydesk.StreamFeedbackHarness"], check=True)
