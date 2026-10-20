"""Compile production Peer and real-socket JVM regressions; no APK/device claim.

Usage: python tests/compile_peer_stability.py KOTLIN_LIB JSON_JAR OUTPUT_JAR
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
sources = [project / "app/src/main/java/br/com/perabru/minianydesk/AndroidNet.kt",
           project / "tests/headless/android/os/Build.kt",
           project / "tests/headless/android/annotation/TargetApi.kt",
           project / "tests/PeerStabilityHarness.kt",
           project / "tests/ConnectionDeadlineHarness.kt"]
subprocess.run(["java", "-cp", str(lib / "*"), "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
                "-no-stdlib", "-no-reflect", "-jvm-target", "17", "-classpath",
                os.pathsep.join(map(str, (stdlib, json_jar))), "-d", str(output), *map(str, sources)], check=True)
print(os.pathsep.join(map(str, (output, json_jar, stdlib))))
