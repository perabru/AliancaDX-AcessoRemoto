"""Visible, app-scoped bridge between the approved Host and a local browser.

Edge/Chrome supplies WebRTC; this module never opens a public listener. The
browser receives a fresh loopback capability in its URL fragment and uses an
Authorization header for every API call. Host credentials stay in this process.
Opening a browser data channel is not authorization: ``net.connect`` still
verifies the invitation's TLS identity, password, and the Host's local approval.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass, field
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import mimetypes
import os
from pathlib import Path, PurePosixPath
import secrets
import socket
import sys
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit
import webbrowser

import mesh
import net

VERSION = "7.4.0"
APP_URL = "https://perabru.github.io/AliancaDX-AcessoRemoto/ios-pwa/"
MAX_BODY = net.MAX_MESSAGE
MAX_QUEUE_BYTES = 8 * 1024 * 1024
MAX_QUEUE_MESSAGES = 128
CLIENT_LEASE_SECONDS = 30.0
IDLE_SHUTDOWN_SECONDS = 300.0
EVENT_STREAM_SECONDS = 30.0
COMMAND_TYPES = frozenset({"input", "monitor", "sharing", "stream_ready",
    "stream_feedback", "stream_resume", "stream_settings", "file_offer", "file_accept", "file_chunk",
    "file_ack", "file_end", "file_done", "file_cancel", "file_request",
    "file_request_result", "file_request_cancel"})
STATIC_SUFFIXES = frozenset({".html", ".js", ".mjs", ".css", ".json", ".webmanifest",
                            ".png", ".svg", ".ico", ".txt", ".woff2"})


class ConnectorError(ValueError):
    """A fixed, user-facing error, never an underlying network/credential dump."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _name(value, default="iPhone AliançaDX"):
    if not isinstance(value, str):
        raise ConnectorError("Informe um nome válido para o dispositivo.")
    value = value.strip() or default
    if len(value) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ConnectorError("O nome do dispositivo deve ter até 80 caracteres.")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ConnectorError("Informe um nome válido para o dispositivo.") from None
    return value


def _pairing(value):
    """Accept the exact raw code or the existing Host's one-code invitation."""
    if not isinstance(value, str) or len(value) > 4096:
        raise ConnectorError("Cole um convite atual do Hospedeiro.")
    text = value.strip()
    try:
        if text.upper().startswith("MDA6-") and "\n" not in text:
            pairing = mesh.parse_pairing(text)
        else:
            lines = text.splitlines()
            if not lines or not lines[0].startswith(("AliançaDX Host ", "MiniAnyDesk Host ")):
                raise ValueError
            codes = [line.partition(":")[2].strip() for line in lines
                     if line.startswith("Código privado:")]
            if len(codes) != 1:
                raise ValueError
            pairing = mesh.parse_pairing(codes[0])
        if not pairing.fingerprint:
            raise ValueError
        return pairing
    except (ValueError, mesh.MeshError):
        raise ConnectorError("O convite precisa conter um código válido e a identidade TLS do Host.") from None


@dataclass(frozen=True, repr=False)
class Configuration:
    pairing: object = field(repr=False)
    password: str = field(repr=False)
    name: str
    mode: str

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or not set(value) <= {"invitation", "password", "name", "mode"}:
            raise ConnectorError("Configuração de conexão inválida.")
        pairing = _pairing(value.get("invitation"))
        password = value.get("password")
        if not isinstance(password, str) or not 8 <= len(password) <= 256:
            raise ConnectorError("Informe a senha atual do Host, com 8 a 256 caracteres.")
        try:
            password.encode("utf-8")
        except UnicodeError:
            raise ConnectorError("Informe uma senha válida.") from None
        mode = value.get("mode", "local")
        if mode not in ("local", "mesh"):
            raise ConnectorError("Escolha este PC ou outro PC por convite.")
        return cls(pairing, password, _name(value.get("name", "")), mode)


