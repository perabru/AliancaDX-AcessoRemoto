"""Direct, explicitly paired TLS transport for the two desktop applications."""
from __future__ import annotations

import datetime
from collections import deque
import hashlib
import hmac
import json
import os
from pathlib import Path
import select
import socket
import ssl
import struct
import threading
import time

VERSION = "3.0.0"
STATUS_VERSION = "3.2.2"
STATUS_ALPN = "minianydesk-status/1"
DEFAULT_PORT = 64321
MAX_MESSAGE = 4 * 1024 * 1024
SESSION_SOCKET_TIMEOUT = 0.05
HEARTBEAT_INTERVAL = 10.0
PEER_IDLE_TIMEOUT = 120.0
SEND_QUEUE_TIMEOUT = 5.0
MAX_PENDING_MESSAGES = 128
MAX_PENDING_BYTES = 8 * 1024 * 1024
# A stop-and-wait file exchange must still enqueue its response when normal
# input occupies the queue. The reserve shares the same FIFO and is bounded;
# in particular the I/O owner never waits for itself to drain the queue.
PROTOCOL_RESERVE_MESSAGES = 16
PROTOCOL_RESERVE_BYTES = 512 * 1024
_RESERVED_TYPES = frozenset({"file_offer", "file_accept", "file_chunk", "file_ack",
                             "file_end", "file_done", "file_cancel", "file_request",
                             "file_request_result", "file_request_cancel"})
IO_CHUNK = 64 * 1024


def _exact(sock, size):
    parts = bytearray()
    while len(parts) < size:
        chunk = sock.recv(size - len(parts))
        if not chunk:
            raise ConnectionError("Conexão encerrada.")
        parts.extend(chunk)
    return bytes(parts)


def _read(sock):
    length = struct.unpack("!I", _exact(sock, 4))[0]
    if not 0 < length <= MAX_MESSAGE:
        raise ValueError("Mensagem excede o limite permitido.")
    message = json.loads(_exact(sock, length).decode("utf-8"))
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ValueError("Mensagem inválida.")
    return message


def _packet(message):
    data = json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if not 0 < len(data) <= MAX_MESSAGE:
        raise ValueError("Mensagem excede o limite permitido.")
    return struct.pack("!I", len(data)) + data


def _write(sock, message):
    # Handshake-only, before the session's nonblocking I/O owner starts.
    sock.sendall(_packet(message))


class _PacketBuffer:
    """Retain fragmented headers/bodies across temporary receive failures."""

    def __init__(self):
        self.buffer = bytearray()
        self.length = None

    def feed(self, chunk):
        self.buffer.extend(chunk)
        messages = []
        while True:
            if self.length is None:
                if len(self.buffer) < 4:
                    break
                self.length = struct.unpack("!I", self.buffer[:4])[0]
                del self.buffer[:4]
                if not 0 < self.length <= MAX_MESSAGE:
                    raise ValueError("Mensagem excede o limite permitido.")
            if len(self.buffer) < self.length:
                break
            raw = bytes(self.buffer[:self.length])
            del self.buffer[:self.length]
            self.length = None
            message = json.loads(raw.decode("utf-8"))
            if not isinstance(message, dict) or not isinstance(message.get("type"), str):
                raise ValueError("Mensagem inválida.")
            messages.append(message)
        return messages


def state_directory():
    path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MiniAnyDesk"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _certificate(directory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    key_path, cert_path = directory / "host-key.pem", directory / "host-cert.pem"
    if key_path.exists() != cert_path.exists():
        raise RuntimeError("Identidade TLS incompleta. Restaure a chave e o certificado juntos.")
    if not key_path.exists():
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MiniAnyDesk Hospedeiro")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=3650))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
        key_bytes = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        # Exclusive creation avoids silently replacing an existing machine identity.
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key_bytes)
        try:
            with cert_path.open("xb") as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))
        except Exception:
            key_path.unlink(missing_ok=True)
            raise
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    return key_path, cert_path, cert.fingerprint(hashes.SHA256()).hex().upper()


