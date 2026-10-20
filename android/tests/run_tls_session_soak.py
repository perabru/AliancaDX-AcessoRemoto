"""22-second complete TLS/file session with production Kotlin + Desktop3.2.2.

Usage: python tests/run_tls_session_soak.py CLASSPATH OUTPUT_JSON [--seconds 22]
Compile compile_transfer_harness.py first. SAF and screen pixels are test fixtures;
TCP, TLS, pin approval, framing, controls, hashes and heartbeat traffic are real.
"""
from pathlib import Path
import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('classpath')
parser.add_argument('output', type=Path)
parser.add_argument('--seconds', type=float, default=22)
args = parser.parse_args()
assert 21 <= args.seconds <= 45, 'Cross at least two default ten-second heartbeat intervals'
reference = Path(os.environ.get('MINIANYDESK_PYTHON_SOURCE', Path(__file__).parent / 'python_reference')).resolve()
sys.path.insert(0, str(reference))
import net
from transfer import FileTransfers

observed = {'approvals': 0, 'received_controls': 0, 'received_ping': 0, 'received_pong': 0,
            'successful_transfers': 0, 'sent_controls': 0, 'sent_frames': 0, 'protocol_errors': []}
original_feed = net._PacketBuffer.feed
def feed(decoder, chunk):
    messages = original_feed(decoder, chunk)
    for message in messages:
        if message['type'] in ('ping', 'pong'):
            observed['received_' + message['type']] += 1
    return messages
net._PacketBuffer.feed = feed
peers, managers, closures = [], [], []
ready = threading.Event()
started = time.monotonic()
with tempfile.TemporaryDirectory(prefix='android-tls-soak-') as directory:
    root = Path(directory)
    android_bytes = bytes(range(256)) * 1301 + b'android-\x00\xff'
    desktop_bytes = b'Desktop\x00\xff' * 44001
    (root / 'android-source.bin').write_bytes(android_bytes)
    source = root / 'desktop-source.bin'; source.write_bytes(desktop_bytes)
    destination = root / 'desktop-destination.bin'
    def status(event):
        if event.get('success') is True: observed['successful_transfers'] += 1
        if event.get('success') is False: observed['protocol_errors'].append(event)
    def connected(peer, name, ip):
        peers.append(peer)
        manager = FileTransfers(peer, lambda fn: fn(), lambda offer, callback: callback(str(destination)), status)
        managers.append(manager)
        def message(value):
            if manager.handle(value): return
            if value['type'] == 'input':
                if value['sequence'] != observed['received_controls']:
                    observed['protocol_errors'].append(value)
                observed['received_controls'] += 1
            elif value['type'] == 'qa_upload_done':
                if not manager.send_file(source): observed['protocol_errors'].append('download refused')
        peer.on_message = message
        ready.set()
    def approve(name, ip):
        observed['approvals'] += 1
        return True
    def disconnected(peer, reason):
        closures.append({'seconds': time.monotonic() - started, 'reason': reason})
        for manager in managers: manager.close()
    host = net.HostServer('127.0.0.1', 0, 'testpassword', approve, connected, disconnected, root / 'identity')
    port = host.start()
    process = subprocess.Popen(['java', '-cp', args.classpath, 'br.com.perabru.minianydesk.TlsSessionSoakHarness',
        str(port), host.fingerprint, str(root), str(round(args.seconds * 1000))], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    try:
        assert ready.wait(5), 'TLS connection/pin/auth/consent not established'
        session_start = time.monotonic()
        while process.poll() is None and time.monotonic() - session_start < args.seconds + 5:
            peer = peers[0]
            if peer.closed: break
            sequence = observed['sent_controls']
            peer.send({'type': 'input', 'action': 'key', 'key': 'ctrl', 'down': False,
                       'captureId': 'qa-screen', 'sequence': sequence})
            observed['sent_controls'] += 1
            peer.send({'type': 'screen', 'width': 32, 'height': 32, 'captureId': 'qa-screen',
                       'data': base64.b64encode(b'synthetic-frame' * 1024).decode('ascii')})
            observed['sent_frames'] += 1
            time.sleep(.05)
        output, errors = process.communicate(timeout=5)
        assert process.returncode == 0, output + errors
        events = [json.loads(line) for line in output.splitlines()]
        kotlin = next(event for event in events if event['event'] == 'soak')
        closed = next(event for event in events if event['event'] == 'closed')
        android_destination = root / 'android-destination.bin'
        assert destination.read_bytes() == android_bytes
        assert android_destination.read_bytes() == desktop_bytes
        assert observed['approvals'] == 1 and observed['successful_transfers'] == 2
        assert observed['received_controls'] == kotlin['sentControls'] > 100
        assert kotlin['receivedControls'] > 100 and kotlin['receivedFrames'] > 100
        assert observed['received_ping'] >= 2 and observed['received_pong'] >= 2
        assert not observed['protocol_errors']
        assert kotlin['unexpectedClosures'] == 0 and closed['livePeerThreads'] == 0 and closed['callbacks'] == 1
        assert not closures or closures[0]['seconds'] >= args.seconds
        report = {'result': 'passed', 'seconds_requested': args.seconds,
                  'elapsed_seconds': round(time.monotonic() - started, 6), 'observed': observed,
                  'kotlin': kotlin, 'cleanup': closed, 'closures': closures,
                  'files': [{'direction': 'Android JVM to Desktop', 'bytes': len(android_bytes), 'sha256': hashlib.sha256(android_bytes).hexdigest()},
                            {'direction': 'Desktop to Android JVM', 'bytes': len(desktop_bytes), 'sha256': hashlib.sha256(desktop_bytes).hexdigest()}],
                  'reference': str(reference), 'reference_sha256': {name: hashlib.sha256((reference / name).read_bytes()).hexdigest() for name in ['net.py', 'transfer.py']},
                  'scope': 'Real localhost TLS and production Peer/FileTransfers. Explicit SAF doubles and synthetic frame payloads; no Android framework screen decoding or physical remote input.'}
    finally:
        for manager in managers: manager.close()
        host.stop()
        if process.poll() is None: process.kill(); process.communicate(timeout=3)
        for peer in peers:
            peer._io_thread.join(2)
            assert not peer._io_thread.is_alive()
        net._PacketBuffer.feed = original_feed
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'result': report['result'], 'elapsed_seconds': report['elapsed_seconds'], 'observed': observed}, ensure_ascii=False))