class BoundedEvents:
    """FIFO for reliable packets, plus one replaceable latest screen packet."""
    def __init__(self, max_bytes=MAX_QUEUE_BYTES, max_messages=MAX_QUEUE_MESSAGES):
        self.condition = threading.Condition(threading.RLock())
        self.items = deque()
        self.frame = None
        self.bytes = 0
        self.max_bytes, self.max_messages = max_bytes, max_messages
        self.closed = False
        self.reader = 0

    def reset(self, state):
        with self.condition:
            self.items.clear(); self.frame = None; self.bytes = 0
            self._put_locked(state, False)
            self.condition.notify_all()

    def _put_locked(self, value, frame):
        data = _encode(value) + b"\n"
        old_size = len(self.frame) if frame and self.frame is not None else 0
        count = len(self.items) + (self.frame is not None)
        if (len(data) > MAX_BODY + 1024 or self.bytes - old_size + len(data) > self.max_bytes
                or (not (frame and self.frame is not None) and count >= self.max_messages)):
            return False
        self.bytes -= old_size
        if frame:
            self.frame = data
        else:
            self.items.append(data)
        self.bytes += len(data)
        return True

    def put(self, value, frame=False):
        with self.condition:
            if self.closed:
                return False
            accepted = self._put_locked(value, frame)
            self.condition.notify_all()
            return accepted

    def discard_frame(self):
        with self.condition:
            if self.frame is not None:
                self.bytes -= len(self.frame); self.frame = None

    def subscribe(self):
        with self.condition:
            self.reader += 1
            self.condition.notify_all()
            return self.reader

    def next(self, reader, timeout=1.0):
        """None means heartbeat, b'' means closed/replaced event consumer."""
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                if self.closed or reader != self.reader:
                    return b""
                data = self.items.popleft() if self.items else self.frame
                if data is not None:
                    if not self.items and data is self.frame:
                        self.frame = None
                    self.bytes -= len(data)
                    return data
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.condition.wait(remaining)

    def close(self):
        with self.condition:
            self.closed = True
            self.items.clear(); self.frame = None; self.bytes = 0
            self.condition.notify_all()


def validate_command(message):
    if not isinstance(message, dict) or message.get("type") not in COMMAND_TYPES:
        raise ConnectorError("Comando não permitido no conector.")
    try:
        if len(_encode(message)) > MAX_BODY:
            raise ConnectorError("O comando excede o tamanho permitido.", 413)
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise ConnectorError("Comando inválido.") from None
    kind = message["type"]
    if kind == "input":
        action = message.get("action")
        if action not in ("move", "button", "key", "wheel", "release"):
            raise ConnectorError("Comando de mouse ou teclado inválido.")
        if action == "release":
            return dict(message)
        if action in ("button", "key") and type(message.get("down")) is not bool:
            raise ConnectorError("Estado do botão ou tecla inválido.")
        if action == "button" and message.get("button") not in ("left", "middle", "right"):
            raise ConnectorError("Botão do mouse inválido.")
        if action == "key":
            key = message.get("key")
            if not isinstance(key, str) or not 1 <= len(key) <= 30 or any(ord(c) < 32 for c in key):
                raise ConnectorError("Tecla inválida.")
        release = action in ("key", "button") and message.get("down") is False
        if not release:
            capture = message.get("captureId")
            if not isinstance(capture, str) or not 1 <= len(capture) <= 256:
                raise ConnectorError("Aguarde uma imagem atual do PC.")
        if action in ("move", "button", "wheel") and not release:
            for key in ("x", "y"):
                point = message.get(key)
                if type(point) not in (int, float) or not math.isfinite(point) or not 0 <= point <= 1:
                    raise ConnectorError("Posição do mouse inválida.")
        if action == "wheel":
            delta = message.get("delta")
            if type(delta) not in (int, float) or not math.isfinite(delta) or not -20 <= delta <= 20:
                raise ConnectorError("Rolagem inválida.")
    elif kind == "monitor":
        value = message.get("id")
        if str(value) not in ("0", "all") and not (str(value).isdigit() and 1 <= int(value) <= 64):
            raise ConnectorError("Monitor inválido.")
    elif kind == "stream_settings":
        if (set(message) != {"type", "version", "mode"}
                or type(message.get("version")) is not int or message["version"] != 1
                or type(message.get("mode")) is not str
                or message["mode"] not in {"auto", "latency", "quality"}):
            raise ConnectorError("Escolha um modo de imagem válido.")
    elif kind == "sharing" and type(message.get("enabled")) is not bool:
        raise ConnectorError("Estado de compartilhamento inválido.")
    return dict(message)


