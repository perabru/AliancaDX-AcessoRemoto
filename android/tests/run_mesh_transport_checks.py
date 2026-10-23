"""Production multi-network lifecycle on JVM with fake FFI and real TLS listeners.

Usage: python3 tests/run_mesh_transport_checks.py KOTLIN_LIB OUTPUT_DIR
This is not a test on Android hardware or public EasyTier relays.
"""
from pathlib import Path
import datetime
import hashlib
import importlib.util
import os
import subprocess
import sys

if len(sys.argv) != 3:
    raise SystemExit(__doc__)
project = Path(__file__).resolve().parents[1]
lib, output = (Path(value).resolve() for value in sys.argv[1:])
output.mkdir(parents=True, exist_ok=True)
stdlib = next(lib.glob("kotlin-stdlib-[0-9]*.jar"))

# Ephemeral fixture key only; never accesses the application's signing material.
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "same-hostname")])
now = datetime.datetime.now(datetime.timezone.utc)
cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
keystore = output / "fixture.p12"
keystore.write_bytes(pkcs12.serialize_key_and_certificates(b"fixture", key, cert, None,
                    serialization.BestAvailableEncryption(b"test-only")))
keystore.chmod(0o600)
fingerprint = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
mesh_path = project.parent / "desktop/mesh.py"
spec = importlib.util.spec_from_file_location("mesh_fixture", mesh_path)
mesh = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mesh
spec.loader.exec_module(mesh)
codes = [mesh.HostPairing(bytes([value]) * 32, 64321, fingerprint).code for value in (1, 2)]

stubs = output / "stubs"
stubs.mkdir(exist_ok=True)
(stubs / "Context.kt").write_text("package android.content\nopen class Context\n")
(stubs / "Build.kt").write_text("package android.os\nobject Build { object VERSION { const val SDK_INT = 36 } }\n")
jar = output / "multi-mesh.jar"
source = project / "app/src/main/java/br/com/perabru/minianydesk"
sources = [source / name for name in ("MeshPairing.kt", "MeshTransport.kt", "NativeMeshInstances.kt")]
sources += [project / "tests/NativeMeshInstancesHarness.kt", project / "tests/MeshTransportHarness.kt",
            project / "tests/headless/MultiMeshNative.kt", stubs / "Context.kt", stubs / "Build.kt"]
subprocess.run(["java", "-cp", str(lib / "*"), "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
                "-no-stdlib", "-no-reflect", "-jvm-target", "17", "-classpath", str(stdlib),
                *map(str, sources), "-d", str(jar)], check=True)
result = subprocess.run(["java", "-Dmesh.test.keystore=" + str(keystore), "-cp",
                         os.pathsep.join((str(jar), str(stdlib))),
                         "br.com.perabru.minianydesk.MeshTransportHarness", *codes],
                        check=False, text=True, capture_output=True, timeout=60)
print(result.stdout, end="")
if result.stderr:
    print(result.stderr, file=sys.stderr, end="")
result.check_returncode()
(output / "checks.log").write_text(result.stdout)
