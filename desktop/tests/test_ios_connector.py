"""Loopback capability, session ownership and actual approved TLS bridge checks."""
from pathlib import Path
import http.client
import io
import json
import secrets
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ios_connector as ios
import mesh
import net


def wait_for(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Condition did not become true")


class HostFixture:
    def __init__(self, directory, *, allow=True, hold=None):
        self.messages = []
        self.requests = []
        self.closed = threading.Event()
        self.requested = threading.Event()
        self.peer = None
        self.password = "synthetic-iphone-password"
        def approval(name, address):
            self.requests.append((name, address)); self.requested.set()
            if hold is not None:
                hold.wait(3)
            return allow
        def connected(peer, name, address):
            self.peer = peer
            peer.on_message = lambda message: self.messages.append(message)
            peer.send({"type": "state", "sharing": True, "computerName": "PC de teste"})
        self.server = net.HostServer("127.0.0.1", 0, self.password, approval,
            connected, lambda peer, reason: self.closed.set(), state_dir=Path(directory)/"identity")
        self.server.start()
        self.pairing = mesh.HostPairing(secrets.token_bytes(32), self.server.port, self.server.fingerprint)

    def configuration(self, **changes):
        value = {"invitation": self.pairing.code, "password": self.password,
                 "mode": "local", "name": "iPhone de teste"}
        value.update(changes)
        return value

    def close(self):
        self.server.stop()


class HttpFixture(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.directory = Path(self.folder.name)
        self.assets = self.directory / "assets"; self.assets.mkdir()
        (self.assets/"bridge.html").write_text("<!doctype html><title>Conector</title>")
        (self.assets/"protocol.mjs").write_text("export const version = 1;")
        self.host = HostFixture(self.directory)
        self.connector = ios.start_connector(asset_root=self.assets)

    def tearDown(self):
        self.connector.stop(); self.host.close(); self.folder.cleanup()

    def request(self, method, path, value=None, *, headers=None, auth=True, origin=True, raw=None):
        supplied = dict(headers or {})
        if auth:
            supplied.setdefault("Authorization", "Bearer " + self.connector.capability)
        if origin and method == "POST":
            supplied.setdefault("Origin", self.connector.origin)
        body = raw
        if value is not None:
            body = json.dumps(value).encode()
        if body is not None:
            supplied.setdefault("Content-Type", "application/json")
        connection = http.client.HTTPConnection("127.0.0.1", self.connector.server_port, timeout=4)
        try:
            connection.request(method, path, body=body, headers=supplied)
            response = connection.getresponse()
            data = response.read()
            result = json.loads(data) if "application/json" in response.getheader("Content-Type", "") else data
            return response.status, result, dict(response.getheaders())
        finally:
            connection.close()

    def authorize(self):
        self.assertEqual(self.request("POST", "/api/configure", self.host.configuration())[0], 200)
        status, reply, _ = self.request("POST", "/api/connect", {"name": "Meu iPhone"})
        self.assertEqual(status, 202)
        generation = reply["generation"]
        wait_for(lambda: self.connector.session.state == "authorized")
        return generation


class ConnectorHttpSecurityTests(HttpFixture):
    def test_loopback_only_and_fragment_capability_not_in_static_html(self):
        self.assertEqual(self.connector.server_address[0], "127.0.0.1")
        self.assertIn("/#local=", self.connector.url)
        status, body, headers = self.request("GET", "/", auth=False)
        self.assertEqual(status, 200)
        self.assertNotIn(self.connector.capability.encode(), body)
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_api_requires_exact_capability_even_with_a_cookie(self):
        self.assertEqual(self.request("GET", "/api/config", auth=False)[0], 401)
        self.assertEqual(self.request("GET", "/api/config", headers={"Authorization": "Bearer wrong"})[0], 401)
        self.assertEqual(self.request("GET", "/api/config", auth=False,
            headers={"Cookie": "local="+self.connector.capability})[0], 401)
        self.assertEqual(self.request("GET", "/api/config?local="+self.connector.capability, auth=False)[0], 400)

    def test_exact_host_and_origin_and_state_change_requires_origin(self):
        for host in ("evil.invalid", "localhost:"+str(self.connector.server_port), "127.0.0.1"):
            self.assertEqual(self.request("GET", "/api/config", headers={"Host": host})[0], 403)
        self.assertEqual(self.request("GET", "/api/config", headers={"Origin": "https://evil.invalid"})[0], 403)
        self.assertEqual(self.request("POST", "/api/disconnect", {}, origin=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/disconnect", {}, headers={"Origin": "null"})[0], 403)
        self.assertEqual(self.request("OPTIONS", "/api/command", headers={"Origin": "https://evil.invalid"})[0], 403)

    def test_absolute_proxy_target_is_rejected(self):
        self.assertEqual(self.request("GET", "http://evil.invalid/api/config")[0], 400)

    def test_configuration_metadata_never_returns_host_password_or_private_code(self):
        config = self.host.configuration()
        self.assertEqual(self.request("POST", "/api/configure", config)[0], 200)
        status, result, _ = self.request("GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertTrue(result["configured"])
        self.assertEqual(result["mode"], "local")
        self.assertTrue(result["appUrl"].endswith("/ios-pwa/"))
        for serialized in (json.dumps(result), repr(self.connector.session.configuration)):
            self.assertNotIn(config["password"], serialized)
            self.assertNotIn(config["invitation"], serialized)

    def test_body_bounds_json_type_nan_and_request_framing_are_checked(self):
        self.assertEqual(self.request("POST", "/api/connect", raw=b"[]")[0], 400)
        self.assertEqual(self.request("POST", "/api/connect", raw=b'{"name":NaN}')[0], 400)
        self.assertEqual(self.request("POST", "/api/connect", raw=b"\xff")[0], 400)
        self.assertEqual(self.request("POST", "/api/connect", raw=b"{}",
            headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/api/connect", raw=b"{}",
            headers={"Content-Length": str(ios.MAX_BODY + 1)})[0], 413)
        self.assertEqual(self.request("POST", "/api/connect", raw=b"{}",
            headers={"Transfer-Encoding": "chunked"})[0], 400)

    def test_static_paths_cannot_read_parent_hidden_files_or_symlinks(self):
        secret = self.directory/"private.mjs"; secret.write_text("private material")
        (self.assets/"secret.py").write_text("private material")
        (self.assets/".private.mjs").write_text("private material")
        (self.assets/"linked.mjs").symlink_to(secret)
        (self.assets/"nested").symlink_to(self.directory, target_is_directory=True)
        for path in ("/../private.mjs", "/%2e%2e/private.mjs", "/secret.py", "/.private.mjs",
                     "/linked.mjs", "/nested/private.mjs", "/foo%5c..%5cprivate.mjs"):
            self.assertEqual(self.request("GET", path, auth=False)[0], 404, path)
        status, data, headers = self.request("GET", "/protocol.mjs", auth=False)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/javascript")

    def test_generation_header_is_required_for_commands_and_checked_on_pulse(self):
        generation = self.authorize()
        command = {"type": "input", "action": "release"}
        self.assertEqual(self.request("POST", "/api/command", command)[0], 409)
        self.assertEqual(self.request("POST", "/api/command", command,
            headers={"X-AADX-Generation": str(generation - 1)})[0], 409)
        self.assertEqual(self.request("POST", "/api/pulse", {},
            headers={"X-AADX-Generation": str(generation - 1)})[0], 409)
        self.assertEqual(self.request("POST", "/api/pulse", {},
            headers={"X-AADX-Generation": str(generation)})[0], 200)


class ConnectorTlsOwnershipTests(HttpFixture):
    def test_actual_tls_authorization_then_commands_remain_fifo(self):
        generation = self.authorize()
        commands = [
            {"type": "input", "action": "key", "key": "ctrl", "down": True, "captureId": "capture-one"},
            {"type": "input", "action": "button", "button": "right", "down": True, "x": .2, "y": .3, "captureId": "capture-one"},
            {"type": "input", "action": "button", "button": "right", "down": False},
            {"type": "input", "action": "key", "key": "ctrl", "down": False},
            {"type": "input", "action": "release"},
        ]
        for command in commands:
            self.assertEqual(self.request("POST", "/api/command", command,
                headers={"X-AADX-Generation": str(generation)})[0], 202)
        wait_for(lambda: len(self.host.messages) == len(commands))
        self.assertEqual(self.host.messages, commands)
        self.assertEqual(self.host.requests[0][0], "Meu iPhone")

    def test_invalid_reconfiguration_preserves_authorized_peer(self):
        generation = self.authorize()
        peer = self.connector.session.peer
        self.assertEqual(self.request("POST", "/api/configure", self.host.configuration(password="short"))[0], 400)
        self.assertIs(self.connector.session.peer, peer)
        self.assertEqual(self.connector.session.generation, generation)
        self.assertFalse(peer.closed)

    def test_wrong_tls_identity_is_rejected_before_host_receives_password_request(self):
        wrong = mesh.HostPairing(secrets.token_bytes(32), self.host.server.port, "AA"*32)
        self.request("POST", "/api/configure", self.host.configuration(invitation=wrong.code))
        self.assertEqual(self.request("POST", "/api/connect", {})[0], 202)
        wait_for(lambda: self.connector.session.state == "error")
        self.assertEqual(self.host.requests, [])
        self.assertIsNone(self.connector.session.peer)
        self.assertNotIn(self.host.password, self.connector.session.message)

    def test_connection_waits_for_host_consent_and_early_state_follows_authorized(self):
        self.connector.stop(); self.host.close()
        hold = threading.Event()
        self.host = HostFixture(self.directory/"waiting", hold=hold)
        def connect_with_early_state(*args, **kwargs):
            peer = net.connect(*args, **kwargs)
            args[4]({"type": "state", "sharing": True, "computerName": "Early state"})
            return peer
        session = ios.ConnectorSession(connect_function=connect_with_early_state)
        self.connector = ios.ConnectorServer(session, self.assets)
        threading.Thread(target=self.connector.serve_forever, daemon=True).start()
        session.configure(self.host.configuration())
        generation = session.connect()["generation"]
        self.assertTrue(self.host.requested.wait(2))
        self.assertTrue(session.connecting)
        self.assertIsNone(session.peer)
        self.assertEqual(session.state, "awaiting")
        hold.set()
        wait_for(lambda: session.state == "authorized")
        reader = session.events.subscribe()
        first = json.loads(session.events.next(reader, timeout=1))
        self.assertEqual((first["type"], first["state"], first["generation"]), ("bridge_state", "authorized", generation))
        second = json.loads(session.events.next(reader, timeout=1))
        self.assertEqual(second["type"], "bridge_packet")
        self.assertEqual(second["generation"], generation)
        self.assertEqual(second["message"]["type"], "state")

    def test_disconnect_and_old_callbacks_never_reach_new_generation(self):
        generation = self.authorize()
        peer = self.connector.session.peer
        self.assertEqual(self.request("POST", "/api/disconnect", {},
            headers={"X-AADX-Generation": str(generation)})[0], 200)
        self.assertTrue(peer.closed)
        self.assertTrue(self.host.closed.wait(2))
        self.connector.session._on_message({"type": "state", "computerName": "stale"}, generation)
        self.assertEqual(self.request("POST", "/api/disconnect", {},
            headers={"X-AADX-Generation": str(generation)})[0], 409)
        self.assertEqual(self.request("POST", "/api/command", {"type": "input", "action": "release"},
            headers={"X-AADX-Generation": str(generation)})[0], 409)
        reader = self.connector.session.events.subscribe()
        event = json.loads(self.connector.session.events.next(reader))
        self.assertEqual(event["state"], "closed")
        self.assertIsNone(self.connector.session.events.next(reader, timeout=.01))

    def test_event_http_stream_is_authenticated_ndjson_with_generation(self):
        generation = self.authorize()
        connection = http.client.HTTPConnection("127.0.0.1", self.connector.server_port, timeout=3)
        try:
            connection.request("GET", "/api/events", headers={"Authorization": "Bearer "+self.connector.capability})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn("application/x-ndjson", response.getheader("Content-Type"))
            first = json.loads(response.readline())
            self.assertEqual(first["generation"], generation)
            self.assertEqual(first["state"], "authorized")
            second = json.loads(response.readline())
            self.assertEqual(second["type"], "bridge_packet")
            self.assertEqual(second["generation"], generation)
        finally:
            connection.close()

    def test_shutdown_closes_peer_and_listener(self):
        self.authorize()
        peer = self.connector.session.peer
        self.assertEqual(self.request("POST", "/api/shutdown", {})[0], 200)
        self.assertTrue(self.connector.stopped.wait(2))
        wait_for(lambda: peer.closed)
        self.assertTrue(self.host.closed.wait(2))

    def test_host_refusal_is_not_treated_as_a_live_phone_session(self):
        self.connector.stop(); self.host.close()
        self.host = HostFixture(self.directory/"denied", allow=False)
        self.connector = ios.start_connector(self.host.configuration(), self.assets)
        self.assertEqual(self.request("POST", "/api/connect", {})[0], 202)
        wait_for(lambda: self.connector.session.state == "error")
        self.assertEqual(len(self.host.requests), 1)
        self.assertIsNone(self.connector.session.peer)
        self.assertIsNone(self.host.peer)

    def test_second_connector_and_its_mesh_owner_survive_first_disconnect(self):
        second_host = HostFixture(self.directory/"second")
        managers = []
        class Manager:
            def __init__(self, role, config_dir, on_status=None):
                self.role, self.config_dir = role, config_dir
                self.stopped = False
                managers.append(self)
            def start(self, pairing): self.pairing = pairing
            def wait_ready(self, timeout, cancel_event): return "127.0.0.1", self.pairing.port
            def stop(self): self.stopped = True
            def join(self, timeout=5): return True
        first = ios.ConnectorSession(mesh_factory=Manager)
        second = ios.ConnectorSession(mesh_factory=Manager)
        try:
            first.configure(self.host.configuration(mode="mesh"))
            second.configure(second_host.configuration(mode="mesh"))
            first.connect(); generation = second.connect()["generation"]
            wait_for(lambda: first.state == second.state == "authorized")
            self.assertEqual(len(managers), 2)
            self.assertNotEqual(managers[0].config_dir, managers[1].config_dir)
            first.disconnect()
            self.assertTrue(managers[0].stopped)
            self.assertFalse(managers[1].stopped)
            self.assertFalse(second.peer.closed)
            second.command({"type": "input", "action": "release"}, generation)
            wait_for(lambda: len(second_host.messages) == 1)
            self.assertEqual(second_host.messages[0]["action"], "release")
        finally:
            first.close(); second.close(); second_host.close()


class ConnectorQueueAndRulesTests(unittest.TestCase):
    def test_events_keep_latest_frame_but_all_reliable_messages(self):
        queue = ios.BoundedEvents()
        queue.put({"type": "state"})
        queue.put({"type": "screen", "frame": 1}, frame=True)
        queue.put({"type": "file_offer", "id": "offer"})
        queue.put({"type": "screen", "frame": 2}, frame=True)
        reader = queue.subscribe()
        events = [json.loads(queue.next(reader)) for _ in range(3)]
        self.assertEqual([e["type"] for e in events], ["state", "file_offer", "screen"])
        self.assertEqual(events[-1]["frame"], 2)
        self.assertEqual(queue.bytes, 0)
        self.assertIsNone(queue.next(reader, timeout=.01))

    def test_event_byte_and_message_bounds_and_new_reader_replaces_old(self):
        queue = ios.BoundedEvents(max_messages=2, max_bytes=300)
        self.assertTrue(queue.put({"type": "a"}))
        self.assertTrue(queue.put({"type": "b"}))
        self.assertFalse(queue.put({"type": "c"}))
        first = queue.subscribe(); second = queue.subscribe()
        self.assertEqual(queue.next(first), b"")
        self.assertEqual(json.loads(queue.next(second))["type"], "a")
        self.assertFalse(queue.put({"type": "large", "body": "x"*300}))
        queue.close(); self.assertEqual(queue.next(second), b"")

    def test_command_rules_reject_nonfinite_or_out_of_range_and_non_wire_actions(self):
        invalid = [
            {"type": "login", "password": "secret"},
            {"type": "input", "action": "text", "text": "test"},
            {"type": "input", "action": "move", "captureId": "a", "x": float("nan"), "y": .2},
            {"type": "input", "action": "move", "captureId": "a", "x": 2, "y": .2},
            {"type": "input", "action": "key", "captureId": "a", "key": "a", "down": 1},
            {"type": "sharing", "enabled": "yes"},
            {"type": "monitor", "id": 65},
        ]
        for message in invalid:
            with self.subTest(message=message), self.assertRaises(ios.ConnectorError):
                ios.validate_command(message)
        self.assertEqual(ios.validate_command({"type": "input", "action": "key", "key": "ctrl", "down": False})["down"], False)

    def test_configuration_requires_current_tls_code_and_safe_password(self):
        pair = mesh.HostPairing(secrets.token_bytes(32), 64321, "11"*32)
        good = {"invitation": pair.code, "password": "test-only-password", "mode": "local"}
        self.assertEqual(ios.Configuration.parse(good).mode, "local")
        invitation = "AliançaDX Host 6.3.0\nComputador: Exemplo\nCódigo privado: "+pair.code+"\nSenha: separate"
        self.assertEqual(ios.Configuration.parse({**good, "invitation": invitation}).pairing, pair)
        for change in ({"password": "x"}, {"mode": "public"}, {"name": "bad\nname"},
                       {"invitation": mesh.HostPairing(secrets.token_bytes(32)).code}):
            with self.assertRaises(ios.ConnectorError):
                ios.Configuration.parse({**good, **change})

    def test_bootstrap_is_bounded_single_json_line_and_sanitized(self):
        pair = mesh.HostPairing(secrets.token_bytes(32), 64321, "22"*32)
        good = {"invitation": pair.code, "password": "stdin-only-password", "mode": "local"}
        self.assertEqual(ios.read_bootstrap(io.StringIO(json.dumps(good)+"\nignored")), good)
        for value in ("", "not json", "x"*16385, "[]"):
            with self.assertRaises(ios.ConnectorError) as caught:
                ios.read_bootstrap(io.StringIO(value))
            self.assertNotIn("stdin-only-password", str(caught.exception))

    def test_watchdog_requires_real_pulse_not_server_events_or_gets(self):
        class Clock:
            value = 0
            def __call__(self): return self.value
        class Peer:
            closed = False
            computer_name = "fixture"
            def send(self, message): return True
            def close(self): self.closed = True
        clock = Clock(); peer = Peer()
        session = ios.ConnectorSession(connect_function=lambda *a, **kw: peer, clock=clock)
        try:
            pair = mesh.HostPairing(secrets.token_bytes(32), 64321, "33"*32)
            session.configure({"invitation": pair.code, "password": "test-password"})
            generation = session.connect()["generation"]
            wait_for(lambda: session.state == "authorized")
            clock.value = 20
            session.pulse(generation)
            clock.value = 40
            session.touch()  # A continuing stream/GET is not a JS pulse.
            self.assertFalse(session.check_lease())
            self.assertFalse(peer.closed)
            clock.value = 51
            session.touch()
            session.check_lease()
            wait_for(lambda: peer.closed)
            self.assertEqual(session.state, "closed")
            self.assertEqual(session.commands, ios.deque())
        finally:
            session.close()

    def test_stale_connection_result_is_closed_instead_of_published(self):
        entered, release = threading.Event(), threading.Event()
        class Peer:
            closed = False
            def close(self): self.closed = True
        peer = Peer()
        def late_connect(*a, **kw):
            entered.set(); release.wait(2); return peer
        session = ios.ConnectorSession(connect_function=late_connect)
        try:
            pair = mesh.HostPairing(secrets.token_bytes(32), 64321, "44"*32)
            session.configure({"invitation": pair.code, "password": "test-password"})
            session.connect(); self.assertTrue(entered.wait(1))
            session.disconnect(); release.set()
            wait_for(lambda: peer.closed)
            self.assertIsNone(session.peer)
            self.assertEqual(session.state, "closed")
        finally:
            release.set(); session.close()

    def test_command_queue_overflow_closes_only_its_peer_instead_of_dropping_releases(self):
        entered, release = threading.Event(), threading.Event()
        class Peer:
            closed = False
            computer_name = "fixture"
            def send(self, message):
                entered.set(); release.wait(2); return not self.closed
            def close(self): self.closed = True
        peer = Peer()
        session = ios.ConnectorSession(connect_function=lambda *a, **kw: peer)
        try:
            pair = mesh.HostPairing(secrets.token_bytes(32), 64321, "55"*32)
            session.configure({"invitation": pair.code, "password": "test-password"})
            generation = session.connect()["generation"]
            wait_for(lambda: session.state == "authorized")
            command = {"type": "input", "action": "release"}
            session.command(command, generation); self.assertTrue(entered.wait(1))
            for _ in range(ios.MAX_QUEUE_MESSAGES):
                session.command(command, generation)
            with self.assertRaises(ios.ConnectorError) as caught:
                session.command(command, generation)
            self.assertEqual(caught.exception.status, 429)
            wait_for(lambda: peer.closed)
            self.assertIsNone(session.peer)
            self.assertEqual(session.state, "error")
            self.assertEqual(len(session.commands), 0)
        finally:
            release.set(); session.close()

    def test_state_pause_discards_pending_old_frame(self):
        class Peer:
            closed = False
            computer_name = "fixture"
            def close(self): self.closed = True
        session = ios.ConnectorSession()
        try:
            session.peer = Peer()
            session.state = "authorized"
            generation = session.generation
            session.events.reset(session._state_message())
            session._on_message({"type": "screen", "data": "old-image"}, generation)
            session._on_message({"type": "state", "sharing": False, "promptActive": True}, generation)
            reader = session.events.subscribe()
            session.events.next(reader)
            event = json.loads(session.events.next(reader))
            self.assertEqual(event["message"]["type"], "state")
            self.assertIsNone(session.events.next(reader, timeout=.01))
        finally:
            session.close()

    def test_failed_or_expired_old_pulse_never_renews_new_generation(self):
        class Clock:
            value = 0
            def __call__(self): return self.value
        clock = Clock()
        session = ios.ConnectorSession(clock=clock)
        try:
            session.pulse()
            old = session.generation
            session.disconnect()
            clock.value = 10
            with self.assertRaises(ios.ConnectorError):
                session.pulse(old)
            self.assertEqual(session.last_pulse, 0)
            self.assertEqual(session.last_http, 0)
        finally:
            session.close()


class BrowserLaunchTests(unittest.TestCase):
    def test_missing_browser_reports_failure_and_closes_loopback_listener(self):
        with tempfile.TemporaryDirectory() as folder:
            assets = Path(folder)
            (assets / "bridge.html").write_text("<!doctype html><title>Conector</title>")
            created = []
            server_class = ios.ConnectorServer
            def record_server(*args, **kwargs):
                server = server_class(*args, **kwargs)
                created.append(server)
                return server
            with mock.patch.object(ios, "ConnectorServer", side_effect=record_server), \
                    mock.patch.object(ios.webbrowser, "open", return_value=False):
                with self.assertRaises(ios.ConnectorError):
                    ios.start_connector(asset_root=assets, open_browser=True)
            self.assertEqual(len(created), 1)
            self.assertTrue(created[0].stopped.is_set())
            self.assertTrue(created[0].session.closed)
            connection = http.client.HTTPConnection("127.0.0.1", created[0].server_port, timeout=1)
            try:
                with self.assertRaises(OSError):
                    connection.request("GET", "/")
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
