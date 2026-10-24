"""Run actual saved-access coordinator/book/AES-GCM rules and two separate JVM reloads.

Usage: python3 tests/run_saved_access_checks.py KOTLIN_LIB ANDROID_JAR JSON_JAR OUTPUT_JAR
Injected file/key fixtures do not represent AndroidKeyStore hardware or a physical phone.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

if len(sys.argv) != 5:
    raise SystemExit(__doc__)
project = Path(__file__).resolve().parents[1]
lib, android, json_jar, output = (Path(value).resolve() for value in sys.argv[1:])
stdlib = next(lib.glob("kotlin-stdlib-[0-9]*.jar"))
classpath = os.pathsep.join(map(str, (stdlib, json_jar, android)))
source = project / "app/src/main/java/br/com/perabru/minianydesk"
output.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(["java", "-cp", str(lib / "*"), "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
    "-no-stdlib", "-no-reflect", "-jvm-target", "17", "-classpath", classpath,
    *[str(source / f"{name}.kt") for name in ("ComputerBook", "MeshPairing", "MeshCodeVault", "SavedComputerAccess")],
    str(project / "tests/SavedComputerAccessHarness.kt"), "-d", str(output)], check=True)
command = ["java", "-cp", os.pathsep.join((str(output), classpath)),
    "br.com.perabru.minianydesk.SavedComputerAccessHarness"]
subprocess.run(command, check=True, timeout=30)
with tempfile.TemporaryDirectory(prefix="aliancadx-saved-access-") as temporary:
    subprocess.run(command + ["write-process", temporary], check=True, timeout=10)
    subprocess.run(command + ["read-process", temporary], check=True, timeout=10)
