"""Opt-in integration with actual EasyTier binaries and a forced LOCAL relay.

This does not prove access across physical ISPs or public relay availability.
Usage: python tests/mesh_relay_integration.py --runtime-dir /path/to/linux/bundle
"""
from __future__ import annotations
import argparse
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mesh
import net


def wait(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.05)
    return False


class TLSEcho:
    def __init__(self, directory):
        key, cert, self.fingerprint = net._certificate(directory)
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context.load_cert_chain(cert, key)
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(12)
        self.listener.settimeout(.2)
        self.port = self.listener.getsockname()[1]
        self.stopped = threading.Event()
        self.accepted = 0
        self.received = 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stopped.is_set():
            try:
                raw, _ = self.listener.accept()
                self.accepted += 1
                threading.Thread(target=self._client, args=(raw,), daemon=True).start()
            except socket.timeout:
                pass
            except OSError:
                break

    def _client(self, raw):
        try:
            raw.settimeout(8)
            with self.context.wrap_socket(raw, server_side=True) as secure:
                while not self.stopped.is_set():
                    data = secure.recv(65536)
                    if not data:
                        break
                    self.received += len(data)
                    secure.sendall(data)
        except (OSError, ssl.SSLError):
            raw.close()

    def close(self):
        self.stopped.set()
        self.listener.close()
        self.thread.join(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--diagnostic-cli", type=Path, required=True,
                        help="Separate official Linux CLI used only to test management-RPC denial; not an application dependency")
    parser.add_argument("--report")
    options = parser.parse_args()
    core = mesh.verify_runtime(options.runtime_dir)
    cli = options.diagnostic_cli.resolve()
    if not cli.is_file():
        parser.error("The separate diagnostic CLI is missing")
    result = {"scope": "Linux real EasyTier 2.6.4; 3 userspace nodes; local relay forced; no P2P",
              "physical_cross_isp": "not tested", "public_relay": "not tested: environment blocks outbound sockets"}
    processes, managers, servers = [], [], []
    with tempfile.TemporaryDirectory(prefix="minianydesk-relay-qa-") as directory:
        directory = Path(directory)
        relay_port, relay_rpc = mesh._free_port(), mesh._free_port()
        relay_cfg = directory / "relay.toml"
        relay_cfg.write_text(f'''instance_name = "MiniAnyDesk-QA-Local-Relay"
listeners = ["tcp://127.0.0.1:{relay_port}"]
[network_identity]
network_name = "mda-qa-relay-{os.urandom(8).hex()}"
network_secret = "{os.urandom(32).hex()}"
[flags]
no_tun = true
enable_encryption = true
bind_device = false
relay_network_whitelist = "*"
disable_upnp = true
enable_ipv6 = false
[console_logger]
level = "off"
[file_logger]
level = "off"
''')
        os.chmod(relay_cfg, 0o600)
        relay = subprocess.Popen([str(core), "--config-file", str(relay_cfg), "--disable-env-parsing",
                                  "--rpc-portal", f"127.0.0.1:{relay_rpc}", "--rpc-portal-whitelist", "192.0.2.1/32"],
                                 stdout=subprocess.DEVNULL, **mesh._child_options())
        processes.append(relay)
        try:
            if not wait(lambda: _port_open(relay_port), 10):
                raise AssertionError("relay did not listen")
            echo = TLSEcho(directory / "echo")
            forbidden = TLSEcho(directory / "forbidden")
            servers += [echo, forbidden]
            pair = mesh.generate_pairing(echo.port, echo.fingerprint)
            kwargs = dict(runtime_dir=options.runtime_dir, peers=[f"tcp://127.0.0.1:{relay_port}"], force_relay=True)
            host = mesh.MeshManager("host", directory / "host", **kwargs)
            controller = mesh.MeshManager("controller", directory / "controller", **kwargs)
            managers += [host, controller]
            host.start(pair)
            controller.start(pair)
            endpoint = controller.wait_ready(timeout=30)
            result["tls_forward_ready"] = bool(endpoint)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            payload = os.urandom(1024 * 1024 + 137)
            with socket.create_connection(endpoint, timeout=10) as raw:
                raw.settimeout(12)
                with context.wrap_socket(raw, server_hostname=None) as secure:
                    assert hashlib.sha256(secure.getpeercert(binary_form=True)).hexdigest().upper() == pair.fingerprint
                    # Keep the echo draining while the full payload is sent.
                    received = bytearray()
                    sender = threading.Thread(target=secure.sendall, args=(payload,))
                    sender.start()
                    while len(received) < len(payload):
                        part = secure.recv(min(65536, len(payload) - len(received)))
                        if not part:
                            raise AssertionError("truncated echo")
                        received.extend(part)
                    sender.join(12)
                    assert not sender.is_alive()
                    assert hashlib.sha256(received).digest() == hashlib.sha256(payload).digest()
            result["bidirectional_bytes"] = len(payload)
            result["sha256_match"] = True
            # A paired peer trying a different localhost service must be denied.
            blocked = mesh.MeshManager("controller", directory / "blocked", **kwargs)
            managers.append(blocked)
            blocked.start(dataclasses.replace(pair, port=forbidden.port, fingerprint=forbidden.fingerprint))
            try:
                blocked.wait_ready(timeout=5)
                raise AssertionError("ACL unexpectedly allowed a second Host local port")
            except mesh.MeshError:
                pass
            assert forbidden.accepted == 0, forbidden.accepted
            result["other_host_local_port_denied"] = True
            # Ordinary local users must not obtain network_secret via config RPC.
            assert host._rpc_port and controller._rpc_port
            for manager in (host, controller):
                try:
                    check = subprocess.run([str(cli), "--rpc-portal", f"127.0.0.1:{manager._rpc_port}",
                                            "--output", "json", "node", "config"],
                                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                           env=mesh._clean_env(), timeout=2)
                    assert check.returncode != 0
                    assert pair.network_secret.encode() not in check.stdout
                except subprocess.TimeoutExpired:
                    pass  # Refused handshake must never return the configuration.
            result["localhost_management_rpc_denied"] = True
            # Stop the relay: no direct path exists, so the endpoint loses TLS.
            mesh._terminate(relay)
            assert wait(lambda: controller.snapshot().state != "ready", timeout=10)
            result["relay_loss_detected"] = True
            result["passed"] = True
        finally:
            for manager in managers:
                manager.stop()
            for manager in managers:
                assert manager.join(6), "manager did not stop"
            result["owned_processes_stopped"] = all(manager._process is None for manager in managers)
            for server in servers:
                server.close()
            for process in processes:
                mesh._terminate(process)
    if options.report:
        Path(options.report).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


def _port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=.1):
            return True
    except OSError:
        return False


if __name__ == "__main__":
    main()
