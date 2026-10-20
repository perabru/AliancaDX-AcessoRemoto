"""File protocol tests with real files and deterministic acknowledgements."""
import base64
import hashlib
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transfer import CHUNK, MAX_FILE, FileTransfers


class RecordingPeer:
    def __init__(self):
        self.messages = queue.Queue()
        self.sent = []

    def send(self, message):
        message = json.loads(json.dumps(message))
        self.sent.append(message)
        self.messages.put(message)


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.directory = Path(self.folder.name)
        self.transfers = []

    def tearDown(self):
        for transfer in self.transfers:
            transfer.close()
        self.folder.cleanup()

    def transfer(self, destination=None, chooser=None, dispatch=None, ask=None, guard=None):
        peer = RecordingPeer()
        statuses, completed = [], threading.Event()

        def status(event):
            statuses.append(event)
            if 'success' in event:
                completed.set()

        receiver = ask or (lambda offer, callback: callback(str(destination) if destination else None))
        transfer = FileTransfers(peer, dispatch or (lambda function: function()), receiver,
                                 status, on_request_file=chooser, prompt_guard=guard)
        transfer.statuses, transfer.completed = statuses, completed
        self.transfers.append(transfer)
        return transfer, peer

    @staticmethod
    def offer(contents, **overrides):
        return {'type': 'file_offer', 'id': str(uuid.uuid4()), 'name': 'arquivo.bin',
                'size': len(contents), 'sha256': hashlib.sha256(contents).hexdigest(), **overrides}

    def accept(self, receiver, peer, contents, **overrides):
        offer = self.offer(contents, **overrides)
        self.assertTrue(receiver.handle(offer))
        response = peer.messages.get(timeout=2)
        self.assertEqual(response, {'type': 'file_accept', 'id': offer['id']})
        return offer

    def drive(self, sender, source_peer, receiver, target_peer):
        offer = source_peer.messages.get(timeout=3)
        self.assertEqual(offer['type'], 'file_offer')
        receiver.handle(offer)
        sender.handle(target_peer.messages.get(timeout=2))
        offsets = []
        while True:
            message = source_peer.messages.get(timeout=3)
            if message['type'] == 'file_chunk':
                offsets.append(message['offset'])
                receiver.handle(message)
                sender.handle(target_peer.messages.get(timeout=2))
            else:
                self.assertEqual(message['type'], 'file_end')
                receiver.handle(message)
                sender.handle(target_peer.messages.get(timeout=2))
                break
        self.assertTrue(sender.completed.wait(timeout=2))
        self.assertTrue(sender.statuses[-1]['success'])
        self.assertTrue(receiver.statuses[-1]['success'])
        self.assertIsNone(sender.active)
        self.assertIsNone(receiver.active)
        return offsets

    def test_bidirectional_ordered_multi_chunk_and_empty_files(self):
        left, left_peer = self.transfer(destination=self.directory / 'received-left.bin')
        right, right_peer = self.transfer(destination=self.directory / 'received-right.bin')
        first = self.directory / 'left-source.bin'
        first.write_bytes(bytes(range(256)) * 300)
        self.assertTrue(left.send_file(first))
        self.assertEqual(self.drive(left, left_peer, right, right_peer), [0, CHUNK])
        self.assertEqual((self.directory / 'received-right.bin').read_bytes(), first.read_bytes())
        left.completed.clear()
        right.completed.clear()
        second = self.directory / 'right-source.bin'
        second.write_bytes(b'')
        self.assertTrue(right.send_file(second))
        self.assertEqual(self.drive(right, right_peer, left, left_peer), [])
        self.assertEqual((self.directory / 'received-left.bin').read_bytes(), b'')

    def test_receiving_uses_owner_destination_and_never_replaces_existing_file(self):
        destination = self.directory / 'chosen.txt'
        destination.write_bytes(b'existing')
        receiver, peer = self.transfer(destination=destination)
        contents = b'new contents'
        offer = self.accept(receiver, peer, contents, name='sender-name.txt')
        receiver.handle({'type': 'file_chunk', 'id': offer['id'], 'offset': 0,
                         'data': base64.b64encode(contents).decode('ascii')})
        peer.messages.get(timeout=2)
        receiver.handle({'type': 'file_end', 'id': offer['id']})
        self.assertEqual(peer.messages.get(timeout=2)['type'], 'file_done')
        self.assertEqual(destination.read_bytes(), b'existing')
        self.assertEqual((self.directory / 'chosen (1).txt').read_bytes(), contents)
        self.assertFalse((self.directory / 'sender-name.txt').exists())

    def test_local_denial_creates_no_partial_file(self):
        receiver, peer = self.transfer()
        offer = self.offer(b'contents')
        receiver.handle(offer)
        self.assertEqual(peer.messages.get(timeout=2), {'type': 'file_cancel', 'id': offer['id']})
        self.assertIsNone(receiver.active)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_invalid_name_size_hash_and_id_never_open_consent_dialog(self):
        ask = Mock()
        receiver, peer = self.transfer(ask=ask)
        for invalid in [{'name': '../secrets'}, {'name': '..\\secrets'}, {'name': '/etc/passwd'},
                        {'name': 'C:\\Windows\\file'}, {'name': '.'}, {'name': '..'}, {'name': 'CON.txt'},
                        {'name': 'NUL'}, {'name': 'LPT1'}, {'name': 'a\x00b'}, {'name': 'trailing.'},
                        {'name': 'a' * 201}, {'size': -1}, {'size': MAX_FILE + 1}, {'size': 0.5},
                        {'size': True}, {'sha256': 'A' * 64}, {'sha256': ['0' * 64]}, {'id': 'invalid'}]:
            with self.subTest(invalid=invalid):
                receiver.handle(self.offer(b'', **invalid))
                self.assertIsNone(receiver.active)
        ask.assert_not_called()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_corrupt_data_hash_is_discarded_before_commit(self):
        destination = self.directory / 'verified.bin'
        receiver, peer = self.transfer(destination=destination)
        offer = self.accept(receiver, peer, b'good')
        receiver.handle({'type': 'file_chunk', 'id': offer['id'], 'offset': 0, 'data': 'ZXZpbA=='})
        self.assertEqual(peer.messages.get(timeout=2)['type'], 'file_ack')
        receiver.handle({'type': 'file_end', 'id': offer['id']})
        self.assertEqual(peer.messages.get(timeout=2)['type'], 'file_cancel')
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.directory.iterdir()), [])
        self.assertFalse(receiver.statuses[-1]['success'])

    def test_invalid_chunks_and_early_end_clean_partial_files(self):
        for change in [{'offset': 1}, {'offset': True}, {'data': 'broken!'},
                       {'data': base64.b64encode(b'x' * (CHUNK + 1)).decode('ascii')},
                       {'data': ''}, {'type': 'file_end'}]:
            with self.subTest(change=change):
                receiver, peer = self.transfer(destination=self.directory / 'target.bin')
                offer = self.accept(receiver, peer, b'a')
                receiver.handle({'type': 'file_chunk', 'id': offer['id'], 'offset': 0, 'data': 'YQ==', **change})
                self.assertEqual(peer.messages.get(timeout=2)['type'], 'file_cancel')
                self.assertIsNone(receiver.active)
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_cancel_and_disconnect_delete_partial_files_and_close_streams(self):
        for action in ['cancel', 'close']:
            receiver, peer = self.transfer(destination=self.directory / 'target.bin')
            offer = self.accept(receiver, peer, b'ab')
            state = receiver.active
            stream = state['stream']
            receiver.handle({'type': 'file_chunk', 'id': offer['id'], 'offset': 0, 'data': 'YQ=='})
            peer.messages.get(timeout=2)
            self.assertTrue(list(self.directory.glob('*.part')))
            getattr(receiver, action)()
            self.assertTrue(stream.closed)
            self.assertIsNone(receiver.active)
            self.assertEqual(list(self.directory.iterdir()), [])

    def test_oversized_source_is_rejected_without_offering_or_reading_it(self):
        source = self.directory / 'large.bin'
        with source.open('wb') as stream:
            stream.truncate(MAX_FILE + 1)
        sender, peer = self.transfer()
        self.assertTrue(sender.send_file(source))
        self.assertTrue(sender.completed.wait(timeout=2))
        self.assertFalse(sender.statuses[-1]['success'])
        self.assertFalse(any(message['type'] == 'file_offer' for message in peer.sent))

    def test_one_operation_only_and_queued_consent_cancellation_balances_guard(self):
        queued, guards = [], []
        ask = Mock()
        receiver, peer = self.transfer(ask=ask, dispatch=queued.append, guard=guards.append)
        offer = self.offer(b'contents')
        receiver.handle(offer)
        self.assertEqual(guards, [True])
        self.assertFalse(receiver.receive_request())
        self.assertFalse(receiver.send_file(self.directory / 'other.bin'))
        receiver.cancel()
        receiver.handle(self.offer(b'another'))
        self.assertIsNone(receiver.active)
        while queued:
            queued.pop(0)()
        ask.assert_not_called()
        self.assertEqual(guards, [True, False])
        self.assertFalse(receiver._prompt_tokens)

    def test_open_modal_keeps_guard_and_blocks_new_offer_until_owner_closes_it(self):
        callbacks, guards = [], []
        receiver, peer = self.transfer(ask=lambda offer, callback: callbacks.append(callback), guard=guards.append)
        receiver.handle(self.offer(b'first'))
        self.assertEqual(guards, [True])
        receiver.cancel()
        receiver.handle(self.offer(b'second'))
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(guards, [True])
        callbacks[0](None)
        self.assertEqual(guards, [True, False])
        self.assertIsNone(receiver.active)

    def test_remote_source_choice_denial_immediately_releases_requesting_peer(self):
        requester, left_peer = self.transfer()
        owner, right_peer = self.transfer(chooser=lambda callback: callback(None))
        self.assertTrue(requester.receive_request())
        owner.handle(left_peer.messages.get(timeout=2))
        result = right_peer.messages.get(timeout=2)
        self.assertEqual(result['type'], 'file_request_result')
        requester.handle(result)
        self.assertFalse(requester._request_pending)
        self.assertTrue(requester.receive_request())

    def test_cancelled_remote_source_picker_cannot_send_the_late_selection(self):
        source = self.directory / 'owner.bin'
        source.write_bytes(b'owner local file')
        callbacks, guards = [], []
        requester, left_peer = self.transfer()
        owner, right_peer = self.transfer(chooser=callbacks.append, guard=guards.append)
        self.assertTrue(requester.receive_request())
        owner.handle(left_peer.messages.get(timeout=2))
        self.assertEqual(guards, [True])
        self.assertFalse(owner.send_file(source))
        requester.cancel()
        owner.handle(left_peer.messages.get(timeout=2))
        callbacks[0](str(source))
        self.assertEqual(guards, [True, False])
        self.assertIsNone(owner.active)
        self.assertFalse(any(message['type'] == 'file_offer' for message in right_peer.sent))

    def test_requested_source_preparation_failure_immediately_releases_requester(self):
        missing = self.directory / 'missing-file.bin'
        oversized = self.directory / 'oversized.bin'
        with oversized.open('wb') as stream:
            stream.truncate(MAX_FILE + 1)
        for source in [missing, oversized, self.directory]:
            with self.subTest(source=source):
                requester, left_peer = self.transfer()
                owner, right_peer = self.transfer(chooser=lambda callback: callback(str(source)))
                self.assertTrue(requester.receive_request())
                owner.handle(left_peer.messages.get(timeout=2))
                self.assertTrue(owner.completed.wait(timeout=2), owner.statuses)
                self.assertFalse(owner.statuses[-1]['success'])
                for message in right_peer.sent:
                    requester.handle(message)
                self.assertFalse(requester._request_pending)
                self.assertFalse(any(message['type'] == 'file_offer' for message in right_peer.sent))

    def test_remote_request_cancel_also_stops_source_preparation_after_picker_closes(self):
        source = self.directory / 'selected.bin'
        source.write_bytes(b'owner selected file')
        requester, left_peer = self.transfer()
        owner, right_peer = self.transfer(chooser=lambda callback: callback(str(source)))
        preparing, release = threading.Event(), threading.Event()

        def pause_preparation(state, path):
            preparing.set()
            release.wait(timeout=3)

        with patch.object(owner, '_sender', side_effect=pause_preparation):
            try:
                self.assertTrue(requester.receive_request())
                owner.handle(left_peer.messages.get(timeout=2))
                self.assertTrue(preparing.wait(timeout=2))
                self.assertIsNotNone(owner.active)
                requester.cancel()
                owner.handle(left_peer.messages.get(timeout=2))
                self.assertIsNone(owner.active)
                self.assertFalse(any(message['type'] == 'file_offer' for message in right_peer.sent))
            finally:
                release.set()

    def test_cancellation_at_hash_completion_never_publishes_a_stale_offer(self):
        source = self.directory / 'source.bin'
        contents = b'owner contents'
        source.write_bytes(contents)
        sender, peer = self.transfer()
        digest_value = hashlib.sha256(contents).hexdigest()
        digest = Mock()

        def cancel_at_completion():
            sender.cancel()
            return digest_value

        digest.hexdigest.side_effect = cancel_at_completion
        original = sender._sender
        stopped = threading.Event()

        def worker(state, path):
            try:
                original(state, path)
            finally:
                stopped.set()

        with patch('transfer.hashlib.sha256', return_value=digest), \
                patch.object(sender, '_sender', side_effect=worker):
            self.assertTrue(sender.send_file(source))
            self.assertTrue(stopped.wait(timeout=3))
        self.assertIsNone(sender.active)
        self.assertFalse(any(message['type'] == 'file_offer' for message in peer.sent))


if __name__ == '__main__':
    unittest.main()
