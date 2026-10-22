"""Compile the complete production Activity and execute only its pure UI rules.

Usage: python3 tests/run_main_ui_rules_checks.py GRADLE_LIB ANDROID_JAR JSON_JAR OUTPUT_JAR
The generated BuildConfig/R constants supply build symbols; framework code is
not mocked or executed. This is neither Android rendering nor a device test.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile

if len(sys.argv) != 5:
    raise SystemExit(__doc__)
project = Path(__file__).resolve().parents[1]
lib, android, json_jar, output = (Path(value).resolve() for value in sys.argv[1:])
stdlib = next(lib.glob("kotlin-stdlib-[0-9]*.jar"))
annotations = next(lib.glob("annotations-[0-9]*.jar"))
classpath = os.pathsep.join(map(str, (stdlib, annotations, json_jar, android)))
excluded = {"HostCaptureService.kt", "HostRuntime.kt", "RemoteAccessibilityService.kt", "AndroidTlsIdentity.kt"}
sources = sorted(path for path in (project / "app/src/main/java").rglob("*.kt") if path.name not in excluded)
resources = {}
for source in sources:
    for kind, name in re.findall(r"(?<![\w.])R\.(\w+)\.(\w+)", source.read_text()):
        resources.setdefault(kind, set()).add(name)
output.parent.mkdir(parents=True, exist_ok=True)
gradle_text = (project / "app/build.gradle.kts").read_text()
version = re.search(r'versionName\s*=\s*"([^"]+)"', gradle_text).group(1)
version_code = re.search(r'versionCode\s*=\s*(\d+)', gradle_text).group(1)
with tempfile.TemporaryDirectory(prefix="main-ui-build-symbols-") as temporary:
    symbols = Path(temporary) / "BuildSymbols.kt"
    values = ["package br.com.perabru.minianydesk", "object BuildConfig {",
              ' const val DEFAULT_ROLE = "controller"', f' const val VERSION_NAME = "{version}"',
              ' const val APPLICATION_ID = "br.com.perabru.minianydesk.controlador"',
              f" const val VERSION_CODE = {version_code}", " const val DEBUG = false", "}", "object R {"]
    for kind, names in sorted(resources.items()):
        values.append(" object " + kind + " {")
        values.extend("  const val " + name + " = " + str(index) for index, name in enumerate(sorted(names), 1))
        values.append(" }")
    values.append("}")
    symbols.write_text("\n".join(values) + "\n")
    subprocess.run(["java", "-cp", str(lib / "*"), "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
        "-no-stdlib", "-no-reflect", "-jvm-target", "17", "-classpath", classpath,
        *map(str, sources), str(symbols), str(project / "tests/MainUiRulesHarness.kt"),
        str(project / "tests/SessionRulesHarness.kt"),
        str(project / "tests/StreamFeedbackHarness.kt"),
        "-d", str(output)], check=True)
subprocess.run(["java", "-cp", os.pathsep.join((str(output), classpath)),
    "br.com.perabru.minianydesk.MainUiRulesHarness"], check=True)
subprocess.run(["java", "-cp", os.pathsep.join((str(output), classpath)),
    "br.com.perabru.minianydesk.SessionRulesHarness"], check=True)
subprocess.run(["java", "-cp", os.pathsep.join((str(output), classpath)),
    "br.com.perabru.minianydesk.StreamFeedbackHarness"], check=True)
