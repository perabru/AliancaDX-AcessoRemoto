"""Real file protocol through a slow writer, fragmented reads and mouse bursts."""
import hashlib
import json
from pathlib import Path
import queue
import re
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import net
from transfer import CHUNK, FileTransfers
from test_net_stability import FragmentedSocket, BlockedWriterSocket, packet


class FileFragmentedSocket(FragmentedSocket):
    """Fragment headers and bodies without one syscall for every file byte.

    The transport unit tests separately retain recv(1) for strict packet
    boundaries. This integration test moves several complete file chunks, so
    a repeating mix of tiny and larger reads keeps its deadline independent
    of hundreds of thousands of select/recv calls on a loaded build machine.
    """
    def __init__(self, sock):
        super().__init__(sock, (3, 25, 60, 100))
        self.timeouts_seen = set()

    def recv(self, size):
        self.reads += 1
        if self.reads in self.timeouts:
            self.timeouts_seen.add(self.reads)
            raise socket.timeout("one transient receive poll")
        fragment = (1, 2, 31, 1024, 4096)[(self.reads - 1) % 5]
        return self.sock.recv(min(size, fragment))


class FirstChunkGate:
    def __init__(self, sock):
        self.sock = sock
        self.entered = threading.Event()
        self.release = threading.Event()

    def sendall(self, data):
        # The actual transport writes a 4-byte length and one complete JSON packet.
        message = json.loads(data[4:].decode("utf-8"))
        if message["type"] == "file_chunk" and not self.entered.is_set():
            self.entered.set()
            if not self.release.wait(3): raise TimeoutError("test chunk gate expired")
        return self.sock.sendall(data)

    def send(self, data):
        if not self.entered.is_set() and re.search(rb'"type"\s*:\s*"file_chunk"', bytes(data[:128])):
            self.entered.set()
        if self.entered.is_set() and not self.release.is_set():
            raise BlockingIOError("test chunk write is temporarily backpressured")
        return self.sock.send(data[:4096])

    def shutdown(self, how):
        self.release.set()
        return self.sock.shutdown(how)

    def __getattr__(self, name): return getattr(self.sock, name)


