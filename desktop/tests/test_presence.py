"""Presence probes use real TLS and never request remote-access approval."""
import concurrent.futures
import json
from pathlib import Path
import queue
import socket
import ssl
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import net
import presence
import host


PASSWORD = "presence-test-password"


class TlsStatusFixture:
    """One controlled TLS endpoint, with optional ALPN and raw replies."""

    def __init__(self, directory, alpn=None, response=None, wait_for_close=False):
        key, certificate, self.fingerprint = net._certificate(directory)
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context.load_cert_chain(certificate, key)
        if alpn:
            self.context.set_alpn_protocols([alpn])
        self.response = response
        self.wait_for_close = wait_for_close
        self.received = queue.Queue()
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.error = None
        self.sock = None
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(3)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        raw = None
        try:
            raw, _ = self.listener.accept()
            raw.settimeout(2)
            self.sock = self.context.wrap_socket(raw, server_side=True)
            self.ready.set()
            if self.response is not None:
                self.sock.sendall(self.response)
            # ALPN itself requests status. No branch may transmit application
            # bytes, credentials or a legacy login packet.
            if self.wait_for_close or self.response is not None:
                while True:
                    try:
                        data = self.sock.recv(4096)
                    except socket.timeout:
                        break
                    if not data:
                        break
                    self.received.put(data)
        except (OSError, ssl.SSLError) as exc:
            self.error = exc
        finally:
            if self.sock is not None:
                self.sock.close()
            elif raw is not None:
                raw.close()
            self.listener.close()
            self.finished.set()

    def close(self):
        for sock in (self.sock, self.listener):
            if sock:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()
        self.thread.join(timeout=3)

    def client_bytes(self):
        self.finished.wait(timeout=3)
        data = bytearray()
        while not self.received.empty():
            data.extend(self.received.get_nowait())
        return bytes(data)


class PresenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.servers = []
        self.fixtures = []
        self.clients = []
        self.consent = Mock(return_value=True)
        self.connected = queue.Queue()
        self.disconnected = queue.Queue()

    def tearDown(self):
        for client in self.clients:
            client.close()
        for server in self.servers:
            server.stop()
        for fixture in self.fixtures:
            fixture.close()
        self.directory.cleanup()

    def host(self, consent=None, name="Computador de teste"):
        server = net.HostServer(
            "127.0.0.1", 0, PASSWORD, consent or self.consent,
            lambda peer, name, address: self.connected.put(peer),
            lambda peer, reason: self.disconnected.put(peer),
            state_dir=Path(self.directory.name) / ("host-" + str(len(self.servers))),
            status_callback=lambda: name)
        self.servers.append(server)
        server.start()
        return server

    def fixture(self, **kwargs):
        fixture = TlsStatusFixture(
            Path(self.directory.name) / ("fixture-" + str(len(self.fixtures))), **kwargs)
        self.fixtures.append(fixture)
        return fixture

    def probe(self, endpoint, pin=True, timeout=1):
        fingerprint = endpoint.fingerprint if pin is True else pin
        return presence.probe("127.0.0.1", endpoint.port, fingerprint, timeout)

    def assert_no_session(self, server):
        self.consent.assert_not_called()
        self.assertTrue(self.connected.empty())
        self.assertTrue(self.disconnected.empty())
        with server._lock:
            self.assertFalse(server._pending)
            self.assertIsNone(server._peer)
            self.assertEqual(server._failed, {})

    def status_fixture(self, status=None, packet=None):
        if packet is None:
            status = status if status is not None else {
                "type": "status", "agent": "MiniAnyDesk", "version": "3.1.0",
                "name": "Oficina São Paulo", "online": True, "busy": False}
            encoded = json.dumps(status, ensure_ascii=False).encode("utf-8")
            packet = struct.pack("!I", len(encoded)) + encoded
        return self.fixture(alpn=net.STATUS_ALPN, response=packet)

    def test_known_pin_reports_online_name_and_idle_without_creating_a_session(self):
        server = self.host(name="Máquina da oficina")
        result = self.probe(server)
        self.assertEqual(result["state"], "online")
        self.assertEqual(result["fingerprint"], server.fingerprint)
        self.assertEqual(result["computer_name"], "Máquina da oficina")
        self.assertIs(result["busy"], False)
        self.assertEqual(result["version"], net.STATUS_VERSION)
        self.assert_no_session(server)

    def test_new_status_host_reports_availability_without_learning_a_pin(self):
        server = self.host()
        result = self.probe(server, pin=None)
        self.assertEqual(result["state"], "online")
        self.assertEqual(result["fingerprint"], server.fingerprint)
        self.assert_no_session(server)

    def test_repeated_probes_neither_rate_limit_logins_nor_reserve_approval(self):
        server = self.host()
        for _ in range(12):
            self.assertEqual(self.probe(server)["state"], "online")
        self.assert_no_session(server)
        client = net.connect("127.0.0.1", server.port, PASSWORD, "Controlador",
            lambda message: None, lambda reason: None,
            lambda address, port, fingerprint: fingerprint == server.fingerprint)
        self.clients.append(client)
        self.assertFalse(client.closed)
        self.assertEqual(client.computer_name, "Computador de teste")
        self.connected.get(timeout=2)
        self.consent.assert_called_once_with("Controlador", "127.0.0.1")

    def test_active_remote_session_is_reported_busy_and_remains_connected(self):
        server = self.host()
        client = net.connect("127.0.0.1", server.port, PASSWORD, "Controlador",
            lambda message: None, lambda reason: None,
            lambda address, port, fingerprint: fingerprint == server.fingerprint)
        self.clients.append(client)
        host_peer = self.connected.get(timeout=2)
        result = self.probe(server)
        self.assertEqual(result["state"], "online")
        self.assertIs(result["busy"], True)
        self.assertFalse(client.closed)
        self.assertFalse(host_peer.closed)
        with server._lock:
            self.assertIs(server._peer, host_peer)
            self.assertEqual(server._failed, {"127.0.0.1": []})
        self.consent.assert_called_once()

    def test_pending_local_approval_is_busy_and_probe_does_not_affect_decision(self):
        requested, release = threading.Event(), threading.Event()
        consent = Mock(side_effect=lambda name, ip: requested.set() or release.wait(5))
        server = self.host(consent=consent)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(net.connect, "127.0.0.1", server.port, PASSWORD,
                "Controlador", lambda message: None, lambda reason: None,
                lambda address, port, fingerprint: fingerprint == server.fingerprint)
            try:
                self.assertTrue(requested.wait(timeout=2))
                result = self.probe(server)
                self.assertEqual(result["state"], "online")
                self.assertIs(result["busy"], True)
                with server._lock:
                    self.assertTrue(server._pending)
                    self.assertIsNone(server._peer)
                consent.assert_called_once()
            finally:
                release.set()
            self.clients.append(pending.result(timeout=3))
        self.assertFalse(self.clients[-1].closed)

    def test_status_probes_do_not_increment_or_clear_failed_login_attempts(self):
        server = self.host()
        with self.assertRaises(PermissionError):
            net.connect("127.0.0.1", server.port, "wrong-password", "Controlador",
                lambda message: None, lambda reason: None,
                lambda address, port, fingerprint: fingerprint == server.fingerprint)
        with server._lock:
            attempts = list(server._failed["127.0.0.1"])
        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.probe(server)["state"], "online")
        with server._lock:
            self.assertEqual(server._failed, {"127.0.0.1": attempts})
            self.assertIsNone(server._peer)
            self.assertFalse(server._pending)
        self.consent.assert_not_called()

    def test_status_callback_cannot_override_real_busy_state(self):
        server = self.host(name={"name": "Nome local", "busy": True})
        result = self.probe(server)
        self.assertEqual(result["computer_name"], "Nome local")
        self.assertIs(result["busy"], False)
        self.assert_no_session(server)

    def test_legacy_tls_host_with_known_pin_is_online_and_gets_no_login_packet(self):
        fixture = self.fixture(wait_for_close=True)
        result = self.probe(fixture)
        self.assertEqual(result["state"], "online")
        self.assertEqual(result["fingerprint"], fixture.fingerprint)
        self.assertEqual(result["computer_name"], "")
        self.assertIsNone(result["busy"])
        self.assertEqual(result["version"], "")
        self.assertEqual(fixture.client_bytes(), b"")

    def test_unknown_legacy_tls_host_is_unverified_and_gets_no_login_packet(self):
        fixture = self.fixture(wait_for_close=True)
        result = self.probe(fixture, pin=None)
        self.assertEqual(result["state"], "unverified")
        self.assertEqual(result["fingerprint"], fixture.fingerprint)
        self.assertEqual(result["computer_name"], "")
        self.assertIsNone(result["busy"])
        self.assertEqual(fixture.client_bytes(), b"")

    def test_legacy_login_handler_sees_eof_without_failed_login_or_consent(self):
        server = self.host()
        server.context.set_alpn_protocols([])
        status = Mock(return_value="Nome que não deve ser consultado")
        server.status_callback = status
        for pin, state in ((True, "online"), (None, "unverified")):
            with self.subTest(pin=pin):
                result = self.probe(server, pin=pin)
                self.assertEqual(result["state"], state)
                self.assertEqual(result["computer_name"], "")
                self.assertIsNone(result["busy"])
                self.assert_no_session(server)
        status.assert_not_called()

    def test_status_alpn_alone_requests_status_and_sends_no_application_bytes(self):
        fixture = self.status_fixture()
        result = self.probe(fixture)
        self.assertEqual(result["state"], "online")
        self.assertEqual(result["computer_name"], "Oficina São Paulo")
        self.assertEqual(fixture.client_bytes(), b"")

    def test_changed_pin_is_unverified_before_accepting_a_status_payload(self):
        fixture = self.status_fixture()
        changed = "0" * 64 if fixture.fingerprint != "0" * 64 else "1" * 64
        result = self.probe(fixture, pin=changed)
        self.assertEqual(result["state"], "unverified")
        self.assertEqual(result["fingerprint"], fixture.fingerprint)
        self.assertEqual(result["computer_name"], "")
        self.assertIsNone(result["busy"])
        self.assertEqual(fixture.client_bytes(), b"")

    def test_pin_with_colons_whitespace_and_lowercase_matches_certificate(self):
        server = self.host()
        normalized = ":".join(server.fingerprint[i:i + 2].lower()
                              for i in range(0, len(server.fingerprint), 2))
        result = self.probe(server, pin="  " + normalized + "  ")
        self.assertEqual(result["state"], "online")
        self.assert_no_session(server)

    def test_invalid_pin_is_rejected_before_network_connection(self):
        for invalid in ("", "not-a-certificate", "a" * 63, "g" * 64):
            with self.subTest(pin=invalid), self.assertRaises(ValueError):
                presence.probe("127.0.0.1", 1, invalid, timeout=.1)

    def test_stopped_host_is_offline_and_stale_metadata_is_absent(self):
        server = self.host()
        self.assertEqual(self.probe(server)["state"], "online")
        server.stop()
        result = self.probe(server)
        self.assertEqual(result["state"], "offline")
        self.assertEqual(result["computer_name"], "")
        self.assertIsNone(result["busy"])
        self.assertTrue(result["detail"])

    def test_malformed_frames_do_not_claim_online_or_trusted_status(self):
        surrogate_status = (
            b'{"type":"status","agent":"MiniAnyDesk","version":"3.1.0",'
            b'"name":"Nome \\ud800","online":true,"busy":false}')
        packets = [
            struct.pack("!I", 0),
            struct.pack("!I", 2049),
            struct.pack("!I", net.MAX_MESSAGE + 1),
            struct.pack("!I", 2) + b"[]",
            struct.pack("!I", 2) + b"{!",
            struct.pack("!I", 9) + b"{}",
            struct.pack("!I", len(surrogate_status)) + surrogate_status,
        ]
        for packet in packets:
            with self.subTest(packet=packet):
                fixture = self.status_fixture(packet=packet)
                result = self.probe(fixture, timeout=.3)
                self.assertEqual(result["state"], "offline")
                self.assertEqual(result["computer_name"], "")
                self.assertIsNone(result["busy"])
                self.assertTrue(result["detail"])
                self.assertEqual(fixture.client_bytes(), b"")

    def test_malformed_status_fields_are_rejected_instead_of_coerced(self):
        valid = {"type": "status", "agent": "MiniAnyDesk", "version": "3.1.0",
                 "name": "Máquina", "online": True, "busy": False}
        invalid = [{**valid, "type": "auth_ok"}, {**valid, "agent": "Outro produto"},
                   {**valid, "online": "true"}, {**valid, "busy": "false"},
                   {**valid, "name": ["Nome"]}, {**valid, "name": "x" * 81},
                   {**valid, "name": "Nome\nremoto"}, {**valid, "name": "Nome\x7f"},
                   {**valid, "version": None}, {**valid, "version": ""},
                   {**valid, "version": "v" * 33}]
        for status in invalid:
            with self.subTest(status=status):
                fixture = self.status_fixture(status=status)
                result = self.probe(fixture)
                self.assertNotEqual(result["state"], "online")
                self.assertEqual(result["computer_name"], "")
                self.assertIsNone(result["busy"])
                self.assertEqual(fixture.client_bytes(), b"")

    def test_status_timeout_is_bounded_and_closes_without_authentication(self):
        fixture = self.fixture(alpn=net.STATUS_ALPN, wait_for_close=True)
        started = time.monotonic()
        result = self.probe(fixture, timeout=.15)
        self.assertLess(time.monotonic() - started, 1)
        self.assertNotEqual(result["state"], "online")
        self.assertTrue(result["detail"])
        self.assertEqual(fixture.client_bytes(), b"")

    def test_bad_idna_names_do_not_kill_dns_workers_or_prevent_a_later_healthy_probe(self):
        server = self.host()
        # The IDNA codec rejects labels longer than 63 characters with
        # UnicodeError, even though the whole hostname is below 253 characters.
        for index in range(6):
            invalid = ("x" * 64) + ".%d.invalid" % index
            result = presence.probe(invalid, server.port, server.fingerprint, timeout=.3)
            self.assertEqual(result["state"], "offline")
        address = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                    "", ("127.0.0.1", server.port))]
        with patch.object(presence.socket, "getaddrinfo", return_value=address):
            result = presence.probe("healthy.presence.invalid", server.port,
                                    server.fingerprint, timeout=1)
        self.assertEqual(result["state"], "online")
        self.assert_no_session(server)

    def test_slow_dns_resolution_obeys_probe_deadline_without_connecting(self):
        entered, release = threading.Event(), threading.Event()

        def stalled_resolution(*args, **kwargs):
            entered.set()
            release.wait(timeout=3)
            return []

        try:
            with patch.object(presence.socket, "getaddrinfo", side_effect=stalled_resolution):
                started = time.monotonic()
                result = presence.probe("slow.presence.invalid", 64321, timeout=.1)
                elapsed = time.monotonic() - started
            self.assertTrue(entered.wait(timeout=.2))
            self.assertLess(elapsed, .6)
            self.assertEqual(result["state"], "offline")
            self.assertEqual(result["fingerprint"], "")
            self.assertIsNone(result["busy"])
        finally:
            release.set()

    def test_numeric_address_does_not_depend_on_dns_resolution(self):
        server = self.host()
        getaddrinfo = presence.socket.getaddrinfo

        def numeric_only(*args, **kwargs):
            flags = args[5] if len(args) > 5 else kwargs.get("flags", 0)
            self.assertTrue(flags & socket.AI_NUMERICHOST)
            return getaddrinfo(*args, **kwargs)

        with patch.object(presence.socket, "getaddrinfo", side_effect=numeric_only):
            result = self.probe(server)
        self.assertEqual(result["state"], "online")
        self.assert_no_session(server)

    def test_concurrent_blocked_dns_queries_are_bounded_and_leave_numeric_hosts_available(self):
        server = self.host()
        release = threading.Event()
        getaddrinfo = presence.socket.getaddrinfo
        calls = []
        lock = threading.Lock()

        def resolution(*args, **kwargs):
            flags = args[5] if len(args) > 5 else kwargs.get("flags", 0)
            if flags & socket.AI_NUMERICHOST:
                return getaddrinfo(*args, **kwargs)
            with lock:
                calls.append(args[0])
            release.wait(timeout=3)
            return []

        try:
            with patch.object(presence.socket, "getaddrinfo", side_effect=resolution):
                with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
                    pending = [executor.submit(presence.probe, "blocked-%d.presence.invalid" % i,
                                               64321, None, .15) for i in range(12)]
                    results = [job.result(timeout=1) for job in pending]
                    self.assertTrue(all(result["state"] == "offline" for result in results))
                    self.assertEqual(self.probe(server)["state"], "online")
                self.assertLessEqual(len(calls), 4)
        finally:
            release.set()
        self.assert_no_session(server)


class ComputerNameTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.folder = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def app(self, name="Computador original"):
        app = host.HostApp.__new__(host.HostApp)
        app.state_dir = self.folder
        app.control_lock = threading.RLock()
        app._computer_name_cache = name
        app.computer_name = Mock()
        app.computer_name.get.return_value = name
        app.log = Mock()
        app.root = Mock()
        app._begin_prompt = Mock()
        app._end_prompt = Mock()
        app.server = object()
        app.peer = Mock()
        app.sharing = True
        app.local_paused = False
        app.prompt_count = 0
        app.inputs = Mock()
        return app

    def test_unicode_name_persists_for_next_launch_without_saving_credentials(self):
        saved = host.save_computer_name(self.folder, "  Estação   da Recepção  ")
        self.assertEqual(saved, "Estação da Recepção")
        self.assertEqual(host.load_computer_name(self.folder), saved)
        self.assertEqual(json.loads((self.folder / "host-settings.json").read_text(encoding="utf-8")),
                         {"computer_name": saved})
        self.assertEqual(list(self.folder.glob("*.tmp")), [])

    def test_invalid_rename_preserves_the_previous_saved_name(self):
        original = host.save_computer_name(self.folder, "Computador original")
        for invalid in ("", "   ", "x" * 81, "Nome\x00", "Nome\x7f",
                        "Nome\ud800", "Nome\udfff", None, 123):
            with self.subTest(name=invalid), self.assertRaises(ValueError):
                host.save_computer_name(self.folder, invalid)
            self.assertEqual(host.load_computer_name(self.folder), original)

    def test_failed_atomic_replace_preserves_the_original_and_cleans_temporary_file(self):
        original = host.save_computer_name(self.folder, "Computador original")
        with patch.object(host.os, "replace", side_effect=OSError("Disco indisponível")):
            with self.assertRaises(OSError):
                host.save_computer_name(self.folder, "Nome novo")
        self.assertEqual(host.load_computer_name(self.folder), original)
        self.assertEqual(list(self.folder.glob("*.tmp")), [])

    def test_missing_invalid_and_oversized_settings_fall_back_to_local_hostname(self):
        path = self.folder / "host-settings.json"
        with patch.object(host.socket, "gethostname", return_value="PC-LOCAL"):
            self.assertEqual(host.load_computer_name(self.folder), "PC-LOCAL")
            for content in ("[]", "invalid json", '{"computer_name": null}',
                            '{"computer_name": ""}', "x" * 4097):
                with self.subTest(content=content[:80]):
                    path.write_text(content, encoding="utf-8")
                    self.assertEqual(host.load_computer_name(self.folder), "PC-LOCAL")

    def test_live_ui_rename_updates_status_cache_without_touching_current_session(self):
        app = self.app()
        server, peer = app.server, app.peer
        app.computer_name.get.return_value = "  Escritório   principal  "
        app.save_computer_name()
        self.assertEqual(app._presence_name(), "Escritório principal")
        self.assertEqual(host.load_computer_name(self.folder), "Escritório principal")
        app.computer_name.set.assert_called_once_with("Escritório principal")
        self.assertIs(app.server, server)
        self.assertIs(app.peer, peer)
        self.assertFalse(app.local_paused)
        app.inputs.release.assert_not_called()
        app._begin_prompt.assert_not_called()
        peer.send.assert_called_once_with({
            "type": "state", "sharing": True, "localPaused": False,
            "promptActive": False, "captureAvailable": True, "computerName": "Escritório principal",
            "streamVersion": 1, "streamSettingsVersion": 1, "agentVersion": host.VERSION})

    def test_presence_worker_reads_cached_name_without_calling_tk(self):
        app = self.app(name="Nome aprovado")
        app.computer_name.get.side_effect = AssertionError("Variável Tk fora da thread UI")
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            self.assertEqual(executor.submit(app._presence_name).result(timeout=1), "Nome aprovado")
        app.computer_name.get.assert_not_called()

    def test_failed_ui_rename_keeps_cache_and_guards_the_error_dialog(self):
        app = self.app()
        app.computer_name.get.return_value = ""
        with patch.object(host.messagebox, "showerror") as show_error:
            app.save_computer_name()
        self.assertEqual(app._presence_name(), "Computador original")
        app.computer_name.set.assert_not_called()
        app.log.assert_not_called()
        app.peer.send.assert_not_called()
        app._begin_prompt.assert_called_once()
        app._end_prompt.assert_called_once()
        show_error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
