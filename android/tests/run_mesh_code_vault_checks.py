#!/usr/bin/env python3
"""Test production AES-GCM vault logic and cross-JVM persistence with injected fixtures.

No AndroidKeyStore or hardware/device claim is made by this JVM runner.
The temporary file-store fixture uses synthetic, public test key material only.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kotlin-lib", type=Path, required=True)
    parser.add_argument("--android-jar", type=Path, required=True)
    parser.add_argument("--java", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    lib = args.kotlin_lib.resolve(strict=True)
    android = args.android_jar.resolve(strict=True)
    java = args.java.resolve(strict=True)
    stdlib = next(lib.glob("kotlin-stdlib-[0-9]*.jar"))
    output = args.output.resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    classpath = os.pathsep.join(map(str, (stdlib, android)))
    sources = [project / "app/src/main/java/br/com/perabru/minianydesk/MeshCodeVault.kt",
               project / "app/src/main/java/br/com/perabru/minianydesk/MeshPairing.kt",
               project / "tests/MeshCodeVaultHarness.kt"]
    subprocess.run([str(java), "-cp", str(lib / "*"), "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
                    "-no-stdlib", "-no-reflect", "-jvm-target", "17", "-classpath", classpath,
                    "-d", str(output), *map(str, sources)], check=True, timeout=120)
    command = [str(java), "-cp", os.pathsep.join((str(output), classpath)),
               "br.com.perabru.minianydesk.MeshCodeVaultHarness"]
    subprocess.run(command, check=True, timeout=20)
    with tempfile.TemporaryDirectory(prefix="aliancadx-vault-fixture-") as directory:
        subprocess.run(command + ["write-process", directory], check=True, timeout=10)
        subprocess.run(command + ["read-process", directory], check=True, timeout=10)


if __name__ == "__main__":
    main()