class SlowFileExchangeTests(unittest.TestCase):
    def test_prompt_completion_state_cannot_deadlock_io_cancellation_behind_full_queue(self):
        local, remote = socket.socketpair()
        remote.settimeout(2)
        gate = BlockedWriterSocket(local)
        peer = net.Peer(gate)
        actions, callbacks, guards, errors = [], [], [], []
        prompt_queued = threading.Event()
        unguard_entered, callback_done, cancel_entered = threading.Event(), threading.Event(), threading.Event()
        def dispatch(callback):
            actions.append(callback)
            prompt_queued.set()
            return True
        def guard(active):
            guards.append(active)
            if not active: unguard_entered.set()
            peer.send({"type": "state", "sharing": not active, "promptActive": active})
        receiver = FileTransfers(peer, dispatch, lambda offer, callback: callbacks.append(callback),
            lambda event: None, prompt_guard=guard, status_dispatch=lambda callback: callback())
        def receive(message):
            if message["type"] == "file_cancel": cancel_entered.set()
            receiver.handle(message)
        peer.on_message = receive
        peer.on_close = lambda reason: receiver.close()
        ui = None
        try:
            peer.send({"type": "control", "sequence": -1})
            peer.start()
            self.assertTrue(gate.entered.wait(1))
            for index in range(128): peer.send({"type": "control", "sequence": index})
            identifier = "d3159e03-9114-4b58-b24d-697b4525405a"
            remote.sendall(packet({"type": "file_offer", "id": identifier, "name": "test.bin",
                "size": 1, "sha256": hashlib.sha256(b"x").hexdigest()}))
            self.assertTrue(prompt_queued.wait(1))
            actions.pop(0)()
            self.assertEqual(len(callbacks), 1)
            def complete_ui():
                try: callbacks[0](None)
                except Exception as error: errors.append(error)
                finally: callback_done.set()
            with patch.object(net, "SEND_QUEUE_TIMEOUT", .3):
                ui = threading.Thread(target=complete_ui, name="simulated-consent-ui", daemon=True)
                ui.start()
                self.assertTrue(unguard_entered.wait(1))
                remote.sendall(packet({"type": "file_cancel", "id": identifier}))
                self.assertTrue(cancel_entered.wait(1))
                gate.release.set()
                self.assertTrue(callback_done.wait(1))
                ui.join(1)
            self.assertEqual(errors, [])
            self.assertFalse(peer.closed, "Prompt completion and incoming cancellation deadlocked the session")
            self.assertEqual(guards, [True, False])
            self.assertIsNone(receiver.active)
            observed = [net._read(remote) for _ in range(131)]
            self.assertEqual([message["sequence"] for message in observed[:129]], [-1] + list(range(128)))
            self.assertEqual(observed[129:], [
                {"type": "state", "sharing": False, "promptActive": True},
                {"type": "state", "sharing": True, "promptActive": False}])
            self.assertFalse(peer.closed)
        finally:
            gate.release.set()
            receiver.close(); peer.close(); remote.close()
            if ui is not None: ui.join(1)

    def test_file_publication_and_incoming_request_cannot_deadlock_io_behind_queue_pressure(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.bin"
            source.write_bytes(b"owner-selected source")
            local, remote = socket.socketpair()
            remote.settimeout(2)
            gate = BlockedWriterSocket(local)
            peer = net.Peer(gate)
            sender = FileTransfers(peer, lambda callback: callback(), lambda offer, callback: callback(None),
                lambda event: None, on_request_file=lambda callback: callback(None))
            peer.on_close = lambda reason: sender.close()
            publish_entered, publish_done, receive_entered = threading.Event(), threading.Event(), threading.Event()
            errors = []
            original_send = peer.send
            def send(message):
                if message["type"] != "file_offer": return original_send(message)
                publish_entered.set()
                try: return original_send(message)
                except Exception as error:
                    errors.append(error)
                    raise
                finally: publish_done.set()
            peer.send = send
            def receive(message):
                receive_entered.set()
                sender.handle(message)
            peer.on_message = receive
            try:
                peer.send({"type": "control", "sequence": -1})
                peer.start()
                self.assertTrue(gate.entered.wait(1))
                for index in range(128): peer.send({"type": "control", "sequence": index})
                with patch.object(net, "SEND_QUEUE_TIMEOUT", .3):
                    self.assertTrue(sender.send_file(source))
                    self.assertTrue(publish_entered.wait(1))
                    request = {"type": "file_request", "id": "ad585c54-1f02-45bf-bfc1-e737c32ef269"}
                    remote.sendall(packet(request))
                    self.assertTrue(receive_entered.wait(1))
                    gate.release.set()
                    self.assertTrue(publish_done.wait(1))
                self.assertEqual(errors, [])
                self.assertFalse(peer.closed, "Transfer lock prevented the I/O owner from draining the queue")
                observed = [net._read(remote) for _ in range(131)]
                self.assertEqual([message["sequence"] for message in observed[:129]], [-1] + list(range(128)))
                self.assertEqual(observed[129]["type"], "file_offer")
                self.assertEqual(observed[130], {"type": "file_request_result", "id": request["id"], "accepted": False})
                self.assertFalse(peer.closed)
            finally:
                gate.release.set()
                sender.close(); peer.close(); remote.close()

    def test_rejected_consent_dispatch_balances_guard_and_allows_a_new_offer(self):
        messages, guards = [], []
        peer = Mock()
        peer.send.side_effect = messages.append
        peer.closed = False
        ask = Mock()
        receiver = FileTransfers(peer, lambda callback: False, ask, lambda event: None,
            prompt_guard=guards.append, status_dispatch=lambda callback: callback())
        self.addCleanup(receiver.close)
        offer = {"type": "file_offer", "id": "d3159e03-9114-4b58-b24d-697b4525405a",
                 "name": "test.bin", "size": 1, "sha256": hashlib.sha256(b"x").hexdigest()}
        receiver.handle(offer)
        self.assertEqual(guards, [True, False])
        self.assertIsNone(receiver.active)
        ask.assert_not_called()
        self.assertIn({"type": "file_cancel", "id": offer["id"]}, messages)
        receiver.dispatch = lambda callback: callback()
        receiver.ask_receive = lambda offered, callback: callback(None)
        receiver.handle(dict(offer, id="ad585c54-1f02-45bf-bfc1-e737c32ef269"))
        self.assertEqual(guards, [True, False, True, False])
        self.assertIsNone(receiver.active)

    def test_rejected_remote_picker_dispatch_clears_request_and_balances_guard(self):
        messages, guards = [], []
        peer = Mock()
        peer.send.side_effect = messages.append
        peer.closed = False
        chooser = Mock()
        receiver = FileTransfers(peer, lambda callback: False, Mock(), lambda event: None,
            on_request_file=chooser, prompt_guard=guards.append, status_dispatch=lambda callback: callback())
        self.addCleanup(receiver.close)
        identifier = "d3159e03-9114-4b58-b24d-697b4525405a"
        receiver.handle({"type": "file_request", "id": identifier})
        self.assertEqual(guards, [True, False])
        self.assertIsNone(receiver._request_token)
        chooser.assert_not_called()
        self.assertIn({"type": "file_request_result", "id": identifier, "accepted": False}, messages)

    def test_file_integrity_and_input_releases_survive_blocked_chunk_and_mouse_bursts(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            source, destination = directory / "source.bin", directory / "received.bin"
            contents = bytes(range(256)) * (CHUNK * 3 // 256) + b"trailing-zero\x00\xff"
            source.write_bytes(contents)
            left_socket, right_socket = socket.socketpair()
            left_socket.settimeout(.05)
            right_socket.settimeout(.05)
            gate = FirstChunkGate(left_socket)
            fragmented = FileFragmentedSocket(right_socket)
            left, right = net.Peer(gate), net.Peer(fragmented)
            left_status, right_status = [], []
            finished_left, finished_right = threading.Event(), threading.Event()
            inputs = queue.Queue()
            def status(events, complete):
                def receive(event):
                    events.append(event)
                    if "success" in event: complete.set()
                return receive
            sender = FileTransfers(left, lambda fn: fn(), lambda offer, callback: callback(None),
                status(left_status, finished_left))
            receiver = FileTransfers(right, lambda fn: fn(), lambda offer, callback: callback(str(destination)),
                status(right_status, finished_right))
            left.on_message = sender.handle
            right.on_message = lambda message: receiver.handle(message) or inputs.put(message)
            left.on_close = lambda reason: sender.close()
            right.on_close = lambda reason: receiver.close()
            try:
                left.start(); right.start()
                self.assertTrue(sender.send_file(source))
                self.assertTrue(gate.entered.wait(2), left_status + right_status)
                for batch in range(2):
                    for index in range(1000):
                        left.send({"type": "input", "action": "move", "captureId": "live",
                                   "x": index / 999, "y": batch, "sequence": batch * 1000 + index})
                    if batch == 0:
                        left.send({"type": "input", "action": "release"})
                self.assertFalse(left.closed)
                self.assertFalse(right.closed)
                self.assertFalse(finished_left.is_set())
                gate.release.set()
                self.assertTrue(finished_left.wait(5), left_status + right_status)
                self.assertTrue(finished_right.wait(1), left_status + right_status)
                self.assertTrue(left_status[-1].get("success"), left_status)
                self.assertTrue(right_status[-1].get("success"), right_status)
                self.assertEqual(destination.read_bytes(), contents)
                self.assertEqual(fragmented.timeouts_seen, fragmented.timeouts)
                observed = [inputs.get(timeout=1) for _ in range(3)]
                self.assertEqual(observed[0]["sequence"], 999)
                self.assertEqual(observed[1], {"type": "input", "action": "release"})
                self.assertEqual(observed[2]["sequence"], 1999)
                self.assertIsNone(sender.active)
                self.assertIsNone(receiver.active)
                self.assertEqual(list(directory.glob("*.part")), [])
                self.assertFalse(left.closed)
                self.assertFalse(right.closed)
            finally:
                gate.release.set()
                sender.close(); receiver.close()
                left.close(); right.close()


if __name__ == "__main__":
    unittest.main()