class ConnectorSession:
    def __init__(self, bootstrap=None, *, connect_function=None, mesh_factory=None,
                 clock=time.monotonic):
        self.lock = threading.RLock()
        self.commands_ready = threading.Condition(self.lock)
        self.events = BoundedEvents()
        self.commands = deque()
        self.command_bytes = 0
        self.generation = 0
        self.configuration = None
        self.peer = self.manager = self.mesh_directory = None
        self.connecting = False
        self.closed = False
        self.cancel = threading.Event()
        self.early = []
        self.early_bytes = 0
        self.state = "ready"
        self.message = "Configure o Host para conectar o iPhone."
        self.clock = clock
        self.last_http = clock()
        self.last_pulse = self.last_http
        self.connect_function = connect_function or net.connect
        self.mesh_factory = mesh_factory or mesh.MeshManager
        self.sender = threading.Thread(target=self._send_commands, daemon=True,
                                       name="AliancaDX-iPhone-commands")
        self.sender.start()
        self.events.reset(self._state_message())
        if bootstrap is not None:
            self.configure(bootstrap)

    def _state_message(self):
        result = {"type": "bridge_state", "state": self.state,
                  "message": self.message, "generation": self.generation}
        if self.peer is not None and not self.peer.closed:
            result["computerName"] = getattr(self.peer, "computer_name", "")
            if self.state == "authorized":
                # This is the local connector's capability, not a claim about
                # the Host. The phone must also see the Host's frame capability.
                result["streamSettingsVersion"] = 1
        return result

    def metadata(self):
        with self.lock:
            return {"version": VERSION, "configured": self.configuration is not None,
                    "generation": self.generation, "state": self.state,
                    "name": self.configuration.name if self.configuration else "",
                    "mode": self.configuration.mode if self.configuration else "local"}

    def touch(self):
        with self.lock:
            self.last_http = self.clock()

    def pulse(self, generation=None):
        with self.lock:
            if self.closed:
                raise ConnectorError("O conector foi encerrado.", 410)
            if generation is not None and generation != self.generation:
                raise ConnectorError("Esta sessão já foi substituída.", 409)
            self.last_pulse = self.last_http = self.clock()
            return {"alive": True, "generation": self.generation}

    def _invalidate_locked(self):
        self.cancel.set()
        self.cancel = threading.Event()
        self.generation += 1
        old = (self.peer, self.manager, self.mesh_directory)
        self.peer = self.manager = self.mesh_directory = None
        self.connecting = False
        self.commands.clear(); self.command_bytes = 0
        self.early.clear(); self.early_bytes = 0
        self.commands_ready.notify_all()
        return old

    @staticmethod
    def _dispose(resources):
        peer, manager, directory = resources
        if peer is not None:
            try:
                peer.close()
            except Exception:
                pass
        if manager is not None:
            try:
                manager.stop()
                manager.join(timeout=5)
            except Exception:
                pass
        if directory is not None:
            try:
                directory.cleanup()
            except OSError:
                pass

    def configure(self, values):
        configuration = Configuration.parse(values)
        with self.lock:
            if self.closed:
                raise ConnectorError("O conector foi encerrado.", 410)
            old = self._invalidate_locked()
            self.configuration = configuration
            self.state, self.message = "ready", "Host configurado. Conecte o iPhone e aprove o pedido no PC."
            self.events.reset(self._state_message())
            result = self.metadata()
        self._dispose(old)
        return result

    def connect(self, name="iPhone AliançaDX"):
        name = _name(name)
        with self.lock:
            if self.closed:
                raise ConnectorError("O conector foi encerrado.", 410)
            if self.configuration is None:
                raise ConnectorError("Configure o Host no computador primeiro.", 409)
            if self.connecting or (self.peer is not None and not self.peer.closed):
                raise ConnectorError("Esta conexão já está aberta ou aguardando aprovação.", 409)
            old = self._invalidate_locked()
            generation, cancel, configuration = self.generation, self.cancel, self.configuration
            self.connecting = True
            self.last_pulse = self.clock()
            self.state, self.message = "awaiting", "Aguardando a senha e a aprovação no Hospedeiro."
            self.events.reset(self._state_message())
        self._dispose(old)
        threading.Thread(target=self._connect_worker,
            args=(configuration, name, generation, cancel), daemon=True,
            name="AliancaDX-iPhone-connect").start()
        return {"generation": generation, "state": "awaiting"}

    def _current(self, generation):
        return not self.closed and self.generation == generation

    def _connect_worker(self, configuration, name, generation, cancel):
        manager = directory = peer = None
        try:
            if configuration.mode == "mesh":
                directory = tempfile.TemporaryDirectory(prefix="aliancadx-ios-")
                manager = self.mesh_factory("controller", Path(directory.name),
                    on_status=lambda status: self._mesh_status(status, generation))
                with self.lock:
                    if not self._current(generation):
                        return
                    self.manager, self.mesh_directory = manager, directory
                manager.start(configuration.pairing)
                address, port = manager.wait_ready(timeout=45, cancel_event=cancel)
            else:
                address, port = "127.0.0.1", configuration.pairing.port
            with self.lock:
                if not self._current(generation):
                    return
            peer = self.connect_function(address, port, configuration.password, name,
                lambda message: self._on_message(message, generation),
                lambda reason: self._transport_closed(generation),
                lambda _address, _port, fingerprint: hmac.compare_digest(
                    str(fingerprint).upper(), configuration.pairing.fingerprint.upper()),
                cancel_event=cancel)
            with self.lock:
                if not self._current(generation) or not self.connecting or peer.closed:
                    return
                self.peer = peer
                self.connecting = False
                self.state, self.message = "authorized", "Acesso autorizado pelo responsável no PC."
                self.events.reset(self._state_message())
                for packet in self.early:
                    if not self._event_locked(packet, generation):
                        raise ConnectorError("A fila de imagens e comandos ficou ocupada.")
                self.early.clear(); self.early_bytes = 0
                peer = None  # Ownership passed to this session.
        except Exception:
            with self.lock:
                if self._current(generation) and self.connecting:
                    self.connecting = False
                    self.cancel.set()
                    self.early.clear(); self.early_bytes = 0
                    self.state = "error"
                    self.message = "Não foi possível autorizar o acesso. Confira o convite, a senha, a rede e a aprovação no Host."
                    self.events.reset(self._state_message())
        finally:
            if peer is not None:
                try:
                    peer.close()
                except Exception:
                    pass
            with self.lock:
                retained = self.manager is manager and self._current(generation) and (
                    self.connecting or self.peer is not None and not self.peer.closed)
                if not retained and self.manager is manager:
                    self.manager = self.mesh_directory = None
            if manager is not None and not retained:
                self._dispose((None, manager, directory))

    def _mesh_status(self, status, generation):
        with self.lock:
            if self._current(generation) and self.connecting:
                # MeshStatus is application-authored and deliberately omits codes.
                self.message = str(status.message)[:500]
                self.events.put(self._state_message())

    def _event_locked(self, message, generation):
        if message.get("type") == "state" and (message.get("sharing") is False
                or message.get("promptActive") or message.get("localPaused")
                or message.get("captureAvailable") is False):
            self.events.discard_frame()
        return self.events.put({"type": "bridge_packet", "generation": generation,
                               "message": message}, frame=message.get("type") == "screen")

    def _on_message(self, message, generation):
        overflow = False
        with self.lock:
            if not self._current(generation) or not isinstance(message, dict):
                return
            if self.connecting:
                try:
                    size = len(_encode(message))
                    if (len(self.early) >= MAX_QUEUE_MESSAGES or self.early_bytes + size > MAX_QUEUE_BYTES):
                        overflow = True
                    else:
                        self.early.append(message); self.early_bytes += size
                except (ValueError, TypeError, UnicodeError):
                    overflow = True
            elif self.peer is not None and not self.peer.closed:
                overflow = not self._event_locked(message, generation)
        if overflow:
            self._fail(generation, "A fila de dados ficou ocupada. Conecte novamente.")

    def _transport_closed(self, generation):
        self._fail(generation, "A conexão com o Hospedeiro foi encerrada.", "closed")

    def _fail(self, generation, message, state="error"):
        with self.lock:
            if not self._current(generation) or (not self.connecting and self.peer is None):
                return
            peer, manager, directory = self.peer, self.manager, self.mesh_directory
            self.peer = self.manager = self.mesh_directory = None
            self.connecting = False; self.cancel.set()
            self.commands.clear(); self.command_bytes = 0
            self.early.clear(); self.early_bytes = 0
            self.state, self.message = state, message
            self.events.reset(self._state_message())
            self.commands_ready.notify_all()
        # A callback can run on the Peer I/O owner. Do not wait for a mesh worker there.
        threading.Thread(target=self._dispose, args=((peer, manager, directory),),
                         daemon=True, name="AliancaDX-iPhone-cleanup").start()

    def command(self, message, generation):
        message = validate_command(message)
        size = len(_encode(message))
        with self.commands_ready:
            if not self._current(generation) or self.peer is None or self.peer.closed or self.connecting:
                raise ConnectorError("Esta sessão não está autorizada ou foi substituída.", 409)
            # Supersede only adjacent cursor positions. Clicks, releases,
            # feedback and capture changes remain ordered barriers.
            previous = self.commands[-1] if self.commands else None
            replace_move = bool(previous and previous[0] == generation
                and previous[1] is self.peer
                and message.get("type") == "input" and message.get("action") == "move"
                and previous[2].get("type") == "input" and previous[2].get("action") == "move"
                and previous[2].get("captureId") == message.get("captureId"))
            queued_bytes = self.command_bytes - (previous[3] if replace_move else 0) + size
            if (not replace_move and len(self.commands) >= MAX_QUEUE_MESSAGES
                    or queued_bytes > MAX_QUEUE_BYTES):
                overflow = True
            else:
                if replace_move:
                    self.commands.pop()
                self.commands.append((generation, self.peer, message, size))
                self.command_bytes = queued_bytes
                if message.get("type") == "monitor":
                    self.events.discard_frame()
                self.commands_ready.notify()
                overflow = False
        if overflow:
            self._fail(generation, "A fila de comandos ficou ocupada. Conecte novamente.")
            raise ConnectorError("A fila de comandos ficou ocupada.", 429)
        return {"accepted": True, "generation": generation}

    def _send_commands(self):
        while True:
            with self.commands_ready:
                while not self.commands and not self.closed:
                    self.commands_ready.wait(.5)
                if self.closed:
                    return
                generation, peer, message, size = self.commands.popleft()
                self.command_bytes -= size
                current = self._current(generation) and self.peer is peer and not peer.closed
            if current:
                try:
                    if peer.send(message) is False:
                        raise OSError
                except Exception:
                    self._fail(generation, "Não foi possível entregar um comando ao PC.")

    def disconnect(self, generation=None):
        with self.lock:
            if generation is not None and generation != self.generation:
                raise ConnectorError("Esta sessão já foi substituída.", 409)
            old = self._invalidate_locked()
            self.state, self.message = "closed", "Acesso encerrado. O Host continua sob controle do responsável."
            self.events.reset(self._state_message())
            result = self.metadata()
        self._dispose(old)
        return result

    def check_lease(self):
        with self.lock:
            elapsed = self.clock() - self.last_http
            unresponsive = self.clock() - self.last_pulse
            active = self.connecting or self.peer is not None and not self.peer.closed
            generation = self.generation
        if active and unresponsive > CLIENT_LEASE_SECONDS:
            self._fail(generation, "O navegador do conector foi fechado ou ficou sem resposta.", "closed")
        return elapsed > IDLE_SHUTDOWN_SECONDS

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            old = self._invalidate_locked()
            self.configuration = None
            self.events.close()
        self._dispose(old)


