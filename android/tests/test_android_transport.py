"""Actual TLS interoperability between Kotlin transport and the shipped Python transport.

Compile with compile_transport_harness.py, then set MINIANYDESK_TRANSPORT_CLASSPATH
to the printed classpath. ANDROID_TRANSPORT_TEST_JAR keeps the older jar override.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import queue
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
NATIVE = Path(os.environ.get("MINIANYDESK_PYTHON_SOURCE", ROOT / "native_project"))
if not (NATIVE / "net.py").exists():
    NATIVE = Path(__file__).resolve().parent / "python_reference"
sys.path.insert(0, str(NATIVE))
import net

JAR = Path(os.environ.get("ANDROID_TRANSPORT_TEST_JAR", ROOT / "android_build/transport-tests.jar"))
JSON = ROOT / "android_build/json-20240303.jar"
STDLIB = ROOT / "android_build/gradle/gradle-8.11.1/lib/kotlin-stdlib-2.0.20.jar"
CLASSPATH = os.environ.get("MINIANYDESK_TRANSPORT_CLASSPATH", os.pathsep.join(map(str, (JAR, JSON, STDLIB))))


def command(*args):
    return ["java", "-cp", CLASSPATH, "br.com.perabru.minianydesk.TransportHarnessKt", *map(str, args)]


def wait_for(predicate, timeout=3):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Condition did not become true")


class AndroidTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("MINIANYDESK_TRANSPORT_CLASSPATH") and not all(path.exists() for path in (JAR, JSON, STDLIB)):
            raise unittest.SkipTest("Compile the Kotlin transport test harness before running")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.requests = []
        self.peers = []
        self.allow = True
        self.malformed = False

        def connected(peer, name, ip):
            self.peers.append(peer)
            if self.malformed:
                # Write after auth_ok rather than racing its packet writer.
                def inject():
                    time.sleep(.05)
                    peer.sock.sendall(struct.pack("!I", 4 * 1024 * 1024 + 1))
                threading.Thread(target=inject, daemon=True).start()
            else:
                peer.on_message = peer.send

        self.host = net.HostServer("127.0.0.1", 0, "testpassword",
            lambda name, ip: self.requests.append(name) or self.allow,
            connected, lambda peer, reason: None, Path(self.temp.name) / "python")
        self.port = self.host.start()

    def tearDown(self):
        self.host.stop()
        self.temp.cleanup()

    def client(self, mode="echo", password="testpassword", name="Android aparelho"):
        result = subprocess.run(command("client", self.port, self.host.fingerprint, password, name, mode),
            text=True, capture_output=True, timeout=12)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_jvm_queues_bound_controls_snapshot_frames_and_pause(self):
        result = subprocess.run(command("queue"), text=True, capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("QUEUE_OK", result.stdout)

    def test_android_controller_python_host_metadata_unicode_and_order(self):
        self.assertIn("CLIENT_OK", self.client())
        self.assertEqual(self.requests, ["Android aparelho"])

    def test_pin_rejected_before_python_owner_or_authentication(self):
        self.assertIn("REJECTED:Identidade", self.client("reject"))
        self.assertEqual(self.requests, [])
        self.assertEqual(self.peers, [])

    def test_wrong_password_never_asks_owner(self):
        self.assertIn("REJECTED:Senha", self.client(password="incorrectpassword"))
        self.assertEqual(self.requests, [])

    def test_python_owner_denial(self):
        self.allow = False
        self.assertIn("REJECTED:O usuário", self.client())
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.peers, [])

    def test_busy_then_reconnect(self):
        peer = net.connect("127.0.0.1", self.port, "testpassword", "Windows", lambda m: None,
            lambda reason: None, lambda *args: True)
        self.assertIn("REJECTED:Hospedeiro ocupado", self.client())
        peer.close()
        wait_for(lambda: self.host._peer is None)
        self.assertIn("CLIENT_OK", self.client())

    def test_malformed_length_closes_android_peer(self):
        self.malformed = True
        self.assertIn("MALFORMED_CLOSED", self.client("malformed"))

    def android_server(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.serialization import pkcs12
        key_path, cert_path, fingerprint = net._certificate(Path(self.temp.name) / "android")
        key = serialization.load_pem_private_key(key_path.read_bytes(), None)
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        path = Path(self.temp.name) / "android.p12"
        path.write_bytes(pkcs12.serialize_key_and_certificates(b"host", key, cert, None,
            serialization.BestAvailableEncryption(b"testpassword")))
        process = subprocess.Popen(command("server", path), text=True, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1)
        lines = queue.Queue()
        threading.Thread(target=lambda: [lines.put(line.rstrip()) for line in process.stdout], daemon=True).start()
        ready = lines.get(timeout=8)
        self.assertTrue(ready.startswith("READY:"), ready)
        _, port, observed = ready.split(":")
        self.assertEqual(fingerprint, observed)
        self.addCleanup(self.stop_android_server, process)
        return process, int(port), fingerprint, lines

    @staticmethod
    def stop_android_server(process):
        if process.poll() is None:
            process.stdin.write("stop\n")
            process.stdin.flush()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()

    def test_python_controller_android_host_with_busy_denial_and_reconnect(self):
        process, port, fingerprint, lines = self.android_server()
        def connect(name="Windows", password="testpassword", received=lambda msg: None):
            return net.connect("127.0.0.1", port, password, name, received, lambda reason: None,
                lambda address, port, pin: pin == fingerprint)
        with self.assertRaises(PermissionError):
            connect(password="incorrectpassword")
        with self.assertRaises(PermissionError):
            connect(name="deny")
        received = queue.Queue()
        peer = connect(received=received.put)
        self.addCleanup(peer.close)
        with self.assertRaises(PermissionError):
            connect()
        for i in range(40):
            peer.send({"type": "input", "seq": i, "text": "ação 日本語", "captureId": "live",
                       "action": "key", "key": "ctrl", "down": False, "x": .25, "y": .75})
        for i in range(40):
            message = received.get(timeout=4)
            self.assertEqual((message["seq"], message["text"], message["captureId"]), (i, "ação 日本語", "live"))
        peer.close()
        wait_for(lambda: not lines.empty())
        deadline = time.monotonic() + 3
        while True:
            try:
                another = connect()
                break
            except PermissionError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(.02)
        another.close()

    def test_android_host_stop_restart_reuses_port_and_drops_peer(self):
        process, port, fingerprint, lines = self.android_server()
        closed = threading.Event()
        peer = net.connect("127.0.0.1", port, "testpassword", "Windows", lambda msg: None,
            lambda reason: closed.set(), lambda *args: True)
        self.addCleanup(peer.close)
        process.stdin.write("restart\n")
        process.stdin.flush()
        deadline = time.monotonic() + 4
        while True:
            line = lines.get(timeout=max(.1, deadline - time.monotonic()))
            if line.startswith("RESTARTED:"):
                self.assertEqual(port, int(line.split(":")[1]))
                break
        self.assertTrue(closed.wait(3))
        fresh = net.connect("127.0.0.1", port, "testpassword", "Windows", lambda msg: None,
            lambda reason: None, lambda *args: True)
        fresh.close()


if __name__ == "__main__":
    unittest.main()
