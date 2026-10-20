"""Read-only TLS availability checks, separate from authentication and control."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import queue
import socket
import ssl
import struct
import threading
import time

from net import STATUS_ALPN


# The OS resolver does not honor socket timeouts. Four daemon resolver workers
# bound blocked lookups and never delay application exit; expired queued tasks
# are discarded rather than spawning more threads on every refresh.
_resolver_queue = queue.Queue(maxsize=4)
_resolver_lock = threading.Lock()
_resolver_started = False


def _resolver_worker():
    while True:
        address, port, deadline, finished, answer = _resolver_queue.get()
        try:
            if time.monotonic() >= deadline:
                continue
            try:
                answer["addresses"] = socket.getaddrinfo(address, port, 0, socket.SOCK_STREAM)
            except (OSError, UnicodeError) as error:
                answer["error"] = error
        finally:
            finished.set()
            _resolver_queue.task_done()


def _resolve(address, port, deadline):
    global _resolver_started
    try:
        ipaddress.ip_address(address)
    except ValueError:
        pass
    else:
        return socket.getaddrinfo(address, port, 0, socket.SOCK_STREAM, 0, socket.AI_NUMERICHOST)
    with _resolver_lock:
        if not _resolver_started:
            for index in range(4):
                threading.Thread(target=_resolver_worker, daemon=True, name=f"MiniAnyDesk-status-dns-{index}").start()
            _resolver_started = True
    finished, answer = threading.Event(), {}
    try:
        _resolver_queue.put_nowait((address, port, deadline, finished, answer))
    except queue.Full:
        raise TimeoutError("Há consultas DNS pendentes.") from None
    if not finished.wait(_remaining(deadline)):
        raise TimeoutError("Tempo de resolução DNS esgotado.")
    _remaining(deadline)
    if "error" in answer:
        raise answer["error"]
    return answer.get("addresses", [])


def _connect(address, port, deadline):
    last_error = None
    for family, kind, protocol, _, endpoint in _resolve(address, port, deadline):
        connection = socket.socket(family, kind, protocol)
        try:
            connection.settimeout(_remaining(deadline))
            connection.connect(endpoint)
            return connection
        except OSError as error:
            last_error = error
            connection.close()
    if last_error is not None:
        raise last_error
    raise OSError("Não foi possível resolver o endereço.")


def _fingerprint(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Impressão de certificado inválida.")
    normalized = "".join(value.split()).replace(":", "").upper()
    if len(normalized) != 64 or any(character not in "0123456789ABCDEF" for character in normalized):
        raise ValueError("Impressão de certificado inválida.")
    return normalized


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Tempo de consulta esgotado.")
    return remaining


def _exact(sock, size, deadline):
    data = bytearray()
    while len(data) < size:
        sock.settimeout(_remaining(deadline))
        part = sock.recv(size - len(data))
        if not part:
            raise ConnectionError("O hospedeiro não respondeu à consulta.")
        data.extend(part)
    return bytes(data)


def _status(sock, deadline):
    length = struct.unpack("!I", _exact(sock, 4, deadline))[0]
    if not 0 < length <= 2048:
        raise ValueError("Resposta de disponibilidade inválida.")
    result = json.loads(_exact(sock, length, deadline).decode("utf-8"))
    if (not isinstance(result, dict) or result.get("type") != "status" or
            result.get("agent") != "MiniAnyDesk" or result.get("online") is not True or
            not isinstance(result.get("busy"), bool) or
            not isinstance(result.get("name"), str) or len(result["name"]) > 80 or
            any(ord(character) < 32 or ord(character) == 127 for character in result["name"]) or
            not isinstance(result.get("version"), str) or not 1 <= len(result["version"]) <= 32):
        raise ValueError("Resposta de disponibilidade inválida.")
    result["name"].encode("utf-8")
    return result


def probe(address, port, expected_fingerprint=None, timeout=3):
    """Return availability only; never send credentials or application packets.

    A valid status reply identifies a 3.1 host's claimed availability, not a
    trusted login. A supplied pin must match; changed pins are never replaced.
    Legacy TLS endpoints are online only when their known certificate matches.
    """
    expected = _fingerprint(expected_fingerprint)
    if not isinstance(address, str) or not address.strip() or len(address) > 253 or "\x00" in address:
        raise ValueError("Endereço inválido.")
    address = address.strip()
    if isinstance(port, bool) or not 1 <= int(port) <= 65535:
        raise ValueError("Porta inválida.")
    port = int(port)
    timeout = float(timeout)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Tempo de consulta inválido.")
    result = {"state": "offline", "detail": "Hospedeiro indisponível.", "fingerprint": "",
              "computer_name": "", "busy": None, "version": ""}
    sock = None
    deadline = time.monotonic() + timeout
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # Explicit SHA-256 pinning below.
        if ssl.HAS_ALPN:
            context.set_alpn_protocols([STATUS_ALPN])
        sock = _connect(address, port, deadline)
        sock.settimeout(_remaining(deadline))
        sock = context.wrap_socket(sock, server_hostname=address)
        actual = hashlib.sha256(sock.getpeercert(binary_form=True)).hexdigest().upper()
        result["fingerprint"] = actual
        if expected is not None and not hmac.compare_digest(expected, actual):
            result.update(state="unverified", detail="O certificado mudou; confira a identidade antes de conectar.")
            return result
        if not ssl.HAS_ALPN or sock.selected_alpn_protocol() != STATUS_ALPN:
            # Send nothing to old Desktop/Android versions. EOF is not a login.
            if expected is not None:
                result.update(state="online", detail="Hospedeiro acessível com certificado conhecido; versão sem consulta de nome.")
            else:
                result.update(state="unverified", detail="Porta TLS acessível; a versão não confirma disponibilidade do MiniAnyDesk.")
            return result
        answer = _status(sock, deadline)
        result.update(state="online", computer_name=answer["name"], busy=answer["busy"], version=answer["version"],
                      detail="Hospedeiro acessível; identidade será conferida ao conectar" if expected is None else
                      ("Hospedeiro online, com uma sessão ou aprovação em andamento." if answer["busy"] else "Hospedeiro online e disponível."))
        return result
    except (socket.timeout, TimeoutError):
        result.update(detail="Tempo de consulta esgotado; tente atualizar novamente.")
        return result
    except (OSError, ssl.SSLError, ConnectionError, ValueError, UnicodeError):
        # TLS/status errors describe availability, never affect saved identity.
        result.update(detail="O hospedeiro não respondeu a uma consulta TLS válida.")
        return result
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
