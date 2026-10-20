"""Synthetic localhost EasyTier relay + exact Desktop6.1 TLS protocol fixture.

Start this inside the emulator runner's persistent network namespace. The private ready
file is test input and must not be copied into a user deliverable or printed to logs.
"""
from __future__ import annotations

import argparse
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import Mock

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, os.environ["MINIANYDESK_PYTHON_SOURCE"])
import host
import mesh
import net
from desktop_geometry import screen_metadata
from transfer import FileTransfers
from lab_fixture import MONITORS, RecordingInputs


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready", required=True, type=Path)
    parser.add_argument("--stop", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    options = parser.parse_args()
    processes = []
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    contents = bytes(range(256)) * 257
    results = {"scope": "Synthetic Linux Desktop6.1 Host, Android emulator, forced local EasyTier2.6.4 relay",
               "public_relays_tested": False, "cross_isp_tested": False, "physical_devices_tested": False,
               "owner_approvals": 0, "commands_exact": False, "android_to_python_file_exact": False,
               "python_to_android_file_protocol_done": False}
    transfers = None
    server = None
    with tempfile.TemporaryDirectory(prefix="android-native-lab-") as scratch:
        directory = Path(scratch)
        source = directory / "source.bin"
        source.write_bytes(contents)
        destination = directory / "python-received.bin"
        app = host.HostApp.__new__(host.HostApp)
        app.control_lock = threading.RLock()
        app.shutdown = threading.Event()
        app.inputs = RecordingInputs()
        app.sharing, app.local_paused, app.prompt_count = True, False, 0
        app.monitor_id, app.generation = "all", 0
        app._diagnostic = Mock()
        app._computer_name_cache = "Desktop Python 6.1"
        app.last_screen = screen_metadata(MONITORS, "all", MONITORS[0], (768, 280))
        image = BytesIO()
        Image.new("RGB", (768, 280), "purple").save(image, format="JPEG")
        screen = {"type": "screen", **app.last_screen,
                  "image": base64.b64encode(image.getvalue()).decode("ascii")}
        def status(event):
            if event.get("success") is True:
                if destination.exists():
                    results["android_to_python_file_exact"] = destination.read_bytes() == contents
                results["python_to_android_file_protocol_done"] = any(message["type"] == "file_done" for message in messages)
        messages = []
        expected = [("move", 1919, 1199), ("move", 1919, 1199), ("button", "left", True),
            ("button", "left", False), ("key", "ctrl", True), ("key", "c", True),
            ("key", "c", False), ("key", "ctrl", False), ("move", 1919, 1199),
            ("wheel", 2), ("release",), ("release",), ("release",), ("release",), ("release",)]
        def connected(peer, name, ip):
            nonlocal transfers
            app.peer = peer
            transfers = app.transfers = FileTransfers(peer, lambda fn: fn(),
                lambda offer, choose: choose(str(destination)), status,
                on_request_file=lambda choose: choose(str(source)))
            def received(message):
                messages.append(message)
                if message["type"] == "port_test_complete":
                    results["commands_exact"] = app.inputs.events == expected and not app._diagnostic.called
                    if results["commands_exact"]: peer.send({"type": "port_test_ack"})
                else: app._message(peer, message)
            peer.on_message = received
            peer.send(screen)
        def approved(name, ip):
            results["owner_approvals"] += 1
            return True
        server = net.HostServer("127.0.0.1", 0, "12345678", approved, connected,
            lambda peer, reason: None, directory / "identity", status_callback=lambda: "Desktop Python 6.1")
        try:
            tls_port = server.start()
            pair = mesh.generate_pairing(tls_port, server.fingerprint)
            relay_port, relay_rpc, host_rpc = free_port(), free_port(), free_port()
            relay_config = directory / "relay.toml"
            relay_config.write_text(f'''instance_name = "MiniAnyDesk-Android-QA-Relay"
listeners = ["tcp://0.0.0.0:{relay_port}"]
[network_identity]
network_name = "qa-{os.urandom(8).hex()}"
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
            host_config = directory / "host.toml"
            host_config.write_text(mesh.build_config("host", pair, host_rpc,
                peers=[f"tcp://127.0.0.1:{relay_port}"], force_relay=True))
            os.chmod(relay_config, 0o600)
            os.chmod(host_config, 0o600)
            core = Path(os.environ["MINIANYDESK_LINUX_CORE"])
            for config, rpc in ((relay_config, relay_rpc), (host_config, host_rpc)):
                processes.append(subprocess.Popen([str(core), "--config-file", str(config), "--disable-env-parsing",
                    "--rpc-portal", f"127.0.0.1:{rpc}", "--rpc-portal-whitelist", "192.0.2.1/32"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=mesh._clean_env()))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if any(process.poll() is not None for process in processes):
                    raise RuntimeError("Native lab Linux process exited")
                try:
                    with socket.create_connection(("127.0.0.1", relay_port), timeout=.5): break
                except OSError: time.sleep(.1)
            else: raise RuntimeError("Relay did not listen")
            with tempfile.NamedTemporaryFile(mode="w", dir=options.ready.parent, delete=False) as pending:
                json.dump({"pairing": pair.code, "relay": f"tcp://10.0.2.2:{relay_port}",
                    "tls_port": tls_port, "fingerprint": pair.fingerprint, "name": "Desktop Python 6.1"}, pending)
                pending.flush()
                os.fsync(pending.fileno())
            os.replace(pending.name, options.ready)
            print("LOCAL_NATIVE_LAB_READY", flush=True)
            while not stopping.is_set() and not options.stop.exists():
                if any(process.poll() is not None for process in processes):
                    raise RuntimeError("Native lab Linux process exited during test")
                stopping.wait(.25)
        except Exception as failure:
            results["lab_error"] = type(failure).__name__ + ": " + str(failure)
            raise
        finally:
            if transfers: transfers.close()
            if server: server.stop()
            for process in processes:
                process.terminate()
                try: process.wait(5)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
            results["input_events_recorded"] = len(app.inputs.events)
            results["message_types"] = sorted(set(message["type"] for message in messages))
            options.report.write_text(json.dumps(results, indent=2) + "\n")
            options.ready.unlink(missing_ok=True)


if __name__ == "__main__": main()
