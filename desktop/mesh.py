"""Account-free, app-scoped EasyTier transport for MiniAnyDesk.

The pairing code is a bearer secret. It joins one Host's encrypted overlay and
binds its TLS certificate; it never replaces the application's password or local
consent. No TUN device, system route, shared service or administrator is needed.
Callbacks run on a worker thread and must be nonblocking. Never log pairing codes.
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid

VERSION = "7.6.1"
EASYTIER_VERSION = "2.6.4"
HOST_IP = "10.126.126.1"
CONTROLLER_IP = "10.126.126.2"
DEFAULT_PORT = 64321
DEFAULT_PEERS = (
    "tcp://public.easytier.top:11010",
    "tcp://38.76.179.190:11010",
    "wss://relay.38-76-179-190.sslip.io/",
)
MAX_CODE_LENGTH = 160
MAX_STATUS_BYTES = 1024 * 1024
RPC_TIMEOUT = 3.0
POLL_INTERVAL = 2.0


class MeshError(RuntimeError):
    """Application-owned messages only; never include native output/secrets."""


class MeshTimeoutError(MeshError, TimeoutError):
    """Discovery timed out; an already authenticated recovery may retry."""


def _port(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise MeshError("Porta de acesso inválida.")
    return value


def _fingerprint(value):
    if value == "":
        return ""
    if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", value):
        raise MeshError("Identidade TLS inválida.")
    return value.upper()


def _derive(token, label):
    return hmac.new(token, b"MiniAnyDesk6/" + label, hashlib.sha256).digest()


@dataclass(frozen=True, repr=False)
class HostPairing:
    token: bytes = field(repr=False)
    port: int = DEFAULT_PORT
    fingerprint: str = ""

    def __post_init__(self):
        if not isinstance(self.token, bytes) or len(self.token) != 32:
            raise MeshError("Código de acesso inválido.")
        _port(self.port)
        object.__setattr__(self, "fingerprint", _fingerprint(self.fingerprint))

    def __repr__(self):
        return f"HostPairing(public_id={self.public_id!r}, port={self.port})"

    @property
    def code(self):
        payload = b"\x01" + self.token + struct.pack("!H", self.port)
        payload += bytes.fromhex(self.fingerprint) if self.fingerprint else b"\x00" * 32
        payload += hashlib.sha256(b"MDA6-check/" + payload).digest()[:4]
        return "MDA6-" + base64.b32encode(payload).decode("ascii").rstrip("=")

    @property
    def public_id(self):
        return _derive(self.token, b"public-id").hex()[:24]

    @property
    def network_name(self):
        return "mda6-" + _derive(self.token, b"network-name").hex()[:32]

    @property
    def network_secret(self):
        return _derive(self.token, b"network-secret").hex()


def generate_pairing(port=DEFAULT_PORT, fingerprint=""):
    return HostPairing(secrets.token_bytes(32), port, fingerprint)


def parse_pairing(code):
    if not isinstance(code, str) or len(code) > MAX_CODE_LENGTH:
        raise MeshError("Código de acesso inválido. Copie o código completo do Host.")
    # Strip only surrounding whitespace; never silently normalize internal text.
    value = code.strip().upper()
    if not re.fullmatch(r"MDA6-[A-Z2-7]{114}", value):
        raise MeshError("Código de acesso inválido. Copie o código completo do Host.")
    try:
        payload = base64.b32decode(value[5:] + "=" * 6)
    except (ValueError, base64.binascii.Error):
        raise MeshError("Código de acesso inválido.") from None
    if len(payload) != 71 or payload[0] != 1:
        raise MeshError("Código de acesso incompatível.")
    if not hmac.compare_digest(payload[-4:], hashlib.sha256(b"MDA6-check/" + payload[:-4]).digest()[:4]):
        raise MeshError("Código de acesso incompleto ou alterado.")
    cert = payload[35:67]
    result = HostPairing(payload[1:33], struct.unpack("!H", payload[33:35])[0],
                         cert.hex() if any(cert) else "")
    # Reject noncanonical padding bits, not merely a valid decoded payload.
    if result.code != value:
        raise MeshError("Código de acesso inválido.")
    return result


def pairing_public_id(code):
    return parse_pairing(code).public_id


def _current_user_sid():
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                         wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise OSError("user token unavailable")
    try:
        needed = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed)):
            raise OSError("user SID unavailable")
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        value = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(value)):
            raise OSError("user SID unavailable")
        try:
            return value.value
        finally:
            kernel.LocalFree(ctypes.cast(value, ctypes.c_void_p))
    finally:
        kernel.CloseHandle(token)


def _protect_path(path, directory=False):
    """Current-user-only ACL on Windows; 0700/0600 on Unix."""
    if os.name != "nt":
        os.chmod(path, 0o700 if directory else 0o600)
        return
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    advapi.SetFileSecurityW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    descriptor = ctypes.c_void_p()
    inherit = "OICI" if directory else ""
    sddl = f"D:P(A;{inherit};FA;;;SY)(A;{inherit};FA;;;{_current_user_sid()})"
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise OSError("private ACL unavailable")
    try:
        if not advapi.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise OSError("private ACL unavailable")
    finally:
        kernel.LocalFree(descriptor)


def _private_dir(path):
    path = Path(path)
    if path.is_symlink():
        raise MeshError("A pasta de acesso privado não é válida.")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _protect_path(path, directory=True)
    return path


def _atomic_private_write(path, data):
    path = Path(path)
    _private_dir(path.parent)
    fd, temp_name = tempfile.mkstemp(prefix=".mda-", suffix=".tmp", dir=path.parent)
    try:
        _protect_path(temp_name)
        with os.fdopen(fd, "wb") as stream:
            fd = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        _protect_path(path)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _dpapi(data, decrypt=False):
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    raw = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(raw, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    if decrypt:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result))
    else:
        fn = crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), "MiniAnyDesk pairing", None, None, None, 1, ctypes.byref(result))
    if not ok:
        raise MeshError("Não foi possível proteger o código de acesso deste usuário.")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


def save_pairing_code(path, code):
    canonical = parse_pairing(code).code.encode("ascii")
    protection = "dpapi-current-user" if os.name == "nt" else "user-file"
    payload = _dpapi(canonical) if os.name == "nt" else canonical
    envelope = {"schema": 1, "protection": protection, "value": base64.b64encode(payload).decode("ascii")}
    try:
        _atomic_private_write(path, json.dumps(envelope).encode("ascii"))
    except OSError:
        raise MeshError("Não foi possível salvar o código de acesso privado.") from None


def load_pairing_code(path):
    path = Path(path)
    try:
        if path.is_symlink() or path.stat().st_size > 16384:
            raise ValueError
        envelope = json.loads(path.read_bytes())
        expected = "dpapi-current-user" if os.name == "nt" else "user-file"
        if envelope.get("schema") != 1 or envelope.get("protection") != expected:
            raise ValueError
        payload = base64.b64decode(envelope["value"], validate=True)
        if os.name == "nt":
            payload = _dpapi(payload, decrypt=True)
        _protect_path(path)
        return parse_pairing(payload.decode("ascii")).code
    except FileNotFoundError:
        raise
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, AttributeError):
        raise MeshError("Código salvo inválido. Não é seguro substituí-lo automaticamente.") from None


def load_or_create_pairing(config_dir, port=DEFAULT_PORT, fingerprint=""):
    _port(port)
    fingerprint = _fingerprint(fingerprint)
    path = Path(config_dir) / "host-pairing.json"
    try:
        current = parse_pairing(load_pairing_code(path))
        updated = HostPairing(current.token, port, fingerprint)
    except FileNotFoundError:
        updated = generate_pairing(port, fingerprint)
        current = None
    if updated != current:
        save_pairing_code(path, updated.code)
    return updated


def rotate_pairing(config_dir, port=DEFAULT_PORT, fingerprint=""):
    """Revoke the former overlay identity after caller stops the active session."""
    pairing = generate_pairing(port, fingerprint)
    save_pairing_code(Path(config_dir) / "host-pairing.json", pairing.code)
    return pairing


@dataclass(frozen=True)
class MeshStatus:
    state: str = "stopped"
    message: str = "Conexão privada parada."
    endpoint: tuple[str, int] | None = None
    peer_online: bool = False
    public_connected: bool = False


def verify_runtime(runtime_dir=None):
    directory = Path(runtime_dir) if runtime_dir is not None else Path(__file__).resolve().parent / "easytier_bundle"
    directory = directory.resolve()
    core_name = "easytier-core.exe" if os.name == "nt" else "easytier-core"
    try:
        manifest_path = directory / "manifest.json"
        if manifest_path.is_symlink() or manifest_path.stat().st_size > 65536:
            raise ValueError
        manifest = json.loads(manifest_path.read_bytes())
        if manifest.get("schema") != 1 or manifest.get("package", {}).get("version") != EASYTIER_VERSION:
            raise ValueError
        entries = manifest["files"]
        if not isinstance(entries, list) or not 1 <= len(entries) <= 40:
            raise ValueError
        names = set()
        for item in entries:
            name = item["filename"]
            if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", name) or name in names:
                raise ValueError
            names.add(name)
            size, digest = item["size"], item["sha256"]
            if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= 300 * 1024 * 1024:
                raise ValueError
            if not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest):
                raise ValueError
            path = directory / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
                raise ValueError
            hasher = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(chunk)
            if not hmac.compare_digest(hasher.hexdigest(), digest):
                raise ValueError
        required = {core_name}
        if os.name == "nt":
            required.add("Packet.dll")
        if not required.issubset(names):
            raise ValueError
        return directory / core_name
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise MeshError("Bibliotecas da conexão privada ausentes ou alteradas. Reinstale o AliançaDX Acesso Remoto.") from None


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _safe_peers(peers):
    values = tuple(peers)
    if not values or len(values) > 8:
        raise MeshError("Lista de retransmissores inválida.")
    for value in values:
        if not isinstance(value, str) or len(value) > 512 or any(ord(c) < 33 or c in '"\\' for c in value):
            raise MeshError("Retransmissor inválido.")
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError:
            raise MeshError("Retransmissor inválido.") from None
        if (parsed.scheme not in {"tcp", "udp", "ws", "wss"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise MeshError("Retransmissor inválido.")
        if parsed.scheme in {"tcp", "udp"} and (not port or parsed.path not in {"", "/"}):
            raise MeshError("Retransmissor inválido.")
    return values


def build_config(role, pairing, rpc_port, forward_port=None, peers=DEFAULT_PEERS, force_relay=False, controller_ip=None):
    """Pure, inspectable configuration; no OS routes or broad local proxies."""
    if role not in {"host", "controller"} or not isinstance(pairing, HostPairing):
        raise MeshError("Modo de conexão inválido.")
    _port(rpc_port)
    if role == "controller":
        _port(forward_port)
    peers = _safe_peers(peers)
    if controller_ip is None:
        controller_ip = f"10.126.{secrets.randbelow(254) + 1}.{secrets.randbelow(254) + 1}"
        if controller_ip == HOST_IP:
            controller_ip = "10.126.1.2"
    import ipaddress
    try:
        address = ipaddress.IPv4Address(controller_ip)
        if address not in ipaddress.IPv4Network("10.126.0.0/16") or str(address) == HOST_IP:
            raise ValueError
    except (ValueError, TypeError):
        raise MeshError("Endereço privado do Controlador inválido.") from None
    local_ip = HOST_IP if role == "host" else controller_ip
    text = [f'instance_name = "MiniAnyDesk-{role}"', f'instance_id = "{uuid.uuid4()}"',
            f'hostname = "MiniAnyDesk-{role}"', f'ipv4 = "{local_ip}/16"',
            'ipv6_public_addr_provider = false', 'ipv6_public_addr_auto = false',
            'dhcp = false', 'listeners = []', 'exit_nodes = []', 'routes = []',
            'tcp_whitelist = []', 'udp_whitelist = []',
            '[network_identity]', f'network_name = "{pairing.network_name}"',
            f'network_secret = "{pairing.network_secret}"']
    for peer in peers:
        text += ['[[peer]]', f'uri = "{peer}"']
    if role == "controller":
        text += ['[[port_forward]]', 'proto = "tcp"', f'bind_addr = "127.0.0.1:{forward_port}"',
                 f'dst_addr = "{HOST_IP}:{pairing.port}"']
    text += ['[flags]', 'no_tun = true', 'use_smoltcp = true', 'enable_encryption = true',
             'encryption_algorithm = "aes-gcm"', 'enable_ipv6 = false', 'enable_exit_node = false',
             'proxy_forward_by_system = false', 'bind_device = false', 'relay_network_whitelist = ""',
             'relay_all_peer_rpc = false', 'accept_dns = false', 'disable_upnp = true',
             'enable_udp_broadcast_relay = false', 'enable_kcp_proxy = false',
             'disable_kcp_input = true', 'enable_quic_proxy = false', 'disable_quic_input = true',
             'enable_relay_foreign_network_kcp = false', 'enable_relay_foreign_network_quic = false',
             'disable_relay_kcp = true', 'disable_relay_quic = true',
             f'disable_p2p = {str(bool(force_relay)).lower()}']
    if force_relay:
        text += ['disable_tcp_hole_punching = true', 'disable_udp_hole_punching = true']
    # Inbound deny-default is crucial: no-TUN maps virtual-IP ports to localhost.
    for chain_type, name in ((1, "inbound"), (3, "forward")):
        text += ['[[acl.acl_v1.chains]]', f'name = "mda-{name}"', f'chain_type = {chain_type}',
                 'enabled = true', 'default_action = 2']
        if role == "host" and chain_type == 1:
            text += ['[[acl.acl_v1.chains.rules]]', 'name = "mda-tls-only"', 'priority = 1000',
                     'enabled = true', 'protocol = 1', f'ports = ["{pairing.port}"]',
                     f'destination_ips = ["{HOST_IP}/32"]', 'action = 1', 'stateful = true']
    # Outbound reply tracking permits responses with Controller inbound denied.
    text += ['[[acl.acl_v1.chains]]', 'name = "mda-outbound"', 'chain_type = 2',
             'enabled = true', 'default_action = 1', '[console_logger]', 'level = "off"',
             '[file_logger]', 'level = "off"']
    return "\n".join(text) + "\n"


def _clean_env():
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("ET_", "RUST_LOG", "RUST_BACKTRACE"))}
    env["RUST_LOG"] = "off"
    env["RUST_BACKTRACE"] = "0"
    return env


def _child_options():
    options = dict(stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False,
                   env=_clean_env(), close_fds=True)
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    return options


def _job_for_process(process):
    if os.name != "nt":
        return None
    class BasicLimits(ctypes.Structure):
        _fields_ = [("ProcessTime", ctypes.c_longlong), ("JobTime", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinWorking", ctypes.c_size_t),
                    ("MaxWorking", ctypes.c_size_t), ("ActiveProcesses", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("Priority", wintypes.DWORD), ("Scheduling", wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]
    class Limits(ctypes.Structure):
        _fields_ = [("Basic", BasicLimits), ("IO", IO), ("ProcessMemory", ctypes.c_size_t),
                    ("JobMemory", ctypes.c_size_t), ("PeakProcess", ctypes.c_size_t), ("PeakJob", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise MeshError("Não foi possível proteger o processo de conexão.")
    limits = Limits()
    limits.Basic.LimitFlags = 0x2000
    if (not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            or not kernel.AssignProcessToJobObject(job, int(process._handle))):
        kernel.CloseHandle(job)
        raise MeshError("Não foi possível proteger o processo de conexão.")
    return lambda: kernel.CloseHandle(job)


def _terminate(process, job=None):
    if job is not None:
        job()
    try:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=1.5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _json_rows(data):
    # v2.6.4 CLI outputs one array for our single process/instance.
    if not isinstance(data, list) or len(data) > 2000 or any(not isinstance(row, dict) for row in data):
        raise ValueError("invalid status")
    return data


class MeshManager:
    def __init__(self, role, config_dir, runtime_dir=None, on_status=None, *, peers=None, force_relay=False):
        if role not in {"host", "controller"}:
            raise MeshError("Modo de conexão inválido.")
        self.role, self.config_dir, self.runtime_dir = role, Path(config_dir), runtime_dir
        self.on_status = on_status
        self.peers = _safe_peers(DEFAULT_PEERS if peers is None else peers)
        self.force_relay = bool(force_relay)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._changed = threading.Condition(self._lock)
        self._status = MeshStatus()
        self._thread = None
        self._process = None
        self._job = None
        self._rpc_port = None
        self._forward_port = None
        self._generation = 0
        self._pairing = None

    def snapshot(self):
        with self._lock:
            return self._status

    def _publish(self, status, generation=None):
        with self._changed:
            if generation is not None and generation != self._generation:
                return
            if status == self._status:
                return
            self._status = status
            self._changed.notify_all()
            # Queue-only callbacks run under the state lock so a stale worker
            # notification cannot be delivered after a newer Stop notification.
            if self.on_status:
                try:
                    self.on_status(status)
                except Exception:
                    pass

    def start(self, pairing, port=None):
        if isinstance(pairing, str):
            pairing = parse_pairing(pairing)
        if not isinstance(pairing, HostPairing):
            raise MeshError("Código de acesso inválido.")
        if self.role == "controller" and not pairing.fingerprint:
            raise MeshError("Código sem identidade TLS. Copie um código atual do Host.")
        if port is not None and _port(port) != pairing.port:
            raise MeshError("A porta mudou. Copie novamente o código do Host.")
        with self._lock:
            previous = self._thread
            if previous is not None and previous.is_alive():
                if self._pairing == pairing and not self._stop.is_set():
                    return
                self._stop.set()
                if self._process is not None:
                    try:
                        self._process.terminate()
                    except OSError:
                        pass
            self._generation += 1
            generation = self._generation
            self._pairing = pairing
            stop_event = self._stop = threading.Event()
            self._publish(MeshStatus("starting", "Preparando conexão privada sem login…"))
            self._thread = threading.Thread(target=self._run_after,
                args=(previous, pairing, stop_event, generation), daemon=True, name="MiniAnyDesk-mesh")
            self._thread.start()

    def _run_after(self, previous, pairing, stop_event, generation):
        if previous is not None:
            previous.join(timeout=6.0)
            if previous.is_alive():
                self._publish(MeshStatus("error", "A conexão anterior está encerrando. Aguarde e tente novamente."), generation)
                return
        if not stop_event.is_set():
            self._run(pairing, stop_event, generation)

    def _observe(self, pairing, stop_event):
        if self.role == "host":
            # The management API is intentionally inaccessible, so do not infer
            # internet reachability from a running process or localhost socket.
            return MeshStatus("connecting", "Rede privada iniciada. Aguardando o Controlador…")
        endpoint = ("127.0.0.1", self._forward_port)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        if ssl.HAS_ALPN:
            context.set_alpn_protocols(["minianydesk-status/1"])
        try:
            with socket.create_connection(endpoint, timeout=1.2) as raw:
                raw.settimeout(1.8)
                with context.wrap_socket(raw, server_hostname=None) as secure:
                    cert = secure.getpeercert(binary_form=True)
                    actual = hashlib.sha256(cert).hexdigest().upper()
                    if not hmac.compare_digest(actual, pairing.fingerprint):
                        raise MeshError("A identidade do Host não corresponde ao código. A conexão foi interrompida.")
            if stop_event.is_set():
                return MeshStatus()
            return MeshStatus("ready", "Host encontrado. Conexão privada e identidade TLS verificadas.", endpoint, True, False)
        except (OSError, ssl.SSLError):
            return MeshStatus("connecting", "Procurando o Host pela internet…")

    def _run(self, pairing, stop_event, generation):
        config_path = None
        process, job = None, None
        try:
            core = verify_runtime(self.runtime_dir)
            directory = _private_dir(self.config_dir / "sessions")
            self._rpc_port = _free_port()
            self._forward_port = _free_port() if self.role == "controller" else None
            config_path = directory / f"mesh-{os.getpid()}-{uuid.uuid4().hex}.toml"
            config = build_config(self.role, pairing, self._rpc_port, self._forward_port,
                                  self.peers, self.force_relay)
            _atomic_private_write(config_path, config.encode("utf-8"))
            failures = 0
            while not stop_event.is_set():
                process = subprocess.Popen([str(core), "--config-file", str(config_path),
                                            "--disable-env-parsing", "--rpc-portal", f"127.0.0.1:{self._rpc_port}",
                                            "--rpc-portal-whitelist", "192.0.2.1/32"],
                                           stdout=subprocess.DEVNULL, cwd=str(core.parent), **_child_options())
                try:
                    job = _job_for_process(process)
                    with self._lock:
                        self._process, self._job = process, job
                    while not stop_event.is_set() and process.poll() is None:
                        try:
                            self._publish(self._observe(pairing, stop_event), generation)
                            failures = 0
                        except (OSError, ValueError, json.JSONDecodeError):
                            if not stop_event.is_set():
                                self._publish(MeshStatus("connecting", "Aguardando a rede privada responder…"), generation)
                        stop_event.wait(POLL_INTERVAL)
                finally:
                    _terminate(process, job)
                    process, job = None, None
                    with self._lock:
                        self._process, self._job = None, None
                if not stop_event.is_set():
                    failures += 1
                    self._publish(MeshStatus("degraded", "Rede interrompida. Tentando restabelecer…"), generation)
                    stop_event.wait(min(30.0, 2.0 ** min(failures, 5)))
        except MeshError as exc:
            self._publish(MeshStatus("error", str(exc)), generation)
        except (OSError, ValueError, TypeError):
            self._publish(MeshStatus("error", "Não foi possível iniciar a conexão privada. Reinstale o aplicativo e tente novamente."), generation)
        finally:
            if process is not None:
                _terminate(process, job)
            if config_path is not None:
                try:
                    config_path.unlink()
                except OSError:
                    pass
            with self._lock:
                self._process, self._job = None, None
            if stop_event.is_set():
                self._publish(MeshStatus(), generation)

    def wait_ready(self, timeout=45.0, cancel_event=None):
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._changed:
            while True:
                status = self._status
                if cancel_event is not None and cancel_event.is_set():
                    raise MeshError("Conexão cancelada.")
                if self._stop.is_set() or status.state == "stopped":
                    raise MeshError("Conexão privada parada.")
                if status.state == "error":
                    raise MeshError(status.message)
                if status.state == "ready" and (status.endpoint or self.role == "host"):
                    return status.endpoint
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MeshTimeoutError("Host ainda não encontrado. Verifique se o Host está aberto e se o código foi copiado corretamente.")
                self._changed.wait(min(0.2, remaining))

    def stop(self):
        with self._changed:
            self._generation += 1
            self._stop.set()
            process = self._process
            self._changed.notify_all()
            self._publish(MeshStatus(), self._generation)
        # Only signal the process this object owns; worker reaps and closes Job.
        if process is not None:
            try:
                process.terminate()
            except OSError:
                pass

    close = stop

    def join(self, timeout=5.0):
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))
        return thread is None or not thread.is_alive()