def resolve_assets():
    directory = Path(__file__).resolve().parent
    for path in (directory / "ios-pwa", directory.parent / "ios-pwa"):
        if path.is_dir() and not path.is_symlink():
            return path.resolve()
    raise ConnectorError("Os arquivos do conector não foram encontrados. Reinstale o aplicativo.")


class ConnectorServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 16
    allow_reuse_address = False

    def __init__(self, session=None, asset_root=None, app_url=APP_URL):
        self.asset_root = Path(asset_root).resolve() if asset_root is not None else resolve_assets()
        if not self.asset_root.is_dir():
            raise ConnectorError("Os arquivos do conector não foram encontrados.")
        self.app_url = app_url
        self.session = session or ConnectorSession()
        self.capability = secrets.token_urlsafe(32)
        self.stopped = threading.Event()
        self._serving = threading.Event()
        self._stop_lock = threading.Lock()
        self._workers = threading.BoundedSemaphore(16)
        super().__init__(("127.0.0.1", 0), ConnectorHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.url = self.origin + "/#local=" + self.capability

    def process_request(self, request, client_address):
        if not self._workers.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._workers.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._workers.release()

    def handle_error(self, request, client_address):
        # HTTP error handlers must not print capability headers or credentials.
        pass

    def serve_forever(self, poll_interval=.2):
        self._serving.set()
        try:
            super().serve_forever(poll_interval)
        finally:
            self._serving.clear()

    def stop(self):
        with self._stop_lock:
            if self.stopped.is_set():
                return
            self.stopped.set()
        self.session.close()
        if self._serving.is_set():
            self.shutdown()
        self.server_close()


class ConnectorHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AliancaDX-iPhone"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(5.0)

    def log_message(self, format, *args):
        pass

    def _headers(self, status, content_type="application/json; charset=utf-8", length=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' https://api.github.com/repos/perabru/AliancaDX-AcessoRemoto/releases/latest; worker-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.close_connection = True

    def _json(self, status, value):
        data = _encode(value)
        self._headers(status, length=len(data))
        self.wfile.write(data)

    def _target(self):
        parts = urlsplit(self.path)
        if parts.scheme or parts.netloc or parts.query or parts.fragment:
            raise ConnectorError("Endereço não permitido.", 400)
        if self.headers.get_all("Host", []) != [self.server.origin.removeprefix("http://")]:
            raise ConnectorError("Origem local não permitida.", 403)
        origin = self.headers.get_all("Origin", [])
        if origin and origin != [self.server.origin]:
            raise ConnectorError("Origem não permitida.", 403)
        return parts.path

    def _authenticate(self):
        values = self.headers.get_all("Authorization", [])
        expected = "Bearer " + self.server.capability
        try:
            accepted = len(values) == 1 and hmac.compare_digest(values[0], expected)
        except TypeError:
            accepted = False
        if not accepted:
            raise ConnectorError("Reabra o conector pelo aplicativo no PC.", 401)
        # Browser POSTs always carry Origin. Require it for state changes.
        if self.command == "POST" and self.headers.get_all("Origin", []) != [self.server.origin]:
            raise ConnectorError("Origem não permitida.", 403)
        self.server.session.touch()

    def _body(self):
        if self.headers.get_all("Content-Type", []) != ["application/json"]:
            raise ConnectorError("Envie um objeto JSON.", 415)
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") is not None or len(lengths) != 1:
            raise ConnectorError("Tamanho de mensagem inválido.", 400)
        try:
            length = int(lengths[0])
        except ValueError:
            raise ConnectorError("Tamanho de mensagem inválido.") from None
        if not 0 <= length <= MAX_BODY:
            raise ConnectorError("A mensagem excede o limite permitido.", 413)
        data = self.rfile.read(length)
        if len(data) != length:
            raise ConnectorError("A mensagem ficou incompleta.")
        try:
            value = json.loads(data.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (UnicodeError, ValueError, RecursionError):
            raise ConnectorError("JSON inválido.") from None
        if not isinstance(value, dict):
            raise ConnectorError("Envie um objeto JSON.")
        return value

    def _generation(self, required=False):
        values = self.headers.get_all("X-AADX-Generation", [])
        if not values and not required:
            return None
        if len(values) != 1 or not values[0].isdigit() or len(values[0]) > 16:
            raise ConnectorError("Identificação de sessão ausente ou inválida.", 409)
        return int(values[0])

    def do_GET(self):
        try:
            path = self._target()
            if path.startswith("/api/"):
                self._authenticate()
                if path == "/api/config":
                    self._json(200, {"appUrl": self.server.app_url, **self.server.session.metadata()})
                elif path == "/api/events":
                    self._stream()
                else:
                    raise ConnectorError("Recurso não encontrado.", 404)
            else:
                self._static(path)
        except ConnectorError as error:
            self._json(error.status, {"error": str(error)})
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass
        except Exception:
            self._json(500, {"error": "Não foi possível concluir a solicitação local."})

    def do_POST(self):
        try:
            path = self._target()
            self._authenticate()
            value = self._body()
            session = self.server.session
            if path == "/api/configure":
                self._json(200, session.configure(value))
            elif path == "/api/connect":
                if not set(value) <= {"name"}:
                    raise ConnectorError("Solicitação de conexão inválida.")
                self._json(202, session.connect(value.get("name", "iPhone AliançaDX")))
            elif path == "/api/command":
                self._json(202, session.command(value, self._generation(required=True)))
            elif path == "/api/pulse":
                if value:
                    raise ConnectorError("Pulso inválido.")
                self._json(200, session.pulse(self._generation()))
            elif path == "/api/disconnect":
                self._json(200, session.disconnect(self._generation()))
            elif path == "/api/shutdown":
                self._json(200, {"closed": True})
                threading.Thread(target=self.server.stop, daemon=True).start()
            else:
                raise ConnectorError("Recurso não encontrado.", 404)
        except ConnectorError as error:
            self._json(error.status, {"error": str(error)})
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass
        except Exception:
            self._json(500, {"error": "Não foi possível concluir a solicitação local."})

    def do_OPTIONS(self):
        try:
            self._target()
            raise ConnectorError("Solicitação entre origens não permitida.", 403)
        except ConnectorError as error:
            self._json(error.status, {"error": str(error)})

    def _static(self, path):
        try:
            decoded = unquote(path, errors="strict")
        except UnicodeError:
            raise ConnectorError("Recurso não encontrado.", 404) from None
        relative = PurePosixPath(decoded.lstrip("/"))
        if ("\\" in decoded or "\x00" in decoded or any(part in ("..", ".") or part.startswith(".")
                for part in relative.parts)):
            raise ConnectorError("Recurso não encontrado.", 404)
        if not relative.parts:
            relative = PurePosixPath("bridge.html")
        target = self.server.asset_root.joinpath(*relative.parts)
        if target.suffix.lower() not in STATIC_SUFFIXES:
            raise ConnectorError("Recurso não encontrado.", 404)
        current = self.server.asset_root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise ConnectorError("Recurso não encontrado.", 404)
        if not target.is_file() or not target.resolve().is_relative_to(self.server.asset_root):
            raise ConnectorError("Recurso não encontrado.", 404)
        data = target.read_bytes()
        if len(data) > 4 * 1024 * 1024:
            raise ConnectorError("Recurso excede o limite local.", 413)
        media = {".mjs": "text/javascript", ".js": "text/javascript", ".webmanifest": "application/manifest+json"}.get(
            target.suffix.lower(), mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self._headers(200, media, len(data))
        self.wfile.write(data)

    def _stream(self):
        reader = self.server.session.events.subscribe()
        self._headers(200, "application/x-ndjson; charset=utf-8")
        deadline = time.monotonic() + EVENT_STREAM_SECONDS
        while time.monotonic() < deadline and not self.server.stopped.is_set():
            data = self.server.session.events.next(reader, timeout=5)
            if data == b"":
                return
            if data is None:
                data = _encode({"type": "bridge_ping"}) + b"\n"
            self.wfile.write(data)
            self.wfile.flush()


def start_connector(bootstrap=None, asset_root=None, app_url=APP_URL, open_browser=False):
    session = ConnectorSession(bootstrap)
    try:
        server = ConnectorServer(session, asset_root, app_url)
    except Exception:
        session.close()
        raise
    thread = threading.Thread(target=server.serve_forever, daemon=True,
                              name="AliancaDX-iPhone-http")
    thread.start()
    server._serving.wait(timeout=2)
    def watch():
        while not server.stopped.wait(1):
            if session.check_lease():
                server.stop()
                return
    threading.Thread(target=watch, daemon=True, name="AliancaDX-iPhone-lease").start()
    if open_browser:
        try:
            opened = webbrowser.open(server.url, new=1)
        except Exception:
            server.stop()
            raise
        if not opened:
            server.stop()
            raise ConnectorError("Não foi possível abrir o navegador do Conector.")
    return server


def read_bootstrap(stream):
    line = stream.readline(16385)
    if len(line.encode("utf-8")) > 16384 or not line:
        raise ConnectorError("Não foi possível receber a configuração privada do Host.")
    try:
        value = json.loads(line)
    except (ValueError, RecursionError):
        raise ConnectorError("Configuração privada inválida.") from None
    Configuration.parse(value)
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description="Conectar um iPhone ao AliançaDX com aprovação no PC.")
    parser.add_argument("--bootstrap-stdin", action="store_true")
    args = parser.parse_args(argv)
    server = None
    try:
        bootstrap = read_bootstrap(sys.stdin) if args.bootstrap_stdin else None
        server = start_connector(bootstrap, open_browser=True)
        while not server.stopped.wait(.5):
            pass
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        # A Pythonw launcher needs a visible failure, without printing inputs.
        text = "Não foi possível abrir o conector do iPhone. Reinstale o aplicativo ou tente novamente."
        try:
            from tkinter import messagebox
            messagebox.showerror("AliançaDX · iPhone", text)
        except Exception:
            print(text, file=sys.stderr)
        return 1
    finally:
        if server is not None:
            server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
