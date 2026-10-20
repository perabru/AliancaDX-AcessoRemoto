"""End-to-end native file exchange over the actual TLS/Peer transport."""
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import net
from transfer import CHUNK, FileTransfers


class FileExchangeTlsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.directory = Path(self.folder.name)
        self.host_destination = self.directory / 'host-received.bin'
        self.client_destination = self.directory / 'controller-received.bin'
        self.owner_source = None
        self.host_complete, self.client_complete = threading.Event(), threading.Event()
        self.host_closed = threading.Event()
        self.host_status, self.client_status = [], []
        self.release_reader = threading.Event()

        def host_status(event):
            self.host_status.append(event)
            if 'success' in event:
                self.host_complete.set()

        def client_status(event):
            self.client_status.append(event)
            if 'success' in event:
                self.client_complete.set()

        def connected(peer, name, address):
            self.host_peer = peer
            self.host_transfer = FileTransfers(peer, lambda action: action(),
                lambda offer, callback: callback(str(self.host_destination)), host_status,
                on_request_file=lambda callback: callback(str(self.owner_source) if self.owner_source else None))
            peer.on_message = self.host_transfer.handle

        def disconnected(peer, reason):
            self.host_transfer.close()
            self.host_closed.set()

        self.server = net.HostServer('127.0.0.1', 0, 'tls-file-test-password', lambda name, address: True,
            connected, disconnected, state_dir=self.directory / 'identity')
        self.server.start()
        self.client_peer = net.connect('127.0.0.1', self.server.port, 'tls-file-test-password', 'Teste',
            lambda message: None, lambda reason: self.client_transfer.close(),
            lambda address, port, fingerprint: fingerprint == self.server.fingerprint)
        self.client_transfer = FileTransfers(self.client_peer, lambda action: action(),
            lambda offer, callback: callback(str(self.client_destination)), client_status)
        self.client_peer.on_message = self.client_transfer.handle

    def tearDown(self):
        self.release_reader.set()
        self.client_transfer.close()
        self.host_transfer.close()
        self.client_peer.close()
        self.server.stop()
        self.folder.cleanup()

    def wait_success(self):
        self.assertTrue(self.host_complete.wait(timeout=4), self.host_status)
        self.assertTrue(self.client_complete.wait(timeout=4), self.client_status)
        self.assertTrue(self.host_status[-1]['success'], self.host_status)
        self.assertTrue(self.client_status[-1]['success'], self.client_status)
        self.assertIsNone(self.host_transfer.active)
        self.assertIsNone(self.client_transfer.active)
        self.host_complete.clear()
        self.client_complete.clear()

    def test_client_send_and_owner_selected_reverse_request_transfer_exact_bytes(self):
        source = self.directory / 'client-source.bin'
        contents = bytes(range(256)) * 300
        source.write_bytes(contents)
        self.assertTrue(self.client_transfer.send_file(source))
        self.wait_success()
        self.assertEqual(self.host_destination.read_bytes(), contents)
        self.owner_source = self.directory / 'owner-selected.bin'
        reverse = b'local owner source selection\x00\xff' * 4000
        self.owner_source.write_bytes(reverse)
        self.assertTrue(self.client_transfer.receive_request())
        self.wait_success()
        self.assertEqual(self.client_destination.read_bytes(), reverse)
        self.assertFalse(self.client_transfer._request_pending)

    def test_host_can_send_a_verified_empty_file(self):
        source = self.directory / 'empty.txt'
        source.write_bytes(b'')
        self.assertTrue(self.host_transfer.send_file(source))
        self.wait_success()
        self.assertEqual(self.client_destination.read_bytes(), b'')

    def test_disconnection_while_streaming_removes_partial_file(self):
        received_first = threading.Event()

        def hold_after_first_chunk(message):
            self.host_transfer.handle(message)
            if message['type'] == 'file_chunk' and not received_first.is_set():
                received_first.set()
                self.release_reader.wait(timeout=4)

        self.host_peer.on_message = hold_after_first_chunk
        source = self.directory / 'large-source.bin'
        source.write_bytes(b'x' * (CHUNK * 4))
        self.assertTrue(self.client_transfer.send_file(source))
        self.assertTrue(received_first.wait(timeout=3), self.host_status)
        self.assertTrue(list(self.directory.glob('*.part')))
        self.host_peer.close('Interrompida durante o envio')
        self.assertTrue(self.host_closed.wait(timeout=2))
        self.assertFalse(self.host_destination.exists())
        self.assertEqual(list(self.directory.glob('*.part')), [])
        self.assertIsNone(self.host_transfer.active)
        self.release_reader.set()


if __name__ == '__main__':
    unittest.main()
