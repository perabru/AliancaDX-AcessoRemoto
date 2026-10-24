#!/usr/bin/env python3
"""Check production Peer byte accounting over real loopback sockets, without an APK/device.

Usage: python3 tests/run_traffic_transport_checks.py --kotlin-lib KOTLIN_LIB
       --json-jar JSON_JAR --java JAVA --output OUTPUT_JAR
"""
import argparse
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kotlin-lib", type=Path, required=True)
    parser.add_argument("--json-jar", type=Path, required=True)
    parser.add_argument("--java", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    lib = args.kotlin_lib.resolve(strict=True)
    json_jar = args.json_jar.resolve(strict=True)
    java = args.java.resolve(strict=True)
    stdlib = next(lib.glob("kotlin-stdlib-[0-9]*.jar"))
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    sources = [project / "app/src/main/java/br/com/perabru/minianydesk/AndroidNet.kt",
               project / "tests/headless/android/os/Build.kt",
               project / "tests/headless/android/annotation/TargetApi.kt",
               project / "tests/TrafficTransportHarness.kt"]
    subprocess.run([str(java), "-cp", str(lib / "*"),
                    "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler", "-no-stdlib", "-no-reflect",
                    "-jvm-target", "17", "-classpath", os.pathsep.join(map(str, (stdlib, json_jar))),
                    "-d", str(output), *map(str, sources)], check=True, timeout=120)
    classpath = os.pathsep.join(map(str, (output, json_jar, stdlib)))
    results = []
    for case in ("fragments", "coalesced-queue", "multichunk-utf8", "partial-write", "independent-peers"):
        result = subprocess.run([str(java), "-cp", classpath,
                                 "br.com.perabru.minianydesk.TrafficTransportHarness", case],
                                capture_output=True, text=True, timeout=8, check=True)
        payload = json.loads(result.stdout.strip())
        assert payload == {"result": "passed", "case": case}, payload
        results.append(payload)
    print(json.dumps({"production_peer_transport_checks": results,
                      "physical_device_test": False}, indent=2))


if __name__ == "__main__":
    main()
