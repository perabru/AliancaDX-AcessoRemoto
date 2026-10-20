"""Real localhost TLS tests for pairing, transport and session lifecycle."""
import concurrent.futures
import json
from pathlib import Path
import queue
import socket
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import net


PASSWORD = 'test-password-2026'


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.servers = []
        self.peers = []
        self.host_connections = queue.Queue()
        self.host_messages = queue.Queue()
        self.host_disconnects = queue.Queue()
        self.client_messages = queue.Queue()
        self.client_disconnects = queue.Queue()

    def tearDown(self):
        for peer in self.peers:
            peer.close()
        for server in self.servers:
            server.stop()
        self.directory.cleanup()

    def host(self, consent=None, folder=None):
        self.consent = consent or Mock(return_value=True)

        def connected(peer, name, address):
            peer.on_message = self.host_messages.put
            self.host_connections.put((peer, name, address))

        server = net.HostServer('127.0.0.1', 0, PASSWORD, self.consent, connected,
            lambda peer, reason: self.host_disconnects.put((peer, reason)),
            state_dir=folder or Path(self.directory.name) / str(len(self.servers)))
        self.servers.append(server)
        server.start()
        return server

    def client(self, server, password=PASSWORD, trust=None, name='Controlador de teste'):
        peer = net.connect('127.0.0.1', server.port, password, name,
            self.client_messages.put, self.client_disconnects.put,
            trust or (lambda address, port, fingerprint: fingerprint == server.fingerprint))
        self.peers.append(peer)
        return peer

    def test_unknown_certificate_rejection_happens_before_host_authorization(self):
        server = self.host()
        seen = []

        def reject(address, port, fingerprint):
            seen.append((address, port, fingerprint))
            return False

        with self.assertRaises(PermissionError):
            self.client(server, trust=reject)
        self.assertEqual(seen, [('127.0.0.1', server.port, server.fingerprint)])
        self.consent.assert_not_called()
        self.assertTrue(self.host_connections.empty())

    def test_certificate_identity_persists_across_host_restarts(self):
        folder = Path(self.directory.name) / 'persistent-host'
        original = self.host(folder=folder)
        fingerprint = original.fingerprint
        original.stop()
        restored = self.host(folder=folder)
        different = self.host(folder=Path(self.directory.name) / 'another-host')
        self.assertEqual(restored.fingerprint, fingerprint)
        self.assertNotEqual(different.fingerprint, fingerprint)

    def test_wrong_password_does_not_show_local_consent(self):
        server = self.host()
        with self.assertRaises(PermissionError):
            self.client(server, password='wrong-password')
        self.consent.assert_not_called()
        self.assertTrue(self.host_connections.empty())

    def test_local_consent_denial_prevents_a_session(self):
        decision = Mock(return_value=False)
        server = self.host(consent=decision)
        with self.assertRaises(PermissionError):
            self.client(server)
        decision.assert_called_once_with('Controlador de teste', '127.0.0.1')
        self.assertTrue(self.host_connections.empty())

    def test_single_active_peer_and_clean_reconnect(self):
        server = self.host()
        first = self.client(server)
        host_peer, _, _ = self.host_connections.get(timeout=2)
        with self.assertRaises(PermissionError):
            self.client(server, name='Segunda janela')
        self.consent.assert_called_once()
        self.assertFalse(first.closed)
        first.close('Reconnect test')
        disconnected, _ = self.host_disconnects.get(timeout=2)
        self.assertIs(disconnected, host_peer)
        second = self.client(server, name='Reconectado')
        _, name, _ = self.host_connections.get(timeout=2)
        self.assertEqual(name, 'Reconectado')
        self.assertFalse(second.closed)

    def test_pending_local_approval_reserves_the_single_session(self):
        requested, release = threading.Event(), threading.Event()

        def decide(name, address):
            requested.set()
            return release.wait(timeout=5)

        server = self.host(consent=decide)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.client, server)
            try:
                self.assertTrue(requested.wait(timeout=2))
                with self.assertRaises(PermissionError):
                    self.client(server, name='Outra solicitação')
            finally:
                release.set()
            self.assertFalse(pending.result(timeout=3).closed)

    def test_control_and_physical_screen_metadata_survive_bidirectional_tls(self):
        server = self.host()
        client = self.client(server)
        host_peer, _, _ = self.host_connections.get(timeout=2)
        control = {'type': 'control', 'command': {'type': 'move', 'captureId': 'physical', 'x': 1, 'y': 0}}
        client.send(control)
        self.assertEqual(self.host_messages.get(timeout=2), control)
        frame = {'type': 'screen', 'image': 'jpeg base64',
            'captureId': 'physical', 'left': -1920, 'top': -180, 'width': 4480, 'height': 1620,
            'frameWidth': 2560, 'frameHeight': 926, 'monitorId': 'all', 'monitors': []}
        host_peer.send(frame)
        self.assertEqual(self.client_messages.get(timeout=2), frame)

    def test_full_duplex_control_packets_remain_ordered(self):
        server = self.host()
        client = self.client(server)
        host_peer, _, _ = self.host_connections.get(timeout=2)

        def send(peer, prefix):
            for index in range(100):
                peer.send({'type': 'control', 'source': prefix, 'sequence': index})

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            outgoing = [executor.submit(send, client, 'client'), executor.submit(send, host_peer, 'host')]
            for job in outgoing:
                job.result(timeout=3)
        received_host = [self.host_messages.get(timeout=4) for _ in range(100)]
        received_client = [self.client_messages.get(timeout=4) for _ in range(100)]
        self.assertEqual([message['sequence'] for message in received_host], list(range(100)))
        self.assertEqual([message['sequence'] for message in received_client], list(range(100)))
        self.assertFalse(client.closed)
        self.assertFalse(host_peer.closed)

    def test_latest_frame_replaces_old_frames_while_control_packets_are_preserved(self):
        local, remote = socket.socketpair()
        local.settimeout(2)
        remote.settimeout(2)
        peer = net.Peer(local)
        self.peers.append(peer)
        try:
            for index in range(10):
                peer.send({'type': 'screen', 'sequence': index})
            for index in range(3):
                peer.send({'type': 'control', 'sequence': index})
            peer.start()
            messages = [net._read(remote) for _ in range(4)]
            self.assertEqual(messages, [{'type': 'control', 'sequence': n} for n in range(3)] +
                             [{'type': 'screen', 'sequence': 9}])
        finally:
            remote.close()

    def test_oversized_and_non_object_packets_close_the_session(self):
        for invalid in [struct.pack('!I', net.MAX_MESSAGE + 1),
                        struct.pack('!I', 2) + b'[]']:
            with self.subTest(packet=invalid):
                server = self.host()
                client = self.client(server)
                host_peer, _, _ = self.host_connections.get(timeout=2)
                client.sock.sendall(invalid)
                disconnected, _ = self.host_disconnects.get(timeout=2)
                self.assertIs(disconnected, host_peer)
                self.assertTrue(host_peer.closed)
                server.stop()

    def test_pending_approval_from_stopped_server_cannot_create_a_new_session(self):
        requested, release = threading.Event(), threading.Event()

        def decide(name, address):
            requested.set()
            return release.wait(timeout=5)

        server = self.host(consent=decide)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.client, server)
            try:
                self.assertTrue(requested.wait(timeout=2))
                server.stop()
                server.start()
            finally:
                release.set()
            with self.assertRaises((ConnectionError, OSError)):
                pending.result(timeout=3)
        # Synchronize with completion of the old request handler before checking callbacks.
        with server._lock:
            self.assertIsNone(server._peer)
        self.assertTrue(self.host_connections.empty())


if __name__ == '__main__':
    unittest.main()