class Peer:
    """One SSL I/O owner, bounded ordered controls and a latest-frame slot.

    Screens and adjacent mouse moves may be superseded. Keys, clicks, release
    events and file packets remain ordered. No application data is replayed
    after a real disconnect; a fresh connection still authenticates normally.
    """

    def __init__(self, sock, on_message=None, on_close=None):
        self.sock = sock
        self.on_message = on_message or (lambda message: None)
        self.on_close = on_close or (lambda reason: None)
        self.closed = False
        self._lock = threading.RLock()
        self._space = threading.Condition(self._lock)
        self._out = deque()
        self._out_bytes = 0
        self._frame = None
        self._wake = threading.Event()
        self._stopped = threading.Event()
        self._started = False
        self._io_thread = None
        self._ping_pending = self._pong_pending = False
        self._last_received = time.monotonic()

    def start(self):
        with self._lock:
            if self._started or self.closed:
                return
            self._started = True
            self._last_received = time.monotonic()
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except (AttributeError, OSError):
                pass  # socketpair/test sockets need not support TCP options.
            self.sock.setblocking(False)
            self._io_thread = threading.Thread(target=self._io_loop,
                                               name="MiniAnyDesk-session-io", daemon=True)
            self._io_thread.start()

    @staticmethod
    def _replace_key(message):
        if message["type"] == "input" and message.get("action") == "move":
            return ("move", message.get("captureId"))
        if message["type"] == "state":
            # Never merge a pause/consent/capture barrier into a resumed state.
            return ("state", message.get("sharing"), message.get("localPaused"),
                    message.get("promptActive"), message.get("captureAvailable"))
        return None

    def send(self, message):
        if not isinstance(message, dict) or not isinstance(message.get("type"), str):
            raise ValueError("Mensagem inválida.")
        # Encoding here produces an immutable snapshot, without JSON decoding
        # and re-encoding every JPEG in the writer.
        packet = _packet(message)
        kind, key = message["type"], self._replace_key(message)
        reserved = (kind in _RESERVED_TYPES and len(packet) <= 128 * 1024) or (
            kind == "state" and len(packet) <= 64 * 1024)
        message_limit = MAX_PENDING_MESSAGES + (PROTOCOL_RESERVE_MESSAGES if reserved else 0)
        byte_limit = MAX_PENDING_BYTES + (PROTOCOL_RESERVE_BYTES if reserved else 0)
        deadline = time.monotonic() + SEND_QUEUE_TIMEOUT
        congested = False
        with self._space:
            if self.closed:
                raise ConnectionError("Conexão encerrada.")
            if kind == "screen":
                self._frame = packet
                self._wake.set()
                return
            if kind in ("ping", "pong"):
                if kind == "ping": self._ping_pending = True
                else: self._pong_pending = True
                self._wake.set()
                return
            if kind == "state" and message.get("sharing") is False:
                self._frame = None
            if key is not None and self._out and self._out[-1][0] == key:
                old_size = len(self._out[-1][1])
                if self._out_bytes - old_size + len(packet) <= MAX_PENDING_BYTES:
                    self._out[-1] = (key, packet)
                    self._out_bytes += len(packet) - old_size
                    self._wake.set()
                    return
            while (len(self._out) >= message_limit or
                   self._out_bytes + len(packet) > byte_limit):
                if kind == "input" and message.get("action") == "move":
                    return False  # A later absolute position replaces this move.
                remaining = deadline - time.monotonic()
                if (remaining <= 0 or not self._started or
                        threading.current_thread() is self._io_thread):
                    congested = True
                    break
                self._space.wait(remaining)
                if self.closed:
                    raise ConnectionError("Conexão encerrada.")
            if not congested:
                self._out.append((key, packet))
                self._out_bytes += len(packet)
            self._wake.set()
        if congested:
            reason = "Rede sem capacidade para os comandos pendentes. Reconecte após verificar a rede."
            self.close(reason)
            raise ConnectionError(reason)

    def _next_packet(self):
        with self._space:
            if self._pong_pending:
                self._pong_pending = False
                return _packet({"type": "pong"})
            if self._ping_pending:
                self._ping_pending = False
                return _packet({"type": "ping"})
            if self._out:
                _key, packet = self._out.popleft()
                self._out_bytes -= len(packet)
                self._space.notify_all()
                return packet
            packet, self._frame = self._frame, None
            self._wake.clear()
            return packet

    def _io_loop(self):
        decoder = _PacketBuffer()
        packet, offset = None, 0
        read_needs_write = write_needs_read = False
        next_ping = time.monotonic() + HEARTBEAT_INTERVAL
        write_progress = time.monotonic()
        try:
            while not self.closed:
                now = time.monotonic()
                if now - self._last_received >= PEER_IDLE_TIMEOUT:
                    raise TimeoutError("Sem resposta do outro dispositivo por tempo prolongado. Verifique a rede e reconecte.")
                if now >= next_ping:
                    self.send({"type": "ping"})
                    next_ping = now + HEARTBEAT_INTERVAL
                if packet is None:
                    packet, offset = self._next_packet(), 0
                    write_progress = now
                elif now - write_progress >= PEER_IDLE_TIMEOUT:
                    raise TimeoutError("Envio sem progresso por tempo prolongado. Verifique a rede e reconecte.")
                read_ready, write_ready, _ = select.select(
                    [self.sock], [self.sock] if read_needs_write or (packet is not None and not write_needs_read) else [], [],
                    min(SESSION_SOCKET_TIMEOUT, HEARTBEAT_INTERVAL,
                        max(0.001, PEER_IDLE_TIMEOUT - (now - self._last_received))))
                buffered = getattr(self.sock, "pending", lambda: 0)()
                if read_ready or buffered or (read_needs_write and write_ready):
                    try:
                        chunk = self.sock.recv(IO_CHUNK)
                        if not chunk:
                            raise ConnectionError("O outro dispositivo encerrou a conexão.")
                        read_needs_write = False
                        for message in decoder.feed(chunk):
                            self._last_received = time.monotonic()
                            if message["type"] == "ping":
                                self.send({"type": "pong"})
                            elif message["type"] != "pong":
                                self.on_message(message)
                    except ssl.SSLWantWriteError:
                        read_needs_write = True
                    except ssl.SSLWantReadError:
                        read_needs_write = False
                    except (BlockingIOError, socket.timeout):
                        read_needs_write = False
                        self._stopped.wait(0.001)
                if packet is not None and ((write_ready and not write_needs_read) or
                                           (write_needs_read and read_ready)):
                    try:
                        written = self.sock.send(memoryview(packet)[offset:offset + IO_CHUNK])
                        if written <= 0:
                            raise ConnectionError("Não foi possível enviar os dados da sessão.")
                        offset += written
                        write_progress = time.monotonic()
                        write_needs_read = False
                        if offset == len(packet):
                            packet, offset = None, 0
                    except ssl.SSLWantReadError:
                        write_needs_read = True
                    except ssl.SSLWantWriteError:
                        write_needs_read = False
                    except (BlockingIOError, socket.timeout):
                        write_needs_read = False
                        self._stopped.wait(0.001)
        except Exception as exc:
            self.close(str(exc))

    def close(self, reason="Conexão encerrada."):
        with self._space:
            if self.closed:
                return
            self.closed = True
            self._frame = None
            self._out.clear()
            self._out_bytes = 0
            self._stopped.set()
            self._wake.set()
            self._space.notify_all()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        finally:
            try:
                self.on_close(str(reason))
            except Exception:
                pass


