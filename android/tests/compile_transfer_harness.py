"""Compile only the transfer/JVM harness; do not use the Android stubs in an APK.

Usage: python3 tests/compile_transfer_harness.py /path/to/gradle/lib /path/json.jar output.jar
Requires JDK 17 and a Gradle distribution containing Kotlin's embeddable compiler.
Prints the classpath needed by MINIANYDESK_TRANSFER_CLASSPATH.
"""
from pathlib import Path
import os
import subprocess
import sys

project = Path(__file__).resolve().parents[1]
if len(sys.argv) != 4:
    raise SystemExit(__doc__)
compiler_lib = Path(sys.argv[1]).resolve()
json_jar = Path(sys.argv[2]).resolve()
output_jar = Path(sys.argv[3]).resolve()
stdlib = next(compiler_lib.glob('kotlin-stdlib-[0-9]*.jar'))
sources = project / 'app/src/main/java/br/com/perabru/minianydesk'
files = [sources / 'AndroidNet.kt', sources / 'FileTransfers.kt',
    *sorted((project / 'tests/headless').rglob('*.kt'))]
output_jar.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(['java', '-cp', str(compiler_lib / '*'),
    'org.jetbrains.kotlin.cli.jvm.K2JVMCompiler', '-no-stdlib', '-no-reflect',
    '-jvm-target', '17', '-classpath', os.pathsep.join(map(str, (stdlib, json_jar))), '-d', str(output_jar),
    *map(str, files)], check=True)
print(os.pathsep.join(map(str, (output_jar, json_jar, stdlib))))
