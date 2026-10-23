"""Real Python FileTransfers ↔ Node MobileFiles over test-only JSON pipes.

No WebRTC/browser claims: these checks verify the production cross-language file
wire protocol, temporary-file cleanup and SHA-256/consent behavior. Automatic
consent is confined to this fixture and never changes either runtime.
"""
import base64
import hashlib
import json
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transfer import CHUNK, FileTransfers

NODE = shutil.which("node")
FIXTURE = Path(__file__).resolve().parents[2] / "ios-pwa/tests/fixtures/mobile_files_bridge.mjs"


class Interop:
    def __init__(self, directory, *, host_accept=True, mobile_accept=True,
                 cancel_send=0, cancel_receive=0, corrupt=None, chooser=None):
        self.directory = directory
        self.destination = directory / "received-on-host.bin"
        self.mobile_destination = directory / "received-on-mobile.bin"
        self.events, self.mobile_wire, self.host_wire, self.statuses = [], [], [], []
        self.incoming = queue.Queue()
        self.write_lock = threading.Lock()
        self.corrupt, self.corrupted = corrupt, False
        self.temporary_seen = False
        self.node = subprocess.Popen([NODE, str(FIXTURE)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
        def read():
            try:
                for line in self.node.stdout:
                    self.incoming.put(json.loads(line))
            except Exception as error:
                self.incoming.put({"event": "error", "message": str(error)})
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        self.transfer = FileTransfers(self, lambda function: function(),
            lambda offer, callback: callback(str(self.destination) if host_accept else None),
            self.statuses.append, on_request_file=chooser)
        self.command({"action": "init", "destination": str(self.mobile_destination),
                      "accept": mobile_accept, "cancelSentChunks": cancel_send,
                      "cancelReceivedChunks": cancel_receive})
        self.wait(lambda: any(event["event"] == "ready" for event in self.events))

    def command(self, value):
        with self.write_lock:
            self.node.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
            self.node.stdin.flush()

    def mutate(self, direction, message):
        if direction == self.corrupt and not self.corrupted and message.get("type") == "file_chunk":
            message = dict(message)
            contents = bytearray(base64.b64decode(message["data"]))
            contents[0] ^= 1
            message["data"] = base64.b64encode(contents).decode("ascii")
            self.corrupted = True
        return message

    def send(self, message):
        self.host_wire.append(message)
        self.command({"action": "wire", "message": self.mutate("host", message)})

    def wait(self, predicate, timeout=6):
        deadline = time.monotonic() + timeout
        while True:
            if predicate():
                return
            if time.monotonic() >= deadline:
                raise AssertionError("Cross-language exchange timeout; " + repr(self.events[-5:]))
            try:
                event = self.incoming.get(timeout=.02)
            except queue.Empty:
                if self.node.poll() is not None:
                    raise AssertionError("Node fixture exited: " + self.node.stderr.read())
                continue
            self.events.append(event)
            if event["event"] == "error":
                raise AssertionError(event["message"])
            if event["event"] == "wire":
                self.mobile_wire.append(event["message"])
                self.transfer.handle(self.mutate("mobile", event["message"]))
                if event["message"].get("type") == "file_chunk":
                    self.temporary_seen |= any(self.directory.glob("*.part"))

    def uploaded(self):
        return any(event.get("success") is True for event in self.statuses)

    def settled(self):
        return (self.transfer.active is None and any("success" in event for event in self.statuses)
                and any(event["event"] == "status" and not event["busy"] for event in self.events))

    def close(self):
        self.transfer.close()
        try:
            self.command({"action": "close"})
        except (BrokenPipeError, OSError):
            pass
        try:
            self.node.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.node.kill(); self.node.wait(timeout=2)
        self.node.stdin.close(); self.node.stdout.close(); self.node.stderr.close()
        self.reader.join(timeout=1)


@unittest.skipUnless(NODE, "Node.js is required for the real mobile runtime fixture")
class IosFileInteropTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.directory = Path(self.folder.name)
        self.exchanges = []
        self.contents = bytes(range(251)) * 1100
        self.source = self.directory / "source.bin"
        self.source.write_bytes(self.contents)

    def tearDown(self):
        for exchange in self.exchanges:
            exchange.close()
        self.folder.cleanup()

    def exchange(self, **kwargs):
        exchange = Interop(self.directory, **kwargs)
        self.exchanges.append(exchange)
        return exchange

    def upload(self, exchange):
        exchange.command({"action": "upload", "path": str(self.source), "name": "source.bin"})

    def test_mobile_upload_to_real_python_receiver_is_ordered_and_hash_verified(self):
        exchange = self.exchange()
        self.upload(exchange)
        exchange.wait(lambda: exchange.uploaded() and exchange.settled())
        self.assertEqual(exchange.destination.read_bytes(), self.contents)
        self.assertEqual(hashlib.sha256(exchange.destination.read_bytes()).digest(), hashlib.sha256(self.contents).digest())
        self.assertEqual([message["offset"] for message in exchange.mobile_wire if message["type"] == "file_chunk"], list(range(0, len(self.contents), CHUNK)))
        self.assertTrue(any(message["type"] == "file_done" for message in exchange.host_wire))

    def test_mobile_request_downloads_real_python_file_before_presenting_blob(self):
        exchange = self.exchange(chooser=lambda callback: callback(str(self.source)))
        exchange.command({"action": "request"})
        exchange.wait(lambda: exchange.uploaded() and any(event["event"] == "download" for event in exchange.events))
        self.assertEqual(exchange.mobile_destination.read_bytes(), self.contents)
        downloaded = next(event for event in exchange.events if event["event"] == "download")
        self.assertEqual(downloaded["sha256"], hashlib.sha256(self.contents).hexdigest())
        self.assertTrue(any(message["type"] == "file_request" for message in exchange.mobile_wire))

    def test_python_refusal_prevents_mobile_payload_and_destination(self):
        exchange = self.exchange(host_accept=False)
        self.upload(exchange)
        exchange.wait(exchange.settled)
        self.assertFalse(exchange.destination.exists())
        self.assertFalse(any(message["type"] == "file_chunk" for message in exchange.mobile_wire))
        self.assertTrue(any(message["type"] == "file_cancel" for message in exchange.host_wire))
        self.assertEqual(list(self.directory.glob("*.part")), [])

    def test_mobile_refusal_prevents_python_payload_and_download(self):
        exchange = self.exchange(mobile_accept=False)
        self.assertTrue(exchange.transfer.send_file(self.source))
        exchange.wait(exchange.settled)
        self.assertFalse(exchange.mobile_destination.exists())
        self.assertFalse(any(message["type"] == "file_chunk" for message in exchange.host_wire))
        self.assertTrue(any(message["type"] == "file_cancel" for message in exchange.mobile_wire))

    def test_cancel_mid_upload_discards_python_temporary_file(self):
        exchange = self.exchange(cancel_send=1)
        self.upload(exchange)
        exchange.wait(exchange.settled)
        self.assertFalse(exchange.destination.exists())
        self.assertEqual(list(self.directory.glob("*.part")), [])
        self.assertTrue(exchange.temporary_seen)
        self.assertTrue(any(message["type"] == "file_chunk" for message in exchange.mobile_wire))
        self.assertTrue(any(message["type"] == "file_cancel" for message in exchange.mobile_wire))
        self.assertFalse(exchange.uploaded())

    def test_cancel_mid_download_never_presents_partial_blob(self):
        exchange = self.exchange(cancel_receive=1)
        self.assertTrue(exchange.transfer.send_file(self.source))
        exchange.wait(exchange.settled)
        self.assertFalse(exchange.mobile_destination.exists())
        self.assertFalse(any(event["event"] == "download" for event in exchange.events))
        self.assertTrue(any(message["type"] == "file_cancel" for message in exchange.mobile_wire))
        self.assertFalse(exchange.uploaded())

    def test_corrupt_mobile_upload_is_rejected_by_python_sha256(self):
        exchange = self.exchange(corrupt="mobile")
        self.upload(exchange)
        exchange.wait(exchange.settled)
        self.assertTrue(exchange.corrupted)
        self.assertFalse(exchange.destination.exists())
        self.assertEqual(list(self.directory.glob("*.part")), [])
        self.assertFalse(exchange.uploaded())
        self.assertTrue(any("SHA-256" in event["message"] for event in exchange.statuses))

    def test_corrupt_python_download_is_rejected_by_mobile_sha256(self):
        exchange = self.exchange(corrupt="host")
        self.assertTrue(exchange.transfer.send_file(self.source))
        exchange.wait(exchange.settled)
        self.assertTrue(exchange.corrupted)
        self.assertFalse(exchange.mobile_destination.exists())
        self.assertFalse(any(event["event"] == "download" for event in exchange.events))
        self.assertFalse(exchange.uploaded())
        self.assertTrue(any(event["event"] == "status" and "SHA-256" in event["message"] for event in exchange.events))


if __name__ == "__main__":
    unittest.main()