def connect(address, port, password, name, on_message, on_close, trust_callback):
    if not isinstance(password, str) or not 8 <= len(password) <= 256:
        raise ValueError("Use uma senha com 8 a 256 caracteres.")
    sock = socket.create_connection((address, int(port)), timeout=8)
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        # Certificate authentication is explicit SHA256 pinning, before credentials.
        context.verify_mode = ssl.CERT_NONE
        sock = context.wrap_socket(sock, server_hostname=address)
        fingerprint = hashlib.sha256(sock.getpeercert(binary_form=True)).hexdigest().upper()
        if not trust_callback(address, int(port), fingerprint):
            raise PermissionError("Identidade do hospedeiro não aprovada.")
        sock.settimeout(100)
        _write(sock, {"type": "login", "version": VERSION, "name": str(name)[:80], "password": password})
        answer = _read(sock)
        if answer.get("type") != "auth_ok":
            raise PermissionError(str(answer.get("message", "Conexão não autorizada.")))
        peer = Peer(sock, on_message, on_close)
        peer.fingerprint = fingerprint
        computer_name = answer.get("computerName", "")
        peer.computer_name = ""
        if (isinstance(computer_name, str) and len(computer_name) <= 80 and
                not any(ord(character) < 32 or ord(character) == 127 for character in computer_name)):
            try:
                computer_name.encode("utf-8")
                peer.computer_name = computer_name
            except UnicodeError:
                pass
        peer.start()
        return peer
    except Exception:
        sock.close()
        raise


