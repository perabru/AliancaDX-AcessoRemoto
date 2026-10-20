"""Consent-based streaming file exchange over an already authenticated Peer."""
from __future__ import annotations

import base64
import binascii
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
import uuid

MAX_FILE = 100 * 1024 * 1024
CHUNK = 64 * 1024
TIMEOUT = 90
_TYPES = {"file_offer", "file_accept", "file_chunk", "file_ack", "file_end", "file_done", "file_cancel", "file_request", "file_request_result", "file_request_cancel"}
_RESERVED = {"CON", "PRN", "AUX", "NUL", *("COM%d" % i for i in range(1, 10)), *("LPT%d" % i for i in range(1, 10))}


def valid_name(name):
    if not isinstance(name, str) or not 1 <= len(name) <= 200:
        return False
    if name in (".", "..") or name.endswith((" ", ".")) or any(ord(c) < 32 for c in name):
        return False
    if any(c in name for c in '\\/:*?"<>|') or name.split(".")[0].upper() in _RESERVED:
        return False
    return True


def _id(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


class FileTransfers:
    def __init__(self, peer, dispatch, ask_receive, on_status, on_request_file=None, prompt_guard=None,
                 status_dispatch=None):
        self.peer, self.dispatch = peer, dispatch
        self.status_dispatch = status_dispatch if status_dispatch is not None else dispatch
        self.ask_receive, self.on_status = ask_receive, on_status
        self.on_request_file = on_request_file
        self.prompt_guard = prompt_guard or (lambda active: None)
        self.lock = threading.RLock()
        self.active = None
        self.closed = False
        self._request_pending = False
        self._request_token = None
        self._prompt_tokens = set()
        self._last_message = "Nenhuma transferência em andamento."
        threading.Thread(target=self._watchdog, daemon=True).start()

    def _status(self, message, state=None, **extra):
        state = state or self.active
        self._last_message = str(message)
        event = {"active": bool(state is not None or self._request_pending or self._request_token or self._prompt_tokens), "message": str(message), "progress": 0}
        if state:
            event.update(name=state.get("name", ""), size=state.get("size", 0),
                         transferred=state.get("offset", 0), direction=state["mode"],
                         progress=(state.get("offset", 0) / state["size"] if state.get("size") else 0))
        event.update(extra)
        self._last_status = dict(event)
        try:
            self.status_dispatch(lambda: self.on_status(event))
        except Exception:
            pass

    def _send(self, message):
        if not self.closed:
            self.peer.send(message)

    def _make_state(self, mode, identifier, **extra):
        return dict(mode=mode, id=identifier, offset=0, event=threading.Event(),
                    updated=time.monotonic(), cancelled=False, stream=None, temp=None, **extra)

    def send_file(self, path, request_id=None):
        with self.lock:
            if self.closed or self.active or self._request_pending or self._request_token or self._prompt_tokens:
                self._status("Já há uma transferência em andamento.")
                return False
            state = self._make_state("send", str(uuid.uuid4()), name=Path(path).name, request_id=request_id)
            self.active = state
        self._status("Preparando arquivo e calculando SHA-256…", state)
        threading.Thread(target=self._sender, args=(state, Path(path)), daemon=True).start()
        return True

    def receive_request(self):
        with self.lock:
            if self.closed or self.active or self._request_pending or self._request_token or self._prompt_tokens:
                self._status("Aguarde a transferência ou seleção atual.")
                return False
            self._request_pending = True
            self._request_sent = time.monotonic()
            self._request_id = str(uuid.uuid4())
        try:
            self._send({"type": "file_request", "id": self._request_id})
            self._status("O outro usuário precisa escolher e autorizar o arquivo.")
            return True
        except Exception as exc:
            self._request_pending = False
            self._status(str(exc))
            return False

    def _wait(self, state, predicate, seconds=TIMEOUT):
        deadline = time.monotonic() + seconds
        while True:
            with self.lock:
                if self.closed or state["cancelled"] or self.active is not state:
                    raise ConnectionError("Transferência cancelada.")
                if predicate():
                    return
                state["event"].clear()
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not state["event"].wait(remaining):
                raise TimeoutError("O outro computador não respondeu à transferência.")

    def _sender(self, state, path):
        try:
            if not valid_name(path.name):
                raise ValueError("Nome de arquivo incompatível com o Windows.")
            stream = path.open("rb")
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= MAX_FILE:
                stream.close()
                raise ValueError("Escolha um arquivo comum de até 100 MiB.")
            with self.lock:
                if self.active is not state or state["cancelled"]:
                    stream.close()
                    return
                state.update(stream=stream, size=info.st_size)
            digest = hashlib.sha256()
            while True:
                chunk = stream.read(CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                with self.lock:
                    if self.active is not state or state["cancelled"]:
                        return
                    state["updated"] = time.monotonic()
            stream.seek(0)
            state["sha256"] = digest.hexdigest()
            with self.lock:
                if self.active is not state or state["cancelled"] or self.closed:
                    return
                self._send({"type": "file_offer", "id": state["id"], "name": state["name"],
                            "size": state["size"], "sha256": state["sha256"]})
                state["offer_sent"] = True
            self._status("Aguardando aprovação e destino no outro computador…", state)
            self._wait(state, lambda: state.get("accepted", False))
            while state["offset"] < state["size"]:
                amount = min(CHUNK, state["size"] - state["offset"])
                chunk = stream.read(amount)
                if len(chunk) != amount:
                    raise ValueError("O arquivo mudou durante o envio.")
                expected = state["offset"] + amount
                with self.lock:
                    if self.active is not state or state["cancelled"] or self.closed:
                        return
                    state["expected_ack"] = expected
                    self._send({"type": "file_chunk", "id": state["id"], "offset": state["offset"],
                                "data": base64.b64encode(chunk).decode("ascii")})
                self._wait(state, lambda: state.get("ack", -1) == expected, 30)
                state["offset"] = expected
                state["updated"] = time.monotonic()
                self._status("Enviando arquivo…", state)
            if stream.read(1):
                raise ValueError("O arquivo mudou durante o envio.")
            with self.lock:
                if self.active is not state or state["cancelled"] or self.closed:
                    return
                self._send({"type": "file_end", "id": state["id"]})
            self._wait(state, lambda: state.get("done", False), 30)
            self._finish_state(state, "Arquivo enviado e conferido no destino.", success=True)
        except Exception as exc:
            self._abort(state, str(exc), notify=True)

    def handle(self, message):
        if not isinstance(message, dict) or message.get("type") not in _TYPES:
            return False
        if self.closed:
            return True
        kind = message["type"]
        if kind == "file_request":
            self._remote_request(message)
            return True
        if kind == "file_request_result":
            with self.lock:
                if message.get("id") == getattr(self, "_request_id", None) and message.get("accepted") is False:
                    self._request_pending = False
                    self._status("O outro usuário recusou o pedido ou não selecionou um arquivo.")
            return True
        if kind == "file_request_cancel":
            with self.lock:
                if message.get("id") == self._request_token:
                    self._request_token = None
                state = self.active
                if state and state["mode"] == "send" and state.get("request_id") == message.get("id"):
                    self._abort(state, "Pedido de arquivo cancelado pelo outro usuário.", notify=True)
            return True
        identifier = message.get("id")
        if not _id(identifier):
            return True
        if kind == "file_offer":
            self._offer(message)
            return True
        with self.lock:
            state = self.active
            if state is None or state["id"] != identifier:
                return True
            state["updated"] = time.monotonic()
            try:
                if kind == "file_cancel":
                    self._abort(state, "Transferência recusada ou cancelada pelo outro usuário.")
                elif kind == "file_accept" and state["mode"] == "send":
                    state["accepted"] = True
                    state["event"].set()
                elif kind == "file_ack" and state["mode"] == "send":
                    offset = message.get("offset")
                    if type(offset) is not int or offset != state.get("expected_ack"):
                        raise ValueError("Confirmação de bloco inválida.")
                    state["ack"] = offset
                    state["event"].set()
                elif kind == "file_done" and state["mode"] == "send":
                    if state["offset"] != state["size"]:
                        raise ValueError("Confirmação prematura.")
                    state["done"] = True
                    state["event"].set()
                elif kind == "file_chunk" and state["mode"] == "receive":
                    self._chunk(state, message)
                elif kind == "file_end" and state["mode"] == "receive":
                    self._received(state)
                else:
                    raise ValueError("Etapa de transferência inválida.")
            except Exception as exc:
                self._abort(state, str(exc), notify=True)
        return True

    def _offer(self, message):
        identifier, name, size, digest = (message.get(k) for k in ("id", "name", "size", "sha256"))
        if not valid_name(name) or type(size) is not int or not 0 <= size <= MAX_FILE or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            self._send({"type": "file_cancel", "id": identifier})
            self._status("Oferta de arquivo inválida ou maior que 100 MiB.")
            return
        with self.lock:
            self._request_pending = False
            if self.active or self._request_token or self._prompt_tokens:
                self._send({"type": "file_cancel", "id": identifier})
                return
            state = self._make_state("receive", identifier, name=name, size=size, sha256=digest)
            self.active = state
        self._status("Escolha se deseja receber este arquivo.", state)
        self._prompt(state["id"], lambda callback: self.ask_receive(dict(message), callback),
                     lambda path: self._receive_path(state, path), lambda: self.active is state)

    def _prompt(self, token, ask, result, still_valid=lambda: True):
        with self.lock:
            self._prompt_tokens.add(token)
            self.prompt_guard(True)
        def complete(path):
            with self.lock:
                if token not in self._prompt_tokens:
                    return
                self._prompt_tokens.remove(token)
                self.prompt_guard(False)
                if self.closed:
                    return
            result(path)
            with self.lock:
                active = bool(self.active or self._request_pending or self._request_token or self._prompt_tokens)
                event = dict(getattr(self, "_last_status", {"message": self._last_message, "progress": 0}))
                if event.get("active") != active:
                    event["active"] = active
                    self._last_status = dict(event)
                    self.status_dispatch(lambda: self.on_status(event))
        def show():
            with self.lock:
                if token not in self._prompt_tokens:
                    return
                skipped = self.closed or not still_valid()
            if skipped:
                complete(None)
                return
            try:
                ask(complete)
            except Exception:
                complete(None)
        try:
            scheduled = self.dispatch(show)
        except Exception:
            scheduled = False
        if scheduled is False:
            # A closing UI may reject new work. Balance its local pause guard
            # and refuse the operation instead of leaving a phantom dialog.
            complete(None)

    def _receive_path(self, state, path):
        with self.lock:
            if self.active is not state or state["cancelled"]:
                return
            if not path:
                self._abort(state, "Recebimento recusado.", notify=True)
                return
            try:
                destination = Path(path).absolute()
                if not destination.parent.is_dir() or not valid_name(destination.name):
                    raise ValueError("Escolha um destino válido.")
                fd, temporary = tempfile.mkstemp(prefix=".minianydesk-", suffix=".part", dir=destination.parent)
                state.update(stream=os.fdopen(fd, "wb"), temp=Path(temporary), destination=destination,
                             digest=hashlib.sha256(), ready=True, updated=time.monotonic())
                self._send({"type": "file_accept", "id": state["id"]})
                self._status("Recebendo arquivo…", state)
            except Exception as exc:
                self._abort(state, str(exc), notify=True)

    def _chunk(self, state, message):
        offset, encoded = message.get("offset"), message.get("data")
        if not state.get("ready") or type(offset) is not int or offset != state["offset"] or not isinstance(encoded, str) or len(encoded) > ((CHUNK + 2) // 3) * 4:
            raise ValueError("Bloco fora de ordem ou inválido.")
        try:
            chunk = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Bloco corrompido.") from None
        if not 0 < len(chunk) <= CHUNK or state["offset"] + len(chunk) > state["size"]:
            raise ValueError("Tamanho de bloco inválido.")
        state["stream"].write(chunk)
        state["digest"].update(chunk)
        state["offset"] += len(chunk)
        self._send({"type": "file_ack", "id": state["id"], "offset": state["offset"]})
        self._status("Recebendo arquivo…", state)

    def _received(self, state):
        if not state.get("ready") or state["offset"] != state["size"] or state["digest"].hexdigest() != state["sha256"]:
            raise ValueError("Arquivo incompleto ou SHA-256 diferente. O recebimento foi descartado.")
        state["stream"].flush()
        os.fsync(state["stream"].fileno())
        state["stream"].close()
        state["stream"] = None
        destination, temporary = state["destination"], state["temp"]
        final_path = None
        for counter in range(1000):
            candidate = destination if counter == 0 else destination.with_name(f"{destination.stem} ({counter}){destination.suffix}")
            try:
                if os.name == "nt":
                    os.rename(temporary, candidate)  # Windows rename never overwrites an existing target.
                else:
                    os.link(temporary, candidate)
                    temporary.unlink()
                final_path = candidate
                break
            except FileExistsError:
                continue
        if final_path is None:
            raise ValueError("Não foi possível criar um nome livre no destino.")
        state["temp"] = None
        self._send({"type": "file_done", "id": state["id"]})
        self._finish_state(state, f"Arquivo recebido: {final_path}", success=True, path=str(final_path))

    def _remote_request(self, message):
        token = message.get("id")
        if not _id(token):
            return
        with self.lock:
            if self.active or self._request_token or self._request_pending or self._prompt_tokens or not self.on_request_file:
                self._send({"type": "file_request_result", "id": token, "accepted": False})
                self._status("Pedido de arquivo não disponível durante outra operação.")
                return
            self._request_token = token
            self._request_time = time.monotonic()
        def selected(path):
            with self.lock:
                if self._request_token != token:
                    return
                self._request_token = None
            if path:
                if not self.send_file(path, request_id=token):
                    self._send({"type": "file_request_result", "id": token, "accepted": False})
            else:
                self._send({"type": "file_request_result", "id": token, "accepted": False})
                self._status("O usuário não selecionou um arquivo para envio.")
        self._prompt(token, self.on_request_file, selected, lambda: self._request_token == token)

    def _finish_state(self, state, message, **extra):
        with self.lock:
            if self.active is not state:
                return
            self.active = None
            state["cancelled"] = True
            state["event"].set()
            if state.get("stream"):
                try:
                    state["stream"].close()
                except OSError:
                    pass
                state["stream"] = None
            if state.get("temp"):
                try:
                    state["temp"].unlink(missing_ok=True)
                except OSError:
                    pass
            # Keep the guard while an actual modal dialog is still open; its callback releases it.
        self._status(message, active=False, progress=1 if extra.get("success") else 0, **extra)

    def _abort(self, state, message, notify=False):
        if notify and not self.closed:
            try:
                self._send({"type": "file_cancel", "id": state["id"]})
                if state.get("request_id") and not state.get("offer_sent"):
                    self._send({"type": "file_request_result", "id": state["request_id"], "accepted": False})
            except Exception:
                pass
        self._finish_state(state, message, success=False)

    def cancel(self):
        with self.lock:
            if self._request_pending:
                try:
                    self._send({"type": "file_request_cancel", "id": self._request_id})
                except Exception:
                    pass
            if self._request_token:
                try:
                    self._send({"type": "file_request_result", "id": self._request_token, "accepted": False})
                except Exception:
                    pass
            self._request_pending = False
            self._request_token = None
            state = self.active
        if state:
            self._abort(state, "Transferência cancelada.", notify=True)
        else:
            self._status("Pedido de arquivo cancelado.")

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self._request_pending = False
            self._request_token = None
            state = self.active
        if state:
            self._abort(state, "Conexão encerrada; transferência descartada.")

    def _watchdog(self):
        while not self.closed:
            time.sleep(1)
            with self.lock:
                state = self.active
                if state and time.monotonic() - state["updated"] > TIMEOUT:
                    self._abort(state, "Tempo de transferência esgotado.", notify=True)
                if self._request_pending and time.monotonic() - self._request_sent > TIMEOUT:
                    self._request_pending = False
                    self._status("O outro usuário não escolheu um arquivo. Você pode tentar novamente.")
