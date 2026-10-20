"""Compile and exercise the real JNI adapter against an ownership-checking fake.

Usage: python run_tests.py /absolute/path/to/jdk17
The JDK must contain include/jni.h and a Linux JVM. Requires host cc.
"""
from pathlib import Path
import subprocess
import sys

test_dir = Path(__file__).resolve().parent
native_dir = test_dir.parent
jdk = Path(sys.argv[1]).resolve()
out = test_dir / "build"
out.mkdir(exist_ok=True)

def run(argv):
    subprocess.run([str(item) for item in argv], check=True)

run(["cc", "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-Wall", "-Wextra", "-Werror",
     "-g", "-fPIC", "-fvisibility=hidden", "-shared", "-pthread",
     "-I" + str(jdk / "include"), "-I" + str(jdk / "include/linux"),
     native_dir / "easytier_jni.c", test_dir / "mock_ffi.c",
     "-o", out / "libeasytier_jni.so"])
run([jdk / "bin/javac", "-d", out, test_dir / "EasyTierJNI.java"])
run([jdk / "bin/java", "-Xcheck:jni", "-Djava.library.path=" + str(out), "-cp", out,
     "com.easytier.jni.EasyTierJNI"])
