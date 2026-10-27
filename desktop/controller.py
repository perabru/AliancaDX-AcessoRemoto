"""Visible native controller for the MiniAnyDesk TCP/TLS host.

Run ``python controller.py`` on Windows. The user chooses each endpoint;
bounded recovery keeps the same TLS identity and requires fresh host consent.
"""
from __future__ import annotations

import base64
import io
import ipaddress
import json
import math
import os
from pathlib import Path
import queue
import socket
import ssl
import sys
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from tkinter import ttk, messagebox, filedialog, font as tkfont

from PIL import Image, ImageTk

from network_addresses import connection_error_hint, normalize_target
from connection_metrics import session_traffic_text


VERSION = "7.1.0"
DEFAULT_PORT = 64321
NETWORK_GUIDANCE = ("Pela internet, cole o código privado do Host e informe a senha. "
                    "Não é necessário criar conta nem configurar VPN ou roteador.")
BG = "#F4FAF7"
PANEL = "#073B2B"
TEXT = "#123A2A"
MUTED = "#587568"
ACCENT = "#007A50"
RECONNECT_DELAYS = (2, 4, 8, 15, 30)
RECONNECT_WINDOW = 120.0
ACTIVE_PUMP_MS = 10
IDLE_PUMP_MS = 30
POINTER_INTERVAL = 1 / 60
STREAM_RESUME_INTERVAL = 1.0
MAX_CONTROLLER_SESSIONS = 8


def stream_frame_id(message):
    """Only explicitly negotiated v1 frames carry presentation feedback."""
    frame_id = message.get("frameId")
    if (type(message.get("streamVersion")) is int and message["streamVersion"] == 1 and
            type(frame_id) is int and 1 <= frame_id <= 2 ** 53 - 1):
        return frame_id
    return None


def retryable_transport_reason(reason):
    """Only retry identifiable transport failures, never an ambiguous remote EOF.

    Older hosts do not send a session-end marker. Their EOF can also mean that
    the responsible person clicked End, so that case requires a manual retry.
    """
    text = str(reason).casefold()
    if any(word in text for word in ("session_end", "não autoriz", "negad", "rejeitad",
                                     "certificado", "identidade", "hospedeiro parou",
                                     "encerrada pelo", "encerrado pelo")):
        return False
    return any(word in text for word in (
        "sem resposta do outro dispositivo", "envio sem progresso",
        "connection reset", "connection aborted", "broken pipe", "timed out",
        "network is unreachable", "no route to host", "conexão redefinida",
        "conexão foi forçada", "winerror 10054", "winerror 10053", "winerror 10060",
        "errno 104", "errno 103", "errno 110", "errno 32", "errno 101", "errno 113"))


class _OutgoingCommands:
    """Bounded FIFO; only an adjacent pointer update may be replaced."""
    def __init__(self, limit=256):
        self.limit = limit
        self.items = deque()
        self.condition = threading.Condition()

    def put(self, generation, peer, message):
        message = dict(message)
        item = (generation, peer, message)
        with self.condition:
            if self.items and message.get("type") == "input" and message.get("action") == "move":
                old_generation, old_peer, old = self.items[-1]
                if (old_generation == generation and old_peer is peer and old.get("type") == "input" and
                        old.get("action") == "move" and old.get("captureId") == message.get("captureId")):
                    self.items[-1] = item
                    return True
            if len(self.items) >= self.limit:
                return False
            self.items.append(item)
            self.condition.notify()
            return True

    def get(self, timeout=.2):
        with self.condition:
            if not self.items:
                self.condition.wait(timeout)
            return self.items.popleft() if self.items else None

    def clear(self):
        with self.condition:
            self.items.clear()
            self.condition.notify_all()


