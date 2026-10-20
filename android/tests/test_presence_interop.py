"""Real TLS/ALPN presence interoperability for the shipped Android sources.

Run compile_presence_harness.py, then this script. The small Android Build stub
selects the API 26 or 35 branch; sockets, certificates, TLS and ALPN are real JVM
implementations. This does not test Android's UI, keystore or an actual device.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
from pathlib import Path
import queue
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

TESTS = Path(__file__).resolve().parent
PROJECT = TESTS.parent
ROOT = PROJECT.parent
if not (ROOT / "android_build").is_dir():
    ROOT = ROOT.parent
REFERENCE = Path(os.environ.get("MINIANYDESK_PRESENCE_REFERENCE", TESTS / "presence_python_reference"))
sys.path.insert(0, str(REFERENCE))
import net
import presence

CLASSPATH = os.environ.get("MINIANYDESK_PRESENCE_CLASSPATH", os.pathsep.join(map(str, (
    ROOT / "android_validation_3_2/presence-tests-portable.jar",
    ROOT / "android_build/json-20240303.jar",
    ROOT / "android_build/gradle/gradle-8.11.1/lib/kotlin-stdlib-2.0.20.jar"))))
PASSWORD = "testpassword"


def command(*args):
    return ["java", "-cp", CLASSPATH, "br.com.perabru.minianydesk.PresenceHarnessKt", *map(str, args)]


def packet(value):
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return struct.pack("!I", len(encoded)) + encoded


def valid_status(**changes):
    return {"type": "status", "agent": "MiniAnyDesk", "version": "3.1.0",
            "name": "Oficina São Paulo 日本語", "online": True, "busy": False, **changes}


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Expected state did not become true")


class TlsFixture:
    def __init__(self, directory, alpn=None, response=None, trickle=False, auth_name=None):
        key, certificate, self.fingerprint = net._certificate(directory)
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context.load_cert_chain(certificate, key)
        if alpn:
            self.context.set_alpn_protocols([alpn])
        self.response, self.trickle, self.auth_name = response, trickle, auth_name
        self.received = bytearray()
        self.login = None
        self.finished = threading.Event()
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(4)
        self.port = self.listener.getsockname()[1]
        self.sock = None
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        raw = None
        try:
            raw, _ = self.listener.accept()
            raw.settimeout(4)
            self.sock = self.context.wrap_socket(raw, server_side=True)
            if self.auth_name is not None:
                self.login = net._read(self.sock)
                self.sock.sendall(packet({"type": "auth_ok", "version": net.VERSION,
                    "computerName": self.auth_name, "agentVersion": "3.1.0"}))
            elif self.response is not None:
                if self.trickle:
                    for value in self.response:
                        self.sock.sendall(bytes([value]))
                        time.sleep(.09)
                else:
                    self.sock.sendall(self.response)
            while True:
                data = self.sock.recv(4096)
                if not data:
                    break
                self.received.extend(data)
        except (OSError, ssl.SSLError):
            pass
        finally:
            if self.sock:
                self.sock.close()
            elif raw:
                raw.close()
            self.listener.close()
            self.finished.set()

    def close(self):
        for connection in (self.sock, self.listener):
            if connection:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
        self.thread.join(timeout=4)

    def client_bytes(self):
        self.finished.wait(4)
        return bytes(self.received)


class AndroidPresenceInteropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not all(Path(path).exists() for path in CLASSPATH.split(os.pathsep)):
            raise unittest.SkipTest("Compile tests/compile_presence_harness.py and set MINIANYDESK_PRESENCE_CLASSPATH first")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.consent = Mock(return_value=True)
        self.connected = queue.Queue()
        self.clients, self.hosts, self.fixtures = [], [], []

    def tearDown(self):
        for client in self.clients:
            client.close()
        for host in self.hosts:
            host.stop()
        for fixture in self.fixtures:
            fixture.close()
        self.temp.cleanup()

    def desktop_host(self, name="Desktop da recepção", consent=None):
        host = net.HostServer("127.0.0.1", 0, PASSWORD, consent or self.consent,
            lambda peer, name, address: self.connected.put(peer), lambda peer, reason: None,
            state_dir=self.directory / f"desktop-{len(self.hosts)}", status_callback=lambda: name)
        self.hosts.append(host)
        host.start()
        return host

    def fixture(self, **kwargs):
        fixture = TlsFixture(self.directory / f"fixture-{len(self.fixtures)}", **kwargs)
        self.fixtures.append(fixture)
        return fixture

    def java(self, *args, timeout=12):
        result = subprocess.run(command(*args), capture_output=True, text=True, timeout=timeout)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]

    def probe(self, endpoint, pin=True, timeout_ms=2000, sdk=35, count=1):
        fingerprint = endpoint.fingerprint if pin is True else pin
        arguments = ["probe-loop" if count > 1 else "probe", "127.0.0.1", endpoint.port,
                     fingerprint if fingerprint is not None else "-", timeout_ms, sdk]
        if count > 1:
            arguments.append(count)
        results = self.java(*arguments, timeout=max(12, count * timeout_ms / 1000 + 5))
        return results if count > 1 else results[0]

    def assert_no_desktop_session(self, host):
        self.consent.assert_not_called()
        self.assertTrue(self.connected.empty())
        with host._lock:
            self.assertFalse(host._pending)
            self.assertIsNone(host._peer)
            self.assertEqual(host._failed, {})

    def test_android_probe_new_desktop_name_pin_and_no_authentication(self):
        host = self.desktop_host("Estação São Paulo 日本語")
        for pin in (True, None):
            answer = self.probe(host, pin=pin)
            self.assertEqual(answer["state"], "online")
            self.assertEqual(answer["computerName"], "Estação São Paulo 日本語")
            self.assertIs(answer["busy"], False)
            self.assertEqual(answer["version"], net.STATUS_VERSION)
            self.assertEqual(answer["fingerprint"], host.fingerprint)
        self.assert_no_desktop_session(host)

    def test_repeated_android_probes_do_not_reserve_consent_or_rate_limit(self):
        host = self.desktop_host()
        self.assertTrue(all(value["state"] == "online" for value in self.probe(host, count=12)))
        self.assert_no_desktop_session(host)
        values = self.java("auth-client", host.port, host.fingerprint)
        self.assertEqual(values[0]["computerName"], "Desktop da recepção")
        self.assertEqual(values[0]["trustCalls"], 1)
        self.assertFalse(values[0]["closed"])
        self.consent.assert_called_once_with("Android controlador", "127.0.0.1")

    def test_android_probe_preserves_existing_failed_attempts(self):
        host = self.desktop_host()
        with self.assertRaises(PermissionError):
            net.connect("127.0.0.1", host.port, "wrong-password", "Desktop",
                lambda message: None, lambda reason: None, lambda *args: True)
        with host._lock:
            before = {address: list(attempts) for address, attempts in host._failed.items()}
        self.assertEqual(self.probe(host)["state"], "online")
        with host._lock:
            self.assertEqual(host._failed, before)
            self.assertIsNone(host._peer)
            self.assertFalse(host._pending)
        self.consent.assert_not_called()

    def test_android_probe_busy_does_not_disconnect_existing_desktop_session(self):
        host = self.desktop_host()
        client = net.connect("127.0.0.1", host.port, PASSWORD, "Desktop", lambda message: None,
            lambda reason: None, lambda *args: True)
        self.clients.append(client)
        owner_peer = self.connected.get(timeout=3)
        answer = self.probe(host)
        self.assertEqual(answer["state"], "online")
        self.assertIs(answer["busy"], True)
        self.assertFalse(client.closed)
        self.assertFalse(owner_peer.closed)
        self.consent.assert_called_once()

    def test_android_probe_pending_desktop_approval_is_busy_and_unchanged(self):
        requested, release = threading.Event(), threading.Event()
        consent = Mock(side_effect=lambda name, ip: requested.set() or release.wait(8))
        host = self.desktop_host(consent=consent)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(net.connect, "127.0.0.1", host.port, PASSWORD,
                "Desktop", lambda message: None, lambda reason: None, lambda *args: True)
            try:
                self.assertTrue(requested.wait(3))
                answer = self.probe(host)
                self.assertIs(answer["busy"], True)
                with host._lock:
                    self.assertTrue(host._pending)
                    self.assertIsNone(host._peer)
                consent.assert_called_once()
            finally:
                release.set()
            self.clients.append(pending.result(timeout=3))

    def test_status_alpn_sends_zero_application_bytes(self):
        fixture = self.fixture(alpn=net.STATUS_ALPN, response=packet(valid_status()))
        answer = self.probe(fixture)
        self.assertEqual(answer["state"], "online")
        self.assertEqual(answer["computerName"], valid_status()["name"])
        self.assertEqual(fixture.client_bytes(), b"")

    def test_changed_pin_rejects_name_and_sends_zero_application_bytes(self):
        fixture = self.fixture(alpn=net.STATUS_ALPN, response=packet(valid_status()))
        answer = self.probe(fixture, pin="0" * 64)
        self.assertEqual(answer["state"], "unverified")
        self.assertEqual(answer["fingerprint"], fixture.fingerprint)
        self.assertFalse(answer["computerName"])
        self.assertIsNone(answer["busy"])
        self.assertEqual(fixture.client_bytes(), b"")

    def test_known_and_unknown_legacy_tls_send_no_login(self):
        for pin, state in ((True, "online"), (None, "unverified")):
            with self.subTest(pin=pin):
                fixture = self.fixture()
                answer = self.probe(fixture, pin=pin)
                self.assertEqual(answer["state"], state)
                self.assertFalse(answer["computerName"])
                self.assertIsNone(answer["busy"])
                self.assertEqual(fixture.client_bytes(), b"")

    def test_api26_fallback_with_status_host_preserves_login_state(self):
        host = self.desktop_host()
        for pin, state in ((True, "online"), (None, "unverified")):
            answer = self.probe(host, pin=pin, sdk=26)
            self.assertEqual(answer["state"], state)
            self.assertFalse(answer["computerName"])
            self.assertIsNone(answer["busy"])
            self.assert_no_desktop_session(host)

    def test_formatted_pin_matches_without_changing_identity(self):
        host = self.desktop_host()
        formatted = " \n" + ":".join(host.fingerprint[index:index + 2].lower()
                                  for index in range(0, 64, 2)) + "\t "
        self.assertEqual(self.probe(host, pin=formatted)["state"], "online")
        self.assert_no_desktop_session(host)

    def test_invalid_arguments_are_rejected_before_network(self):
        for address, port, pin, timeout in (("", 64321, "-", 100),
                ("bad\x00address", 64321, "-", 100), ("127.0.0.1", 0, "-", 100),
                ("127.0.0.1", 65536, "-", 100), ("127.0.0.1", 1, "", 100),
                ("127.0.0.1", 1, "g" * 64, 100), ("127.0.0.1", 1, "-", 0)):
            if "\x00" in address:
                continue  # Process arguments cannot contain NUL; production validates this independently.
            with self.subTest(address=address, port=port, pin=pin, timeout=timeout):
                answer = self.java("probe", address, port, pin, timeout, 35)[0]
                self.assertEqual(answer["state"], "offline")
                self.assertFalse(answer["fingerprint"])
                self.assertIsNone(answer["busy"])

    def test_stopped_desktop_is_offline_without_stale_metadata(self):
        host = self.desktop_host()
        host.stop()
        answer = self.probe(host)
        self.assertEqual(answer["state"], "offline")
        self.assertFalse(answer["computerName"])
        self.assertIsNone(answer["busy"])

    def test_malformed_status_fields_are_never_coerced(self):
        invalid = [valid_status(type="auth_ok"), valid_status(agent="Other"),
                   valid_status(online="true"), valid_status(busy="false"),
                   valid_status(name=["Name"]), valid_status(name="x" * 81),
                   valid_status(name="Name\nremote"), valid_status(name="Name\x7f"),
                   valid_status(name="Name\ud800"), valid_status(version=None),
                   valid_status(version=""), valid_status(version="v" * 33)]
        for value in invalid:
            with self.subTest(value=value):
                fixture = self.fixture(alpn=net.STATUS_ALPN, response=packet(value))
                answer = self.probe(fixture)
                self.assertEqual(answer["state"], "offline")
                self.assertFalse(answer["computerName"])
                self.assertIsNone(answer["busy"])
                self.assertEqual(fixture.client_bytes(), b"")

    def test_malformed_frames_and_utf8_are_rejected_without_app_bytes(self):
        for response in (struct.pack("!I", 0), struct.pack("!I", 2049),
                         struct.pack("!I", 0xFFFFFFFF), struct.pack("!I", 2) + b"[]",
                         struct.pack("!I", 2) + b"{!", struct.pack("!I", 1) + b"\xff"):
            with self.subTest(response=response):
                fixture = self.fixture(alpn=net.STATUS_ALPN, response=response)
                answer = self.probe(fixture)
                self.assertEqual(answer["state"], "offline")
                self.assertEqual(fixture.client_bytes(), b"")

    def test_status_read_timeout_and_trickle_obey_one_deadline(self):
        for trickle in (False, True):
            with self.subTest(trickle=trickle):
                fixture = self.fixture(alpn=net.STATUS_ALPN,
                    response=packet(valid_status()) if trickle else None, trickle=trickle)
                answer = self.probe(fixture, timeout_ms=1000)
                self.assertEqual(answer["state"], "offline")
                self.assertLess(answer["elapsedMs"], 1600)
                self.assertEqual(fixture.client_bytes(), b"")

    def test_dns_pool_deadline_bound_and_numeric_bypass(self):
        host = self.desktop_host()
        events = self.java("resolver", host.port, host.fingerprint, timeout=15)
        blocked, recovered = events
        self.assertTrue(blocked["allOffline"])
        self.assertLess(blocked["elapsedMs"], 1000)
        self.assertLessEqual(blocked["workerThreads"], 4)
        self.assertLessEqual(blocked["calls"], 4)
        self.assertEqual(blocked["calls"], blocked["callsBeforeNumeric"])
        self.assertEqual(blocked["numeric"]["state"], "online")
        self.assertEqual(recovered["result"]["state"], "online")
        self.assert_no_desktop_session(host)

    def test_auth_name_is_validated_without_breaking_existing_wire_protocol(self):
        for name, expected in (("Computador São Paulo 日本語", "Computador São Paulo 日本語"),
                ("x" * 81, ""), ("Name\nremote", ""), ("Name\x7f", ""),
                ("Name\ud800", ""), (["Name"], "")):
            with self.subTest(name=name):
                fixture = self.fixture(auth_name=name)
                answer = self.java("auth-client", fixture.port, fixture.fingerprint)[0]
                self.assertEqual(answer["computerName"], expected)
                self.assertEqual(fixture.login["type"], "login")
                self.assertEqual(fixture.login["version"], "3.0.0")

    def android_server(self, sdk=35, name="Android da oficina"):
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.serialization import pkcs12
        key_path, cert_path, fingerprint = net._certificate(self.directory / "android")
        key = serialization.load_pem_private_key(key_path.read_bytes(), None)
        certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
        store_path = self.directory / "android.p12"
        store_path.write_bytes(pkcs12.serialize_key_and_certificates(b"host", key, certificate, None,
            serialization.BestAvailableEncryption(PASSWORD.encode())))
        process = subprocess.Popen(command("server", store_path, name, sdk), text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1)
        self.addCleanup(self.stop_android_server, process)
        events = queue.Queue()
        def read_events():
            for line in process.stdout:
                events.put(json.loads(line))
        threading.Thread(target=read_events, daemon=True).start()
        ready = events.get(timeout=8)
        self.assertEqual(ready["event"], "ready", ready)
        self.assertEqual(ready["fingerprint"], fingerprint)
        return process, ready["port"], fingerprint, events

    @staticmethod
    def stop_android_server(process):
        if process.poll() is None:
            process.stdin.write("stop\n")
            process.stdin.flush()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=4)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()

    def server_command(self, process, events, command_text, expected):
        process.stdin.write(command_text + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + 4
        while True:
            value = events.get(timeout=max(.1, deadline - time.monotonic()))
            if value["event"] == expected:
                return value

    def python_connect(self, port, fingerprint, name="Desktop controlador", password=PASSWORD,
                       on_message=lambda value: None):
        client = net.connect("127.0.0.1", port, password, name, on_message, lambda reason: None,
            lambda address, port, observed: observed == fingerprint)
        self.clients.append(client)
        return client

    def test_python_probe_android_host_name_no_auth_and_failed_counter_preserved(self):
        process, port, fingerprint, events = self.android_server()
        for _ in range(12):
            answer = presence.probe("127.0.0.1", port, fingerprint, timeout=2)
            self.assertEqual(answer["state"], "online")
            self.assertEqual(answer["computer_name"], "Android da oficina")
            self.assertEqual(answer["version"], os.environ.get("MINIANYDESK_EXPECTED_APP_VERSION", "4.0.0"),
                             "The updated Android host must advertise its application version")
            self.assertIs(answer["busy"], False)
        snapshot = self.server_command(process, events, "snapshot", "snapshot")
        self.assertEqual((snapshot["requests"], snapshot["connections"], snapshot["failedAttempts"]), (0, 0, 0))
        self.assertFalse(snapshot["active"])
        self.assertFalse(snapshot["pending"])
        with self.assertRaises(PermissionError):
            self.python_connect(port, fingerprint, password="wrong-password")
        before = self.server_command(process, events, "snapshot", "snapshot")
        self.assertEqual(before["failedAttempts"], 1)
        self.assertEqual(presence.probe("127.0.0.1", port, fingerprint, timeout=2)["state"], "online")
        after = self.server_command(process, events, "snapshot", "snapshot")
        self.assertEqual(after["failedAttempts"], 1)
        self.assertEqual(after["requests"], 0)

    def test_python_probe_android_active_session_live_rename_and_echo(self):
        process, port, fingerprint, events = self.android_server()
        messages = queue.Queue()
        client = self.python_connect(port, fingerprint, on_message=messages.put)
        self.assertEqual(client.computer_name, "Android da oficina")
        self.server_command(process, events, "rename:Estação nova 日本語", "renamed")
        answer = presence.probe("127.0.0.1", port, fingerprint, timeout=2)
        self.assertEqual(answer["computer_name"], "Estação nova 日本語")
        self.assertIs(answer["busy"], True)
        self.assertFalse(client.closed)
        client.send({"type": "input", "seq": 41, "text": "ação 日本語", "captureId": "presence-isolated"})
        self.assertEqual(messages.get(timeout=3)["text"], "ação 日本語")
        snapshot = self.server_command(process, events, "snapshot", "snapshot")
        self.assertEqual((snapshot["requests"], snapshot["connections"], snapshot["failedAttempts"]), (1, 1, 0))
        self.assertTrue(snapshot["active"])

    def test_python_probe_android_pending_approval_stays_busy(self):
        process, port, fingerprint, events = self.android_server()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.python_connect, port, fingerprint, "hold")
            request = events.get(timeout=3)
            self.assertEqual(request["event"], "request")
            try:
                answer = presence.probe("127.0.0.1", port, fingerprint, timeout=2)
                self.assertIs(answer["busy"], True)
                snapshot = self.server_command(process, events, "snapshot", "snapshot")
                self.assertTrue(snapshot["pending"])
                self.assertFalse(snapshot["active"])
                self.assertEqual(snapshot["requests"], 1)
            finally:
                self.server_command(process, events, "release", "released")
            self.assertFalse(pending.result(timeout=4).closed)

    def test_python_probe_api26_android_host_is_legacy_without_auth(self):
        process, port, fingerprint, events = self.android_server(sdk=26)
        for pin, state in ((fingerprint, "online"), (None, "unverified")):
            answer = presence.probe("127.0.0.1", port, pin, timeout=2)
            self.assertEqual(answer["state"], state)
            self.assertFalse(answer["computer_name"])
            self.assertIsNone(answer["busy"])
        snapshot = self.server_command(process, events, "snapshot", "snapshot")
        self.assertEqual((snapshot["requests"], snapshot["connections"], snapshot["failedAttempts"]), (0, 0, 0))
        self.assertFalse(snapshot["pending"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
