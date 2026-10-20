"""Java socket/stream regressions using the production Kotlin Peer and policy."""
import json
import os
import subprocess
import socket
import ssl
import struct
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest


@unittest.skipUnless(os.environ.get("MINIANYDESK_PEER_STABILITY_CLASSPATH"), "Compile the Peer stability harness first")
class PeerStabilityTests(unittest.TestCase):
    def run_case(self, mode):
        result = subprocess.run(["java", "-cp", os.environ["MINIANYDESK_PEER_STABILITY_CLASSPATH"],
            "br.com.perabru.minianydesk.PeerStabilityHarness", mode], capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout.strip()), {"result": "passed", "case": mode})

    def test_partial_header_body_utf8_and_transient_read_timeouts(self): self.run_case("fragments")
    def test_mouse_coalescing_preserves_capture_click_wheel_file_barriers(self): self.run_case("pointer-barriers")
    def test_disposable_move_and_reliable_release_under_short_backpressure(self): self.run_case("queue-pressure")
    def test_full_queue_keeps_prompt_state_and_file_ack_in_fifo(self): self.run_case("reserved-state-ack")
    def test_unknown_partial_output_write_is_never_replayed(self): self.run_case("partial-write-failure")
    def test_stalled_writer_expires_even_while_pongs_arrive(self): self.run_case("tx-idle-pongs")
    def test_live_heartbeat_then_real_silence_deadline(self): self.run_case("heartbeat-silence")

    def connection_case(self, mode, *args):
        result = subprocess.run(["java", "-cp", os.environ["MINIANYDESK_PEER_STABILITY_CLASSPATH"],
            "br.com.perabru.minianydesk.ConnectionDeadlineHarness", mode, *map(str, args)],
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout.strip()), {"result": "passed", "case": mode})

    def test_dns_cancel_is_prompt_before_socket_or_credentials(self): self.connection_case("dns-cancel")
    def test_dns_workers_are_bounded_and_numeric_address_bypasses_blocked_resolvers(self): self.connection_case("dns-bounded-numeric")

    def test_total_authentication_deadline_cannot_be_extended_by_partial_response(self):
        reference = Path(os.environ.get("MINIANYDESK_PYTHON_SOURCE", Path(__file__).parent / "python_reference"))
        sys.path.insert(0, str(reference))
        import net
        with tempfile.TemporaryDirectory() as directory:
            key, cert, pin = net._certificate(directory)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(cert, key)
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0)); listener.listen(1); listener.settimeout(2)
            finished = threading.Event()
            observed = {"login": False, "closed": False}
            def server():
                sock = None
                try:
                    sock, _ = listener.accept(); sock.settimeout(2)
                    sock = context.wrap_socket(sock, server_side=True)
                    observed["login"] = net._read(sock)["password"] == "testpassword"
                    data = json.dumps({"type": "auth_ok", "version": "3.0.0"}).encode()
                    for byte in struct.pack("!I", len(data)) + data:
                        sock.sendall(bytes([byte])); time.sleep(.06)
                except OSError:
                    observed["closed"] = True
                finally:
                    if sock is not None: sock.close()
                    finished.set()
            thread = threading.Thread(target=server, daemon=True); thread.start()
            try:
                self.connection_case("auth-trickle", listener.getsockname()[1], pin)
                self.assertTrue(finished.wait(2))
                self.assertTrue(observed["login"], observed)
                self.assertTrue(observed["closed"], observed)
            finally:
                listener.close(); thread.join(1)


if __name__ == "__main__": unittest.main()
