"""Production Kotlin transport against the actual recovered Desktop 6.1 Host and transfers.

Uses real TLS on localhost. InputController and Android document selection are recording test
doubles; these tests do not assert Windows device injection, Android providers, or WAN routing.
"""
from __future__ import annotations

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import queue
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DESKTOP = Path(os.environ.get("MINIANYDESK_PYTHON_SOURCE", ROOT.parent / "desktop"))
sys.path.insert(0, str(DESKTOP))
import host
import net
from desktop_geometry import screen_metadata
from transfer import FileTransfers

CLASSPATH = os.environ.get("MINIANYDESK_PORT_CLASSPATH") or (HERE / "port.classpath").read_text().strip()
MONITORS = [
    {"left": -1920, "top": -200, "width": 3840, "height": 1400},
    {"left": 0, "top": 0, "width": 1920, "height": 1080},
    {"left": -1920, "top": -200, "width": 1920, "height": 1200},
]


class RecordingInputs:
    def __init__(self):
        self.events, self.held_keys, self.held_buttons = [], set(), set()
    def move(self, x, y): self.events.append(("move", x, y))
    def key(self, key, down):
        self.events.append(("key", key, down))
        (self.held_keys.add if down else self.held_keys.discard)(key)
    def button(self, button, down):
        self.events.append(("button", button, down))
        (self.held_buttons.add if down else self.held_buttons.discard)(button)
    def wheel(self, delta): self.events.append(("wheel", delta))
    def release(self):
        self.events.append(("release",))
        self.held_keys.clear()
        self.held_buttons.clear()


class Desktop61PortTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.source = self.directory / "source.bin"
        self.contents = bytes(range(256)) * 1201
        self.source.write_bytes(self.contents)
        self.destination = self.directory / "android-new-document.bin"
        self.python_destination = self.directory / "python-new-file.bin"
        self.status = []
        self.messages = []
        self.requests = []
        self.transfers = None
        self.app = host.HostApp.__new__(host.HostApp)
        self.app.control_lock = threading.RLock()
        self.app.shutdown = threading.Event()
        self.app.inputs = RecordingInputs()
        self.app.sharing = True
        self.app.local_paused = False
        self.app.prompt_count = 0
        self.app.monitor_id = "all"
        self.app.generation = 0
        self.app._diagnostic = Mock()
        self.app._computer_name_cache = "Desktop Python 6.1"
        self.app.last_screen = screen_metadata(MONITORS, "all", MONITORS[0], (768, 280))
        image = BytesIO()
        Image.new("RGB", (768, 280), "purple").save(image, format="JPEG")
        self.screen = {"type": "screen", **self.app.last_screen,
                       "image": base64.b64encode(image.getvalue()).decode("ascii")}
        self.peer = None
        self.complete = threading.Event()
        self.host = net.HostServer("127.0.0.1", 0, "12345678",
            lambda name, ip: self.requests.append((name, ip)) or True,
            self.connected, lambda peer, reason: None, self.directory / "identity",
            status_callback=lambda: "Desktop Python 6.1")
        self.port = self.host.start()

    def tearDown(self):
        if self.transfers: self.transfers.close()
        self.host.stop()
        self.temp.cleanup()

    def connected(self, peer, name, ip):
        self.peer = self.app.peer = peer
        self.transfers = self.app.transfers = FileTransfers(peer, lambda fn: fn(),
            lambda offer, choose: choose(str(self.python_destination)), self.status.append,
            on_request_file=lambda choose: choose(str(self.source)))
        def message(message):
            self.messages.append(message)
            if message["type"] == "port_test_complete":
                peer.send({"type": "port_test_ack"})
                self.complete.set()
            elif message["type"] == "port_test_ready":
                self.assertTrue(self.transfers.send_file(self.source))
            else:
                self.app._message(peer, message)
        peer.on_message = message
        peer.send(self.screen)

    def client(self, mode):
        result = subprocess.run(["java", "-cp", CLASSPATH,
            "br.com.perabru.minianydesk.DesktopPortHarness", mode, "127.0.0.1", str(self.port),
            self.host.fingerprint, str(self.source), str(self.destination), str(self.directory / "cache")],
            text=True, capture_output=True, timeout=18)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("TRUST:" + self.host.fingerprint, result.stdout)
        return result.stdout

    def test_real_tls_jpeg_and_commands_reach_actual_desktop_host_dispatcher(self):
        self.assertIn("SESSION_OK", self.client("session"))
        self.assertTrue(self.complete.is_set())
        self.assertEqual(self.requests[0][0], "Android port test")
        self.assertEqual(self.app.inputs.events, [
            ("move", 1919, 1199), ("move", 1919, 1199), ("button", "left", True),
            ("button", "left", False), ("key", "ctrl", True), ("key", "c", True),
            ("key", "c", False), ("key", "ctrl", False), ("move", 1919, 1199),
            ("wheel", 2), ("release",), ("release",), ("release",), ("release",), ("release",)])
        self.assertEqual(self.app.monitor_id, "all")
        self.assertTrue(self.app.sharing)
        self.assertFalse(self.app.inputs.held_keys | self.app.inputs.held_buttons)
        self.app._diagnostic.assert_not_called()

    def test_android_to_desktop_file_multiblock_over_authenticated_tls(self):
        self.assertIn("TLS_TRANSFER_OK:send", self.client("send"))
        self.assertEqual(self.python_destination.read_bytes(), self.contents)
        self.assertTrue(any(event.get("success") is True for event in self.status))

    def test_desktop_to_android_file_multiblock_over_authenticated_tls(self):
        self.assertIn("TLS_TRANSFER_OK:receive", self.client("receive"))
        self.assertEqual(self.destination.read_bytes(), self.contents)
        self.assertTrue(any(event.get("success") is True for event in self.status))

    def test_android_requests_owner_selected_file_over_authenticated_tls(self):
        self.assertIn("TLS_TRANSFER_OK:request", self.client("request"))
        self.assertEqual(self.destination.read_bytes(), self.contents)
        self.assertTrue(any(event["type"] == "file_request" for event in self.messages))

    def test_pin_refusal_sends_no_application_bytes_or_password(self):
        # Observe TLS application bytes directly, rather than inferring from consent callbacks.
        self.host.stop()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(6)
        self.port = listener.getsockname()[1]
        observed = queue.Queue()
        def receive():
            try:
                with listener.accept()[0] as raw:
                    with self.host.context.wrap_socket(raw, server_side=True) as secure:
                        secure.settimeout(6)
                        observed.put(secure.recv(65536))
            except Exception as error:
                observed.put(error)
            finally: listener.close()
        reader = threading.Thread(target=receive, daemon=True)
        reader.start()
        self.assertIn("PIN_REJECTED_BEFORE_LOGIN", self.client("reject-pin"))
        self.assertEqual(observed.get(timeout=6), b"")
        reader.join(1)


if __name__ == "__main__": unittest.main(verbosity=2)
