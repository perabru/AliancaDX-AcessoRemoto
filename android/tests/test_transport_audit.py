"""Cancellation and parser/close regression checks for production AndroidNet on a JVM.

Run compile_transport_audit.py first. These exercise real TCP/TLS sockets; they do
not claim to test Android framework rendering or behavior on a physical device.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import ssl
import struct
import queue
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[2]
NATIVE = Path(os.environ.get("MINIANYDESK_PYTHON_SOURCE", ROOT / "native_project"))
if not (NATIVE / "net.py").exists():
    NATIVE = Path(__file__).resolve().parent / "python_reference"
sys.path.insert(0, str(NATIVE))
import net

JAR = ROOT / "android_build/transport-audit.jar"
JSON = ROOT / "android_build/json-20240303.jar"
STDLIB = ROOT / "android_build/gradle/gradle-8.11.1/lib/kotlin-stdlib-2.0.20.jar"
CLASSPATH = os.environ.get("MINIANYDESK_TRANSPORT_AUDIT_CLASSPATH", os.pathsep.join(map(str, (JAR, JSON, STDLIB))))


class TransportAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("MINIANYDESK_TRANSPORT_AUDIT_CLASSPATH") and not all(path.exists() for path in (JAR, JSON, STDLIB)):
            raise unittest.SkipTest("Compile the production transport audit harness first")

    def run_case(self, mode):
        with tempfile.TemporaryDirectory() as temp:
            key, cert, pin = net._certificate(Path(temp))
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(cert, key)
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(2)
            port = listener.getsockname()[1]
            observed = {"accepted": False, "login": False, "eof": False, "failure": None}
            finished = threading.Event()

            def server():
                connection = None
                try:
                    connection, _ = listener.accept()
                    observed["accepted"] = True
                    connection.settimeout(3)
                    if mode == "cancel-handshake":
                        # Consume ClientHello without answering. EOF proves cancellation
                        # closed the socket while startHandshake() was still blocked.
                        while connection.recv(16384):
                            pass
                        observed["eof"] = True
                        return
                    connection = context.wrap_socket(connection, server_side=True)
                    if mode in ("cancel-trust", "pin-throw"):
                        observed["eof"] = connection.recv(1) == b""
                        return
                    login = net._read(connection)
                    observed["login"] = login["password"] == "testpassword"
                    if mode == "cancel-auth":
                        observed["eof"] = connection.recv(1) == b""
                        return
                    net._write(connection, {"type": "auth_ok", "version": "3.0.0"})
                    if mode == "early-close":
                        return
                    if mode == "invalid-utf8":
                        payload = b'{"type":"log","message":"\xff"}'
                    elif mode == "deep-nesting":
                        payload = ('{"type":"log","value":' + '[' * 65 + '0' + ']' * 65 + '}').encode()
                    elif mode == "missing-type":
                        payload = b'{"message":"No packet type"}'
                    else:
                        raise AssertionError(mode)
                    connection.sendall(struct.pack("!I", len(payload)) + payload)
                    observed["eof"] = connection.recv(1) == b""
                except socket.timeout:
                    if mode != "cancel-pre":
                        observed["failure"] = "Server timeout"
                except Exception as error:
                    observed["failure"] = repr(error)
                finally:
                    if connection:
                        connection.close()
                    finished.set()

            thread = threading.Thread(target=server, daemon=True)
            thread.start()
            try:
                result = subprocess.run(["java", "-cp", CLASSPATH,
                    "br.com.perabru.minianydesk.TransportAuditHarnessKt", str(port), pin, mode],
                    text=True, capture_output=True, timeout=7)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(finished.wait(3), "Socket worker remained blocked")
                self.assertIsNone(observed["failure"], observed)
                if mode == "cancel-pre":
                    self.assertFalse(observed["accepted"], observed)
                else:
                    self.assertTrue(observed["accepted"], observed)
                if mode in ("cancel-pre", "cancel-handshake", "cancel-trust", "pin-throw"):
                    self.assertFalse(observed["login"], "Password was sent before successful approval")
                else:
                    self.assertTrue(observed["login"], observed)
                if mode not in ("cancel-pre", "early-close"):
                    self.assertTrue(observed["eof"], "Socket was not closed")
                return result.stdout
            finally:
                listener.close()
                thread.join(timeout=.1)

    def test_cancel_before_connect_never_opens_socket(self):
        self.assertIn("CANCELLED", self.run_case("cancel-pre"))

    def test_cancel_during_tls_handshake_closes_promptly(self):
        self.assertIn("CANCELLED", self.run_case("cancel-handshake"))

    def test_cooperative_trust_cancel_never_sends_password(self):
        self.assertIn("CANCELLED", self.run_case("cancel-trust"))

    def test_cancel_waiting_for_owner_closes_promptly(self):
        self.assertIn("CANCELLED", self.run_case("cancel-auth"))

    def test_approval_callback_exception_closes_without_login(self):
        self.assertIn("APPROVAL_ERROR_CLOSED", self.run_case("pin-throw"))

    def test_early_eof_and_parser_errors_close_exactly_once(self):
        for mode in ("early-close", "invalid-utf8", "deep-nesting", "missing-type"):
            with self.subTest(mode=mode):
                self.assertIn("CLOSED_ONCE", self.run_case(mode))

    def test_queued_control_bytes_are_bounded_and_dequeue_restores_budget(self):
        result = subprocess.run(["java", "-cp", CLASSPATH,
            "br.com.perabru.minianydesk.TransportAuditHarnessKt", "queue-bytes"],
            text=True, capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("BYTE_QUEUE_OK", result.stdout)

    def test_screen_geometry_and_ascii_mapping(self):
        result = subprocess.run(["java", "-cp", CLASSPATH,
            "br.com.perabru.minianydesk.GeometryHarnessKt"], text=True, capture_output=True, timeout=4)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("GEOMETRY_OK:9", result.stdout)

    def test_failed_address_records_expire_are_bounded_and_count_real_failures(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.serialization import pkcs12
        with tempfile.TemporaryDirectory() as temp:
            key_path, cert_path, _ = net._certificate(Path(temp))
            key = serialization.load_pem_private_key(key_path.read_bytes(), None)
            cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            store = Path(temp) / "host.p12"
            store.write_bytes(pkcs12.serialize_key_and_certificates(b"host", key, cert, None,
                serialization.BestAvailableEncryption(b"testpassword")))
            process = subprocess.Popen(["java", "-cp", CLASSPATH,
                "br.com.perabru.minianydesk.TransportAuditHarnessKt", "rate-limit", str(store)],
                text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1)
            lines = queue.Queue()
            threading.Thread(target=lambda: [lines.put(line.rstrip()) for line in process.stdout], daemon=True).start()
            try:
                ready = lines.get(timeout=4)
                self.assertTrue(ready.startswith("READY:"), ready)
                _, port, fingerprint = ready.split(":")

                def login(secret):
                    with self.assertRaises(PermissionError):
                        net.connect("127.0.0.1", int(port), secret, "Test visitor", lambda message: None,
                            lambda reason: None, lambda address, number, pin: pin == fingerprint)

                def instruction(value):
                    process.stdin.write(value + "\n"); process.stdin.flush()
                    return lines.get(timeout=3)

                login("wrongpassword")
                self.assertEqual(instruction("stats"), "STATS:1024:false:false:1:0")
                self.assertEqual(instruction("clear"), "CLEARED")
                login("testpassword")
                self.assertEqual(instruction("stats"), "STATS:0:false:false:0:1")
                for _ in range(5):
                    login("wrongpassword")
                login("testpassword")
                self.assertEqual(instruction("stats"), "STATS:1:false:false:5:1")
            finally:
                if process.poll() is None:
                    process.stdin.write("stop\n"); process.stdin.flush()
                    try: process.wait(timeout=3)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
                stderr = process.stderr.read()
                for stream in (process.stdin, process.stdout, process.stderr): stream.close()
                self.assertEqual(process.returncode, 0, stderr)


if __name__ == "__main__":
    unittest.main()