def fit_geometry(container_width, container_height, image_width, image_height):
    """Return centered integer (left, top, width, height), with all edges visible."""
    if min(container_width, container_height, image_width, image_height) <= 0:
        return (0, 0, 0, 0)
    scale = min(container_width / image_width, container_height / image_height)
    width = min(int(container_width), max(1, round(image_width * scale)))
    height = min(int(container_height), max(1, round(image_height * scale)))
    return ((int(container_width) - width) // 2, (int(container_height) - height) // 2, width, height)


def normalized_point(x, y, bounds, clamp=False):
    """Map only the displayed image rectangle, never its surrounding bars."""
    left, top, width, height = bounds
    if width <= 0 or height <= 0:
        return None
    if not clamp and not (left <= x <= left + width - 1 and top <= y <= top + height - 1):
        return None
    return (max(0.0, min(1.0, (x - left) / max(1, width - 1))), max(0.0, min(1.0, (y - top) / max(1, height - 1))))


def monitor_identity(value):
    if str(value).lower() in ("all", "0"):
        return 0
    identity = int(value)
    if not 1 <= identity <= 64:
        raise ValueError("Identificação de monitor inválida.")
    return identity


KEY_MAP = {
    "space": "space", "Escape": "esc", "Return": "enter", "KP_Enter": "enter",
    "BackSpace": "backspace", "Delete": "delete", "Tab": "tab", "ISO_Left_Tab": "tab",
    "Control_L": "ctrl", "Control_R": "ctrl", "Shift_L": "shift", "Shift_R": "shift",
    "Alt_L": "alt", "Alt_R": "alt", "Meta_L": "win", "Meta_R": "win",
    "Super_L": "win", "Super_R": "win", "Win_L": "win", "Win_R": "win",
    "Left": "left", "Right": "right", "Up": "up", "Down": "down",
    "Prior": "pageup", "Next": "pagedown", "Home": "home", "End": "end",
    "Insert": "insert", "Caps_Lock": "capslock", "Num_Lock": "numlock",
    "Scroll_Lock": "scrolllock", "Print": "printscreen", "Pause": "pause",
    "KP_Add": "add", "KP_Subtract": "subtract", "KP_Multiply": "multiply",
    "KP_Divide": "divide", "KP_Decimal": "decimal",
}


def remote_key(keysym, character=""):
    if keysym in KEY_MAP:
        return KEY_MAP[keysym]
    if keysym.startswith("F") and keysym[1:].isdigit() and 1 <= int(keysym[1:]) <= 24:
        return keysym.lower()
    if keysym.startswith("KP_") and keysym[3:].isdigit():
        return keysym[3:]
    if len(keysym) == 1 and 32 <= ord(keysym) <= 126:
        return keysym.lower()
    if len(character) == 1 and 32 <= ord(character) <= 126:
        return character.lower()
    return None


def pin_key(address, port):
    address = address.strip()
    if address.startswith("[") and address.endswith("]"):
        address = address[1:-1]
    try:
        address = ipaddress.ip_address(address).compressed
    except ValueError:
        address = address.rstrip(".").encode("idna").decode("ascii").lower()
    return f"[{address}]:{port}" if ":" in address else f"{address}:{port}"


def fingerprint_text(value):
    value = str(value).replace(":", "").replace(" ", "").lower()
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("A impressão digital do certificado é inválida.")
    return value


class Settings:
    """Connection labels and certificate pins; passwords are never written."""
    def __init__(self, path=None):
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".config"))) / "MiniAnyDesk"
        self.path = Path(path) if path else base / "control.json"
        self.data = {"address": "", "port": DEFAULT_PORT, "name": socket.gethostname(), "pins": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                for key in ("address", "name"):
                    if isinstance(value.get(key), str):
                        self.data[key] = value[key][:255]
                if isinstance(value.get("port"), int) and 1 <= value["port"] <= 65535:
                    self.data["port"] = value["port"]
                if isinstance(value.get("pins"), dict):
                    conflicting = set()
                    for key, pin in value["pins"].items():
                        if isinstance(key, str):
                            try:
                                address, separator, port = key.rpartition(":")
                                number = int(port)
                                if not separator or not address or not 1 <= number <= 65535:
                                    continue
                                canonical = pin_key(address, number)
                                fingerprint = fingerprint_text(pin)
                                previous = self.data["pins"].get(canonical)
                                if previous and previous != fingerprint:
                                    conflicting.add(canonical)
                                    self.data["pins"].pop(canonical, None)
                                elif canonical not in conflicting:
                                    self.data["pins"][canonical] = fingerprint
                            except (ValueError, UnicodeError):
                                pass
        except (OSError, ValueError, TypeError):
            pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(self.path)


class _PageWorkspace(ttk.Frame):
    """Native sidebar pages, with the old Notebook selection API for integrations."""
    def __init__(self, parent, on_select, **kwargs):
        super().__init__(parent, style="App.TFrame", **kwargs)
        self.pages = {}
        self.current = None
        self.on_select = on_select

    def add(self, page, name):
        self.pages[name] = page

    def page_name(self, page):
        for name, widget in self.pages.items():
            if page == name or str(page) == str(widget): return name
        raise ValueError("Página desconhecida.")

    def show(self, name):
        if self.current is not None: self.current.pack_forget()
        self.current = self.pages[name]
        self.current.pack(fill="both", expand=True)

    def select(self, page=None):
        if page is not None: self.on_select(self.page_name(page))
        return str(self.current) if self.current is not None else ""


class ControllerSessions:
    """Own independent remote windows; only saved names and TLS pins are shared.

    Each Controller retains its own peer, mesh process, queues, file transfers,
    consent, frame acknowledgement and reconnect generation. The hidden Tk owner
    outlives individual windows, so closing one never closes another session.
    """
    def __init__(self, owner, settings=None, book=None, *, window_factory=None,
                 controller_factory=None, limit=MAX_CONTROLLER_SESSIONS):
        self.owner = owner
        self.settings = settings or Settings()
        self.book = book
        self.window_factory = window_factory or tk.Toplevel
        self.controller_factory = controller_factory or Controller
        self.limit = limit
        self.controllers = {}
        self.next_id = 1
        self.closing = False

    def new_window(self, *, entry=None, connect=False, parent=None):
        if self.closing:
            return None
        if len(self.controllers) >= self.limit:
            messagebox.showinfo("Conexões abertas", f"Você já tem {self.limit} janelas abertas. Feche uma janela para abrir outra conexão.", parent=parent or self.owner)
            return None
        identity = self.next_id
        self.next_id += 1
        window = self.window_factory(self.owner)
        try:
            controller = self.controller_factory(window, settings=self.settings,
                book=self.book, session_manager=self, session_id=identity)
        except Exception:
            window.destroy()
            raise
        self.book = controller.book
        self.controllers[identity] = controller
        controller._session_status_trace = controller.connection_var.trace_add(
            "write", lambda *_args: self.refresh())
        self.refresh()
        if entry is not None:
            controller._prepare_computer_connection(dict(entry))
        elif connect:
            controller._open_mesh_setup()
        self.focus_session(identity)
        return controller

    @staticmethod
    def endpoint(controller):
        endpoint = getattr(controller, "connected_endpoint", None)
        if endpoint:
            return endpoint
        pending = getattr(controller, "connecting_endpoint", None)
        if pending:
            return pin_key(*pending)
        retry = getattr(controller, "retry_credentials", None)
        if getattr(controller, "retry_job", None) and retry:
            return pin_key(retry[0], retry[1])
        return None

    def find_endpoint(self, address, port):
        target = pin_key(address, port)
        for identity, controller in self.controllers.items():
            if not controller.stopping.is_set() and self.endpoint(controller) == target:
                return identity
        return None

    def focus_session(self, identity):
        controller = self.controllers.get(identity)
        if controller is None or controller.stopping.is_set():
            return False
        for other in tuple(self.controllers.values()):
            if other is not controller and not other.stopping.is_set():
                other._release_input()
        try:
            controller.root.deiconify()
            controller.root.lift()
            controller.root.focus_force()
            if controller.selected_page == "session":
                controller.canvas.focus_set()
        except tk.TclError:
            return False
        return True

    def describe(self, identity, controller):
        peer = getattr(controller, "peer", None)
        status = ("Conectado" if peer and not peer.closed else
                  "Reconectando" if getattr(controller, "retry_job", None) or getattr(controller, "retry_attempting", False) else
                  "Conectando" if controller.connecting else "Desconectado")
        name = getattr(controller, "mesh_target_name", "")
        if not name:
            endpoint = self.endpoint(controller)
            if endpoint:
                for entry in getattr(controller, "computer_entries", {}).values():
                    if pin_key(entry["address"], entry["port"]) == endpoint:
                        name = entry["name"]
                        break
            if not name and endpoint:
                name = "PC pela internet" if endpoint.startswith("mesh-") else endpoint
        title = f"Conexão {identity}" + (f" · {name[:48]}" if name else "")
        return title, status

    def refresh(self):
        rows = [(identity, *self.describe(identity, controller))
                for identity, controller in tuple(self.controllers.items())
                if not controller.stopping.is_set()]
        for identity, controller in tuple(self.controllers.items()):
            if controller.stopping.is_set():
                continue
            controller._set_session_windows(rows)
            title, status = self.describe(identity, controller)
            controller.root.title(f"AliançaDX {VERSION} — {title} — {status}")

    def unregister(self, controller):
        identity = getattr(controller, "session_window_id", None)
        if self.controllers.get(identity) is not controller:
            return
        self.controllers.pop(identity)
        trace, controller._session_status_trace = getattr(controller, "_session_status_trace", None), None
        if trace:
            try:
                controller.connection_var.trace_remove("write", trace)
            except tk.TclError:
                pass
        if self.controllers:
            self.refresh()
        else:
            self.owner.destroy()

    def close_all(self):
        self.closing = True
        for controller in tuple(self.controllers.values()):
            controller.close()


class Controller:
    def __init__(self, root, settings=None, book=None, probe=None, session_manager=None, session_id=None):
        self.root = root
        self.session_manager = session_manager
        self.session_window_id = session_id
        self.session_window_ids = []
        self.settings = settings or Settings()
        self.book_error = ""
        try:
            if book is None:
                from computer_book import ComputerBook
                book = ComputerBook(self.settings.path.with_name("computers.json"))
            self.book = book
        except (ValueError, OSError) as error:
            self.book = None
            self.book_error = str(error)
        self.probe = probe
        self.computer_entries = {}
        self.computer_revisions = {}
        self.computer_presence = {}
        self.presence_pending = {}
        self.presence_refresh_requested = set()
        self.presence_tasks = queue.Queue(maxsize=256)
        self.seen_persisted = {}
        self.connected_endpoint = None
        self.connecting_endpoint = None
        self.computer_dialog = None
        self.peer = None
        self.transfers = None
        self.connecting = False
        self.generation = 0
        self.frame_epoch = 0
        self.sharing = False
        self.local_paused = False
        self.pending_monitor = None
        self.current_screen = None
        self.current_image = None
        self.photo = None
        self.image_bounds = (0, 0, 0, 0)
        self.buttons_down = set()
        self.keys_down = {}
        self.last_point = (0.5, 0.5)
        self.last_move = 0.0
        self.pending_move = None
        self.move_job = None
        self.wheel_remainder = 0.0
        self.dialog_active = False
        self.local_prompt_active = False
        self.remote_prompt_active = False
        self.file_active = False
        self.fullscreen = False
        self.fullscreen_controls_hidden = False
        self.fullscreen_hint_job = None
        self.monitor_ids = [0]
        self.messages = queue.Queue()
        self.latest_lock = threading.Lock()
        self.latest_callbacks = {}
        self.queued_latest = set()
        self.pending_states = {}
        self.network_suspended = False
        self.outgoing = _OutgoingCommands()
        self.send_failed_generation = None
        self.buffered_messages = []
        self.buffered_bytes = 0
        self.buffer_overflow_generation = None
        self.frame_lock = threading.Lock()
        self.raw_frame = None
        self.decoded_frame = None
        self.frame_event = threading.Event()
        self.stopping = threading.Event()
        self.resize_job = None
        self.capture_available = True
        self.frame_stalled = False
        self.last_frame_at = 0.0
        self.frame_wait_started = 0.0
        self.stream_ready_generation = None
        self.last_stream_feedback_id = 0
        self.stream_active_generation = None
        self.last_stream_resume_at = None
        self.stream_resume_needed = False
        self.reconnect_available = False
        self.retry_credentials = None
        self.retry_job = None
        self.retry_expiry_job = None
        self.retry_deadline = None
        self.retry_attempt = 0
        self.retry_attempting = False
        self.retry_token = None
        self.connect_cancel = None
        self.mesh_panel = None
        self.mesh_status = None
        self.mesh_pairing = None
        self.mesh_start_lock = threading.RLock()
        self.mesh_target_name = ""
        self._updating_computer_id = None
        self._saved_computer_id = None
        self._endpoint_details_open = False
        self._manual_connection = bool(self.settings.data.get("address") and not self.settings.data["address"].startswith("mesh-"))
        self._editing_code = False
        self._connection_options_open = False
        self._connection_progress_running = False
        self.auto_reconnect_var = tk.BooleanVar(value=True)
        self.network_hint_var = tk.StringVar(value=NETWORK_GUIDANCE)
        self.address_var = tk.StringVar(value=self.settings.data["address"])
        self.port_var = tk.StringVar(value=str(self.settings.data["port"]))
        self.password_var = tk.StringVar()
        self.name_var = tk.StringVar(value=self.settings.data["name"])
        self.connection_var = tk.StringVar(value="Desconectado")
        self.traffic_var = tk.StringVar(value="Sessão · sem conexão")
        self.screen_var = tk.StringVar(value="Conecte usando o código e a senha exibidos no Hospedeiro.")
        self.file_var = tk.StringVar(value="Até 100 MiB por arquivo. A outra pessoa confirma a transferência.")
        self.progress_var = tk.DoubleVar(value=0)
        self.computer_notice_var = tk.StringVar(value="Escolha um computador salvo ou abra Conectar para usar um código privado.")
        self._build_ui()
        self._refresh_computer_rows()
        self._show_page(self._initial_page())
        for index in range(4):
            threading.Thread(target=self._presence_worker, name=f"MiniAnyDesk-presence-{index + 1}", daemon=True).start()
        threading.Thread(target=self._decode_frames, name="MiniAnyDesk-frame-decoder", daemon=True).start()
        threading.Thread(target=self._send_commands, name="MiniAnyDesk-input-sender", daemon=True).start()
        self.pump_job = self.root.after(IDLE_PUMP_MS, self._pump)
        self.presence_job = self.root.after(150, self._presence_tick)
        self.traffic_job = self.root.after(1000, self._traffic_tick)
        if self.book_error:
            self.root.after_idle(lambda: self._computer_error(self.book_error, loading=True))
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _build_ui(self):
        import desktop_ui as ui
        ui.apply_theme(self.root)
        self.ui_font = getattr(self.root, "_minianydesk_ui_font", "Segoe UI")
        self.root.title(f"AliançaDX Acesso Remoto {VERSION} — Controlador")
        initial_width = max(820, min(1180, self.root.winfo_screenwidth() - 80))
        initial_height = max(560, min(800, self.root.winfo_screenheight() - 90))
        self.root.geometry(f"{initial_width}x{initial_height}")
        self.root.minsize(820, 560)
        self.root.configure(bg=BG)
        self.selected_page = "computers"
        self.controls_visible = False
        self.files_visible = False
        self.search_var = tk.StringVar()
        self.password_endpoint = None
        self.password_visible = False
        self.page_buttons = {}
        self.sidebar = tk.Frame(self.root, bg=PANEL, width=236)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        sidebar_brand = ui.brand(self.sidebar, subtitle="CONTROLADOR · ACESSAR UM PC")
        sidebar_brand.pack(fill="x", padx=12, pady=(16, 24))
        for key, title in (("connect", "Conectar"), ("computers", "Computadores"),
                           ("session", "Sessão remota"), ("internet", "Como usar")):
            button = ttk.Button(self.sidebar, text=title, style="Sidebar.TButton", command=lambda key=key: self._show_page(key))
            button.pack(fill="x", padx=12, pady=4)
            self.page_buttons[key] = button
        ttk.Button(self.sidebar, text="iPhone e iPad", style="Sidebar.TButton",
                   command=self._open_ios_connector).pack(fill="x", padx=12, pady=(12, 4))
        if self.session_manager is not None:
            ttk.Button(self.sidebar, text="＋ Nova conexão", style="Sidebar.TButton", command=self._new_connection).pack(fill="x", padx=12, pady=(18, 4))
            self.session_windows_count = tk.StringVar()
            tk.Label(self.sidebar, textvariable=self.session_windows_count, bg=PANEL, fg="#A8CDB8", anchor="w", font=(self.ui_font, 9)).pack(fill="x", padx=18, pady=(10, 5))
            self.session_windows_var = tk.StringVar()
            self.session_windows_combo = ttk.Combobox(self.sidebar, textvariable=self.session_windows_var, state="readonly", width=20)
            self.session_windows_combo.pack(fill="x", padx=12, pady=(0, 4))
            self.session_windows_combo.bind("<<ComboboxSelected>>", self._select_session_window)
            tk.Label(self.sidebar, text="Cada janela mantém sua própria conexão.\nEscolha uma conexão para trazê-la à frente.", bg=PANEL, fg="#A8CDB8", justify="left", wraplength=210, font=(self.ui_font, 9)).pack(fill="x", padx=18, pady=(4, 10))
        self.root.update_idletasks()
        sidebar_width = max(236, sidebar_brand.winfo_reqwidth() + 36, max(b.winfo_reqwidth() for b in self.page_buttons.values()) + 24)
        self.sidebar.configure(width=sidebar_width)
        self.root.minsize(max(820, sidebar_width + 644), 560)
        tk.Label(self.sidebar, text=f"AliançaDX · {VERSION}\nVocê conecta. A pessoa autoriza.", bg=PANEL, fg="#A8CDB8", justify="left", anchor="w", wraplength=sidebar_width - 36, font=(self.ui_font, 9)).pack(side="bottom", fill="x", padx=18, pady=(8, 16))
        self.sidebar_status = tk.Label(self.sidebar, textvariable=self.connection_var, bg=PANEL, fg="#E1F4E8", justify="left", wraplength=max(142, sidebar_width - 36), font=(self.ui_font, 10))
        self.sidebar_status.pack(side="bottom", anchor="w", padx=18, pady=12)
        self.workspace = _PageWorkspace(self.root, self._show_page, padding=20)
        self.workspace.pack(side="left", fill="both", expand=True)
        self.computers_page = ttk.Frame(self.workspace, style="App.TFrame")
        self.connect_page = ttk.Frame(self.workspace, style="App.TFrame")
        self.remote_page = ttk.Frame(self.workspace, style="App.TFrame")
        self.internet_page = ttk.Frame(self.workspace, style="App.TFrame")
        self.workspace.add(self.computers_page, "computers")
        self.workspace.add(self.connect_page, "connect")
        self.workspace.add(self.remote_page, "session")
        self.workspace.add(self.internet_page, "internet")
        self._build_computers(self.computers_page)
        self._build_connect(self.connect_page)
        self._build_session(self.remote_page)
        self._build_connection_help(self.internet_page)
        self.address_var.trace_add("write", self._credential_endpoint_changed)
        self.port_var.trace_add("write", self._credential_endpoint_changed)
        self.password_var.trace_add("write", self._credential_password_changed)
        self.root.bind("<FocusOut>", lambda _e: self.root.after_idle(self._check_focus))
        self.root.bind("<Escape>", self._escape)
        self.root.bind("<F8>", self._toggle_fullscreen_commands)
        self._show_page("computers")

    def _open_ios_connector(self):
        current = getattr(self, "_ios_connector_process", None)
        if current is not None and current.poll() is None:
            messagebox.showinfo("iPhone e iPad", "O conector já está aberto. Use a janela do navegador para conectar o iPhone.", parent=self.root)
            return
        try:
            from ios_launcher import launch_connector
            self._ios_connector_process = launch_connector()
        except (OSError, RuntimeError, ValueError):
            messagebox.showerror("iPhone e iPad", "Não foi possível abrir o conector. Atualize o aplicativo e tente novamente.", parent=self.root)

    def _stop_ios_connector(self):
        process, self._ios_connector_process = getattr(self, "_ios_connector_process", None), None
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
            except OSError:
                pass

    def _new_connection(self):
        manager = getattr(self, "session_manager", None)
        if manager is not None:
            self._release_input()
            return manager.new_window(connect=True, parent=self.root)

    def _set_session_windows(self, rows):
        if not hasattr(self, "session_windows_combo"):
            return
        self.session_window_ids = [identity for identity, _name, _state in rows]
        self.session_windows_combo.configure(values=[f"{name} · {state}" for _identity, name, state in rows])
        count = len(rows)
        self.session_windows_count.set(f"{count} {'conexão aberta' if count == 1 else 'conexões abertas'}")
        if self.session_window_id in self.session_window_ids:
            self.session_windows_combo.current(self.session_window_ids.index(self.session_window_id))

    def _select_session_window(self, _event=None):
        index = self.session_windows_combo.current()
        manager = getattr(self, "session_manager", None)
        if manager is not None and 0 <= index < len(self.session_window_ids):
            manager.focus_session(self.session_window_ids[index])
            manager.refresh()

    def _initial_page(self):
        """Guide a new installation while preserving returning users' list."""
        has_history = (self.settings.data.get("address") or self.settings.data.get("pins")
                       or self.computer_entries or self.book_error)
        return "computers" if has_history else "connect"

    def _open_mesh_setup(self):
        if not self.stopping.is_set():
            self._updating_computer_id = None
            self._set_password_visible(False)
            self._editing_code = True
            self._manual_connection = False
            self._update_connect_journey()
            self._show_page("connect")
            self.connect_form.canvas.yview_moveto(0.0)
            if self.mesh_panel is not None:
                self.mesh_panel.code_entry.focus_set()

    def _build_mesh(self, page):
        from mesh_ui import MeshPanel
        self.mesh_form = self.connect_form
        self.mesh_panel = MeshPanel(page, role="controller", compact=True,
                                    config_dir=self.settings.path.parent / "mesh-controller",
                                    on_status=self._mesh_status_changed, on_select=self._select_mesh_pairing,
                                    on_save=self._save_selected_pairing)
        self.mesh_panel.pack(fill="both", expand=True)
        self.mesh_panel.select_button.pack_forget()
        self.mesh_panel.select_button.master.pack_forget()
        self.mesh_panel.continue_focus = self.connect_button

    def _build_connection_help(self, page):
        import desktop_ui as ui
        form = ui.ScrollForm(page)
        form.pack(fill="both", expand=True)
        ui.page_brand(form.content, "ALIANÇADX · COMO USAR").pack(fill="x", pady=(0, 17))
        ttk.Label(form.content, text="Uma conexão em três passos", style="Title.TLabel").pack(anchor="w", pady=(0, 8))
        intro = ttk.Label(form.content, text="Hospedeiro é quem recebe ajuda. Controlador é quem acessa o outro computador.", style="Muted.TLabel", justify="left")
        intro.pack(fill="x", pady=(0, 20))
        paragraphs = [
            ("1  ·  Copie o código no Hospedeiro", "No computador que será acessado, abra o Hospedeiro e deixe-o disponível. Peça à pessoa o código privado e a senha exibidos na janela."),
            ("2  ·  Informe o código e a senha", "Abra Conectar, cole o código completo e continue. Depois informe a senha de acesso. A preparação da rede acontece automaticamente, sem login de VPN."),
            ("3  ·  Aguarde a autorização", "A pessoa no Hospedeiro confirma seu nome antes do acesso. Durante a sessão, você pode usar a tela cheia, controlar a tela e enviar ou receber arquivos."),
            ("Se não conectar", "Mantenha os dois programas abertos, confira a internet e peça um código e uma senha atuais. Se a rede não ficar disponível, tente novamente. Em redes diferentes, o serviço de retransmissão também precisa estar disponível."),
        ]
        for heading, text in paragraphs:
            body = ui.card(form.content, padding=20)
            body.pack(fill="x", pady=(0, 12))
            title = ttk.Label(body, text=heading, style="CardHeading.TLabel", justify="left")
            title.pack(anchor="w", fill="x", pady=(0, 7))
            description = ttk.Label(body, text=text, style="CardMuted.TLabel", justify="left")
            description.pack(fill="x")
            body.bind("<Configure>", lambda event, title=title, description=description: [item.configure(wraplength=max(140, event.width - 44)) for item in (title, description)], add="+")
        ttk.Button(form.content, text="Começar uma conexão", style="Primary.TButton", command=self._open_mesh_setup).pack(fill="x", pady=(4, 14))
        form.content.bind("<Configure>", lambda event: intro.configure(wraplength=max(140, event.width - 4)), add="+")

    def _mesh_status_changed(self, status):
        if self.stopping.is_set():
            return
        self.mesh_status = status
        if self.connecting and self.mesh_pairing is not None:
            self.connection_var.set(status.message)
            if hasattr(self, "connection_action_hint"):
                self.connection_action_hint.set(status.message)
        self._update_address_hint()

    def _mesh_endpoint_busy(self):
        return bool(self.peer or self.connecting or getattr(self, "retry_job", None)
                    or getattr(self, "retry_attempting", False) or getattr(self, "retry_deadline", None) is not None)

    def _select_mesh_pairing(self, pairing):
        from mesh_ui import pairing_address
        if self.stopping.is_set():
            return False
        if self._mesh_endpoint_busy():
            messagebox.showinfo("Encerre a conexão atual", "Encerre a sessão ou cancele a tentativa antes de escolher outro computador.", parent=self.root)
            return False
        address = pairing_address(pairing)
        previous = self._credential_endpoint()
        identity_changed = self.mesh_pairing != pairing
        if identity_changed:
            self.mesh_panel.stop()
        self.mesh_pairing = pairing
        self._updating_computer_id = None
        self.mesh_target_name = "Host pela internet"
        self._saved_computer_id = None
        if self.book is not None:
            try:
                entry = self.book.find_endpoint(address, pairing.port)
                if entry:
                    self.mesh_target_name = entry["name"]
                    self._saved_computer_id = entry["id"]
            except (ValueError, OSError):
                pass
        self.address_var.set(address)
        self.port_var.set(str(pairing.port))
        if identity_changed or previous != self._credential_endpoint():
            self.password_var.set("")
        self._set_endpoint_details(False)
        self._set_password_visible(False)
        self._update_address_hint()
        self._show_page("connect")
        self.connect_form.canvas.yview_moveto(0.0)
        self.password_entry.focus_set()
        return True

    def _save_selected_pairing(self, pairing):
        if self._mesh_endpoint_busy() or self.stopping.is_set():
            messagebox.showinfo("Encerre a conexão atual", "Encerre a sessão ou cancele a tentativa antes de atualizar o acesso salvo.", parent=self.root)
            return False
        if self.book is None:
            raise OSError("O cadastro de computadores está indisponível. O código não foi salvo.")
        record = self.book.remember_pairing(pairing, self.mesh_panel.config_dir,
            identifier=getattr(self, "_updating_computer_id", None), name=self.mesh_panel.saved_name_var.get())
        self._updating_computer_id = None
        self._saved_computer_id = record["id"]
        self._refresh_computer_rows()
        self.computers_tree.selection_set(record["id"])
        manager = getattr(self, "session_manager", None)
        for other in tuple(getattr(manager, "controllers", {}).values()):
            if other is not self and not other.stopping.is_set():
                other._refresh_computer_rows()
        self.computer_notice_var.set("Acesso salvo com código protegido neste usuário. A senha e a aprovação no Hospedeiro continuam necessárias.")
        return True

    def _use_lan(self):
        if self._mesh_endpoint_busy():
            return
        self.mesh_pairing = None
        self.mesh_target_name = ""
        if getattr(self, "mesh_panel", None) is not None:
            self.mesh_panel.stop()
        from mesh_ui import is_pairing_address
        if is_pairing_address(self.address_var.get()):
            self.address_var.set("")
        self._set_endpoint_details(True)
        self._show_page("connect")

    def _build_connect(self, page):
        import desktop_ui as ui
        self.connection_actions = ttk.Frame(page, style="App.TFrame", padding=(0, 10, 0, 0))
        self.connection_actions.pack(side="bottom", fill="x")
        self.connection_actions.columnconfigure(0, weight=1)
        self.connection_actions.columnconfigure(1, weight=1)
        self.connection_progress = ttk.Progressbar(self.connection_actions, mode="indeterminate", maximum=100)
        self.connection_divider = ttk.Separator(self.connection_actions)
        self.connection_divider.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.connection_action_hint = tk.StringVar(value="Comece pelo código privado do Hospedeiro.")
        action_hint = ttk.Label(self.connection_actions, textvariable=self.connection_action_hint, style="Muted.TLabel", justify="left")
        action_hint.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 9))
        self.connect_button = ttk.Button(self.connection_actions, text="Conectar", style="Primary.TButton", command=self._connect_primary)
        self.connect_button.grid(row=2, column=0, sticky="ew", padx=(0, 10))
        self.cancel_connect_button = ttk.Button(self.connection_actions, text="Cancelar conexão", style="Secondary.TButton", command=self._cancel_connection_action)
        self.cancel_connect_button.grid(row=2, column=1, sticky="ew")
        self.connection_actions.bind("<Configure>", lambda event: action_hint.configure(wraplength=max(140, event.width - 4)), add="+")

        form = ui.ScrollForm(page)
        form.pack(fill="both", expand=True)
        self.connect_form = form
        ui.page_brand(form.content, f"CONTROLADOR · {VERSION}").pack(fill="x", pady=(0, 17))
        title = ttk.Label(form.content, text="Conecte-se a um computador", style="Title.TLabel")
        title.pack(anchor="w", fill="x", pady=(0, 5))
        introduction = ttk.Label(form.content, text="Cole o convite recebido. A conexão começa com a aprovação de quem está no PC.", style="Muted.TLabel", justify="left")
        introduction.pack(anchor="w", fill="x", pady=(0, 18))
        steps = ttk.Frame(form.content, style="App.TFrame")
        steps.pack(fill="x", pady=(0, 18))
        self.connection_steps = []
        for number, caption in ((1, "Código"), (2, "Senha"), (3, "Aprovação")):
            step = tk.Label(steps, text=f"{number}  {caption}", bg="#E4F7EC", fg=MUTED,
                            padx=12, pady=8, anchor="w", font=(self.ui_font, 9, "bold"))
            step.pack(side="left", expand=True, fill="x", padx=(0, 6) if number < 3 else 0)
            self.connection_steps.append(step)

        self.code_stage = ui.card(form.content, padding=20)
        self.code_stage.pack(fill="x", pady=(0, 12))
        ttk.Label(self.code_stage, text="01  ·  Convite do computador", style="CardHeading.TLabel").pack(anchor="w", fill="x", pady=(0, 5))
        code_hint = ttk.Label(self.code_stage, text="Peça à pessoa o código privado exibido no computador que você deseja acessar.", style="CardMuted.TLabel", justify="left")
        code_hint.pack(fill="x", pady=(0, 12))
        self._build_mesh(self.code_stage)
        self.code_stage.bind("<Configure>", lambda event: code_hint.configure(wraplength=max(140, event.width - 44)), add="+")

        connection = ui.card(form.content, padding=20)
        self.connection_card = connection
        connection.pack(fill="x", pady=(0, 12))
        connection.columnconfigure(0, weight=3)
        connection.columnconfigure(1, weight=1)
        self.connection_entries = []
        self.target_label_var = tk.StringVar(value="2  ·  Senha de acesso")
        self.target_label = ttk.Label(connection, textvariable=self.target_label_var, style="CardHeading.TLabel", wraplength=500)
        self.target_label.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        self.target_summary_var = tk.StringVar(value="Código validado. A identidade do Host será conferida automaticamente.")
        self.target_summary = ttk.Label(connection, textvariable=self.target_summary_var, style="CardAccent.TLabel", justify="left")
        self.target_summary.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 16))
        self.endpoint_widgets = []
        for label, variable, column, width in (("IP ou nome do Hospedeiro", self.address_var, 0, 20), ("Porta", self.port_var, 1, 7)):
            spacing = (0, 14) if column == 0 else 0
            caption = ttk.Label(connection, text=label, style="Card.TLabel")
            caption.grid(row=2, column=column, sticky="w", pady=(0, 6), padx=spacing)
            entry = ttk.Entry(connection, textvariable=variable, width=width)
            entry.grid(row=3, column=column, sticky="ew", ipady=4, padx=spacing)
            entry.bind("<Return>", lambda _e: self.connect())
            self.connection_entries.append(entry)
            self.endpoint_widgets.extend((caption, entry))
        network_hint = ttk.Label(connection, textvariable=self.network_hint_var, style="CardMuted.TLabel", justify="left")
        network_hint.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(7, 14))
        self.endpoint_widgets.append(network_hint)
        ttk.Label(connection, text="Senha de acesso", style="Card.TLabel").grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 6))
        secret = ttk.Frame(connection, style="CardBody.TFrame")
        secret.grid(row=6, column=0, columnspan=2, sticky="ew")
        secret.columnconfigure(0, weight=1)
        self.password_entry = ttk.Entry(secret, textvariable=self.password_var, show="*", width=12, style="Password.TEntry", font=(self.ui_font, 12))
        self.password_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.password_entry.bind("<Return>", lambda _e: self.connect())
        self.connection_entries.append(self.password_entry)
        self.password_toggle_button = ttk.Button(secret, text="Mostrar senha", style="Password.TButton", command=self._toggle_password_visibility)
        self.password_toggle_button.grid(row=0, column=1, sticky="ns")
        password_hint = ttk.Label(connection, text="Use a senha atual exibida no Hospedeiro. Ela não é salva.", style="CardMuted.TLabel", justify="left")
        password_hint.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(7, 12))
        self.connection_options_button = ttk.Button(connection, text="Mais opções", style="Quiet.TButton", command=self._toggle_connection_options)
        self.connection_options_button.grid(row=8, column=0, columnspan=2, sticky="w")
        self.connection_options = ttk.Frame(connection, style="CardBody.TFrame")
        self.connection_options.columnconfigure(0, weight=1)
        ttk.Label(self.connection_options, text="Seu nome no pedido de acesso", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=(12, 6))
        self.name_entry = ttk.Entry(self.connection_options, textvariable=self.name_var, width=20)
        self.name_entry.grid(row=1, column=0, sticky="ew", ipady=4)
        self.name_entry.bind("<Return>", lambda _e: self.connect())
        self.connection_entries.append(self.name_entry)
        ttk.Checkbutton(self.connection_options, text="Reconectar após uma queda de rede", style="Card.TCheckbutton",
                        variable=self.auto_reconnect_var, command=self._auto_reconnect_changed).grid(row=2, column=0, sticky="w", pady=(12, 0))
        reconnect_hint = ttk.Label(self.connection_options, text="Por até 2 minutos, com nova aprovação no Hospedeiro. Encerrar cancela as tentativas.", style="CardMuted.TLabel", justify="left")
        reconnect_hint.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self.endpoint_details_button = ttk.Button(connection, text="Trocar código", style="Quiet.TButton", command=self._open_mesh_setup)
        self.endpoint_details_button.grid(row=10, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self.connection_mode_bar = ttk.Frame(form.content, style="App.TFrame")
        self.connection_mode_bar.pack(fill="x", pady=(0, 12))
        self.internet_setup_button = ttk.Button(self.connection_mode_bar, text="Usar código pela internet", style="Quiet.TButton", command=self._open_mesh_setup)
        self.internet_setup_button.pack(side="left")
        self.lan_setup_button = ttk.Button(self.connection_mode_bar, text="Rede local · avançado", style="Quiet.TButton", command=self._use_lan)
        self.lan_setup_button.pack(side="right")
        self.connection_tip = ttk.Label(form.content, text="A pessoa no Hospedeiro precisa aprovar o acesso antes da sessão começar.", style="Muted.TLabel", justify="left")
        self.connection_tip.pack(fill="x", pady=(0, 12))
        self.connection_status_label = ttk.Label(form.content, textvariable=self.connection_var, style="Status.TLabel", justify="left")
        self.connection_status_label.pack(fill="x", pady=(0, 10))
        form.content.bind("<Configure>", lambda event: [label.configure(wraplength=max(160, event.width - 4)) for label in (title, introduction, self.connection_status_label, self.connection_tip)], add="+")
        connection.bind("<Configure>", lambda event: [label.configure(wraplength=max(140, event.width - 44)) for label in (self.target_label, self.target_summary, network_hint, password_hint, reconnect_hint)], add="+")
        self._set_password_visible(False)
        self._update_connect_journey()
        self._update_address_hint()

    def _connect_primary(self):
        """Keep the persistent action useful at either step of the guided form."""
        if (getattr(self, "_editing_code", False) or
                (getattr(self, "mesh_pairing", None) is None and not getattr(self, "_manual_connection", False))):
            return self.mesh_panel.select_code()
        return self.connect()

    def _toggle_connection_options(self):
        self._connection_options_open = not self._connection_options_open
        if self._connection_options_open:
            self.connection_options.grid(row=9, column=0, columnspan=2, sticky="ew")
        else:
            self.connection_options.grid_remove()
        self.connection_options_button.configure(text="Menos opções" if self._connection_options_open else "Mais opções")

    def _update_connect_journey(self):
        """Render one active step while keeping a single pairing owner and secret."""
        if not hasattr(self, "code_stage"):
            return
        pairing = getattr(self, "mesh_pairing", None)
        manual = getattr(self, "_manual_connection", False)
        code_step = getattr(self, "_editing_code", False) or (pairing is None and not manual)
        if code_step:
            self.connection_card.pack_forget()
            self.code_stage.pack(fill="x", pady=(0, 12), before=self.connection_mode_bar)
            self.connection_action_hint.set("Cole o código privado e continue para informar a senha.")
            self.connect_button.configure(text="Validar código e continuar")
            self.internet_setup_button.pack_forget()
        else:
            self.code_stage.pack_forget()
            self.connection_card.pack(fill="x", pady=(0, 12), before=self.connection_mode_bar)
            self.connection_action_hint.set("Ao conectar, aguarde a autorização da pessoa no Hospedeiro.")
            self.connect_button.configure(text="Conectar ao computador")
            self.internet_setup_button.pack(side="left")
            self.target_label_var.set("2  ·  Informe a senha" if pairing is not None else "Conectar pela rede local")
            self.target_summary_var.set(f"Código validado · {self.mesh_target_name or 'Hospedeiro pela internet'}" if pairing is not None else "Use o endereço do Hospedeiro na mesma rede Wi-Fi ou cabo.")
        self.internet_setup_button.configure(state="disabled" if code_step else "normal")
        for widget in self.endpoint_widgets:
            if manual and not code_step:
                widget.grid()
            else:
                widget.grid_remove()
        self.endpoint_details_button.configure(text="Trocar código" if pairing is not None else "Usar código pela internet")
        self._refresh_connection_steps()

    def _refresh_connection_steps(self):
        if not hasattr(self, "connection_steps"):
            return
        connected = bool(self.peer and not self.peer.closed)
        active = 3 if connected or self.connecting else 1 if (getattr(self, "_editing_code", False) or (self.mesh_pairing is None and not self._manual_connection)) else 2
        first_caption = "Endereço" if self._manual_connection and self.mesh_pairing is None else "Código"
        for index, (widget, caption) in enumerate(zip(self.connection_steps, (first_caption, "Senha", "Aprovação")), 1):
            widget.configure(text=f"{'✓' if index < active else index}  {caption}",
                             bg=ACCENT if index == active else "#E4F7EC",
                             fg="#FFFFFF" if index == active else TEXT if index < active else MUTED)

    def _set_endpoint_details(self, opened):
        self._endpoint_details_open = bool(opened)
        if not hasattr(self, "endpoint_widgets"):
            return
        self._manual_connection = bool(opened and getattr(self, "mesh_pairing", None) is None)
        self._editing_code = False
        self._update_connect_journey()

    def _build_session(self, page):
        import desktop_ui as ui
        self.session_heading = ttk.Frame(page, style="App.TFrame")
        self.session_heading.pack(fill="x", pady=(0, 14))
        ui.page_brand(self.session_heading, f"CONTROLADOR · {VERSION}").pack(fill="x", pady=(0, 14))
        ttk.Label(self.session_heading, text="Sessão remota", style="Title.TLabel").pack(anchor="w")
        self.session_connection_label = ttk.Label(self.session_heading, textvariable=self.connection_var, style="Muted.TLabel", justify="left")
        self.session_connection_label.pack(fill="x", pady=(4, 0))
        self.traffic_label = ttk.Label(self.session_heading, textvariable=self.traffic_var, style="Muted.TLabel")
        self.traffic_label.pack(anchor="w", pady=(4, 0))
        self.session_heading.bind("<Configure>", lambda event: self.session_connection_label.configure(wraplength=max(140, event.width - 4)), add="+")
        self.session_toolbar = ttk.Frame(page, style="App.TFrame")
        self.session_toolbar.pack(fill="x", pady=(0, 12))
        self.controls_toggle_button = ttk.Button(self.session_toolbar, text="Controles", style="Secondary.TButton", command=self._toggle_controls)
        self.controls_toggle_button.pack(side="left", padx=(0, 8))
        self.files_toggle_button = ttk.Button(self.session_toolbar, text="Arquivos", style="Secondary.TButton", command=self._toggle_files)
        self.files_toggle_button.pack(side="left", padx=(0, 8))
        self.fullscreen_button = ttk.Button(self.session_toolbar, text="Tela cheia", style="Secondary.TButton", command=self._toggle_fullscreen)
        self.fullscreen_button.pack(side="left", padx=(0, 8))
        self.disconnect_button = ttk.Button(self.session_toolbar, text="Encerrar", style="Danger.TButton", command=self.disconnect)
        self.disconnect_button.pack(side="right")
        self.controls_panel = ui.card(page, padding=12)
        self.controls_panel.columnconfigure(1, weight=1)
        ttk.Label(self.controls_panel, text="Área de trabalho", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.monitor_combo = ttk.Combobox(self.controls_panel, values=["Todos os monitores"], state="disabled", width=28)
        self.monitor_combo.current(0)
        self.monitor_combo.grid(row=0, column=1, sticky="ew", ipady=5)
        self.monitor_combo.bind("<<ComboboxSelected>>", self._choose_monitor)
        tools = ttk.Frame(self.controls_panel, style="CardBody.TFrame"); tools.grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.fit_button = ttk.Button(tools, text="Ajustar imagem", style="Secondary.TButton", command=self._render_frame)
        self.pause_button = ttk.Button(tools, text="Pausar", style="Secondary.TButton", command=lambda: self._request_sharing(False))
        self.resume_button = ttk.Button(tools, text="Retomar", style="Primary.TButton", command=lambda: self._request_sharing(True))
        for button in (self.fit_button, self.pause_button, self.resume_button): button.pack(side="left", padx=(0, 8))
        ttk.Label(self.controls_panel, text="↓ Recebido  ·  ↑ Enviado  ·  Tráfego real da sessão, média de 3 s.\nNão mede a capacidade da internet.", style="CardMuted.TLabel", justify="left").grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.session_stage = ttk.Frame(page, style="App.TFrame")
        self.session_stage.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(self.session_stage, bg="#061F18", highlightthickness=0, takefocus=True)
        self.canvas.pack(fill="both", expand=True)
        self.image_item = self.canvas.create_image(0, 0, anchor="nw", state="hidden")
        self.placeholder_title = self.canvas.create_text(0, 0, text="Nenhuma sessão ativa", fill="#E1F4E8", font=(self.ui_font, 18, "bold"))
        self.placeholder_detail = self.canvas.create_text(0, 0, text="Abra Conectar para iniciar uma conexão autorizada.", fill="#A1C8B3", font=(self.ui_font, 11), justify="center")
        self.canvas.bind("<Configure>", self._resize)
        self.canvas.bind("<Motion>", self._mouse_move)
        for number in (1, 2, 3):
            self.canvas.bind(f"<ButtonPress-{number}>", self._mouse_down)
            self.canvas.bind(f"<ButtonRelease-{number}>", self._mouse_up)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda e: self._wheel(e, 1)); self.canvas.bind("<Button-5>", lambda e: self._wheel(e, -1))
        self.canvas.bind("<KeyPress>", self._key_down); self.canvas.bind("<KeyRelease>", self._key_up)
        self.canvas.bind("<FocusOut>", lambda _e: self._release_input())
        self.session_footer = ttk.Label(page, textvariable=self.screen_var, style="Muted.TLabel", wraplength=760, justify="left")
        self.session_footer.pack(side="bottom", fill="x", pady=(10, 0), before=self.session_stage)
        self.files_panel = ui.card(page, padding=12)
        actions = ttk.Frame(self.files_panel, style="CardBody.TFrame"); actions.pack(fill="x")
        self.send_button = ttk.Button(actions, text="Enviar arquivo", style="Secondary.TButton", command=self._send_file)
        self.receive_button = ttk.Button(actions, text="Receber arquivo", style="Secondary.TButton", command=self._receive_file)
        self.cancel_button = ttk.Button(actions, text="Cancelar", style="Danger.TButton", command=self._cancel_file)
        for button in (self.send_button, self.receive_button, self.cancel_button): button.pack(side="left", padx=(0, 8))
        self.progress = ttk.Progressbar(self.files_panel, variable=self.progress_var, maximum=100)
        self.progress.pack(fill="x", pady=(10, 5))
        self.file_label = ttk.Label(self.files_panel, textvariable=self.file_var, style="CardMuted.TLabel", wraplength=700, justify="left")
        self.file_label.pack(fill="x")
        self.fullscreen_actions = ttk.Frame(self.canvas, style="CardBody.TFrame", padding=4)
        self.fullscreen_hide = ttk.Button(self.fullscreen_actions, text="Ocultar comandos · F8", style="Secondary.TButton", command=self._toggle_fullscreen_commands)
        self.fullscreen_hide.pack(side="left", padx=(0, 6))
        self.fullscreen_exit = ttk.Button(self.fullscreen_actions, text="Sair · Esc", style="Secondary.TButton", command=lambda: self._toggle_fullscreen(False))
        self.fullscreen_exit.pack(side="left")
        self.fullscreen_traffic = tk.Label(self.canvas, textvariable=self.traffic_var, bg="#073B2B", fg="#E1F4E8", padx=10, pady=6, font=(self.ui_font, 10))
        self.fullscreen_hint = tk.Label(self.canvas, text="F8 mostra os comandos  ·  Esc sai da tela cheia", bg="#073B2B", fg="#E1F4E8", padx=10, pady=8, font=(self.ui_font, 10))
        page.bind("<Configure>", lambda e: (self.session_footer.configure(wraplength=max(100, e.width)), self.file_label.configure(wraplength=max(100, e.width - 24))))

    def _show_page(self, page):
        if not hasattr(self, "workspace"):
            return
        name = self.workspace.page_name(page)
        if name != "internet" and getattr(self, "mesh_panel", None) is not None:
            self.mesh_panel.hide_code()
        if name != "session" and self.fullscreen:
            self._toggle_fullscreen(False)
        if name != "connect":
            self._set_password_visible(False)
            if getattr(self, "_updating_computer_id", None) is not None:
                self._discard_code_update()
        self._release_input()
        self.selected_page = name
        self.workspace.show(name)
        for key, button in self.page_buttons.items():
            button.configure(style="SidebarSelected.TButton" if key == name else "Sidebar.TButton")
        self._update_controls()
        if name == "session":
            self._resize()
            candidate = stream_frame_id(self.current_screen or {})
            if (not self.current_image or getattr(self, "frame_stalled", False) or
                    candidate is not None and candidate > getattr(self, "last_stream_feedback_id", 0)):
                self._resume_stream(needed=True)

    def _toggle_controls(self, enabled=None):
        self._release_input()
        self.controls_visible = not self.controls_visible if enabled is None else bool(enabled)
        if self.controls_visible:
            self._toggle_files(False)
            self.controls_panel.pack(fill="x", pady=(0, 10), before=self.session_stage)
        else: self.controls_panel.pack_forget()
        self.controls_toggle_button.configure(text="Fechar controles" if self.controls_visible else "Controles")
        self._resize()

    def _toggle_files(self, enabled=None):
        self._release_input()
        self.files_visible = not self.files_visible if enabled is None else bool(enabled)
        if self.files_visible:
            if self.controls_visible: self._toggle_controls(False)
            self.files_panel.pack(side="bottom", fill="x", pady=(10, 0), before=self.session_stage)
        else: self.files_panel.pack_forget()
        self.files_toggle_button.configure(text="Fechar arquivos" if self.files_visible else "Arquivos")
        self._resize()

    def _credential_endpoint(self):
        try:
            number = int(self.port_var.get())
            return pin_key(normalize_target(self.address_var.get()), number) if 1 <= number <= 65535 else None
        except (ValueError, UnicodeError): return None

    def _credential_endpoint_changed(self, *_args):
        self._set_password_visible(False)
        credentials = getattr(self, "retry_credentials", None)
        if credentials and pin_key(credentials[0], credentials[1]) != self._credential_endpoint():
            if getattr(self, "retry_job", None) or getattr(self, "retry_attempting", False):
                self.disconnect()
            else:
                self._cancel_reconnect()
        if self.password_var.get() and self.password_endpoint != self._credential_endpoint():
            self.password_var.set("")
        if getattr(self, "mesh_pairing", None) is not None:
            from mesh_ui import pairing_address
            if self.address_var.get() != pairing_address(self.mesh_pairing):
                self.mesh_pairing = None
                self.mesh_target_name = ""
                self._set_endpoint_details(True)
        elif hasattr(self, "code_stage"):
            from mesh_ui import is_pairing_address
            address = self.address_var.get().strip()
            if address and not is_pairing_address(address):
                self._manual_connection = True
                self._editing_code = False
                self._update_connect_journey()
        self._update_address_hint()

    def _update_address_hint(self):
        if not hasattr(self, "network_hint_var"):
            return
        text = NETWORK_GUIDANCE
        try:
            address = normalize_target(self.address_var.get())
            try:
                local = ipaddress.ip_address(address).is_link_local
            except ValueError:
                local = False
            if local:
                text = ("Endereço 169.254 (APIPA): funciona somente no mesmo enlace. "
                        "Use o código privado para redes diferentes ou o IP de Wi-Fi/Ethernet para a mesma rede.")
            elif getattr(self, "mesh_pairing", None) is not None:
                text = "Código privado selecionado. A rede será preparada ao conectar. O certificado incluído no código é conferido antes do envio da senha."
        except ValueError as error:
            if self.address_var.get().strip():
                text = str(error)
        self.network_hint_var.set(text)

    def _credential_password_changed(self, *_args):
        self.password_endpoint = self._credential_endpoint() if self.password_var.get() else None
        if not self.password_var.get():
            self._set_password_visible(False)

    def _set_password_visible(self, visible):
        """Change only the mask; the password stays in this window's memory."""
        self.password_visible = bool(visible)
        if hasattr(self, "password_entry"):
            self.password_entry.configure(show="" if self.password_visible else "*")
        if hasattr(self, "password_toggle_button"):
            self.password_toggle_button.configure(text="Ocultar senha" if self.password_visible else "Mostrar senha")

    def _toggle_password_visibility(self):
        if self.connecting or (self.peer and not self.peer.closed):
            return
        self._set_password_visible(not getattr(self, "password_visible", False))
        self.password_entry.focus_set()

    def _build_computers(self, page):
        import desktop_ui as ui
        ui.page_brand(page, f"CONTROLADOR · {VERSION}").pack(fill="x", pady=(0, 17))
        header = ttk.Frame(page, style="App.TFrame"); header.pack(fill="x", pady=(0, 8))
        heading = ttk.Label(header, text="Seus computadores", style="Title.TLabel")
        heading.pack(side="left", fill="x", expand=True)
        self.computer_count_var = tk.StringVar(value="0 salvos")
        ttk.Label(header, textvariable=self.computer_count_var, style="Count.TLabel").pack(side="right", padx=(12, 0))
        subtitle = ttk.Label(page, text="Seus acessos recentes ficam aqui. Dê nomes fáceis de reconhecer aos computadores.", style="Muted.TLabel", justify="left")
        subtitle.pack(anchor="w", fill="x", pady=(0, 20))
        self.reconnect_panel = ui.card(page, padding=12)
        self.reconnect_button = ttk.Button(self.reconnect_panel, text="Conectar novamente", style="Secondary.TButton", command=self._prepare_reconnect)
        self.reconnect_button.pack(side="right", padx=(12, 0))
        reconnect_caption = ttk.Label(self.reconnect_panel, text="Conexão encerrada. Use a senha atual para tentar novamente.", style="CardMuted.TLabel", justify="left")
        reconnect_caption.pack(side="left", fill="x", expand=True)
        body = ui.card(page, padding=16); body.pack(fill="both", expand=True)
        self.computers_card = body
        actions = ttk.Frame(body, style="CardBody.TFrame"); actions.pack(fill="x", pady=(0, 14))
        self.add_computer_button = ttk.Button(actions, text="Adicionar", style="Primary.TButton", command=self._add_computer)
        self.refresh_computers_button = ttk.Button(actions, text="Atualizar", style="Secondary.TButton", command=lambda: self._refresh_computers(force=True))
        self.add_computer_button.pack(side="left")
        self.refresh_computers_button.pack(side="right")
        ttk.Label(body, text="Buscar por nome, endereço ou porta", style="Card.TLabel").pack(anchor="w", pady=(0, 6))
        search = ttk.Frame(body, style="CardBody.TFrame"); search.pack(fill="x", pady=(0, 14))
        self.search_entry = ttk.Entry(search, textvariable=self.search_var)
        self.search_entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.clear_search_button = ttk.Button(search, text="Limpar", style="Secondary.TButton", command=lambda: self.search_var.set(""), state="disabled")
        self.clear_search_button.pack(side="right", padx=(10, 0))
        self.search_var.trace_add("write", lambda *_args: self._filter_computers())
        selection_actions = ttk.Frame(body, style="CardBody.TFrame")
        selection_actions.pack(side="bottom", fill="x", pady=(14, 0))
        self.edit_computer_button = ttk.Button(selection_actions, text="Editar", style="Secondary.TButton", command=self._edit_computer)
        self.edit_computer_button.pack(side="left", padx=(0, 8))
        self.delete_computer_button = ttk.Button(selection_actions, text="Excluir", style="Secondary.TButton", command=self._delete_computer)
        self.delete_computer_button.pack(side="left")
        self.update_code_button = ttk.Button(selection_actions, text="Atualizar código", style="Secondary.TButton", command=self._update_computer_code)
        self.update_code_button.pack(side="left", padx=(8, 0))
        self.connect_computer_button = ttk.Button(selection_actions, text="Conectar", style="Primary.TButton", command=self._connect_computer)
        self.connect_computer_button.pack(side="right")
        table = ttk.Frame(body, style="CardBody.TFrame"); table.pack(fill="both", expand=True)
        self.computers_table = table
        columns = ("name", "address", "port", "status", "lastseen")
        self.computers_tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse")
        tree_style = ttk.Style(self.root)
        heading_font = tkfont.Font(root=self.root, font=tree_style.lookup("Treeview.Heading", "font"))
        row_font = tkfont.Font(root=self.root, font=tree_style.lookup("Treeview", "font"))
        density = max(1.0, float(self.root.tk.call("tk", "scaling")) / (96 / 72))
        samples = {"name": "Escritório", "address": "192.168.100.100", "port": "65535", "status": "Online · conectado", "lastseen": "31/12/2026 23:59"}
        for key, label, width in (("name", "Nome", 155), ("address", "Endereço", 190), ("port", "Porta", 62), ("status", "Status", 145), ("lastseen", "Última vez online", 150)):
            minimum = max(55, heading_font.measure(label) + 24, row_font.measure(samples[key]) + 20)
            self.computers_tree.heading(key, text=label)
            self.computers_tree.column(key, width=max(minimum, round(width * density)), minwidth=minimum, stretch=key in ("name", "address"))
        for state, color in (("online", "#187B55"), ("offline", "#607089"), ("unverified", "#A54B10"), ("checking", MUTED)):
            self.computers_tree.tag_configure(state, foreground=color)
        vertical = ttk.Scrollbar(table, orient="vertical", command=self.computers_tree.yview)
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.computers_tree.xview)
        self.computers_tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.computers_tree.grid(row=0, column=0, sticky="nsew"); vertical.grid(row=0, column=1, sticky="ns"); horizontal.grid(row=1, column=0, sticky="ew")
        table.columnconfigure(0, weight=1); table.rowconfigure(0, weight=1)
        self.computers_tree.bind("<<TreeviewSelect>>", lambda _event: self._computer_selection())
        self.computers_tree.bind("<Double-1>", lambda _event: self._connect_computer()); self.computers_tree.bind("<Return>", lambda _event: self._connect_computer())
        self.empty_state = tk.Frame(table, bg="#FFFFFF")
        self.empty_title = tk.Label(self.empty_state, text="Seu primeiro computador", bg="#FFFFFF", fg=TEXT, justify="center", font=(self.ui_font, 16, "bold"))
        self.empty_title.pack(pady=(0, 8))
        self.empty_detail = tk.Label(self.empty_state, text="Faça sua primeira conexão com o código do Hospedeiro.\nO computador será salvo aqui com um nome.", bg="#FFFFFF", fg=MUTED, justify="center", font=(self.ui_font, 10))
        self.empty_detail.pack(pady=(0, 14))
        ttk.Button(self.empty_state, text="Conectar com código", style="Secondary.TButton", command=self._open_mesh_setup).pack()
        notice = ttk.Label(page, textvariable=self.computer_notice_var, style="Muted.TLabel", wraplength=800, justify="left")
        notice.pack(fill="x", pady=(12, 0))
        page.bind("<Configure>", lambda e: (notice.configure(wraplength=max(100, e.width)), subtitle.configure(wraplength=max(100, e.width)), heading.configure(wraplength=max(160, e.width - 120)), reconnect_caption.configure(wraplength=max(100, e.width - self.reconnect_button.winfo_reqwidth() - 60))))
        table.bind("<Configure>", lambda e: (self.empty_title.configure(wraplength=max(120, e.width - 40)), self.empty_detail.configure(wraplength=max(120, e.width - 40))))

    def _filter_computers(self):
        if not hasattr(self, "search_var"):
            return
        query = self.search_var.get().casefold().strip()
        if hasattr(self, "clear_search_button"):
            self.clear_search_button.configure(state="normal" if self.search_var.get() else "disabled")
        shown = 0
        for identity, entry in self.computer_entries.items():
            if not self.computers_tree.exists(identity): continue
            if query and query not in f"{entry['name']} {entry['address']} {entry['port']}".casefold():
                self.computers_tree.detach(identity)
            else:
                self.computers_tree.move(identity, "", "end"); shown += 1
        if shown:
            self.empty_state.place_forget()
        else:
            self.empty_title.configure(text="Nenhum resultado" if query else "Seu primeiro computador")
            self.empty_detail.configure(text="Tente outro nome ou endereço." if query else "Faça sua primeira conexão com o código do Hospedeiro.\nO computador será salvo aqui com um nome.")
            self.empty_state.place(relx=0.5, rely=0.55, anchor="center", relwidth=0.94)
        if hasattr(self, "computer_count_var"):
            total = len(self.computer_entries)
            self.computer_count_var.set(f"{shown} de {total}" if query else f"{total} {'salvo' if total == 1 else 'salvos'}")
        self._update_computer_controls()

    def _computer_error(self, error, loading=False, parent=None):
        text = f"Não foi possível {'abrir' if loading else 'salvar'} a lista de computadores: {error}"
        if loading:
            text += (f"\n\nA lista original foi preservada. Restaure o arquivo {self.settings.path.with_name('computers.json')} "
                     "ou renomeie-o e reabra o Controlador. Você pode usar a conexão manual enquanto isso.")
        self.computer_notice_var.set(text.split("\n", 1)[0])
        messagebox.showerror("Lista de computadores", text, parent=parent or self.root)

    @staticmethod
    def _computer_revision(entry):
        return (entry.get("name"), entry.get("address"), entry.get("port"))

    @staticmethod
    def _last_seen_text(value):
        if not value:
            return "Nunca"
        try:
            date = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else datetime.fromtimestamp(value)
            return date.astimezone().strftime("%d/%m/%Y %H:%M")
        except (ValueError, TypeError, OverflowError):
            return str(value)[:30]

    def _refresh_computer_rows(self):
        if self.book is None:
            self._update_computer_controls()
            return
        try:
            entries = {entry["id"]: entry for entry in self.book.entries()}
        except (ValueError, OSError) as error:
            self.book = None
            self.book_error = str(error)
            self._update_computer_controls()
            self.root.after_idle(lambda: self._computer_error(self.book_error, loading=True))
            return
        for identity in tuple(self.computer_entries):
            if identity not in entries:
                self.computers_tree.delete(identity)
                self.computer_revisions.pop(identity, None)
                self.computer_presence.pop(identity, None)
                self.seen_persisted.pop(identity, None)
                self.presence_refresh_requested.discard(identity)
        for identity, entry in entries.items():
            previous = self.computer_entries.get(identity)
            if previous is None or self._computer_revision(previous) != self._computer_revision(entry):
                self.computer_revisions[identity] = self.computer_revisions.get(identity, 0) + 1
                self.computer_presence.pop(identity, None)
            result = self.computer_presence.get(identity, {})
            state = result.get("state", "checking")
            active = bool(self.peer and not self.peer.closed and self.connected_endpoint == pin_key(entry["address"], entry["port"]))
            label = "Online · conectado" if active else "Online · ocupado" if state == "online" and result.get("busy") else {
                "online": "Online", "offline": "Offline", "unverified": "Não verificado"}.get(state, "Verificando…" if identity in self.presence_pending else "Aguardando consulta")
            tag = "online" if active else state
            from mesh_ui import is_pairing_address
            shown_address = f"Internet · {entry['address'][5:13]}" if is_pairing_address(entry["address"]) else entry["address"]
            values = (entry["name"], shown_address, str(entry["port"]), label, self._last_seen_text(entry.get("last_seen")))
            if self.computers_tree.exists(identity):
                self.computers_tree.item(identity, values=values, tags=(tag,))
            else:
                self.computers_tree.insert("", "end", iid=identity, values=values, tags=(tag,))
        self.computer_entries = entries
        self._filter_computers()
        self._update_computer_controls()

    def _selected_computer(self):
        selection = self.computers_tree.selection()
        entry = self.computer_entries.get(selection[0]) if selection else None
        if entry and hasattr(self, "search_var"):
            query = self.search_var.get().casefold().strip()
            if query and query not in f"{entry['name']} {entry['address']} {entry['port']}".casefold(): return None
        return entry

    def _computer_selection(self):
        entry = self._selected_computer()
        if entry:
            result = self.computer_presence.get(entry["id"], {})
            self.computer_notice_var.set(result.get("detail") or "Online indica o programa Hospedeiro acessível. A conexão ainda pede senha e conferência do certificado.")
        self._update_computer_controls()

    def _update_computer_controls(self):
        if not hasattr(self, "computers_tree"):
            return
        selected = self._selected_computer() is not None
        available = self.book is not None
        self.add_computer_button.configure(state="normal" if available and len(self.computer_entries) < 256 else "disabled")
        for button in (self.edit_computer_button, self.delete_computer_button):
            button.configure(state="normal" if available and selected else "disabled")
        self.refresh_computers_button.configure(state="normal" if available and self.computer_entries else "disabled")
        self.connect_computer_button.configure(state="normal" if selected and (getattr(self, "session_manager", None) is not None or not self._mesh_endpoint_busy()) else "disabled")
        if hasattr(self, "update_code_button"):
            from mesh_ui import is_pairing_address
            entry = self._selected_computer()
            self.update_code_button.configure(state="normal" if available and entry and is_pairing_address(entry["address"]) else "disabled")
            self.connect_computer_button.configure(text="Reconectar" if entry and entry.get("last_seen") else "Conectar")

    def _show_computers(self):
        self._release_input()
        self.workspace.select(self.computers_page)

    def _set_reconnect_available(self, available):
        self.reconnect_available = bool(available)
        if not hasattr(self, "reconnect_panel"):
            return
        if self.reconnect_available:
            self.reconnect_panel.pack(fill="x", pady=(0, 12), before=self.computers_card)
        else:
            self.reconnect_panel.pack_forget()

    def _prepare_reconnect(self):
        if self.peer or self.connecting:
            return
        self.password_var.set("")
        self._set_password_visible(False)
        self._set_reconnect_available(False)
        if getattr(self, "book", None) is not None:
            try:
                saved_id = getattr(self, "_saved_computer_id", None)
                entry = self.book.get(saved_id) if saved_id else self.book.find_endpoint(self.address_var.get(), int(self.port_var.get()))
            except (ValueError, OSError):
                entry = None
            if entry is not None:
                return self._prepare_computer_connection(entry)
        self._show_page("connect")
        if hasattr(self, "password_entry"):
            self.password_entry.focus_set()

    def _add_computer(self):
        self._computer_editor()

    def _edit_computer(self):
        entry = self._selected_computer()
        if entry:
            self._computer_editor(entry)

    def _update_computer_code(self, entry=None):
        entry = entry or self._selected_computer()
        if not entry:
            return
        if self._mesh_endpoint_busy():
            manager = getattr(self, "session_manager", None)
            if manager is not None:
                window = manager.new_window(parent=self.root)
                if window is not None:
                    window._update_computer_code(entry)
            return
        if self.book is not None:
            try:
                entry = self.book.get(entry["id"])
            except (ValueError, OSError) as error:
                return self._computer_error(error)
        self._open_mesh_setup()
        self._updating_computer_id = entry["id"]
        self.mesh_panel.code_var.set("")
        self.mesh_panel.saved_name_var.set(entry["name"])
        self.mesh_panel.remember_var.set(True)
        self.mesh_panel.status_var.set(f"Cole o novo código ou convite de {entry['name']}. Ao continuar, o código será atualizado mantendo este computador salvo. Cancelar ou voltar preserva o acesso anterior.")
        self.mesh_panel.code_entry.focus_set()
        self._update_controls()

    def _discard_code_update(self):
        self._updating_computer_id = None
        self.mesh_panel.code_var.set("")
        self.mesh_panel.saved_name_var.set("")
        self.mesh_panel.remember_var.set(False)
        self.mesh_panel.status_var.set("Atualização cancelada. O código anterior e o computador salvo foram preservados.")

    def _cancel_connection_action(self):
        if getattr(self, "_updating_computer_id", None) is not None and not self._mesh_endpoint_busy():
            self._discard_code_update()
            self._show_computers()
            self._update_controls()
            return
        self.disconnect()

    def _computer_editor(self, entry=None):
        if self.book is None or self.computer_dialog is not None:
            return
        self._release_input()
        self.dialog_active = True
        self._update_controls()
        dialog = tk.Toplevel(self.root)
        self.computer_dialog = dialog
        dialog.title("Editar computador" if entry else "Adicionar computador")
        dialog.transient(self.root); dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=18); body.pack(fill="both", expand=True)
        variables = {}
        for row, (key, title, value) in enumerate((("name", "Nome para reconhecer o computador", entry["name"] if entry else ""),
                                                  ("address", "IP ou nome do hospedeiro", entry["address"] if entry else ""),
                                                  ("port", "Porta", str(entry["port"] if entry else DEFAULT_PORT)))):
            ttk.Label(body, text=title).grid(row=row * 2, column=0, sticky="w", pady=(0, 3))
            variables[key] = tk.StringVar(value=value)
            field = ttk.Entry(body, textvariable=variables[key], width=46)
            field.grid(row=row * 2 + 1, column=0, sticky="ew", pady=(0, 10))
            from mesh_ui import is_pairing_address
            if entry and is_pairing_address(entry["address"]) and key in ("address", "port"):
                field.configure(state="readonly")
            if key == "name": field.focus_set()
        def dismiss():
            if self.computer_dialog is dialog:
                self.computer_dialog = None
            self.dialog_active = False
            dialog.destroy()
            self._update_controls()
        def save():
            if self.book is None:
                self._computer_error(self.book_error or "O cadastro está indisponível.", loading=True, parent=dialog)
                return
            try:
                port = int(variables["port"].get())
                record = self.book.update(entry["id"], variables["name"].get(), variables["address"].get(), port) if entry else self.book.add(variables["name"].get(), variables["address"].get(), port)
            except (ValueError, OSError) as error:
                self._computer_error(error, parent=dialog)
                return
            dismiss()
            self._refresh_computer_rows()
            self.computers_tree.selection_set(record["id"]); self.computers_tree.see(record["id"])
            self._refresh_computers(force=True)
        buttons = ttk.Frame(body); buttons.grid(row=6, column=0, sticky="e")
        ttk.Button(buttons, text="Cancelar", command=dismiss).pack(side="left", padx=(0, 8))
        save_button = ttk.Button(buttons, text="Salvar", command=save); save_button.pack(side="left")
        dialog.book_fields = variables
        dialog.save_button = save_button
        dialog.bind("<Return>", lambda _event: save())
        dialog.bind("<Escape>", lambda _event: dismiss())
        dialog.protocol("WM_DELETE_WINDOW", dismiss)
        dialog.grab_set()

    def _delete_computer(self):
        entry = self._selected_computer()
        if not entry:
            return
        self._release_input()
        if not messagebox.askyesno("Excluir computador", f"Excluir {entry['name']} da lista?\nA conexão e os certificados não serão apagados.", default="no", parent=self.root):
            return
        try:
            self.book.remove(entry["id"])
        except (ValueError, OSError) as error:
            self._computer_error(error)
            return
        self._refresh_computer_rows()

    def _connect_computer(self):
        manager = getattr(self, "session_manager", None)
        if self._mesh_endpoint_busy() and manager is None:
            return
        entry = self._selected_computer()
        if not entry:
            return
        if self.book is not None and hasattr(self.book, "get"):
            try:
                entry = self.book.get(entry["id"])
            except (ValueError, OSError) as error:
                return self._computer_error(error)
        if manager is not None:
            existing = manager.find_endpoint(entry["address"], entry["port"])
            if existing is not None:
                manager.focus_session(existing)
                return
            if self._mesh_endpoint_busy():
                self._release_input()
                return manager.new_window(entry=entry, parent=self.root)
        prepared = self._prepare_computer_connection(entry)
        if prepared and getattr(self, "password_endpoint", None) == self._credential_endpoint() and 8 <= len(self.password_var.get()) <= 256:
            self.connect()

    def _prepare_computer_connection(self, entry):
        from mesh_ui import is_pairing_address, pairing_address
        self._updating_computer_id = None
        if self.book is not None:
            try:
                entry = self.book.get(entry["id"])
            except (ValueError, OSError) as error:
                self._computer_error(error)
                return False
        self._saved_computer_id = entry["id"]
        if is_pairing_address(entry["address"]):
            import mesh
            current = getattr(self, "mesh_pairing", None)
            vault_error = False
            try:
                remembered = self.mesh_panel.load_remembered(entry["address"])
            except (OSError, ValueError, mesh.MeshError):
                remembered = None
                vault_error = True
            pairing = remembered or (current if current is not None and pairing_address(current) == entry["address"] else None)
            if vault_error or pairing is None or pairing.port != entry["port"]:
                self.mesh_panel.code_var.set("")
                self.mesh_panel.remember_var.set(True)
                self._open_mesh_setup()
                self._updating_computer_id = entry["id"]
                self.mesh_panel.saved_name_var.set(entry["name"])
                self.mesh_panel.status_var.set(("Não foi possível abrir ou confirmar o cofre deste usuário. " if vault_error else "O código não está salvo neste usuário. ") + f"Cole um código atualizado de {entry['name']} e mantenha Salvar marcado para recuperar este acesso sem apagar o computador.")
                self.mesh_panel.code_entry.focus_set()
                return False
            self.mesh_panel.saved_name_var.set(entry["name"])
            return self.mesh_panel.select_pairing(pairing, remembered=remembered is not None)
        self._use_lan()
        previous = self._credential_endpoint()
        endpoint = pin_key(entry["address"], entry["port"])
        if previous != endpoint:
            self._set_password_visible(False)
            self.password_var.set("")
        self.address_var.set(entry["address"])
        self.port_var.set(str(entry["port"]))
        if hasattr(self, "password_entry"):
            self.password_entry.focus_set()
        elif hasattr(self, "connection_entries"):
            self.connection_entries[2].focus_set()
        return True

    def _presence_tick(self):
        self.presence_job = None
        if self.stopping.is_set():
            return
        self._refresh_computers()
        self.presence_job = self.root.after(10_000, self._presence_tick)

    def _invalidate_presence_endpoint(self, endpoint):
        if not endpoint:
            return
        for identity, entry in getattr(self, "computer_entries", {}).items():
            if pin_key(entry["address"], entry["port"]) == endpoint:
                self.computer_revisions[identity] = self.computer_revisions.get(identity, 0) + 1
                self.computer_presence.pop(identity, None)
                if identity in self.presence_pending:
                    self.presence_refresh_requested.add(identity)

    def _refresh_computers(self, force=False, identities=None):
        if self.book is None or self.stopping.is_set():
            return
        self._refresh_computer_rows()
        if self.book is None:
            return
        now = time.monotonic()
        for identity, entry in self.computer_entries.items():
            if identities is not None and identity not in identities:
                continue
            endpoint = pin_key(entry["address"], entry["port"])
            if self.peer and not self.peer.closed and endpoint == self.connected_endpoint:
                self.computer_presence[identity] = {"state": "online", "detail": "Sessão ativa neste Controlador.", "checked_at": now, "source": "session"}
                self._mark_computer_seen(identity)
                self.presence_refresh_requested.discard(identity)
                continue
            if identity in self.presence_pending:
                if force:
                    self.presence_refresh_requested.add(identity)
                continue
            cached = self.computer_presence.get(identity)
            if not force and cached and now - cached.get("checked_at", 0) < 10:
                continue
            pin = self.settings.data["pins"].get(endpoint)
            target_address, target_port = entry["address"], entry["port"]
            from mesh_ui import is_pairing_address, pairing_address
            if is_pairing_address(target_address):
                pairing = getattr(self, "mesh_pairing", None)
                snapshot = self.mesh_panel.manager.snapshot()
                if (pairing is None or pairing_address(pairing) != target_address or
                        pairing.port != target_port or snapshot.state != "ready" or not snapshot.endpoint):
                    self.computer_presence[identity] = {"state": "unverified", "detail": "Disponibilidade pela internet ainda não consultada. Selecione este computador e use seu código privado para preparar a rede.", "checked_at": now}
                    continue
                target_address, target_port = snapshot.endpoint
                pin = pairing.fingerprint
            token = (identity, self.computer_revisions[identity], endpoint, self.settings.data["pins"].get(endpoint))
            try:
                self.presence_tasks.put_nowait((token, target_address, target_port, pin))
                self.presence_pending[identity] = token
            except queue.Full:
                break
        self._refresh_computer_rows()

    def _presence_worker(self):
        while not self.stopping.is_set():
            try:
                token, address, port, pin = self.presence_tasks.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                if self.stopping.is_set():
                    return
                if self.probe is None:
                    import presence
                    result = presence.probe(address, port, expected_fingerprint=pin, timeout=3)
                else:
                    result = self.probe(address, port, expected_fingerprint=pin, timeout=3)
                if not isinstance(result, dict) or result.get("state") not in ("online", "offline", "unverified"):
                    raise ValueError("Resposta de disponibilidade inválida.")
            except Exception as error:
                result = {"state": "offline", "detail": str(error)[:500] or "Hospedeiro indisponível."}
            finally:
                self.presence_tasks.task_done()
            self._post(lambda token=token, result=result: self._presence_result(token, result), -1)

    def _presence_result(self, token, result):
        identity, revision, endpoint, pin = token
        refresh_requested = identity in self.presence_refresh_requested
        self.presence_refresh_requested.discard(identity)
        if self.presence_pending.get(identity) == token:
            self.presence_pending.pop(identity, None)
        self._refresh_computer_rows()
        if self.book is None:
            return
        entry = self.computer_entries.get(identity)
        if entry is None:
            return
        if (revision != self.computer_revisions.get(identity) or endpoint != pin_key(entry["address"], entry["port"]) or
                pin != self.settings.data["pins"].get(endpoint)):
            self._refresh_computers()
            return
        if self.peer and not self.peer.closed and endpoint == self.connected_endpoint:
            return
        result = dict(result, checked_at=time.monotonic())
        self.computer_presence[identity] = result
        if result["state"] == "online":
            self._mark_computer_seen(identity)
        self._refresh_computer_rows()
        if self._selected_computer() and self._selected_computer()["id"] == identity:
            self._computer_selection()
        if refresh_requested:
            self._refresh_computers(force=True, identities={identity})

    def _mark_computer_seen(self, identity, force=False):
        if not force and time.monotonic() - self.seen_persisted.get(identity, -60) < 60:
            return
        try:
            self.book.mark_seen(identity)
            self.seen_persisted[identity] = time.monotonic()
        except (ValueError, OSError) as error:
            self.computer_notice_var.set(f"Disponibilidade atualizada, mas a última vez online não foi salva: {error}")

    def _remember_connected_computer(self, peer):
        if self.book is None:
            return
        address, port = self.connecting_endpoint or (self.address_var.get(), int(self.port_var.get()))
        self.connected_endpoint = pin_key(address, port)
        try:
            saved_id = getattr(self, "_saved_computer_id", None)
            entry = self.book.get(saved_id) if saved_id else self.book.find_endpoint(address, port)
            if entry is None:
                session_names = [m.get("computerName") for m in self.buffered_messages if m.get("type") == "state"]
                name = str(next((name for name in reversed(session_names) if isinstance(name, str) and name.strip()),
                                getattr(peer, "computer_name", "") or address)).strip()[:80] or address
                entry = self.book.add(name, address, port)
            self._saved_computer_id = entry["id"]
            self._mark_computer_seen(entry["id"], force=True)
            self._refresh_computer_rows()
            self.computer_presence[entry["id"]] = {"state": "online", "detail": "Sessão ativa neste Controlador.", "checked_at": time.monotonic(), "source": "session"}
            self._refresh_computer_rows()
        except (ValueError, OSError) as error:
            self.computer_notice_var.set(f"Conectado, mas o computador não foi salvo na lista: {error}")

    def _post(self, function, generation=None):
        if not self.stopping.is_set():
            self.messages.put((self.generation if generation is None else generation, function))

    def _post_latest(self, key, function, generation=None):
        """At most one queued UI wakeup for a replaceable status update."""
        if self.stopping.is_set():
            return
        generation = self.generation if generation is None else generation
        identity = (generation, key)
        with self.latest_lock:
            self.latest_callbacks[identity] = function
            if identity in self.queued_latest:
                return
            self.queued_latest.add(identity)
        def apply():
            with self.latest_lock:
                callback = self.latest_callbacks.pop(identity, None)
                self.queued_latest.discard(identity)
            if callback:
                callback()
        self._post(apply, generation)

    def _clear_latest(self):
        if hasattr(self, "latest_lock"):
            with self.latest_lock:
                self.latest_callbacks.clear()
                self.queued_latest.clear()
                self.pending_states.clear()

    def _pump(self):
        self.pump_job = None
        if self.stopping.is_set():
            return
        try:
            deadline = time.monotonic() + .008
            for _ in range(128):
                try:
                    generation, callback = self.messages.get_nowait()
                except queue.Empty:
                    break
                if generation in (-1, self.generation):
                    try:
                        callback()
                    except Exception as error:
                        self.connection_var.set(f"Não foi possível atualizar uma informação: {str(error)[:300]}")
                if self.stopping.is_set() or time.monotonic() >= deadline:
                    break
            if self.stopping.is_set():
                return
            with self.frame_lock:
                decoded, self.decoded_frame = self.decoded_frame, None
            if decoded:
                generation, epoch, message, image = decoded
                if (generation == self.generation and epoch == self.frame_epoch and self.peer and self.sharing and
                        not getattr(self, "network_suspended", False) and not self.local_paused and
                        not self.remote_prompt_active and getattr(self, "capture_available", True)):
                    try:
                        self._show_frame(message, image)
                    except Exception as error:
                        image.close()
                        self._frame_failed(str(error))
                elif generation == self.generation and epoch == self.frame_epoch and self.connecting:
                    # The reader can deliver the first frame before connect() publishes Peer.
                    with self.frame_lock:
                        if self.decoded_frame is None:
                            self.decoded_frame = decoded
                        else:
                            image.close()
                else:
                    image.close()
            self._check_frame_health()
        except Exception as error:
            if not self.stopping.is_set():
                try:
                    self.screen_var.set(f"Aguardando atualização da interface: {str(error)[:240]}")
                except tk.TclError:
                    self.stopping.set()
        finally:
            if not self.stopping.is_set():
                try:
                    # Keep idle polling inexpensive, but do not add a whole
                    # 30 ms display interval to every interactive frame.
                    active = self.peer and not self.peer.closed and self.sharing
                    delay = ACTIVE_PUMP_MS if active else IDLE_PUMP_MS
                    self.pump_job = self.root.after(1 if not self.messages.empty() else delay, self._pump)
                except tk.TclError:
                    self.stopping.set()

    def connect(self):
        if self.peer or self.connecting or getattr(self, "retry_job", None):
            return
        if hasattr(self, "code_stage") and (getattr(self, "_editing_code", False) or
                (self.mesh_pairing is None and not getattr(self, "_manual_connection", False))):
            # The first step only selects the identity. It cannot start a
            # network session or reuse a password before the user continues.
            return self.mesh_panel.select_code()
        try:
            address = normalize_target(self.address_var.get())
        except ValueError as error:
            return messagebox.showerror("Endereço inválido", str(error), parent=self.root)
        try:
            port = int(self.port_var.get())
            if not 1 <= port <= 65535:
                raise ValueError()
        except ValueError:
            return messagebox.showerror("Porta inválida", "Informe uma porta entre 1 e 65535.", parent=self.root)
        from mesh_ui import is_pairing_address
        if is_pairing_address(address) and self.mesh_pairing is None:
            self._open_mesh_setup()
            return messagebox.showinfo("Código privado necessário", "Selecione ou cole o código privado deste Host antes de conectar pela internet.", parent=self.root)
        password = self.password_var.get()
        if not 8 <= len(password) <= 256:
            return messagebox.showerror("Senha necessária", "Informe a senha do hospedeiro, com 8 a 256 caracteres.", parent=self.root)
        name = self.name_var.get().strip()[:80] or socket.gethostname()
        self._cancel_reconnect()
        if self._auto_reconnect_enabled():
            self.retry_credentials = (address, port, password, name)
        self._begin_connection(address, port, password, name)

    def _begin_connection(self, address, port, password, name, automatic=False):
        """A fresh authenticated connection; previous controls/files are never replayed."""
        if self.peer or self.connecting or self.stopping.is_set():
            return
        self._set_password_visible(False)
        self._set_reconnect_available(False)
        self.generation += 1
        generation = self.generation
        self.connecting = True
        self.retry_attempting = automatic
        attempt_cancel = self.connect_cancel = threading.Event()
        deadline = self.retry_deadline if automatic else None
        self.connecting_endpoint = (address, port)
        if getattr(self, "book", None) is not None:
            try:
                saved_entry = self.book.find_endpoint(address, port)
                self._saved_computer_id = saved_entry["id"] if saved_entry else None
            except (ValueError, OSError):
                self._saved_computer_id = None
        pairing = getattr(self, "mesh_pairing", None)
        mesh_manager = self.mesh_panel.manager if pairing is not None else None
        self.buffered_messages = []
        self.buffered_bytes = 0
        self.buffer_overflow_generation = None
        self._clear_latest()
        self.network_suspended = False
        self.capture_available = True
        self.frame_stalled = False
        self.frame_wait_started = time.monotonic()
        self.last_frame_at = 0.0
        self.send_failed_generation = None
        self.connection_var.set(f"{'Reconectando' if automatic else 'Conectando'} a {address}:{port}…")
        self._placeholder("Conectando ao hospedeiro", "Confira a impressão digital do certificado antes de autorizar a conexão.")
        self._show_page("session")
        self.settings.data.update(address=address, port=port, name=name)
        try:
            self.settings.save()
        except OSError as error:
            self.file_var.set(f"Não foi possível salvar as preferências: {error}")
        self._update_controls()

        def worker():
            try:
                import net
                target_address, target_port = address, port
                if pairing is not None:
                    from mesh_ui import pairing_address
                    if pairing_address(pairing) != address or pairing.port != port:
                        raise ValueError("O código privado não corresponde ao computador selecionado.")
                    with self.mesh_start_lock:
                        if attempt_cancel.is_set() or self.stopping.is_set():
                            raise InterruptedError("Conexão cancelada.")
                        mesh_manager.start(pairing)
                    timeout = 50 if deadline is None else max(0.1, min(50, deadline - time.monotonic()))
                    target_address, target_port = mesh_manager.wait_ready(timeout=timeout, cancel_event=attempt_cancel)
                peer = net.connect(target_address, target_port, password, name,
                    on_message=lambda message: self._network_message(message, generation),
                    on_close=lambda reason="Conexão encerrada": self._post(lambda: self._connection_closed(reason), generation),
                    trust_callback=lambda host, remote_port, pin: self._trust_pairing(address, port, pin, generation, pairing),
                    cancel_event=attempt_cancel, deadline=deadline)
                # Publishing also closes a completed connection whose user
                # cancelled between worker return and the next UI pump.
                self._post(lambda: self._publish_connection(peer, generation), -1)
                if generation != self.generation or self.stopping.is_set():
                    peer.close()
            except Exception as error:
                text = str(error)[:500] if pairing is not None else connection_error_hint(error, address)
                # Initial failures need a deliberate new attempt. Once a real
                # session existed, a network failure in a retry can use the
                # remainder of the same bounded recovery window.
                transport_error = isinstance(error, OSError) and not isinstance(error, (PermissionError, ssl.SSLError, InterruptedError))
                retryable = automatic and transport_error and not attempt_cancel.is_set()
                self._post(lambda text=text, retryable=retryable: self._connection_closed(text, retryable=retryable), generation)
        threading.Thread(target=worker, name="MiniAnyDesk-connect", daemon=True).start()

    def _auto_reconnect_enabled(self):
        value = getattr(self, "auto_reconnect_var", None)
        return bool(value is not None and value.get())

    def _cancel_reconnect(self):
        active, self.connect_cancel = getattr(self, "connect_cancel", None), None
        if active:
            active.set()
        for attribute in ("retry_job", "retry_expiry_job"):
            identity = getattr(self, attribute, None)
            setattr(self, attribute, None)
            if identity:
                try:
                    self.root.after_cancel(identity)
                except tk.TclError:
                    pass
        self.retry_deadline = None
        self.retry_attempt = 0
        self.retry_attempting = False
        self.retry_credentials = None
        self.retry_token = None

    def _auto_reconnect_changed(self):
        if self._auto_reconnect_enabled():
            # Enabling after connection cannot recover an erased password.
            # The next manually authenticated session can retain it in RAM.
            return
        pending = bool(getattr(self, "retry_job", None) or getattr(self, "retry_attempting", False))
        self._cancel_reconnect()
        if pending:
            self.disconnect()
        else:
            self.password_var.set("")
            self._set_password_visible(False)

    def _schedule_reconnect(self, reason):
        credentials = getattr(self, "retry_credentials", None)
        if (not self._auto_reconnect_enabled() or not credentials or self.stopping.is_set() or
                getattr(self, "manual_disconnect", False)):
            return False
        if pin_key(credentials[0], credentials[1]) != self._credential_endpoint():
            self._cancel_reconnect()
            return False
        if getattr(self, "retry_job", None):
            return True
        now = time.monotonic()
        if self.retry_deadline is None:
            self.retry_deadline = now + RECONNECT_WINDOW
            token = self.retry_token = object()
            self.retry_expiry_job = self.root.after(int(RECONNECT_WINDOW * 1000), lambda: self._reconnect_expired(token))
        remaining = self.retry_deadline - now
        if remaining <= 0:
            self._cancel_reconnect()
            return False
        delay = min(RECONNECT_DELAYS[min(self.retry_attempt, len(RECONNECT_DELAYS) - 1)], remaining)
        self.retry_attempt += 1
        generation = self.generation
        # Exactly one timer owns a retry; even a late cancelled callback cannot
        # overwrite the identity of a later session's timer.
        identity = [None]
        def retry():
            if self.retry_job != identity[0] or generation != self.generation:
                return
            self.retry_job = None
            if self.stopping.is_set() or not self._auto_reconnect_enabled() or not self.retry_credentials:
                self._cancel_reconnect()
                return
            if self.retry_deadline is None or time.monotonic() >= self.retry_deadline:
                self._reconnect_expired()
                return
            address, port, password, name = self.retry_credentials
            if pin_key(address, port) != self._credential_endpoint():
                self._cancel_reconnect()
                return
            self._begin_connection(address, port, password, name, automatic=True)
        self.retry_job = identity[0] = self.root.after(max(1, round(delay * 1000)), retry)
        self.connection_var.set(f"Conexão interrompida. Nova tentativa em {delay:g} s. Encerrar cancela. {str(reason)[:220]}")
        self.screen_var.set("Aguardando a rede. A nova sessão exige autorização no Hospedeiro.")
        self._set_reconnect_available(False)
        return True

    def _reconnect_expired(self, token=None):
        if token is not None and token is not getattr(self, "retry_token", None):
            return
        if getattr(self, "retry_deadline", None) is None:
            return
        self._cancel_reconnect()
        # A handshake that finishes after expiration cannot publish a session.
        self._connection_closed("A reconexão expirou após 2 minutos. Confira rede, endereço e senha para conectar novamente.", retryable=False)

    def _publish_connection(self, peer, generation):
        if generation != self.generation or self.stopping.is_set():
            peer.close()
            return
        self._connected(peer)

    def _trust_pairing(self, address, port, fingerprint, generation, pairing):
        if pairing is None:
            return self._trust(address, port, fingerprint, generation)
        try:
            pin = fingerprint_text(fingerprint)
            expected = fingerprint_text(pairing.fingerprint)
        except ValueError:
            return False
        if pin != expected or generation != self.generation or self.stopping.is_set():
            return False
        old = self.settings.data["pins"].get(pin_key(address, port))
        if old and old != pin:
            return self._trust(address, port, fingerprint, generation)
        # The private pairing code binds this Host's TLS identity before password.
        # Persist only the public identity label and certificate, never the code.
        self._post(lambda: self._save_pairing_pin(address, port, pin, generation), generation)
        return True

    def _save_pairing_pin(self, address, port, pin, generation):
        if generation != self.generation or self.stopping.is_set():
            return
        self.settings.data["pins"][pin_key(address, port)] = pin
        try:
            self.settings.save()
        except OSError as error:
            self.file_var.set(f"Certificado válido nesta sessão; não foi possível salvar: {error}")

    def _trust(self, address, port, fingerprint, generation):
        try:
            pin = fingerprint_text(fingerprint)
        except ValueError:
            return False
        result = [False]
        completed = threading.Event()

        def ask():
            try:
                key = pin_key(address, port)
                old = self.settings.data["pins"].get(key)
                if old == pin:
                    result[0] = True
                    return
                if getattr(self, "retry_attempting", False):
                    # An automatic recovery must never replace the identity of
                    # the computer the user originally approved.
                    messagebox.showwarning("Reconexão interrompida", "O certificado do Hospedeiro mudou ou não está salvo. Confira a identidade com o responsável e faça uma nova conexão manual.", parent=self.root)
                    return
                formatted = ":".join(pin[i:i + 2].upper() for i in range(0, 64, 2))
                if old:
                    previous = ":".join(old[i:i + 2].upper() for i in range(0, 64, 2))
                    title = "O certificado do hospedeiro mudou"
                    text = (f"O certificado de {address}:{port} é diferente do certificado salvo.\n\n"
                            f"ANTERIOR:\n{previous}\n\nNOVO:\n{formatted}\n\n"
                            "Confira o novo código na janela do hospedeiro e confirme a mudança com o responsável.\n"
                            "Confiar neste novo certificado?")
                else:
                    title = "Conferir certificado do hospedeiro"
                    text = (f"Primeira conexão com {address}:{port}.\n\nSHA-256:\n{formatted}\n\n"
                            "Compare este código com a impressão digital exibida na janela do hospedeiro.\n"
                            "Os códigos coincidem e você confia neste computador?")
                self.dialog_active = True
                self._release_input()
                approved = messagebox.askyesno(title, text, icon="warning" if old else "question", default="no", parent=self.root)
                if approved and generation == self.generation and not self.stopping.is_set():
                    self.settings.data["pins"][key] = pin
                    try:
                        self.settings.save()
                    except OSError as error:
                        messagebox.showwarning("Certificado não salvo", f"A confiança vale para esta sessão. Não foi possível guardar o certificado: {error}", parent=self.root)
                    result[0] = True
            finally:
                self.dialog_active = False
                completed.set()
        self._post(ask, generation)
        while not completed.wait(0.1):
            if generation != self.generation or self.stopping.is_set():
                return False
        return result[0] and generation == self.generation and not self.stopping.is_set()

    def _connected(self, peer):
        if getattr(self, "buffer_overflow_generation", None) == self.generation:
            peer.close()
            return self._connection_closed("Mensagens demais antes da confirmação da conexão. Tente novamente.")
        if peer.closed:
            return self._connection_closed("O hospedeiro encerrou a conexão.")
        credentials = getattr(self, "retry_credentials", None) if self._auto_reconnect_enabled() else None
        self._cancel_reconnect()
        self.retry_credentials = credentials
        import transfer
        self.peer = peer
        generation = self.generation
        self.stream_ready_generation = None
        self.last_stream_feedback_id = 0
        self.stream_active_generation = None
        self.last_stream_resume_at = None
        self.stream_resume_needed = False
        # Old hosts ignore this optional message. New hosts wait for display
        # feedback instead of filling TCP with images the user has not seen.
        if self._send({"type": "stream_ready", "version": 1}):
            self.stream_ready_generation = generation
        self.transfers = transfer.FileTransfers(peer,
            dispatch=lambda fn: self._post(fn, generation),
            ask_receive=lambda offer, callback: self._ask_receive(offer, callback, generation),
            on_status=lambda status: self._transfer_status(status, generation),
            on_request_file=lambda callback: self._ask_request_file(callback, generation),
            prompt_guard=lambda active: self._post(lambda: self._local_prompt_guard(active), generation),
            status_dispatch=lambda callback: callback())
        self.connecting = False
        self.password_var.set("")
        self._set_password_visible(False)
        address, port = self.connecting_endpoint or (self.address_var.get(), int(self.port_var.get()))
        self.connected_endpoint = pin_key(address, port)
        self._invalidate_presence_endpoint(self.connected_endpoint)
        self._remember_connected_computer(peer)
        self.sharing = True
        self.local_paused = False
        self.capture_available = True
        self.frame_stalled = False
        self.frame_wait_started = time.monotonic()
        self.connection_var.set(f"Conectado · {self.mesh_target_name or 'Host pela internet'}" if getattr(self, "mesh_pairing", None) is not None else f"Conectado a {self.address_var.get()}:{self.port_var.get()}")
        self._placeholder("Aguardando a tela", "O hospedeiro precisa permitir o compartilhamento da área de trabalho.")
        self.workspace.select(self.remote_page)
        self._update_controls()
        with self.frame_lock:
            buffered, self.buffered_messages = self.buffered_messages, []
            self.buffered_bytes = 0
        for message in buffered:
            self._handle_message(message)

    def _transfer_status(self, status, generation):
        event = dict(status) if isinstance(status, dict) else {}
        self._post_latest("file_status", lambda: self._file_status(event), generation)

    def _buffer_message(self, message, generation):
        try:
            size = len(json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError):
            return
        with self.frame_lock:
            if len(self.buffered_messages) >= 128 or self.buffered_bytes + size > 8 * 1024 * 1024:
                self.buffer_overflow_generation = generation
                overflow = True
            else:
                self.buffered_messages.append(message)
                self.buffered_bytes += size
                overflow = False
        if overflow:
            self._post_latest("buffer_overflow", lambda: self._connection_closed("Mensagens demais antes da confirmação da conexão. Tente novamente."), generation)

    def _queue_state(self, message, generation):
        blocked = not message.get("sharing") or message.get("localPaused") or message.get("promptActive") or message.get("captureAvailable") is False
        if blocked:
            self.network_suspended = True
        with self.latest_lock:
            previous = self.pending_states.get(generation)
            self.pending_states[generation] = (message, bool(blocked or (previous and previous[1])))
        self._post_latest("state", lambda: self._apply_queued_state(generation), generation)

    def _apply_queued_state(self, generation):
        with self.latest_lock:
            pending = self.pending_states.pop(generation, None)
        if pending and generation == self.generation:
            message, barrier = pending
            if barrier:
                # Even a quick pause/resume must release held input and wait for a new frame.
                self.sharing = False
            self._handle_message(message)

    def _network_message(self, message, generation):
        if not isinstance(message, dict) or generation != self.generation or self.stopping.is_set():
            return
        if message.get("type") == "screen":
            if stream_frame_id(message) is not None:
                self.stream_active_generation = generation
            with self.frame_lock:
                self.raw_frame = (generation, self.frame_epoch, message)
            self.frame_event.set()
            return
        kind = message.get("type")
        transfers = self.transfers
        if transfers and transfers.handle(message):
            return
        if kind == "state":
            message = {key: message[key] for key in ("type", "sharing", "localPaused", "promptActive", "captureAvailable", "computerName") if key in message}
            if isinstance(message.get("computerName"), str):
                message["computerName"] = message["computerName"][:80]
            if self.connecting and not self.peer:
                self._buffer_message(message, generation)
            else:
                self._queue_state(message, generation)
        elif kind in ("error", "log"):
            text = str(message.get("message", message.get("text", "")))[:500]
            self._post_latest("remote_log", lambda: self._handle_message({"type": kind, "message": text}), generation)
        elif isinstance(kind, str) and kind.startswith("file_") and self.connecting:
            self._buffer_message(message, generation)

    def _handle_message(self, message):
        if not self.peer:
            if self.connecting:
                self._buffer_message(message, self.generation)
            return
        if self.transfers and self.transfers.handle(message):
            return
        kind = message.get("type")
        if kind == "state":
            was_sharing = self.sharing
            self.local_paused = bool(message.get("localPaused"))
            self.remote_prompt_active = bool(message.get("promptActive", False))
            self.capture_available = message.get("captureAvailable") is not False
            self.sharing = bool(message.get("sharing")) and not self.local_paused and not self.remote_prompt_active and self.capture_available
            if hasattr(self, "latest_lock"):
                with self.latest_lock:
                    newer = self.pending_states.get(self.generation)
                    self.network_suspended = not self.sharing or bool(newer and newer[1])
            else:
                self.network_suspended = not self.sharing
            if not self.sharing:
                self._clear_frame()
                title = "Tela pausada no hospedeiro" if self.local_paused else "Confirmação no hospedeiro" if self.remote_prompt_active else "Captura temporariamente indisponível" if not self.capture_available else "Compartilhamento pausado"
                detail = "O responsável precisa retomar a tela na janela do hospedeiro." if self.local_paused else "A tela retorna quando o responsável concluir a confirmação ou a escolha do arquivo." if self.remote_prompt_active else "Aguardando o Hospedeiro recuperar a captura. A conexão não foi encerrada." if not self.capture_available else "Clique em Retomar para pedir o compartilhamento da tela."
                self._placeholder(title, detail)
                self.screen_var.set(f"{title}. {detail}")
            elif not was_sharing:
                self._clear_frame()
                self.frame_wait_started = time.monotonic()
                self.frame_stalled = False
                self._placeholder("Retomando a tela", "Aguarde a próxima imagem do hospedeiro.")
            self._update_controls()
        elif kind in ("error", "log"):
            text = str(message.get("message", message.get("text", "")))[:500]
            if text:
                self.connection_var.set(text)

    def _decode_frames(self):
        while not self.stopping.is_set():
            self.frame_event.wait(0.2)
            self.frame_event.clear()
            with self.frame_lock:
                frame, self.raw_frame = self.raw_frame, None
            if not frame:
                continue
            generation, epoch, message = frame
            if generation != self.generation or epoch != self.frame_epoch:
                continue
            try:
                decode_started = time.monotonic()
                image_data = message.get("image")
                if not isinstance(image_data, str) or not 1 <= len(image_data) <= 32 * 1024 * 1024:
                    raise ValueError("Imagem de tela inválida.")
                if not isinstance(message.get("captureId"), str) or not message["captureId"]:
                    raise ValueError("Identificação da tela ausente.")
                for field in ("width", "height"):
                    if not isinstance(message.get(field), int) or not 1 <= message[field] <= 65535:
                        raise ValueError("Dimensões de tela inválidas.")
                for field in ("left", "top"):
                    if not isinstance(message.get(field), (int, float)) or not math.isfinite(message[field]):
                        raise ValueError("Origem da tela inválida.")
                monitor_identity(message.get("monitorId", "all"))
                if not isinstance(message.get("monitors", []), list):
                    raise ValueError("Lista de monitores inválida.")
                with Image.open(io.BytesIO(base64.b64decode(image_data, validate=True))) as source:
                    if source.format != "JPEG" or source.width * source.height > 64_000_000:
                        raise ValueError("Formato de tela inválido.")
                    source.load()
                    image = source.convert("RGB")
                message = dict(message)
                message["_adxDecodeMs"] = max(0.0, (time.monotonic() - decode_started) * 1000)
                with self.frame_lock:
                    if generation != self.generation or epoch != self.frame_epoch or self.stopping.is_set():
                        image.close()
                        continue
                    previous = self.decoded_frame
                    self.decoded_frame = (generation, epoch, message, image)
                if previous:
                    previous[3].close()
            except Exception as error:
                text = str(error)
                if hasattr(self, "latest_lock"):
                    self._post_latest("frame_error", lambda text=text: self._frame_failed(text), generation)
                else:
                    self._post(lambda text=text: self.screen_var.set(f"Não foi possível exibir a tela: {text}"), generation)

    def _frame_failed(self, text):
        self.frame_stalled = True
        self._clear_frame()
        detail = f"Não foi possível exibir a imagem: {str(text)[:240]}. Aguardando uma nova imagem do Hospedeiro."
        self._placeholder("Imagem temporariamente indisponível", detail)
        self.screen_var.set(detail)
        self._update_controls()
        self._resume_stream(needed=True)

    def _check_frame_health(self, now=None):
        if (not self.peer or self.peer.closed or not self.sharing or self.local_paused or
                self.remote_prompt_active or self.local_prompt_active or self.dialog_active or
                not getattr(self, "capture_available", True) or getattr(self, "frame_stalled", False)):
            return
        now = time.monotonic() if now is None else now
        since = max(getattr(self, "last_frame_at", 0), getattr(self, "frame_wait_started", 0))
        if not since or now - since < 6:
            return
        pending = self.pending_monitor
        self.frame_stalled = True
        self._clear_frame()
        self.pending_monitor = pending
        detail = "Sem atualização da imagem. A conexão ainda não foi encerrada; aguardando o Hospedeiro."
        self._placeholder("Aguardando nova imagem", detail)
        self.screen_var.set(detail)
        self._update_controls()
        self._resume_stream(needed=True)

    def _show_frame(self, message, image):
        if stream_frame_id(message) is not None:
            self.stream_active_generation = self.generation
        monitor_id = monitor_identity(message.get("monitorId", "all"))
        if self.pending_monitor is not None and monitor_id != self.pending_monitor:
            ids = []
            for monitor in message.get("monitors", []):
                try:
                    ids.append(monitor_identity(monitor.get("id")))
                except (AttributeError, TypeError, ValueError):
                    pass
            if self.pending_monitor in ids or self.pending_monitor == 0:
                image.close()
                return
            self.pending_monitor = None
            self.screen_var.set("O monitor foi desconectado. Exibindo a área disponível.")
        if self.current_screen and self.current_screen["captureId"] != message["captureId"]:
            self._release_input()
        self.pending_monitor = None
        previous = self.current_image
        self.current_screen, self.current_image = message, image
        self._set_monitors(message)
        render_started = time.monotonic()
        rendered = self._render_frame()
        render_ms = max(0.0, (time.monotonic() - render_started) * 1000)
        if previous and previous is not image:
            previous.close()
        if rendered is not True:
            return
        self.frame_stalled = False
        self.last_frame_at = time.monotonic()
        self.stream_resume_needed = False
        label = "Todos os monitores" if monitor_id == 0 else f"Monitor {monitor_id}"
        self.screen_var.set(f"{message['width']} × {message['height']} · {label} · Clique na tela para controlar mouse e teclado. Esc sai da tela cheia.")
        self._update_controls()
        if rendered is True:
            self._ack_presented_frame(message, render_ms)

    def _ack_presented_frame(self, message, render_ms):
        """Acknowledge presentation once, for this opted-in session only.

        No peer clock is involved: the Host measures its own send-to-display
        round trip. Legacy screens keep working without generating feedback.
        """
        frame_id = stream_frame_id(message)
        if (getattr(self, "stream_ready_generation", None) != getattr(self, "generation", -1) or
                frame_id is None or
                frame_id <= getattr(self, "last_stream_feedback_id", 0)):
            return
        feedback = {"type": "stream_feedback", "frameId": frame_id}
        for name, value in (("decodeMs", message.get("_adxDecodeMs")), ("renderMs", render_ms)):
            if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 30_000:
                feedback[name] = round(value, 2)
        if self._send(feedback):
            self.last_stream_feedback_id = frame_id

    def _resume_stream(self, *, needed=False):
        """Request a fresh frame after losing a candidate, without a retry loop.

        A missing presentation ACK deliberately closes the Host's frame window.
        Render/decode recovery and returning to the viewer can reopen it once;
        fresh failures may request again, at most once a second. This changes
        no sharing, consent or remote input permission.
        """
        generation = getattr(self, "generation", -1)
        if (getattr(self, "stream_ready_generation", None) != generation or
                getattr(self, "stream_active_generation", None) != generation):
            return False
        if needed:
            self.stream_resume_needed = True
        if not getattr(self, "stream_resume_needed", False):
            return False
        if (not self.peer or self.peer.closed or not self.sharing or
                self.local_paused or self.remote_prompt_active or self.local_prompt_active or
                self.dialog_active or getattr(self, "network_suspended", False) or
                not getattr(self, "capture_available", True) or self.stopping.is_set()):
            return False
        now = time.monotonic()
        previous = getattr(self, "last_stream_resume_at", None)
        if previous is not None and now - previous < STREAM_RESUME_INTERVAL:
            return False
        if not self._send({"type": "stream_resume", "version": 1}):
            return False
        self.last_stream_resume_at = now
        self.stream_resume_needed = False
        return True

    def _render_frame(self):
        self.resize_job = None
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if not self.current_image or not self.current_screen:
            self._position_placeholder(width, height)
            return
        bounds = fit_geometry(width, height, self.current_image.width, self.current_image.height)
        if bounds[2] <= 0 or bounds[3] <= 0:
            return
        try:
            with self.current_image.resize((bounds[2], bounds[3]), Image.Resampling.BILINEAR) as rendered:
                photo = ImageTk.PhotoImage(rendered, master=self.root)
            self.canvas.itemconfigure(self.image_item, image=photo, state="normal")
            self.canvas.coords(self.image_item, bounds[0], bounds[1])
            self.photo = photo
            self.canvas.itemconfigure(self.placeholder_title, state="hidden")
            self.canvas.itemconfigure(self.placeholder_detail, state="hidden")
            self.image_bounds = bounds
            return True
        except Exception as error:
            self._frame_failed(str(error))
            return False

    def _resize(self, _event=None):
        if self.resize_job:
            self.root.after_cancel(self.resize_job)
        self.resize_job = self.root.after(30, self._render_frame)

    def _position_placeholder(self, width, height):
        self.canvas.coords(self.placeholder_title, width // 2, max(30, height // 2 - 20))
        self.canvas.coords(self.placeholder_detail, width // 2, max(60, height // 2 + 23))
        self.canvas.itemconfigure(self.placeholder_title, width=max(100, width - 70))
        self.canvas.itemconfigure(self.placeholder_detail, width=max(100, width - 70))

    def _placeholder(self, title, detail):
        self.canvas.itemconfigure(self.image_item, state="hidden")
        self.canvas.itemconfigure(self.placeholder_title, text=title, state="normal")
        self.canvas.itemconfigure(self.placeholder_detail, text=detail, state="normal")
        self._position_placeholder(self.canvas.winfo_width(), self.canvas.winfo_height())

    def _clear_frame(self):
        self._release_input()
        self.frame_epoch += 1
        self.pending_monitor = None
        self.current_screen = None
        if self.current_image:
            self.current_image.close()
        self.current_image = None
        self.photo = None
        self.image_bounds = (0, 0, 0, 0)
        self.canvas.itemconfigure(self.image_item, image="", state="hidden")
        with self.frame_lock:
            self.raw_frame = None
            decoded, self.decoded_frame = self.decoded_frame, None
        if decoded:
            decoded[3].close()
        self.monitor_combo.configure(state="disabled")

    def _set_monitors(self, message):
        choices, ids = ["Todos os monitores"], [0]
        for monitor in message.get("monitors", [])[:64]:
            try:
                identity = int(monitor["id"])
                if identity <= 0 or identity in ids:
                    continue
                ids.append(identity)
                choices.append(f"Monitor {identity} ({int(monitor['width'])} × {int(monitor['height'])})")
            except (KeyError, TypeError, ValueError):
                continue
        self.monitor_ids = ids
        self.monitor_combo.configure(values=choices)
        selected = monitor_identity(message.get("monitorId", "all"))
        self.monitor_combo.current(ids.index(selected) if selected in ids else 0)

    def _choose_monitor(self, _event=None):
        if not self._input_ready():
            return
        selection = self.monitor_combo.current()
        if not 0 <= selection < len(self.monitor_ids):
            return
        identity = self.monitor_ids[selection]
        self._clear_frame()
        self.pending_monitor = identity
        self._placeholder("Alterando o monitor", "Aguarde a primeira imagem da nova área de trabalho.")
        self._send({"type": "monitor", "id": identity})
        self._update_controls()

    def _request_sharing(self, enabled):
        if enabled and not getattr(self, "capture_available", True):
            self.screen_var.set("Aguardando o Hospedeiro recuperar a captura. Não é necessário retomar a tela.")
            return
        if not self.peer or self.peer.closed:
            return
        if enabled and self.local_paused:
            return messagebox.showinfo("Tela pausada no hospedeiro", "O responsável precisa retomar a tela na janela do hospedeiro.", parent=self.root)
        self._release_input()
        self._send({"type": "sharing", "enabled": bool(enabled)})
        if not enabled:
            self.sharing = False
            self._clear_frame()
            self._placeholder("Compartilhamento pausado", "Clique em Retomar para pedir o compartilhamento da tela.")
        else:
            self._placeholder("Aguardando o hospedeiro", "A tela será exibida quando o compartilhamento for autorizado.")
        self._update_controls()

    def _input_ready(self):
        return bool(getattr(self, "selected_page", "session") == "session" and
                    self.peer and not self.peer.closed and self.current_screen and self.sharing and
                    not self.local_paused and self.pending_monitor is None and not self.dialog_active and
                    not self.local_prompt_active and not self.remote_prompt_active and
                    not getattr(self, "network_suspended", False) and not getattr(self, "frame_stalled", False) and
                    getattr(self, "capture_available", True))

    def _send_commands(self):
        while not self.stopping.is_set():
            item = self.outgoing.get(.2)
            if item is None:
                continue
            generation, peer, message = item
            if generation != self.generation or peer is not self.peer or peer.closed:
                continue
            try:
                accepted = peer.send(message)
                if accepted is False and not (message.get("type") == "input" and message.get("action") == "move"):
                    raise ConnectionError("Não foi possível enviar um comando confiável.")
            except Exception as error:
                if self.send_failed_generation == generation:
                    continue
                self.send_failed_generation = generation
                text = str(error) or "A conexão foi interrompida ao enviar um comando."
                try:
                    try:
                        peer.close(text)
                    except TypeError:
                        peer.close()
                except Exception:
                    pass
                self._post(lambda text=text: self._connection_closed(text), generation)

    def _send(self, message):
        if not self.peer or self.peer.closed:
            return False
        if message.get("type") == "input" and message.get("action") not in ("move", "release"):
            self._flush_pending_move()
        if hasattr(self, "outgoing"):
            accepted = self.outgoing.put(self.generation, self.peer, message)
            if not accepted and not (message.get("type") == "input" and message.get("action") == "move"):
                self._connection_closed("Fila de comandos ocupada por muito tempo. Conecte novamente com a senha atual.")
            return accepted
        try:
            accepted = self.peer.send(message)
            return accepted is not False
        except Exception as error:
            self._connection_closed(str(error))
            return False

    def _drop_pending_move(self):
        job, self.move_job = getattr(self, "move_job", None), None
        self.pending_move = None
        if job:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass

    def _flush_pending_move(self):
        message = getattr(self, "pending_move", None)
        self._drop_pending_move()
        if message and self._input_ready() and message.get("captureId") == self.current_screen.get("captureId"):
            self.last_move = time.monotonic()
            self._send(message)

    def _point_message(self, action, point=None):
        point = point or self.last_point
        return {"type": "input", "action": action, "x": point[0], "y": point[1],
                "captureId": self.current_screen["captureId"] if self.current_screen else ""}

    def _mouse_move(self, event):
        if not self._input_ready():
            return
        point = normalized_point(event.x, event.y, self.image_bounds, bool(self.buttons_down))
        if point is None:
            return
        self.last_point = point
        now = time.monotonic()
        self.pending_move = self._point_message("move", point)
        if now - self.last_move >= POINTER_INTERVAL:
            self._flush_pending_move()
        elif not getattr(self, "move_job", None):
            self.move_job = self.root.after(max(1, round((POINTER_INTERVAL - (now - self.last_move)) * 1000)), self._flush_pending_move)

    def _mouse_down(self, event):
        if not self._input_ready():
            return "break"
        point = normalized_point(event.x, event.y, self.image_bounds)
        if point is None:
            return "break"
        self.canvas.focus_set()
        self._drop_pending_move()
        self.last_point = point
        button = {1: "left", 2: "middle", 3: "right"}.get(event.num)
        if button:
            if not self._send(self._point_message("move", point)) or not self._input_ready():
                return "break"
            if self._send(dict(self._point_message("button", point), button=button, down=True)):
                self.buttons_down.add(button)
                self.canvas.grab_set()
        return "break"

    def _mouse_up(self, event):
        button = {1: "left", 2: "middle", 3: "right"}.get(event.num)
        if button in self.buttons_down:
            point = normalized_point(event.x, event.y, self.image_bounds, True) or self.last_point
            self.last_point = point
            self._drop_pending_move()
            if self._input_ready():
                self._send(self._point_message("move", point))
            self.buttons_down.discard(button)
            self._send(dict(self._point_message("button", point), button=button, down=False))
        if not self.buttons_down:
            try:
                self.canvas.grab_release()
            except tk.TclError:
                pass
        return "break"

    def _wheel(self, event, steps=None):
        if not self._input_ready():
            return "break"
        point = normalized_point(event.x, event.y, self.image_bounds)
        if point is None:
            return "break"
        if steps is None:
            delta = getattr(event, "delta", 0)
            if sys.platform == "darwin":
                steps = int(delta)
            else:
                total = getattr(self, "wheel_remainder", 0.0) + delta / 120
                steps = math.trunc(total)
                self.wheel_remainder = total - steps
        if steps:
            self.last_point = point
            self._send(dict(self._point_message("wheel", point), delta=max(-20, min(20, int(steps)))))
        return "break"

    def _key_down(self, event):
        if event.keysym == "Escape" and self.fullscreen:
            self._toggle_fullscreen(False)
            return "break"
        if event.keysym == "F8" and self.fullscreen:
            return self._toggle_fullscreen_commands()
        if not self._input_ready() or self.root.focus_get() != self.canvas:
            return None
        key = remote_key(event.keysym, event.char)
        if key and event.keycode not in self.keys_down:
            self.keys_down[event.keycode] = key
            self._send(dict(self._point_message("key"), key=key, down=True))
        return "break"

    def _key_up(self, event):
        key = self.keys_down.pop(event.keycode, None)
        if key and key not in self.keys_down.values():
            self._send(dict(self._point_message("key"), key=key, down=False))
            return "break"
        return None

    def _release_input(self):
        self._drop_pending_move()
        self.wheel_remainder = 0.0
        had_inputs = bool(self.buttons_down or self.keys_down)
        self.buttons_down.clear()
        self.keys_down.clear()
        try:
            self.canvas.grab_release()
        except tk.TclError:
            pass
        if self.peer and not self.peer.closed:
            self._send({"type": "input", "action": "release"})
        return had_inputs

    def _check_focus(self):
        if not self.stopping.is_set() and self.root.focus_displayof() is None:
            self._release_input()

    def _toggle_fullscreen(self, enabled=None):
        self._release_input()
        target = not self.fullscreen if enabled is None else bool(enabled)
        if target and hasattr(self, "workspace") and self.selected_page != "session": self._show_page("session")
        self.fullscreen = target
        self.fullscreen_controls_hidden = False
        if getattr(self, "fullscreen_hint_job", None):
            self.root.after_cancel(self.fullscreen_hint_job)
            self.fullscreen_hint_job = None
        self.root.attributes("-fullscreen", self.fullscreen)
        self.fullscreen_button.configure(text="Sair da tela cheia" if self.fullscreen else "Tela cheia")
        if hasattr(self, "sidebar"):
            if self.fullscreen:
                self.sidebar.pack_forget(); self.workspace.configure(padding=0)
                for widget in (self.session_heading, self.session_toolbar, self.controls_panel, self.files_panel, self.session_footer): widget.pack_forget()
                self.fullscreen_actions.place(relx=1, x=-16, y=16, anchor="ne")
                self.fullscreen_traffic.place(x=16, y=16, anchor="nw")
            else:
                self.fullscreen_actions.place_forget()
                self.fullscreen_traffic.place_forget()
                self.fullscreen_hint.place_forget()
                self.sidebar.pack(side="left", fill="y", before=self.workspace); self.workspace.configure(padding=20)
                self.session_heading.pack(fill="x", pady=(0, 14), before=self.session_stage)
                self.session_toolbar.pack(fill="x", pady=(0, 12), before=self.session_stage)
                if self.controls_visible: self.controls_panel.pack(fill="x", pady=(0, 10), before=self.session_stage)
                self.session_footer.pack(side="bottom", fill="x", pady=(10, 0), before=self.session_stage)
                if self.files_visible: self.files_panel.pack(side="bottom", fill="x", pady=(10, 0), before=self.session_stage)
        self._resize()
        if self.fullscreen:
            self.canvas.focus_set()

    def _toggle_fullscreen_commands(self, _event=None):
        if not self.fullscreen:
            return None
        self._release_input()
        self.fullscreen_controls_hidden = not self.fullscreen_controls_hidden
        if self.fullscreen_hint_job:
            self.root.after_cancel(self.fullscreen_hint_job)
            self.fullscreen_hint_job = None
        if self.fullscreen_controls_hidden:
            self.fullscreen_actions.place_forget()
            self.fullscreen_traffic.place_forget()
            self.fullscreen_hint.place(relx=.5, rely=1, y=-16, anchor="s")
            self.fullscreen_hint_job = self.root.after(3000, self._hide_fullscreen_hint)
        else:
            self.fullscreen_hint.place_forget()
            self.fullscreen_actions.place(relx=1, x=-16, y=16, anchor="ne")
            self.fullscreen_traffic.place(x=16, y=16, anchor="nw")
        self.canvas.focus_set()
        return "break"

    def _hide_fullscreen_hint(self):
        self.fullscreen_hint_job = None
        self.fullscreen_hint.place_forget()

    def _traffic_tick(self):
        if self.stopping.is_set():
            return
        self.traffic_var.set(session_traffic_text(self.peer))
        self.traffic_job = self.root.after(1000, self._traffic_tick)

    def _escape(self, _event=None):
        if self.fullscreen:
            self._toggle_fullscreen(False)
            return "break"

    def _ask_receive(self, offer, callback, generation):
        def ask():
            if generation != self.generation or not self.peer or self.peer.closed:
                return callback(None)
            self.dialog_active = True
            self._release_input()
            try:
                name = str(offer.get("name", "arquivo"))
                size = offer.get("size", 0)
                approved = messagebox.askyesno("Receber arquivo", f"Receber {name}\nTamanho: {size} bytes\n\nEscolha onde salvar no seu computador.", default="no", parent=self.root)
                path = filedialog.asksaveasfilename(title="Salvar arquivo recebido", initialfile=Path(name).name,
                    initialdir=str(Path.home() / "Downloads"), parent=self.root) if approved else ""
                callback(path or None if generation == self.generation and self.peer and not self.peer.closed else None)
            except tk.TclError:
                callback(None)
            finally:
                self.dialog_active = False
                self._update_controls()
        self._post(ask, generation)

    def _ask_request_file(self, callback, generation):
        def ask():
            if generation != self.generation or not self.peer or self.peer.closed:
                return callback(None)
            self.dialog_active = True
            self._release_input()
            try:
                path = filedialog.askopenfilename(title="A outra pessoa solicitou um arquivo. Escolha o arquivo a enviar.", parent=self.root)
                callback(path or None if generation == self.generation and self.peer and not self.peer.closed else None)
            except tk.TclError:
                callback(None)
            finally:
                self.dialog_active = False
                self._update_controls()
        self._post(ask, generation)

    def _send_file(self):
        if not self.transfers or self.file_active:
            return
        self.dialog_active = True
        self._release_input()
        try:
            path = filedialog.askopenfilename(title="Escolha o arquivo para enviar ao hospedeiro", parent=self.root)
        finally:
            self.dialog_active = False
        if path and self.transfers:
            self.transfers.send_file(path)

    def _receive_file(self):
        if self.transfers and not self.file_active:
            self._release_input()
            self.transfers.receive_request()

    def _cancel_file(self):
        if self.transfers:
            self.transfers.cancel()

    def _file_status(self, status):
        if not isinstance(status, dict):
            return
        was_active = getattr(self, "file_active", False)
        self.file_active = bool(status.get("active", False))
        if self.file_active and not was_active and hasattr(self, "files_panel") and self.selected_page == "session" and not self.fullscreen and not self.files_visible:
            self._toggle_files(True)
        self.file_var.set(str(status.get("message", ""))[:500])
        value = status.get("progress", 0)
        try:
            self.progress_var.set(max(0, min(100, float(value) * 100)))
        except (TypeError, ValueError):
            self.progress_var.set(0)
        self._update_controls()

    def _local_prompt_guard(self, active):
        self.local_prompt_active = bool(active)
        if active:
            self._release_input()
        self._update_controls()
        if not active:
            self._resume_stream()

    def _update_controls(self):
        connected = bool(self.peer and not self.peer.closed)
        waiting = bool(getattr(self, "retry_job", None))
        busy = connected or self.connecting or waiting
        self.connect_button.configure(state="disabled" if connected or self.connecting or waiting else "normal")
        self.disconnect_button.configure(state="normal" if connected or self.connecting or waiting else "disabled")
        if hasattr(self, "cancel_connect_button"):
            editing_saved = getattr(self, "_updating_computer_id", None) is not None
            self.cancel_connect_button.configure(state="normal" if self.connecting or waiting or editing_saved else "disabled",
                text="Cancelar atualização" if editing_saved and not busy else "Cancelar conexão")
        for entry in self.connection_entries:
            entry.configure(state="disabled" if connected or self.connecting or waiting else "normal")
        if hasattr(self, "password_toggle_button"):
            self.password_toggle_button.configure(state="disabled" if connected or self.connecting or waiting else "normal")
        if hasattr(self, "connection_progress"):
            progress_active = bool(self.connecting or waiting)
            if progress_active and not self._connection_progress_running:
                self.connection_divider.grid_remove()
                self.connection_progress.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
                self.connection_progress.start(12)
            elif not progress_active and self._connection_progress_running:
                self.connection_progress.stop()
                self.connection_progress.grid_remove()
                self.connection_divider.grid()
            self._connection_progress_running = progress_active
            if progress_active:
                self.connection_action_hint.set(self.connection_var.get())
            for button in (self.internet_setup_button, self.lan_setup_button, self.endpoint_details_button):
                button.configure(state="disabled" if busy else "normal")
            if getattr(self, "mesh_panel", None) is not None:
                for name in ("code_entry", "show_button", "paste_button", "select_button", "remember_check"):
                    widget = getattr(self.mesh_panel, name, None)
                    if widget is not None:
                        widget.configure(state="disabled" if busy else "normal")
            self._refresh_connection_steps()
        self.fit_button.configure(state="normal" if self.current_image else "disabled")
        self.pause_button.configure(state="normal" if connected and self.sharing else "disabled")
        self.resume_button.configure(state="normal" if connected and not self.sharing and not self.local_paused and not self.remote_prompt_active and getattr(self, "capture_available", True) else "disabled")
        self.fullscreen_button.configure(state="normal" if connected else "disabled")
        self.monitor_combo.configure(state="readonly" if self._input_ready() and len(self.monitor_ids) > 1 else "disabled")
        for button in (self.send_button, self.receive_button):
            button.configure(state="normal" if connected and getattr(self, "selected_page", "session") == "session" and not self.file_active and not self.dialog_active else "disabled")
        self.cancel_button.configure(state="normal" if connected and self.file_active else "disabled")
        self.canvas.configure(cursor="crosshair" if self._input_ready() else "arrow")
        if hasattr(self, "files_toggle_button"):
            self.files_toggle_button.configure(text="Fechar arquivos" if self.files_visible else "Arquivos •" if self.file_active else "Arquivos")
        self._update_computer_controls()
        # A local dialog may have deferred a recovery request. Closing it is a
        # fresh UI event; this consumes that request once, not on every frame.
        self._resume_stream()

    def _connection_closed(self, reason, retryable=None):
        retry = bool(self.peer or self.connecting) and not getattr(self, "manual_disconnect", False) and not self.stopping.is_set()
        automatic = retry and bool(self.peer or getattr(self, "retry_attempting", False))
        automatic = automatic and (getattr(reason, "retryable", retryable_transport_reason(reason)) if retryable is None else retryable)
        active, self.connect_cancel = getattr(self, "connect_cancel", None), None
        if active:
            active.set()
        self._invalidate_presence_endpoint(getattr(self, "connected_endpoint", None))
        peer, self.peer = self.peer, None
        transfers, self.transfers = self.transfers, None
        self.generation += 1
        self.connecting = False
        self.retry_attempting = False
        self.connected_endpoint = self.connecting_endpoint = None
        for identity, result in getattr(self, "computer_presence", {}).items():
            if result.get("source") == "session":
                result["checked_at"] = -60
                result["state"] = "checking"
        self.sharing = False
        self.local_paused = False
        self.local_prompt_active = False
        self.remote_prompt_active = False
        self.file_active = False
        self.buffered_messages = []
        self.buffered_bytes = 0
        self._clear_latest()
        self.network_suspended = False
        self.capture_available = True
        self.frame_stalled = False
        self.last_frame_at = self.frame_wait_started = 0.0
        if hasattr(self, "outgoing"):
            self.outgoing.clear()
        if transfers:
            transfers.close()
        if peer and not peer.closed:
            if getattr(self, "manual_disconnect", False) and callable(getattr(peer, "end_session", None)):
                peer.end_session("Sessão encerrada pelo Controlador.")
            else:
                peer.close()
        self._clear_frame()
        self.connection_var.set(str(reason or "Desconectado")[:500])
        self.screen_var.set("Conexão encerrada. Mouse e teclado remotos foram liberados.")
        self.file_var.set("Desconectado. Nenhuma transferência em andamento.")
        self.progress_var.set(0)
        self._placeholder("Desconectado", "Abra Conectar e confira o código e a senha atuais do Hospedeiro.")
        self.password_var.set("")
        self._set_password_visible(False)
        scheduled = bool(automatic and self._schedule_reconnect(reason))
        if not scheduled:
            self._cancel_reconnect()
            self._set_reconnect_available(retry)
        if self.fullscreen: self._toggle_fullscreen(False)
        self._update_controls()
        if hasattr(self, "workspace"):
            self.workspace.select(self.computers_page)
            self._refresh_computers(force=True)

    def disconnect(self):
        self.manual_disconnect = True
        try:
            self._cancel_reconnect()
            self._release_input()
            self._connection_closed("Desconectado pelo controlador")
            if getattr(self, "mesh_panel", None) is not None:
                with self.mesh_start_lock:
                    self.mesh_panel.stop()
        finally:
            self.manual_disconnect = False

    def close(self):
        if self.stopping.is_set():
            return
        self.stopping.set()
        self._stop_ios_connector()
        if getattr(self, "mesh_panel", None) is not None:
            with self.mesh_start_lock:
                self.mesh_panel.close()
        for job in (self.pump_job, self.presence_job, self.resize_job, getattr(self, "move_job", None), getattr(self, "traffic_job", None), getattr(self, "fullscreen_hint_job", None)):
            if job:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
        self.disconnect()
        self.frame_event.set()
        self.root.destroy()
        manager = getattr(self, "session_manager", None)
        if manager is not None:
            manager.unregister(self)


def enable_dpi_awareness():
    if os.name != "nt":
        return
    import ctypes
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    sessions = ControllerSessions(root)
    root.protocol("WM_DELETE_WINDOW", sessions.close_all)
    sessions.new_window()
    root.mainloop()


if __name__ == "__main__":
    main()
