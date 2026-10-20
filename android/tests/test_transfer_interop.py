"""Run the production Kotlin transfer manager against Python over production Peer packets.

The JVM uses tiny Android SAF test doubles. They model exclusively user-created destination
documents; they do not prove behavior of an Android device or a particular document provider.
Set MINIANYDESK_TRANSFER_CLASSPATH to the compiled harness, transport, JSON and Kotlin jars.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

NATIVE = Path(__file__).resolve().parents[2] / 'native_project'
NATIVE = Path(os.environ.get('MINIANYDESK_PYTHON_SOURCE', str(NATIVE)))
if not (NATIVE / 'net.py').exists():
    NATIVE = Path(__file__).resolve().parent / 'python_reference'
sys.path.insert(0, str(NATIVE))
import net
from transfer import FileTransfers


@unittest.skipUnless(os.environ.get('MINIANYDESK_TRANSFER_CLASSPATH'), 'Compile the Kotlin headless harness first')
class AndroidTransferInteropTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.bin'
        self.contents = bytes(range(256)) * 801
        self.source.write_bytes(self.contents)
        self.android_destination = self.root / 'android-new-document.bin'
        self.python_destination = self.root / 'python-received.bin'
        self.cache = self.root / 'private-cache'
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(1)
        self.listener.settimeout(5)
        self.proc = None
        self.sock = None
        self.peer = None
        self.transfer = None
        self.status = []
        self.done = threading.Event()

    def tearDown(self):
        if self.transfer: self.transfer.close()
        if self.peer: self.peer.close()
        if self.sock:
            try: self.sock.close()
            except OSError: pass
        self.listener.close()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.communicate(timeout=3)
        self.temp.cleanup()

    def launch(self, mode, python_transfer=True):
        self.proc = subprocess.Popen([
            'java', '-cp', os.environ['MINIANYDESK_TRANSFER_CLASSPATH'],
            'br.com.perabru.minianydesk.TransferHarness', mode, '127.0.0.1',
            str(self.listener.getsockname()[1]), str(self.source),
            str(self.android_destination), str(self.cache)
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.sock, _ = self.listener.accept()
        self.sock.settimeout(5)
        if python_transfer:
            self.sock.settimeout(None)
            self.peer = net.Peer(self.sock)
            def status(event):
                self.status.append(event)
                if 'success' in event: self.done.set()
            self.transfer = FileTransfers(self.peer, lambda fn: fn(),
                lambda offer, callback: callback(str(self.python_destination)), status,
                on_request_file=lambda callback: callback(str(self.source)))
            self.peer.on_message = self.transfer.handle
            self.peer.on_close = lambda reason: self.transfer.close()
            self.peer.start()

    def finish(self, success=True):
        output, errors = self.proc.communicate(timeout=6)
        self.assertEqual(self.proc.returncode, 0, output + errors)
        events = [json.loads(line) for line in output.splitlines()]
        statuses = [event for event in events if event['event'] == 'status']
        self.assertTrue(any(event.get('success') is success for event in statuses), output)
        final = next(event for event in reversed(events) if event['event'] == 'finished')
        self.assertTrue(final['completed'])
        self.assertEqual(final['cacheFiles'], 0, output)
        return events

    def wait_python(self, success=True):
        self.assertTrue(self.done.wait(5), self.status)
        self.assertTrue(any(event.get('success') is success for event in self.status), self.status)

    def test_android_to_python_multiblock_exact_bytes(self):
        self.launch('send')
        self.wait_python()
        self.finish()
        self.assertEqual(self.python_destination.read_bytes(), self.contents)

    def test_python_to_android_multiblock_exact_bytes(self):
        self.launch('receive')
        self.assertTrue(self.transfer.send_file(self.source))
        self.wait_python()
        events = self.finish()
        self.assertEqual(self.android_destination.read_bytes(), self.contents)
        guards = [event['active'] for event in events if event['event'] == 'prompt_guard']
        self.assertEqual(guards, [True, False])

    def test_zero_length_file(self):
        self.source.write_bytes(b'')
        self.launch('receive')
        self.assertTrue(self.transfer.send_file(self.source))
        self.wait_python()
        self.finish()
        self.assertEqual(self.android_destination.read_bytes(), b'')

    def test_source_without_size_hint_is_spooled_and_sent(self):
        self.launch('send-unknown-size')
        self.wait_python()
        self.finish()
        self.assertEqual(self.python_destination.read_bytes(), self.contents)

    def test_source_without_size_hint_still_enforces_limit_and_cleans_cache(self):
        with self.source.open('wb') as source:
            source.truncate(100 * 1024 * 1024 + 1)
        self.launch('send-unknown-size', False)
        self.assertEqual(net._read(self.sock)['type'], 'file_cancel')
        events = self.finish(False)
        self.assertTrue(any('100 MiB' in event.get('message', '') for event in events), events)
        self.assertFalse(self.android_destination.exists())

    def test_request_source_preparation_failure_releases_remote_request(self):
        self.source.unlink()
        self.launch('serve-request', False)
        identifier = str(uuid.uuid4())
        net._write(self.sock, {'type': 'file_request', 'id': identifier})
        self.assertEqual(net._read(self.sock)['type'], 'file_cancel')
        response = net._read(self.sock)
        self.assertEqual(response, {'type': 'file_request_result', 'id': identifier, 'accepted': False})
        self.finish(False)

    def test_android_requests_owner_selected_python_file(self):
        self.launch('request')
        self.wait_python()
        self.finish()
        self.assertEqual(self.android_destination.read_bytes(), self.contents)

    def test_python_requests_owner_selected_android_file(self):
        self.launch('serve-request')
        self.assertTrue(self.transfer.receive_request())
        self.wait_python()
        self.finish()
        self.assertEqual(self.python_destination.read_bytes(), self.contents)

    def test_user_refusal_creates_no_destination(self):
        self.launch('refuse')
        self.assertTrue(self.transfer.send_file(self.source))
        self.wait_python(False)
        self.finish(False)
        self.assertFalse(self.android_destination.exists())

    def offer(self, contents=b'abc', name='safe.bin', digest=None):
        identifier = str(uuid.uuid4())
        net._write(self.sock, {'type': 'file_offer', 'id': identifier, 'name': name,
            'size': len(contents), 'sha256': digest or hashlib.sha256(contents).hexdigest()})
        return identifier

    def test_sha_mismatch_deletes_private_partial_and_fresh_document(self):
        self.launch('receive', False)
        identifier = self.offer(digest='0' * 64)
        self.assertEqual(net._read(self.sock)['type'], 'file_accept')
        net._write(self.sock, {'type': 'file_chunk', 'id': identifier, 'offset': 0,
            'data': base64.b64encode(b'abc').decode()})
        self.assertEqual(net._read(self.sock)['offset'], 3)
        net._write(self.sock, {'type': 'file_end', 'id': identifier})
        self.assertEqual(net._read(self.sock)['type'], 'file_cancel')
        self.finish(False)
        self.assertFalse(self.android_destination.exists())

    def test_out_of_order_block_is_rejected_without_document(self):
        self.launch('receive', False)
        identifier = self.offer()
        self.assertEqual(net._read(self.sock)['type'], 'file_accept')
        net._write(self.sock, {'type': 'file_chunk', 'id': identifier, 'offset': 1, 'data': 'YWJj'})
        self.assertEqual(net._read(self.sock)['type'], 'file_cancel')
        self.finish(False)
        self.assertFalse(self.android_destination.exists())

    def test_noncanonical_base64_is_rejected(self):
        self.launch('receive', False)
        identifier = self.offer(contents=b'a')
        self.assertEqual(net._read(self.sock)['type'], 'file_accept')
        net._write(self.sock, {'type': 'file_chunk', 'id': identifier, 'offset': 0, 'data': 'YR=='})
        self.assertEqual(net._read(self.sock)['type'], 'file_cancel')
        self.finish(False)
        self.assertFalse(self.android_destination.exists())

    def test_cancel_while_picker_open_keeps_guard_until_local_callback(self):
        self.launch('receive-stale', False)
        identifier = self.offer()
        time.sleep(.15)
        net._write(self.sock, {'type': 'file_cancel', 'id': identifier})
        events = self.finish(False)
        self.assertEqual([e['active'] for e in events if e['event'] == 'prompt_guard'], [True, False])
        self.assertFalse(self.android_destination.exists())

    def test_premature_done_cannot_finish_sender(self):
        self.launch('send', False)
        offered = net._read(self.sock)
        self.assertEqual(offered['type'], 'file_offer')
        net._write(self.sock, {'type': 'file_done', 'id': offered['id']})
        self.assertEqual(net._read(self.sock)['type'], 'file_cancel')
        self.finish(False)

    def test_disconnect_discards_partial_document(self):
        self.launch('receive', False)
        identifier = self.offer()
        self.assertEqual(net._read(self.sock)['type'], 'file_accept')
        net._write(self.sock, {'type': 'file_chunk', 'id': identifier, 'offset': 0, 'data': 'YQ=='})
        self.assertEqual(net._read(self.sock)['offset'], 1)
        self.sock.shutdown(socket.SHUT_RDWR)
        self.sock.close()
        self.finish(False)
        self.assertFalse(self.android_destination.exists())


if __name__ == '__main__': unittest.main(verbosity=2)