class HostServer:
    def __init__(self, bind, port, password, on_request, on_connect, on_disconnect, state_dir=None, status_callback=None):
        if not isinstance(password, str) or not 8 <= len(password) <= 256:
            raise ValueError("Use uma senha com 8 a 256 caracteres.")
        self.bind, self.port = bind, int(port)
        self._password_hash = hashlib.sha256(password.encode("utf-8")).digest()
        self.on_request, self.on_connect, self.on_disconnect = on_request, on_connect, on_disconnect
        key, cert, self.fingerprint = _certificate(state_dir or state_directory())
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context.load_cert_chain(cert, key)
        # ALPN separates availability from login; old clients advertise none.
        if ssl.HAS_ALPN:
            self.context.set_alpn_protocols([STATUS_ALPN])
        self.status_callback = status_callback or socket.gethostname
        self._lock = threading.RLock()
        self._running = False
        self._generation = 0
        self._peer = None
        self._pending = False
        self._sockets = set()
        self._failed = {}
        self._workers = threading.BoundedSemaphore(4)

    def _status_name(self):
        try:
            name = self.status_callback()
            if isinstance(name, dict):
                name = name.get("name", name.get("computer_name", ""))
            name = " ".join(str(name).split())[:80]
            name.encode("utf-8")
            return "".join(character for character in name if ord(character) >= 32 and ord(character) != 127)
        except Exception:
            return ""

    def start(self):
        with self._lock:
            if self._running:
                return self.port
            self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self.listener.bind((self.bind, self.port))
                self.listener.listen(4)
                self.listener.settimeout(.5)
            except Exception:
                self.listener.close()
                raise
            self.port = self.listener.getsockname()[1]
            self._generation += 1
            self._workers = threading.BoundedSemaphore(4)
            self._running = True
            generation, listener, workers = self._generation, self.listener, self._workers
        self._accept_thread = threading.Thread(target=self._accept, args=(generation, listener, workers), daemon=True)
        self._accept_thread.start()
        return self.port

    def _accept(self, generation, listener, workers):
        while self._running and generation == self._generation:
            try:
                sock, address = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            if not workers.acquire(False):
                sock.close()
                continue
            with self._lock:
                if generation != self._generation or not self._running:
                    workers.release()
                    sock.close()
                    return
                self._sockets.add(sock)
            threading.Thread(target=self._handle, args=(sock, address, generation, workers), daemon=True).start()

    def _handle(self, raw, address, generation, workers):
        sock, owns_pending, peer = raw, False, None
        try:
            raw.settimeout(8)
            with self._lock:
                if not self._running or generation != self._generation:
                    return
                sock = self.context.wrap_socket(raw, server_side=True, do_handshake_on_connect=False)
                self._sockets.discard(raw)
                self._sockets.add(sock)
            sock.do_handshake()
            if ssl.HAS_ALPN and sock.selected_alpn_protocol() == STATUS_ALPN:
                # No login packet, password comparison, rate counter, consent or
                # Peer is involved in this read-only status connection.
                with self._lock:
                    if not self._running or generation != self._generation:
                        return
                    busy = bool(self._pending or (self._peer and not self._peer.closed))
                sock.settimeout(3)
                _write(sock, {"type": "status", "agent": "MiniAnyDesk", "version": STATUS_VERSION,
                              "name": self._status_name(), "online": True, "busy": busy})
                return
            sock.settimeout(100)  # Allow time to compare the fingerprint before any password is sent.
            login = _read(sock)
            password, name = login.get("password"), login.get("name")
            ip = address[0]
            now = time.monotonic()
            with self._lock:
                attempts = [t for t in self._failed.get(ip, []) if now - t < 60]
                self._failed[ip] = attempts
                allowed = (len(attempts) < 5 and login.get("type") == "login"
                           and isinstance(password, str) and 8 <= len(password) <= 256
                           and isinstance(name, str) and 0 < len(name) <= 80
                           and hmac.compare_digest(hashlib.sha256(password.encode("utf-8")).digest(), self._password_hash))
                if not allowed:
                    if len(attempts) < 5:
                        attempts.append(now)
            if not allowed:
                _write(sock, {"type": "error", "message": "Senha incorreta ou limite de tentativas atingido."})
                return
            with self._lock:
                if not self._running or generation != self._generation or self._pending or (self._peer and not self._peer.closed):
                    _write(sock, {"type": "error", "message": "Hospedeiro ocupado. Há uma sessão ou aprovação em andamento."})
                    return
                self._pending = owns_pending = True
            if not self.on_request(name, ip):
                _write(sock, {"type": "error", "message": "O usuário do hospedeiro recusou a conexão."})
                return
            with self._lock:
                if not self._running or generation != self._generation:
                    return
                peer = Peer(sock, on_close=lambda reason: self._disconnected(peer, reason))
                self._peer = peer
            self.on_connect(peer, name, ip)
            with self._lock:
                cancelled = peer.closed or not self._running or generation != self._generation
            if cancelled:
                peer.close("Hospedeiro parou de receber conexões.")
                return
            _write(sock, {"type": "auth_ok", "version": VERSION,
                          "computerName": self._status_name(), "agentVersion": STATUS_VERSION})
            peer.start()
        except Exception as exc:
            if peer:
                peer.close(str(exc))
        finally:
            with self._lock:
                if owns_pending and generation == self._generation:
                    self._pending = False
                self._sockets.discard(sock)
                self._sockets.discard(raw)
            if peer is None:
                sock.close()
            workers.release()

    def _disconnected(self, peer, reason):
        with self._lock:
            if self._peer is peer:
                self._peer = None
        self.on_disconnect(peer, reason)

    def stop(self):
        with self._lock:
            self._running = False
            self._generation += 1
            self._pending = False
            peer = self._peer
            pending = list(self._sockets)
            listener = getattr(self, "listener", None)
        if listener:
            try:
                listener.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            listener.close()
        if peer:
            peer.close("Hospedeiro parou de receber conexões.")
        for sock in pending:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        worker = getattr(self, "_accept_thread", None)
        if worker and worker is not threading.current_thread():
            worker.join(timeout=1)
