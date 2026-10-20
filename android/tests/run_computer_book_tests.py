"""Compile and run the production Kotlin registry on a JVM with an injected Store.

Usage: python3 tests/run_computer_book_tests.py GRADLE_LIB ANDROID_JAR JSON_JAR OUTPUT_JAR
Android's jar is present for Context/SharedPreferences types only; no framework methods run.
"""
from pathlib import Path
import os
import subprocess
import sys

if len(sys.argv) != 5:
    raise SystemExit(__doc__)
project = Path(__file__).resolve().parents[1]
lib, android, json_jar, output = map(lambda path: Path(path).resolve(), sys.argv[1:])
stdlib = next(lib.glob('kotlin-stdlib-[0-9]*.jar'))
classpath = os.pathsep.join(map(str, (stdlib, json_jar, android)))
source = project / 'app/src/main/java/br/com/perabru/minianydesk/ComputerBook.kt'
harness = project / 'tests/ComputerBookHarness.kt'
output.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(['java', '-cp', str(lib / '*'), 'org.jetbrains.kotlin.cli.jvm.K2JVMCompiler',
    '-no-stdlib', '-no-reflect', '-jvm-target', '17', '-classpath', classpath,
    '-d', str(output), str(source), str(harness)], check=True)
subprocess.run(['java', '-cp', os.pathsep.join((str(output), classpath)),
    'br.com.perabru.minianydesk.ComputerBookHarness'], check=True)
