"""Native MiniAnyDesk Host: visible local consent, TLS and full desktop capture."""
from __future__ import annotations

import base64
from collections import deque
from collections.abc import Mapping
import ctypes
from datetime import datetime
from io import BytesIO
import ipaddress
import json
import math
import os
from pathlib import Path
import queue
import secrets
import socket
import sys
import threading
import time
import tempfile
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

from adaptive_stream import AdaptiveStream, BACKPRESSURE_POLL_SECONDS, STREAM_VERSION
from desktop_geometry import choose_monitor, physical_point, screen_metadata
from desktop_ui import BG, BORDER, INK, MUTED, NAVY, ORANGE, WHITE, ScrollForm, apply_theme, brand, card, page_brand
from network_addresses import classify_address, local_addresses as _local_addresses


VERSION = "6.2.2"
DEFAULT_PORT = 64321
FPS = 12
CAPTURE_RETRY_MIN = 0.25
CAPTURE_RETRY_MAX = 2.0
ADDRESS_REFRESH_MS = 5000


def generate_session_password() -> str:
    """Generate an eight-digit temporary code, including possible leading zeros."""
    return "".join(secrets.choice("0123456789") for _ in range(8))


def _computer_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Informe um nome com 1 a 80 caracteres.")
    value = " ".join(value.split())
    if not 1 <= len(value) <= 80 or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Informe um nome com 1 a 80 caracteres, sem caracteres de controle.")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("O nome contém caracteres Unicode inválidos.") from None
    return value


def load_computer_name(state_dir) -> str:
    """Only the public host name persists; passwords never enter this file."""
    fallback = " ".join(socket.gethostname().split())[:80] or "Meu computador"
    try:
        path = Path(state_dir) / "host-settings.json"
        if path.stat().st_size > 4096:
            return fallback
        settings = json.loads(path.read_text(encoding="utf-8"))
        return _computer_name(settings.get("computer_name"))
    except (OSError, ValueError, AttributeError, TypeError):
        return fallback


def save_computer_name(state_dir, name: str) -> str:
    name = _computer_name(name)
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix="host-settings-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            json.dump({"computer_name": name}, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, directory / "host-settings.json")
        return name
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_available_while_open(state_dir) -> bool:
    """A new Host is available while open; preserve an owner's later choice."""
    try:
        path = Path(state_dir) / "availability.json"
        if path.stat().st_size > 1024:
            return False
        value = json.loads(path.read_text(encoding="utf-8"))
        return (isinstance(value, dict) and value.get("available_while_open") is True)
    except FileNotFoundError:
        return True
    except (OSError, ValueError, TypeError):
        return False


def save_available_while_open(state_dir, enabled: bool) -> None:
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix="availability-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            json.dump({"available_while_open": bool(enabled)}, output)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, directory / "availability.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def initialize_dpi_awareness() -> None:
    """Set physical coordinates before creating Tk or importing screen libraries."""
    if sys.platform != "win32":
        return
    try:
        setter = ctypes.windll.user32.SetProcessDpiAwarenessContext
        setter.argtypes = [ctypes.c_void_p]
        setter.restype = ctypes.c_int
        if setter(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def local_addresses() -> list[str]:
    """Return the shared route-aware IPv4 list, without combining addresses."""
    return _local_addresses()


def _usable_address(value: str) -> bool:
    try:
        address = ipaddress.IPv4Address(value)
        return not (address.is_loopback or address.is_unspecified or address.is_multicast
                    or address == ipaddress.IPv4Address("255.255.255.255"))
    except (ipaddress.AddressValueError, TypeError):
        return False


class _MouseInput(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32),
                ("mouseData", ctypes.c_uint32), ("dwFlags", ctypes.c_uint32),
                ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("data", _InputUnion)]


class InputController:
    """Lazy desktop dependency, physical mouse input and explicit release state."""

    def __init__(self) -> None:
        self.held_buttons: set[str] = set()
        self.held_keys: set[str] = set()
        self._keyboard = None

    def keyboard(self):
        if self._keyboard is None:
            import pyautogui
            # Remote screen coordinates can reach any monitor corner. The visible
            # local pause/disconnect controls remain the emergency stop.
            pyautogui.FAILSAFE = False
            pyautogui.PAUSE = 0
            self._keyboard = pyautogui
        return self._keyboard

    def move(self, x: int, y: int) -> None:
        if sys.platform == "win32":
            user32 = ctypes.windll.user32
            setter = getattr(user32, "SetPhysicalCursorPos", user32.SetCursorPos)
            setter.argtypes = [ctypes.c_int, ctypes.c_int]
            setter.restype = ctypes.c_int
            if not setter(int(x), int(y)):
                raise OSError("O Windows bloqueou o movimento neste desktop.")
        else:
            self.keyboard().moveTo(x, y)

    @staticmethod
    def _send_mouse(flags: int, data: int = 0) -> None:
        event = _Input(type=0, data=_InputUnion(mi=_MouseInput(
            dx=0, dy=0, mouseData=ctypes.c_uint32(data).value,
            dwFlags=flags, time=0, dwExtraInfo=0)))
        sender = ctypes.windll.user32.SendInput
        sender.argtypes = [ctypes.c_uint32, ctypes.POINTER(_Input), ctypes.c_int]
        sender.restype = ctypes.c_uint32
        if sender(1, ctypes.byref(event), ctypes.sizeof(_Input)) != 1:
            raise OSError("O Windows bloqueou a entrada nesta janela.")

    def button(self, name: str, down: bool) -> None:
        if name not in ("left", "middle", "right"):
            return
        if down:
            if name in self.held_buttons:
                return
            self.held_buttons.add(name)
        try:
            if sys.platform == "win32":
                flags = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010),
                         "middle": (0x0020, 0x0040)}
                self._send_mouse(flags[name][0 if down else 1])
            elif down:
                self.keyboard().mouseDown(button=name)
            else:
                self.keyboard().mouseUp(button=name)
        finally:
            if not down:
                self.held_buttons.discard(name)

    def key(self, name: str, down: bool) -> None:
        keyboard = self.keyboard()
        if name not in keyboard.KEYBOARD_KEYS:
            return
        if down:
            if name in self.held_keys:
                return
            self.held_keys.add(name)
            keyboard.keyDown(name)
        else:
            try:
                keyboard.keyUp(name)
            finally:
                self.held_keys.discard(name)

    def wheel(self, delta: int) -> None:
        if delta:
            if sys.platform == "win32":
                self._send_mouse(0x0800, delta * 120)
            else:
                self.keyboard().scroll(delta)

    def release(self) -> None:
        for name in tuple(self.held_buttons):
            try:
                self.button(name, False)
            except Exception:
                pass
        self.held_buttons.clear()
        for name in tuple(self.held_keys):
            try:
                self.key(name, False)
            except Exception:
                pass
        self.held_keys.clear()


class HostApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.control_lock = threading.RLock()
        self.shutdown = threading.Event()
        self.ui_queue: queue.Queue = queue.Queue()
        self._pending_logs = deque(maxlen=80)
        self._log_queued = False
        self._diagnostic_times = {}
        self._pending_transfer_status = None
        self._transfer_status_queued = False
        self._address_refresh_busy = False
        self._address_candidates: tuple[str, ...] = ()
        self._network_address_candidates: tuple[str, ...] = ()
        self._manual_address: str | None = None
        self.mesh_status = None
        self.mesh_panel = None
        self._address_after_id = None
        self._ui_after_id = None
        self._gui_after_id = None
        self._availability_after_id = None
        self._listener_start_token = None
        self.server = None
        self.starting = False
        self.peer = None
        self.transfers = None
        self.sharing = True
        self.local_paused = False
        self.prompt_count = 0
        self.pending_consents: set[threading.Event] = set()
        self.monitor_id = "all"
        self.last_screen = None
        self.capture_available = True
        self.stream = AdaptiveStream()
        self.generation = 0
        self.inputs = InputController()
        self.state_dir = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "MiniAnyDeskNativo" / "Host"
        self._computer_name_cache = load_computer_name(self.state_dir)
        self.computer_name = tk.StringVar(value=self._computer_name_cache)
        self.password = tk.StringVar(value=generate_session_password())
        self.port = tk.StringVar(value=str(DEFAULT_PORT))
        self.fingerprint = tk.StringVar(value="Inicie o Host para visualizar o certificado.")
        self.addresses = tk.StringVar(value="")
        self.address_hint = tk.StringVar(value="Identificando os endereços deste computador…")
        self.address_details = tk.StringVar(value="Aguardando a identificação da rede.")
        self.status = tk.StringVar(value="Host parado")
        self.transfer_status = tk.StringVar(value="Nenhuma transferência em andamento")
        self.show_password = tk.BooleanVar(value=False)
        self.available_while_open = tk.BooleanVar(value=load_available_while_open(self.state_dir))
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._ui_after_id = self.root.after(40, self._drain_ui)
        self._poll_addresses()
        self._availability_after_id = self.root.after(200, self._start_available_while_open)
        threading.Thread(target=self._capture_loop, daemon=True, name="MiniAnyDesk-capture").start()
        self.log("A rede privada pela internet é preparada automaticamente, sem conta. Compartilhe o código privado e a senha.")
        self.log("O Host pode ficar disponível enquanto esta janela estiver aberta. Cada sessão exige sua aprovação.")
        self.log("Cada conexão e recebimento de arquivo exige aprovação neste computador.")

    def _build_ui(self) -> None:
        self.root.title(f"AliançaDX Acesso Remoto {VERSION} — Host")
        apply_theme(self.root)
        width = max(760, min(1040, self.root.winfo_screenwidth() - 80))
        height = max(540, min(780, self.root.winfo_screenheight() - 90))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(760, 540)
        self.root.configure(bg=BG)
        self._wrap_labels = []
        self.advanced_open = False
        # Keep the section identifiers stable for keyboard/QA integrations while
        # using everyday labels in the navigation.
        sections = ("Dados de conexão", "Internet sem login", "Arquivos", "Meu computador", "Rede e registros")
        section_labels = {"Dados de conexão": "Receber acesso", "Internet sem login": "Internet",
                          "Arquivos": "Arquivos", "Meu computador": "Meu computador",
                          "Rede e registros": "Ajuda e detalhes"}
        nav_font = tkfont.Font(root=self.root, font=ttk.Style(self.root).lookup("Sidebar.TButton", "font"))
        family = getattr(self.root, "_minianydesk_ui_font", "Segoe UI")
        brand_width = tkfont.Font(root=self.root, font=(family, 14, "bold")).measure("Acesso Remoto") + 36
        sidebar_width = max(238, brand_width, max(nav_font.measure(title) for title in section_labels.values()) + 48)
        self.root.minsize(max(760, sidebar_width + 480), 540)

        shell = ttk.Frame(self.root, style="App.TFrame")
        shell.pack(fill="both", expand=True)
        sidebar = tk.Frame(shell, bg=NAVY, width=sidebar_width)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self.sidebar_brand = brand(sidebar, subtitle="HOST · RECEBER ACESSO")
        self.sidebar_brand.pack(fill="x", padx=16, pady=(18, 16))
        self.nav_buttons = {}
        for title in sections:
            button = ttk.Button(sidebar, text=section_labels[title], style="Sidebar.TButton",
                                command=lambda section=title: self._navigate(section))
            button.pack(fill="x", padx=12, pady=3)
            self.nav_buttons[title] = button
        self.nav_buttons["Dados de conexão"].configure(style="SidebarSelected.TButton")
        self.sidebar_version = tk.Label(sidebar, text=f"AliançaDX · {VERSION}\nAcesso Remoto para empresas", bg=NAVY,
                 fg="#A8CDB8", justify="left", anchor="w", wraplength=sidebar_width - 36)
        self.sidebar_version.pack(side="bottom", fill="x", padx=18, pady=16)
        self.sidebar_hint = tk.Label(sidebar, text="Você decide quem entra.\nAprove cada solicitação\nneste computador.", bg=NAVY,
                 fg=WHITE, justify="left", anchor="w", wraplength=sidebar_width - 36)
        self.sidebar_hint.pack(side="bottom", fill="x", padx=18, pady=(0, 12))
        self._sidebar_hint_visible = True
        self._sidebar_version_visible = True
        sidebar.bind("<Configure>", self._layout_sidebar, add="+")

        main = ttk.Frame(shell, style="App.TFrame")
        main.pack(side="left", fill="both", expand=True)
        header = ttk.Frame(main, padding=(22, 20, 22, 14), style="App.TFrame")
        header.pack(fill="x")
        self.page_header = header
        page_brand(header, f"HOST · {VERSION}").pack(fill="x", pady=(0, 12))
        title = ttk.Label(header, text="Receba suporte com segurança", style="Title.TLabel", wraplength=680)
        title.pack(anchor="w")
        subtitle = ttk.Label(header, text="Compartilhe o convite e autorize quem pode acessar este computador.",
                             style="Muted.TLabel", wraplength=680)
        subtitle.pack(anchor="w", pady=(5, 0))
        header.bind("<Configure>", lambda event: [label.configure(wraplength=max(200, event.width - 44))
                                                for label in (title, subtitle)], add="+")
        self.session_card = card(main, padding=(18, 12))
        self.session_card.pack(side="bottom", fill="x", padx=(20, 12), pady=(0, 16))
        self.form = ScrollForm(main)
        self.form.pack(fill="both", expand=True, padx=(20, 12), pady=(0, 16))
        content = self.form.content
        content.bind("<Configure>", self._resize_labels, add="+")

        state_row = ttk.Frame(self.session_card, style="CardBody.TFrame")
        state_row.pack(fill="x", pady=(0, 7))
        ttk.Label(state_row, text="Seu acesso", style="CardHeading.TLabel").pack(side="left")
        self.state_badge = tk.Label(state_row, text="PARADO", bg="#EEF5F1", fg=MUTED, padx=10, pady=4)
        self.state_badge.pack(side="right")
        self.state_hint = tk.StringVar(value="Inicie o hospedeiro para receber uma solicitação de acesso.")
        self.session_hint_label = self._card_label(self.session_card, variable=self.state_hint, muted=True)
        self.session_hint_label.pack(anchor="w", fill="x", pady=(0, 6))
        self.available_check = ttk.Checkbutton(self.session_card, text="Ficar disponível enquanto esta janela estiver aberta",
                                               style="Card.TCheckbutton", variable=self.available_while_open,
                                               command=self._availability_changed)
        self.available_check.pack(anchor="w", pady=(0, 7))
        actions = ttk.Frame(self.session_card, style="CardBody.TFrame")
        actions.pack(fill="x")
        for column in range(4):
            actions.columnconfigure(column, weight=1, uniform="actions")
        self.start_button = ttk.Button(actions, text="Iniciar", style="Primary.TButton", command=self.start_listener)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 7))
        self.pause_button = ttk.Button(actions, text="Pausar", style="Secondary.TButton", command=self.toggle_pause, state="disabled")
        self.pause_button.grid(row=0, column=1, sticky="ew", padx=(0, 7))
        self.stop_button = ttk.Button(actions, text="Parar acesso", style="Danger.TButton", command=self.stop_listener, state="disabled")
        self.stop_button.grid(row=0, column=2, sticky="ew", padx=(0, 7))
        self.disconnect_button = ttk.Button(actions, text="Desconectar", style="Secondary.TButton", command=self.disconnect, state="disabled")
        self.disconnect_button.grid(row=0, column=3, sticky="ew")
        self.actions_frame = actions
        self._session_buttons = (self.start_button, self.pause_button, self.stop_button, self.disconnect_button)
        self._action_columns = None
        actions.bind("<Configure>", self._layout_actions, add="+")

        self.guide_card = card(content, padding=(16, 12))
        self.guide_card.pack(fill="x", pady=(0, 12))
        self.guide_steps = []
        for index, text in enumerate(("1  Copie o convite", "2  Envie à pessoa", "3  Autorize aqui")):
            label = ttk.Label(self.guide_card, text=text, style="Step.TLabel", wraplength=220)
            label.grid(row=0, column=index, sticky="w", padx=(0, 12 if index < 2 else 0))
            self.guide_steps.append(label)
        self._guide_columns = None
        self.guide_card.bind("<Configure>", self._layout_guide, add="+")

        self.connection_card = card(content, padding=18)
        self.connection_card.pack(fill="x", pady=(0, 12))
        connection_heading = ttk.Frame(self.connection_card, style="CardBody.TFrame")
        connection_heading.pack(fill="x")
        ttk.Label(connection_heading, text="Convite para este computador", style="CardHeading.TLabel").pack(side="left")
        self.copy_button = ttk.Button(connection_heading, text="Copiar convite", style="Primary.TButton", command=self.copy_connection, state="disabled")
        self.copy_button.pack(side="right", padx=(10, 0))
        self.copy_notice = tk.StringVar(value="O convite reúne código e senha. Envie à pessoa que abrirá o Controlador.")
        self.copy_notice_label = self._card_label(self.connection_card, variable=self.copy_notice, muted=True)
        self.copy_notice_label.pack(anchor="w", fill="x", pady=(7, 12))

        # The private code and the password belong together in the first view.
        # Transport setup stays owned by MeshPanel; no second setup screen is
        # necessary to perform the everyday receive/share flow.
        self.mesh_card = ttk.Frame(self.connection_card, style="CardBody.TFrame")
        self.mesh_card.pack(fill="x", pady=(0, 14))
        from mesh_ui import MeshPanel
        self.mesh_panel = MeshPanel(self.mesh_card, role="host", config_dir=self.state_dir / "mesh",
                                    on_status=self._mesh_status_changed, on_revoke=self.disconnect, compact=True)
        self.mesh_panel.pack(fill="both", expand=True)
        self.internet_setup_button = getattr(self.mesh_panel, "prepare_button", None)

        self.manual_address_open = False
        self.manual_address_panel = ttk.Frame(self.connection_card, style="CardBody.TFrame")
        self._card_label(self.manual_address_panel, "Conexão na mesma rede", muted=True).pack(anchor="w", pady=(0, 4))
        family = getattr(self.root, "_minianydesk_ui_font", "Segoe UI")
        address_row = ttk.Frame(self.manual_address_panel, style="CardBody.TFrame")
        address_row.pack(fill="x")
        address_row.columnconfigure(0, weight=1)
        self.address_entry = ttk.Combobox(address_row, textvariable=self.addresses, state="readonly",
                                           font=(family, 11), width=15, values=())
        self.address_entry.grid(row=0, column=0, sticky="ew")
        self.address_entry.bind("<<ComboboxSelected>>", self._choose_address)
        self.copy_ip_button = ttk.Button(address_row, text="Copiar IP", style="Secondary.TButton",
                                          command=self.copy_address, state="disabled")
        self.copy_ip_button.grid(row=0, column=1, padx=(8, 0))
        self.refresh_address_button = ttk.Button(address_row, text="Atualizar", style="Secondary.TButton",
                                                  command=self.refresh_addresses)
        self.refresh_address_button.grid(row=0, column=2, padx=(8, 0))
        self._card_label(self.manual_address_panel, variable=self.address_hint, muted=True).pack(anchor="w", fill="x", pady=(6, 12))
        ttk.Separator(self.connection_card).pack(fill="x", pady=(0, 12))
        self._card_label(self.connection_card, "Senha de acesso", muted=True).pack(anchor="w", pady=(0, 5))
        password_row = ttk.Frame(self.connection_card, style="CardBody.TFrame")
        password_row.pack(fill="x")
        password_row.columnconfigure(0, weight=1)
        self.password_entry = ttk.Entry(password_row, textvariable=self.password, show="*", width=1,
                                        style="Password.TEntry", font=(family, 18, "bold"), justify="center")
        self.password_entry.grid(row=0, column=0, sticky="ew")
        self.password_toggle_button = ttk.Button(password_row, text="Mostrar senha", style="Password.TButton",
                                                 command=self._toggle_password_visibility)
        self.password_toggle_button.grid(row=0, column=1, sticky="ns", padx=(10, 0))
        self.password_actions = ttk.Frame(self.connection_card, style="CardBody.TFrame")
        self.password_actions.pack(fill="x", pady=(8, 0))
        self.new_password_button = ttk.Button(self.password_actions, text="Nova senha", style="Quiet.TButton", command=self.new_password)
        self.new_password_button.pack(side="left")
        self.copy_password_button = ttk.Button(self.password_actions, text="Copiar só a senha", style="Quiet.TButton", command=self.copy_password)
        self.copy_password_button.pack(side="left", padx=(8, 0))
        self._card_label(self.connection_card, "A senha muda ao reabrir o aplicativo. Para trocar agora, pare o acesso primeiro.", muted=True).pack(anchor="w", fill="x", pady=(7, 0))
        self.manual_address_button = ttk.Button(self.connection_card, text="Mesmo Wi-Fi ou cabo? Ver IP",
                                                style="Link.TButton", command=self._toggle_manual_address)
        self.manual_address_button.pack(anchor="w", pady=(12, 0))

        self.identity_card = card(content, padding=18)
        self.identity_card.pack(fill="x", pady=(0, 12))
        ttk.Label(self.identity_card, text="Este computador", style="CardHeading.TLabel").pack(anchor="w")
        self.name_feedback = tk.StringVar(value="Dê um nome fácil de reconhecer, como Notebook do Bruno.")
        identity_row = ttk.Frame(self.identity_card, style="CardBody.TFrame")
        identity_row.pack(fill="x", pady=(8, 0))
        identity_row.columnconfigure(0, weight=1)
        self.name_entry = ttk.Entry(identity_row, textvariable=self.computer_name, width=1)
        self.name_entry.grid(row=0, column=0, sticky="ew")
        self.name_entry.bind("<Return>", lambda event: self.save_computer_name())
        self.save_name_button = ttk.Button(identity_row, text="Salvar nome", style="Secondary.TButton", command=self.save_computer_name)
        self.save_name_button.grid(row=0, column=1, padx=(10, 0))
        self._card_label(self.identity_card, variable=self.name_feedback, muted=True).pack(anchor="w", fill="x", pady=(8, 0))

        self.files_card = card(content, padding=18)
        self.files_card.pack(fill="x", pady=(0, 12))
        ttk.Label(self.files_card, text="Trocar arquivos", style="CardHeading.TLabel").pack(anchor="w")
        self._card_label(self.files_card, "Durante a sessão, envie um arquivo ou peça um à outra pessoa. Quem recebe escolhe onde salvar.", muted=True).pack(anchor="w", pady=(4, 12))
        files = ttk.Frame(self.files_card, style="CardBody.TFrame")
        files.pack(fill="x")
        self.send_button = ttk.Button(files, text="Enviar arquivo", style="Primary.TButton", command=self.send_file, state="disabled")
        self.request_button = ttk.Button(files, text="Solicitar arquivo", style="Secondary.TButton", command=self.request_file, state="disabled")
        self.cancel_button = ttk.Button(files, text="Cancelar", style="Quiet.TButton", command=self.cancel_transfer, state="disabled")
        self.file_actions_frame = files
        self._file_buttons = (self.send_button, self.request_button, self.cancel_button)
        self._file_columns = None
        files.bind("<Configure>", self._layout_file_actions, add="+")
        for column, button in enumerate(self._file_buttons):
            button.grid(row=0, column=column, sticky="ew")
        self._card_label(self.files_card, variable=self.transfer_status, muted=True).pack(anchor="w", fill="x", pady=(12, 0))

        self.advanced_panel = card(content, padding=18)
        self.advanced_panel.pack(fill="x", pady=(0, 12))
        self.advanced_button = ttk.Button(self.advanced_panel, text="Ajuda e detalhes  +", style="Quiet.TButton", command=self.toggle_advanced)
        self.advanced_button.pack(fill="x")
        self.advanced_content = ttk.Frame(self.advanced_panel, style="CardBody.TFrame")
        self._card_label(self.advanced_content, "Como a conexão funciona", muted=False).pack(anchor="w", pady=(14, 5))
        self._card_label(self.advanced_content, "1. Copie o convite e envie a quem vai acessar.\n2. A pessoa cola o código no Controlador e informa a senha.\n3. Quando a solicitação aparecer aqui, confira o nome e clique em Sim.", muted=True).pack(anchor="w", fill="x")
        self._card_label(self.advanced_content, "Em redes diferentes, use o código completo: ele prepara a conexão pela internet sem login. Os dois computadores precisam estar ligados e com internet.\nNa mesma rede, também é possível usar o IP. Pausar interrompe tela, mouse e teclado; Parar acesso encerra a disponibilidade.", muted=True).pack(anchor="w", fill="x", pady=(8, 0))
        self._card_label(self.advanced_content, "Endereços detectados", muted=True).pack(anchor="w", pady=(14, 4))
        self._card_label(self.advanced_content, variable=self.address_details, muted=True).pack(anchor="w", fill="x")
        network = ttk.Frame(self.advanced_content, style="CardBody.TFrame")
        network.pack(fill="x", pady=(14, 0))
        self._card_label(network, "Porta TCP", muted=True).pack(side="left")
        self.port_entry = ttk.Entry(network, textvariable=self.port, width=10)
        self.port_entry.pack(side="left", padx=(10, 0))
        self._card_label(self.advanced_content, "Certificado SHA-256", muted=True).pack(anchor="w", pady=(14, 4))
        self.fingerprint_entry = ttk.Entry(self.advanced_content, textvariable=self.fingerprint, state="readonly")
        self.fingerprint_entry.pack(fill="x")
        self._card_label(self.advanced_content, "Confira o certificado com a pessoa autorizada antes de conectar.", muted=True).pack(anchor="w", pady=(5, 14))
        self._card_label(self.advanced_content, "Registros", muted=True).pack(anchor="w", pady=(0, 5))
        log_frame = ttk.Frame(self.advanced_content, style="CardBody.TFrame")
        log_frame.pack(fill="both", expand=True)
        self.log_box = tk.Text(log_frame, width=1, height=8, wrap="word", font="TkFixedFont",
                               bg=WHITE, fg=INK, relief="flat", highlightthickness=1,
                               highlightbackground=BORDER, bd=0, padx=8, pady=8, state="disabled")
        self.log_box.pack(side="left", fill="both", expand=True)
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_box.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_box.configure(yscrollcommand=log_scroll.set)
        self._card_label(content, "Para sair, feche esta janela. O aplicativo não continua compartilhando em segundo plano.", muted=True).pack(anchor="w", fill="x", pady=(2, 6))
        # Preserve a useful scroll viewport when larger system fonts make the
        # fixed header and consent actions taller. Derive the minimum from the
        # real Tk measurements rather than allowing them to hide the form.
        self._viewport_minimum = max(180, tkfont.Font(root=self.root, font=(family, 10)).metrics("linespace") * 7)
        header.bind("<Configure>", self._minimum_layout_height, add="+")
        self.session_card.bind("<Configure>", self._minimum_layout_height, add="+")
        self.root.after_idle(self._minimum_layout_height)
        self._gui_after_id = self.root.after(200, self._poll_gui_state)

    def _minimum_layout_height(self, event=None) -> None:
        required = (self.page_header.winfo_reqheight() + self.session_card.winfo_reqheight()
                    + self._viewport_minimum + 32)
        width, _height = self.root.minsize()
        self.root.minsize(width, max(540, required))

    def _layout_guide(self, event=None) -> None:
        """Keep the three short steps readable at narrow widths and large fonts."""
        available = self.guide_card.winfo_width() - 34
        required = sum(label.winfo_reqwidth() for label in self.guide_steps) + 24
        columns = 3 if available >= required else 1
        if columns == self._guide_columns:
            return
        self._guide_columns = columns
        for column in range(3):
            self.guide_card.columnconfigure(column, weight=1 if column < columns else 0)
        for index, label in enumerate(self.guide_steps):
            label.grid_configure(row=0 if columns == 3 else index,
                                 column=index if columns == 3 else 0,
                                 padx=(0, 12 if columns == 3 and index < 2 else 0),
                                 pady=(0, 4 if columns == 1 and index < 2 else 0))

    def _card_label(self, parent, text=None, *, variable=None, muted=False):
        options = {"style": "CardMuted.TLabel" if muted else "Card.TLabel", "wraplength": 520}
        if parent is self.form.content:
            options["style"] = "Muted.TLabel" if muted else "TLabel"
        if variable is not None:
            options["textvariable"] = variable
        else:
            options["text"] = text
        label = ttk.Label(parent, **options)
        self._wrap_labels.append(label)
        return label

    def _layout_sidebar(self, event) -> None:
        navigation_height = (self.sidebar_brand.winfo_reqheight() + 34 +
                             sum(button.winfo_reqheight() + 6 for button in self.nav_buttons.values()))
        with_version = navigation_height + self.sidebar_version.winfo_reqheight() + 32
        version_visible = event.height >= with_version
        hint_visible = event.height >= with_version + self.sidebar_hint.winfo_reqheight() + 24
        if (version_visible, hint_visible) != (self._sidebar_version_visible, self._sidebar_hint_visible):
            self._sidebar_version_visible = version_visible
            self._sidebar_hint_visible = hint_visible
            # Repack both in their original order. At large font sizes the
            # optional footer yields space to every navigation destination.
            self.sidebar_version.pack_forget()
            self.sidebar_hint.pack_forget()
            if version_visible:
                self.sidebar_version.pack(side="bottom", fill="x", padx=18, pady=16)
            if hint_visible:
                self.sidebar_hint.pack(side="bottom", fill="x", padx=18, pady=(0, 12))

    def _resize_labels(self, event) -> None:
        for label in self._wrap_labels:
            label.configure(wraplength=max(220, event.width - 38))

    def _layout_actions(self, event=None) -> None:
        available = self.actions_frame.winfo_width()
        required = max(button.winfo_reqwidth() for button in self._session_buttons) * 4 + 21
        columns = 4 if available >= required else 2
        if columns == self._action_columns:
            return
        self._action_columns = columns
        for column in range(4):
            self.actions_frame.columnconfigure(column, weight=1 if column < columns else 0,
                                               uniform="actions" if column < columns else "")
        for index, button in enumerate(self._session_buttons):
            row, column = divmod(index, columns)
            button.grid_configure(row=row, column=column, sticky="ew",
                                  padx=(0, 7 if column < columns - 1 else 0),
                                  pady=(0, 7 if columns == 2 and row == 0 else 0))

    def _layout_file_actions(self, event=None) -> None:
        available = self.file_actions_frame.winfo_width()
        required = max(button.winfo_reqwidth() for button in self._file_buttons) * 3 + 16
        columns = 3 if available >= required else 2
        if columns == self._file_columns:
            return
        self._file_columns = columns
        for column in range(3):
            self.file_actions_frame.columnconfigure(column, weight=1 if column < columns else 0,
                                                    uniform="files" if column < columns else "")
        for index, button in enumerate(self._file_buttons):
            row, column = divmod(index, columns)
            button.grid_configure(row=row, column=column, sticky="ew",
                                  padx=(0, 8 if column < columns - 1 else 0),
                                  pady=(0, 7 if columns == 2 and row == 0 else 0))

    def _navigate(self, section: str) -> None:
        if section != "Internet sem login" and self.mesh_panel is not None:
            self.mesh_panel.hide_code()
        for name, button in self.nav_buttons.items():
            button.configure(style="SidebarSelected.TButton" if name == section else "Sidebar.TButton")
        if section == "Rede e registros" and not self.advanced_open:
            self.toggle_advanced()
        target = {"Meu computador": self.identity_card, "Dados de conexão": self.connection_card,
                  "Internet sem login": self.mesh_card, "Arquivos": self.files_card,
                  "Rede e registros": self.advanced_panel}[section]
        self.root.update_idletasks()
        # Internet controls now sit inside the invitation card. Resolve any
        # nesting depth instead of treating every destination as a direct child.
        offset = target.winfo_rooty() - self.form.content.winfo_rooty()
        self.form.canvas.yview_moveto(max(0, offset) / max(1, self.form.content.winfo_height()))

    def _open_mesh_setup(self) -> None:
        if self.shutdown.is_set():
            return
        self._navigate("Internet sem login")
        if self.server is None and not self.starting:
            self.start_listener()
        elif self.mesh_panel is not None:
            self.mesh_panel.prepare_access()

    def _toggle_manual_address(self) -> None:
        self.manual_address_open = not self.manual_address_open
        self.manual_address_button.configure(text="Ocultar IP da rede local" if self.manual_address_open
                                             else "Mesmo Wi-Fi ou cabo? Ver IP")
        if self.manual_address_open:
            self.manual_address_panel.pack(fill="x", pady=(12, 0))
        else:
            self.manual_address_panel.pack_forget()

    def _set_available_while_open(self, enabled: bool) -> None:
        option = getattr(self, "available_while_open", None)
        if option is None:
            return
        changed = option.get() != bool(enabled)
        option.set(bool(enabled))
        if changed:
            self._save_availability_choice()

    def _save_availability_choice(self) -> None:
        try:
            save_available_while_open(self.state_dir, self.available_while_open.get())
        except OSError as error:
            self.log(f"Não foi possível guardar a preferência de disponibilidade: {error}")
            messagebox.showwarning("Preferência não salva",
                                   "A alteração vale nesta janela. Não foi possível guardá-la para a próxima abertura.",
                                   parent=self.root)

    def _availability_changed(self) -> None:
        self._save_availability_choice()
        if self.available_while_open.get():
            self._start_available_while_open()
        else:
            self.stop_listener()

    def _start_available_while_open(self) -> None:
        self._availability_after_id = None
        if not self.shutdown.is_set() and self.available_while_open.get():
            self.start_listener()

    def toggle_advanced(self) -> None:
        self.advanced_open = not self.advanced_open
        self.advanced_button.configure(text="Ajuda e detalhes  −" if self.advanced_open else "Ajuda e detalhes  +")
        if self.advanced_open:
            self.advanced_content.pack(fill="both", expand=True)
        else:
            self.advanced_content.pack_forget()

    def _poll_addresses(self) -> None:
        self._address_after_id = None
        if self.shutdown.is_set():
            return
        self.refresh_addresses()
        self._address_after_id = self.root.after(ADDRESS_REFRESH_MS, self._poll_addresses)

    def refresh_addresses(self) -> bool:
        """One background discovery at a time; its result is applied only by Tk."""
        with self.control_lock:
            if self.shutdown.is_set() or self._address_refresh_busy:
                return False
            self._address_refresh_busy = True
        self.refresh_address_button.configure(text="Atualizando…", state="disabled")

        def worker():
            error = None
            try:
                candidates = tuple(dict.fromkeys(address for address in local_addresses() if _usable_address(address)))
            except Exception as failure:
                candidates, error = (), str(failure)[:300]
            with self.control_lock:
                if self.shutdown.is_set():
                    self._address_refresh_busy = False
                    return
                if not self.dispatch(lambda: self._apply_addresses(candidates, error)):
                    self._address_refresh_busy = False

        threading.Thread(target=worker, daemon=True, name="MiniAnyDesk-network-addresses").start()
        return True

    def _mesh_status_changed(self, status) -> None:
        if self.shutdown.is_set():
            return
        self.mesh_status = status
        if self.server is not None and self.peer is None and not self.starting:
            self.status.set(status.message)

    def _apply_addresses(self, candidates: tuple[str, ...], error: str | None = None) -> None:
        if self.shutdown.is_set():
            return
        with self.control_lock:
            self._address_refresh_busy = False
        self._network_address_candidates = tuple(candidates)
        self.refresh_address_button.configure(text="Atualizar", state="normal")
        self._update_address_selection(error)

    def _update_address_selection(self, error: str | None = None) -> None:
        candidates = self._network_address_candidates
        previous = self._address_candidates
        previous_selected = self.addresses.get()
        self._address_candidates = candidates
        if self._manual_address not in candidates:
            self._manual_address = None
        # Automatic selection follows the shared route-aware ordering and replaces
        # an automatic APIPA when DHCP obtains a useful address.
        selected = self._manual_address or next(iter(candidates), "")
        self.addresses.set(selected)
        if previous != candidates or previous_selected != selected:
            if selected and not getattr(self, "manual_address_open", True):
                self.copy_notice.set("Use Copiar convite para enviar código e senha juntos à pessoa autorizada.")
            else:
                self.copy_notice.set("Endereço atualizado. Copie novamente o IP ou os dados de conexão."
                                     if selected else "Sem IP disponível. Confira a rede e clique em Atualizar.")
        self.address_entry.configure(values=candidates)
        self._show_address_details(error)
        if previous != candidates:
            if selected:
                self.log(f"Endereços de rede atualizados. IP selecionado: {selected}. As sessões ativas foram mantidas.")
            else:
                self.log("Nenhum IPv4 local disponível. A conexão pela internet usa o código privado e não depende deste IP.")
        if error:
            self._diagnostic("addresses", f"Não foi possível identificar os endereços de rede: {error}")
        self._sync_gui_state()

    def _selected_address(self) -> str:
        address = self.addresses.get()
        return address if address in self._address_candidates and _usable_address(address) else ""

    def _choose_address(self, event=None) -> None:
        selected = self._selected_address()
        self._manual_address = selected or None
        self.copy_notice.set("Endereço selecionado. Copie novamente o IP ou os dados de conexão."
                             if selected else "Sem IP disponível. Confira a rede e clique em Atualizar.")
        self._show_address_details()
        self._sync_gui_state()

    def _show_address_details(self, error: str | None = None) -> None:
        selected = self._selected_address()
        self.copy_ip_button.configure(state="normal" if selected else "disabled")
        labels = {"link-local": "APIPA · alcance local", "local": "rede local", "network": "interface de rede"}
        self.address_details.set("\n".join(
            f"{address} — {labels.get(classify_address(address), 'interface de rede')}"
            + (" · selecionado" if address == selected else "") for address in self._address_candidates
        ) or "Nenhum IPv4 local disponível neste momento.")
        if error:
            hint = "Não foi possível atualizar a rede local; tente Atualizar. Acompanhe a internet na opção Internet."
        elif not selected:
            hint = "Sem IPv4 local disponível. Para acessar por outra rede, use o código privado de conexão."
        elif classify_address(selected) == "link-local":
            hint = "IP 169.254 (APIPA): alcance local limitado. Confira Wi-Fi, cabo e DHCP. Para redes diferentes, use o código privado."
        else:
            selection = "escolhido" if self._manual_address else "sugerido"
            hint = f"IP {selection}, opcional para a mesma rede. Em redes diferentes, use o código privado de conexão."
        self.address_hint.set(hint)

    def copy_address(self) -> None:
        selected = self._selected_address()
        if not selected:
            self.copy_notice.set("Nenhum IP disponível para copiar. Confira a rede e clique em Atualizar.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(selected)
        self.copy_notice.set(f"IP copiado: {selected}. Informe também a porta e os dados da sessão.")

    def _poll_gui_state(self) -> None:
        if self.shutdown.is_set():
            return
        self._sync_gui_state()
        self._gui_after_id = self.root.after(200, self._poll_gui_state)

    def _sync_gui_state(self) -> None:
        with self.control_lock:
            running, peer, paused, prompts = self.server is not None, self.peer, self.local_paused, self.prompt_count
        if self.starting:
            badge, color, ink = "PREPARANDO", "#E4F7EC", INK
            hint = "Aguarde a preparação dos dados de conexão."
        elif not running:
            badge, color, ink = "INDISPONÍVEL", "#EEF5F1", MUTED
            hint = "Clique em Iniciar para preparar o convite e receber solicitações."
        elif prompts:
            badge, color, ink = "AGUARDANDO VOCÊ", "#FFF3D6", INK
            hint = "Confira a solicitação. A tela e o controle ficam bloqueados enquanto você decide."
        elif paused:
            badge, color, ink = "PAUSADO", "#FFF3D6", INK
            hint = "Tela e controle pausados. Só você pode retomar neste computador."
        elif peer is not None:
            badge, color, ink = "CONECTADO", "#DFF3E8", "#176442"
            hint = "Acesso aprovado. Você pode pausar ou encerrar a qualquer momento."
        else:
            badge, color, ink = "DISPONÍVEL", "#E4F7EC", INK
            hint = "Compartilhe o convite. O acesso só começa depois da sua aprovação aqui."
        self.state_badge.configure(text=badge, bg=color, fg=ink)
        self.state_hint.set(hint)
        self.pause_button.configure(text="Retomar" if paused else "Pausar", state="normal" if running and not prompts else "disabled")
        self.copy_button.configure(state="normal" if running and not self.starting and (getattr(getattr(self, "mesh_panel", None), "pairing", None) is not None or self._selected_address()) else "disabled")
        self.disconnect_button.configure(state="normal" if running and peer is not None else "disabled")
        for button in (self.send_button, self.request_button, self.cancel_button):
            button.configure(state="normal" if running and peer is not None and not prompts else "disabled")

    def dispatch(self, action) -> bool:
        with self.control_lock:
            if self.shutdown.is_set():
                return False
            self.ui_queue.put(action)
            return True

    def _drain_ui(self) -> None:
        if self.shutdown.is_set():
            return
        for _ in range(80):
            try:
                action = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                action()
            except Exception as error:
                self._diagnostic("ui", f"Erro de interface: {str(error)[:700]}")
        self._ui_after_id = self.root.after(40, self._drain_ui)

    def _write_log(self, text: str) -> None:
        text = str(text)[:1024].replace("\r", " ").replace("\n", " ")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{datetime.now():%H:%M:%S}] {text}\n")
        self.log_box.see("end")
        if int(self.log_box.index("end-1c").split(".")[0]) > 400:
            self.log_box.delete("1.0", "101.0")
        self.log_box.configure(state="disabled")

    def log(self, text: str) -> None:
        if self.shutdown.is_set():
            return
        text = str(text)[:1024].replace("\r", " ").replace("\n", " ")
        with self.control_lock:
            if not hasattr(self, "_pending_logs"):
                self._pending_logs = deque(maxlen=80)
                self._log_queued = False
            self._pending_logs.append(text)
            if self._log_queued:
                return
            self._log_queued = True
        def flush():
            with self.control_lock:
                messages = list(self._pending_logs)
                self._pending_logs.clear()
                self._log_queued = False
            for message in messages:
                self._write_log(message)
        if self.dispatch(flush) is False:
            with self.control_lock:
                self._pending_logs.clear()
                self._log_queued = False

    def _diagnostic(self, category: str, text: str) -> None:
        with self.control_lock:
            if not hasattr(self, "_diagnostic_times"):
                self._diagnostic_times = {}
            now = time.monotonic()
            if now - self._diagnostic_times.get(category, float("-inf")) < 5.0:
                return
            self._diagnostic_times[category] = now
        self.log(text)

    def new_password(self) -> None:
        if self.server is None and not self.starting:
            self.password.set(generate_session_password())
            self._set_password_visible(False)
            if hasattr(self, "copy_notice"):
                self.copy_notice.set("Nova senha gerada. Inicie o compartilhamento para receber conexões.")

    def _set_password_visible(self, visible: bool) -> None:
        self.show_password.set(bool(visible))
        self.password_entry.configure(show="" if visible else "*")
        self.password_toggle_button.configure(text="Ocultar senha" if visible else "Mostrar senha")

    def _toggle_password_visibility(self) -> None:
        self._set_password_visible(not self.show_password.get())

    def copy_password(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(self.password.get())
        if hasattr(self, "copy_notice"):
            self.copy_notice.set("Senha copiada. A outra pessoa também precisa do código. Use Copiar convite para reunir os dois.")
        self.log("Senha da sessão copiada para a área de transferência.")

    def _presence_name(self) -> str:
        # Status workers read the cache, never Tcl/Tk variables.
        with self.control_lock:
            return self._computer_name_cache

    def save_computer_name(self) -> None:
        try:
            name = save_computer_name(self.state_dir, self.computer_name.get())
        except (OSError, ValueError) as error:
            self._begin_prompt()
            try:
                messagebox.showerror("Nome do computador", str(error), parent=self.root)
            finally:
                self._end_prompt()
            return
        with self.control_lock:
            self._computer_name_cache = name
        self.computer_name.set(name)
        if hasattr(self, "name_feedback"):
            self.name_feedback.set("Nome salvo. Ele será exibido para quem se conecta.")
        self._emit_state()
        self.log(f"Nome deste computador salvo: {name}")

    def copy_connection(self) -> None:
        pairing = getattr(getattr(self, "mesh_panel", None), "pairing", None)
        address = self._selected_address()
        if pairing is None and not address:
            self.copy_notice.set("Nenhum IP disponível. Inicie o Host e aguarde o código de conexão pela internet.")
            return
        text = f"AliançaDX Host {VERSION}\nComputador: {self._presence_name()}\n"
        if pairing is not None:
            text += f"Código privado: {pairing.code}\n"
        if address:
            text += f"IP: {address}\nPorta TCP: {self.port.get()}\n"
        text += f"Senha: {self.password.get()}\nCertificado SHA-256: {self.fingerprint.get()}"
        text += ("\n\nComo conectar: abra o AliançaDX Acesso Remoto — Controlador, cole o código privado e informe a senha. "
                 "Aguarde a aprovação no computador que receberá o acesso."
                 if pairing is not None else
                 "\n\nComo conectar: no AliançaDX Acesso Remoto — Controlador, use a conexão manual com o IP e a porta acima, "
                 "informe a senha e aguarde a aprovação neste computador. O IP funciona na mesma rede.")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.copy_notice.set("Convite copiado: código e senha juntos. Envie à pessoa autorizada e aguarde a solicitação aqui."
                             if pairing is not None else
                             "Dados da rede local copiados. Para acessar de outra rede, aguarde o código de internet.")
        self.log("Dados de conexão copiados para compartilhamento autorizado.")

    def start_listener(self) -> None:
        if self.shutdown.is_set() or self.server is not None or self.starting:
            return
        try:
            port = int(self.port.get())
            if not 1024 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("Porta inválida", "Informe uma porta entre 1024 e 65535.", parent=self.root)
            return
        password = self.password.get()
        if not 8 <= len(password) <= 256 or not password.strip():
            messagebox.showerror("Senha inválida", "Use a senha automática de 8 números ou uma senha personalizada de 8 a 256 caracteres. Não use apenas espaços.", parent=self.root)
            return
        self._set_available_while_open(True)
        token = self._listener_start_token = object()
        self.starting = True
        self.status.set("Preparando conexão TLS…")
        self.start_button.configure(state="disabled")
        self.port_entry.configure(state="disabled")
        self.password_entry.configure(state="disabled")
        self.new_password_button.configure(state="disabled")
        self.stop_button.configure(state="normal")

        def worker():
            server = None
            try:
                from net import HostServer
                server = HostServer("0.0.0.0", port, password, self._connection_request,
                                    self._connected, self._disconnected, self.state_dir, status_callback=self._presence_name)
                actual_port = server.start()
                with self.control_lock:
                    cancelled = self.shutdown.is_set() or self._listener_start_token is not token
                    if not cancelled:
                        self.server = server
                if cancelled:
                    server.stop()
                    return
                def done():
                    if self.shutdown.is_set() or self._listener_start_token is not token or self.server is not server:
                        return
                    self.starting = False
                    self.port.set(str(actual_port))
                    self.fingerprint.set(server.fingerprint)
                    self.status.set(f"Aguardando conexão autorizada · TCP {actual_port}")
                    self.stop_button.configure(state="normal")
                    self.log("Host iniciado. Preparando a conexão privada pela internet, sem conta.")
                    try:
                        self.mesh_panel.configure_host(actual_port, server.fingerprint)
                    except Exception as error:
                        self.status.set("Host local disponível; a rede pela internet não iniciou")
                        self.log(f"Não foi possível preparar a rede privada: {error}")
                self.dispatch(done)
            except Exception as error:
                if server is not None:
                    server.stop()
                error_text = str(error)
                def failed():
                    if self.shutdown.is_set() or self._listener_start_token is not token:
                        return
                    self.starting = False
                    self._listener_controls(False)
                    self.status.set("Não foi possível iniciar o Host")
                    self.log(error_text)
                    messagebox.showerror("Erro ao iniciar Host", error_text, parent=self.root)
                self.dispatch(failed)
        threading.Thread(target=worker, daemon=True, name="MiniAnyDesk-listener-start").start()

    def _listener_controls(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.port_entry.configure(state="disabled" if running else "normal")
        self.password_entry.configure(state="disabled" if running else "normal")
        self.new_password_button.configure(state="disabled" if running else "normal")

    def stop_listener(self) -> None:
        if getattr(self, "mesh_panel", None) is not None:
            self.mesh_panel.stop()
        self._set_available_while_open(False)
        job = getattr(self, "_availability_after_id", None)
        if job is not None:
            self.root.after_cancel(job)
            self._availability_after_id = None
        with self.control_lock:
            self._listener_start_token = None
            self.starting = False
            server, self.server = self.server, None
            peer = self.peer
            self.generation += 1
            self.last_screen = None
            self.inputs.release()
            for event in tuple(self.pending_consents):
                event.set()
        if peer is not None:
            self._disconnected(peer, "Host encerrado localmente.")
        if server is not None:
            threading.Thread(target=server.stop, daemon=True).start()
        self._listener_controls(False)
        self._set_password_visible(False)
        self.status.set("Host parado")
        self.log("Host parado; novas conexões não serão aceitas.")

    def _begin_prompt(self) -> None:
        # This is synchronous in the network worker, before any UI scheduling.
        with self.control_lock:
            self.prompt_count += 1
            self.generation += 1
            self.last_screen = None
            self.inputs.release()
        self._emit_state()

    def _end_prompt(self) -> None:
        with self.control_lock:
            self.prompt_count = max(0, self.prompt_count - 1)
            self.generation += 1
            self.last_screen = None
        self._emit_state()

    def _connection_request(self, name: str, address) -> bool:
        with self.control_lock:
            if self.shutdown.is_set() or self.server is None or self.peer is not None:
                return False
        self._begin_prompt()
        finished = threading.Event()
        answer = {"accepted": False}
        with self.control_lock:
            self.pending_consents.add(finished)
        display_name = " ".join(str(name).split())[:100] or "Visitante"
        display_address = str(address)[:120]

        def prompt():
            try:
                if not finished.is_set() and not self.shutdown.is_set():
                    answer["accepted"] = messagebox.askyesno(
                        "Autorizar acesso remoto?",
                        f"{display_name}\nEndereço: {display_address}\n\n"
                        "Permitir que esta pessoa veja a tela e controle mouse e teclado?\n"
                        "Você pode pausar ou desconectar a qualquer momento.", default="no", parent=self.root)
            finally:
                self._end_prompt()
                finished.set()

        if not self.dispatch(prompt):
            self._end_prompt()
            finished.set()
        answered_in_time = finished.wait(60)
        if not answered_in_time:
            # An expired queued question must never appear later as a fresh
            # authorization. A dialog already open keeps its guard until close.
            finished.set()
            self.log("Solicitação de acesso expirou sem confirmação local.")
        with self.control_lock:
            self.pending_consents.discard(finished)
            allowed = answered_in_time and answer["accepted"] and not self.shutdown.is_set() and self.server is not None
        return bool(allowed)

    def _connected(self, peer, name: str, address) -> None:
        from transfer import FileTransfers
        with self.control_lock:
            if self.shutdown.is_set() or self.server is None or self.peer is not None or peer.closed:
                peer.close()
                return
            self.peer = peer
            self.stream = AdaptiveStream()
            self.sharing = True
            self.monitor_id = "all"
            self.generation += 1
            self.last_screen = None
            self.inputs.release()
            self.transfers = FileTransfers(peer, self.dispatch, self._ask_receive,
                                           lambda update: self._transfer_status(update, peer), on_request_file=self._pick_requested_file,
                                           prompt_guard=lambda active: self._begin_prompt() if active else self._end_prompt(),
                                           status_dispatch=lambda action: action())
            peer.on_message = lambda message: self._message(peer, message)
        display_name = " ".join(str(name).split())[:100] or "Visitante"
        def connected_ui():
            if self.peer is not peer:
                return
            self.status.set(f"Conectado: {display_name} · {address}")
            for button in (self.disconnect_button, self.send_button, self.request_button, self.cancel_button):
                button.configure(state="normal")
            self.log(f"Acesso aprovado para {display_name}.")
        self.dispatch(connected_ui)
        self._emit_state()

    def _disconnected(self, peer, reason: str) -> None:
        with self.control_lock:
            if peer is not self.peer:
                return
            transfers, self.transfers = self.transfers, None
            self.peer = None
            self._pending_transfer_status = None
            self.last_screen = None
            self.generation += 1
            self.inputs.release()
        if transfers is not None:
            transfers.close()
        def disconnected_ui():
            if self.peer is not None:
                return
            self.status.set("Aguardando conexão autorizada" if self.server else "Host parado")
            self.transfer_status.set("Transferências encerradas")
            for button in (self.disconnect_button, self.send_button, self.request_button, self.cancel_button):
                button.configure(state="disabled")
            self.log(f"Visitante desconectado: {reason}")
        self.dispatch(disconnected_ui)

    def _emit_state(self) -> None:
        with self.control_lock:
            peer = self.peer
            available = getattr(self, "capture_available", True)
            message = {"type": "state", "sharing": bool(self.sharing and not self.local_paused and not self.prompt_count and available),
                       "localPaused": self.local_paused, "promptActive": self.prompt_count > 0,
                       "captureAvailable": available,
                       "streamVersion": STREAM_VERSION,
                       "computerName": getattr(self, "_computer_name_cache", ""), "agentVersion": VERSION}
        if peer is not None:
            try:
                peer.send(message)
            except Exception:
                pass

    def _message(self, peer, message) -> None:
        if not isinstance(message, dict):
            return
        with self.control_lock:
            if peer is not self.peer or self.shutdown.is_set():
                return
            transfers = self.transfers
        try:
            if transfers is not None and transfers.handle(message):
                return
            kind = message.get("type")
            with self.control_lock:
                if peer is not self.peer:
                    return
                if kind == "input":
                    self._input(message)
                elif kind == "stream_ready":
                    stream = self._stream_controller()
                    stream.synchronize(self.generation)
                    stream.enable(message.get("version"))
                elif kind == "stream_feedback":
                    stream = self._stream_controller()
                    stream.synchronize(self.generation)
                    stream.feedback(message, time.monotonic())
                elif kind == "stream_resume":
                    stream = self._stream_controller()
                    stream.synchronize(self.generation)
                    stream.resume(message.get("version"), time.monotonic())
                elif kind == "monitor" and not self.prompt_count:
                    monitor_id = str(message.get("id", "all"))
                    if monitor_id == "0":
                        monitor_id = "all"
                    if monitor_id != "all" and (not monitor_id.isdigit() or not 1 <= int(monitor_id) <= 64):
                        return
                    self.inputs.release()
                    self.monitor_id = monitor_id
                    self.last_screen = None
                    self.generation += 1
                elif kind == "sharing" and isinstance(message.get("enabled"), bool) and not self.prompt_count:
                    enabled = message["enabled"]
                    self.sharing = enabled and not self.local_paused
                    self.inputs.release()
                    self.last_screen = None
                    self.generation += 1
            if kind == "sharing":
                self._emit_state()
        except Exception as error:
            if message.get("type") == "input":
                with self.control_lock:
                    if peer is self.peer:
                        self.last_screen = None
                        self.generation += 1
                        try:
                            self.inputs.release()
                        except Exception:
                            pass
            self._diagnostic("input" if message.get("type") == "input" else "command",
                             f"Comando recusado: {str(error)[:700]}")

    def _input(self, message: dict) -> None:
        action = message.get("action")
        if action == "release":
            self.inputs.release()
            return
        down = message.get("down")
        # Releases remain allowed during local pause and all consent dialogs.
        if action == "button" and down is False:
            name = message.get("button", "left")
            if name in self.inputs.held_buttons:
                self.inputs.button(name, False)
            return
        if action == "key" and down is False:
            name = message.get("key", "")
            if name in self.inputs.held_keys:
                self.inputs.key(name, False)
            return
        if not self.sharing or self.local_paused or self.prompt_count or self.peer is None:
            return
        screen = self.last_screen
        if not screen or message.get("captureId") != screen["captureId"]:
            return
        if action in ("move", "button", "wheel"):
            if "x" not in message or "y" not in message:
                return
            self.inputs.move(*physical_point(screen, message["x"], message["y"]))
        if action == "button" and down is True:
            self.inputs.button(str(message.get("button", "left")), True)
        elif action == "wheel":
            delta = float(message.get("delta", 0))
            if math.isfinite(delta):
                self.inputs.wheel(max(-20, min(20, int(delta))))
        elif action == "key" and down is True:
            name = str(message.get("key", ""))
            if len(name) <= 30:
                self.inputs.key(name, True)

    def toggle_pause(self) -> None:
        with self.control_lock:
            self.local_paused = not self.local_paused
            self.sharing = not self.local_paused
            self.generation += 1
            self.last_screen = None
            self.inputs.release()
        self.pause_button.configure(text="Retomar" if self.local_paused else "Pausar")
        self._emit_state()
        self.log("Tela e controle pausados localmente." if self.local_paused else "Tela e controle retomados localmente.")

    def disconnect(self) -> None:
        with self.control_lock:
            peer = self.peer
            self.generation += 1
            self.last_screen = None
            self.inputs.release()
        if peer is not None:
            reason = "Sessão encerrada no Hospedeiro."
            # Revoke the local peer and close file operations synchronously.
            # Graceful notification may take a moment on a congested socket;
            # it must never extend permission to capture or inject input.
            self._disconnected(peer, reason)
            end_session = getattr(peer, "end_session", None)
            if end_session is not None:
                threading.Thread(target=lambda: end_session(reason), daemon=True).start()
            else:
                threading.Thread(target=peer.close, daemon=True).start()

    def _ask_receive(self, offer: dict, callback) -> None:
        # FileTransfers already set prompt_guard synchronously in its reader
        # worker; this callback is now running in the Tk UI thread.
        destination = None
        try:
            name = str(offer.get("name", "arquivo"))[:200]
            size = int(offer.get("size", 0))
            if messagebox.askyesno("Receber arquivo?", f"Arquivo: {name}\nTamanho: {size:,} bytes\n\n"
                                   "Autorizar o recebimento e escolher onde salvar?", parent=self.root):
                destination = filedialog.asksaveasfilename(parent=self.root, title="Escolha o destino do arquivo recebido",
                                                          initialfile=Path(name).name) or None
        finally:
            callback(destination)

    def _pick_requested_file(self, callback) -> None:
        path = None
        try:
            if messagebox.askyesno("Enviar um arquivo?", "O visitante solicitou um arquivo.\n\n"
                                   "Você autoriza e deseja escolher o arquivo a enviar?", parent=self.root):
                path = filedialog.askopenfilename(parent=self.root, title="Escolha o arquivo autorizado") or None
        finally:
            callback(path)

    def send_file(self) -> None:
        with self.control_lock:
            transfers = self.transfers
        if transfers is None:
            return
        self._begin_prompt()
        try:
            path = filedialog.askopenfilename(parent=self.root, title="Arquivo a enviar ao visitante")
        finally:
            self._end_prompt()
        if path:
            try:
                transfers.send_file(path)
            except Exception as error:
                messagebox.showerror("Transferência", str(error), parent=self.root)

    def request_file(self) -> None:
        with self.control_lock:
            transfers = self.transfers
        if transfers is not None:
            transfers.receive_request()

    def cancel_transfer(self) -> None:
        with self.control_lock:
            transfers = self.transfers
        if transfers is not None:
            transfers.cancel()

    def _transfer_status(self, update: dict, origin=None) -> None:
        if self.shutdown.is_set():
            return
        text = str(update.get("message") or update.get("status") or "Atualizando transferência")
        text = text[:1024]
        if update.get("active") and "progress" in update:
            try:
                text += f" · {min(100, max(0, float(update['progress']) * 100)):.0f}%"
            except (TypeError, ValueError):
                pass
        with self.control_lock:
            if origin is not None and origin is not self.peer:
                return
            self._pending_transfer_status = (self.peer, text)
            queued = getattr(self, "_transfer_status_queued", False)
            self._transfer_status_queued = True
        if not queued:
            def flush():
                with self.control_lock:
                    pending = self._pending_transfer_status
                    self._pending_transfer_status = None
                    self._transfer_status_queued = False
                    if pending is None or pending[0] is not self.peer or self.peer is None:
                        return
                self.transfer_status.set(pending[1])
            if self.dispatch(flush) is False:
                with self.control_lock:
                    self._pending_transfer_status = None
                    self._transfer_status_queued = False
        if not update.get("active"):
            self.log(text)

    def _stream_controller(self):
        # Lazy creation also keeps embedders using HostApp without its Tk
        # constructor compatible. All callers hold control_lock.
        stream = getattr(self, "stream", None)
        if stream is None:
            stream = self.stream = AdaptiveStream()
        return stream

    def _capture_loop(self) -> None:
        capture = None
        next_refresh = 0.0
        retry_delay = CAPTURE_RETRY_MIN
        try:
            while not self.shutdown.is_set():
                image = None
                peer = None
                generation = None
                interval = 1.0 / FPS
                try:
                    with self.control_lock:
                        peer = self.peer
                        requested_id = self.monitor_id
                        generation = self.generation
                        enabled = peer is not None and self.sharing and not self.local_paused and not self.prompt_count
                        stream = self._stream_controller()
                        stream.synchronize(generation)
                        statistics = None
                        if enabled:
                            read_statistics = getattr(peer, "stream_stats", None)
                            if callable(read_statistics):
                                statistics = read_statistics()
                        ready = enabled and stream.ready(time.monotonic(), statistics)
                        profile = stream.profile
                    if ready:
                        capture_started = time.monotonic()
                        import mss
                        from PIL import Image
                        if capture is None or time.monotonic() >= next_refresh:
                            if capture is not None:
                                previous, capture = capture, None
                                try:
                                    previous.close()
                                except Exception as error:
                                    self._diagnostic("capture-close", f"Não foi possível liberar a captura anterior: {str(error)[:700]}")
                            capture = mss.mss()
                            next_refresh = time.monotonic() + 2.0
                        monitors = capture.monitors
                        monitor_id, rectangle = choose_monitor(monitors, requested_id)
                        shot = capture.grab(rectangle)
                        rectangle["width"], rectangle["height"] = shot.size
                        image = Image.frombytes("RGB", shot.size, shot.rgb)
                        # Resize before JPEG compression: a congested connection
                        # receives fewer pixels as well as fewer JPEG bytes.
                        # Bilinear avoids spending an extra high-quality resize
                        # pass on pixels a slow link would immediately discard.
                        resampling = getattr(Image.Resampling, "BILINEAR", Image.Resampling.LANCZOS)
                        image.thumbnail((profile.width, profile.height), resampling)
                        with BytesIO() as buffer:
                            image.save(buffer, format="JPEG", quality=profile.quality)
                            metadata = screen_metadata(monitors, monitor_id, rectangle, image.size)
                            metadata["agentVersion"] = VERSION
                            frame = {"type": "screen", "image": base64.b64encode(buffer.getvalue()).decode("ascii"), **metadata}
                        recovered = False
                        publish = False
                        with self.control_lock:
                            if (peer is self.peer and self.generation == generation and self.monitor_id == requested_id
                                    and self.sharing and not self.local_paused and not self.prompt_count and not self.shutdown.is_set()):
                                publish = True
                                recovered = not getattr(self, "capture_available", True)
                                self.capture_available = True
                                if self.last_screen and self.last_screen["captureId"] != metadata["captureId"]:
                                    self.inputs.release()
                                self.last_screen = metadata
                                self.monitor_id = monitor_id
                                frame["computerName"] = getattr(self, "_computer_name_cache", "")
                        if recovered:
                            self._emit_state()
                            self._diagnostic("capture-recovered", "Captura de tela restabelecida.")
                        with self.control_lock:
                            if (publish and peer is self.peer and self.generation == generation and self.monitor_id == monitor_id
                                    and self.sharing and not self.local_paused and not self.prompt_count and not self.shutdown.is_set()):
                                frame.update(stream.frame_fields(time.monotonic()))
                                frame["streamProfile"] = profile.name
                                peer.send(frame)
                        retry_delay = CAPTURE_RETRY_MIN
                        interval = 1.0 / profile.fps
                        if isinstance(statistics, Mapping):
                            # Frame rate is a capture-start budget, rather than
                            # another full sleep after compression has finished.
                            interval = max(0.001, interval - (time.monotonic() - capture_started))
                    elif enabled:
                        # Do not capture/encode repeatedly while the previous
                        # image is still being written or shown. The next frame
                        # is captured fresh when the window reopens.
                        interval = BACKPRESSURE_POLL_SECONDS
                    elif capture is not None:
                        previous, capture = capture, None
                        try:
                            previous.close()
                        except Exception as error:
                            self._diagnostic("capture-close", f"Não foi possível liberar a captura anterior: {str(error)[:700]}")
                    self.shutdown.wait(interval)
                except Exception as error:
                    changed = False
                    with self.control_lock:
                        if peer is self.peer and self.generation == generation:
                            self.last_screen = None
                            try:
                                self.inputs.release()
                            except Exception:
                                pass
                            changed = getattr(self, "capture_available", True)
                            self.capture_available = False
                    if changed:
                        self._emit_state()
                    self._diagnostic("capture", f"Captura temporariamente indisponível: {str(error)[:700]}")
                    if capture is not None:
                        previous, capture = capture, None
                        try:
                            previous.close()
                        except Exception:
                            pass
                    self.shutdown.wait(retry_delay)
                    retry_delay = min(CAPTURE_RETRY_MAX, retry_delay * 2)
                finally:
                    if image is not None:
                        try:
                            image.close()
                        except Exception:
                            pass
        finally:
            if capture is not None:
                try:
                    capture.close()
                except Exception:
                    pass

    def close(self) -> None:
        with self.control_lock:
            if self.shutdown.is_set():
                return
            self.shutdown.set()
            self._listener_start_token = None
            self._address_refresh_busy = False
            self.inputs.release()
            self.sharing = False
            self.last_screen = None
            self.generation += 1
            server, self.server = self.server, None
            transfers, self.transfers = self.transfers, None
            for event in tuple(self.pending_consents):
                event.set()
        if getattr(self, "mesh_panel", None) is not None:
            self.mesh_panel.close()
        for attribute in ("_address_after_id", "_ui_after_id", "_gui_after_id", "_availability_after_id"):
            job = getattr(self, attribute, None)
            if job is not None:
                self.root.after_cancel(job)
                setattr(self, attribute, None)
        if transfers is not None:
            transfers.close()
        if server is not None:
            threading.Thread(target=server.stop, daemon=True).start()
        self.root.destroy()


def main() -> None:
    initialize_dpi_awareness()
    root = tk.Tk()
    HostApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
