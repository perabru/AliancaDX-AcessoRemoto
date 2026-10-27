"""Shared Tk styling for the desktop apps and the Python installer.

Usage: ``apply_theme(root)``; pack ``card(parent)`` or a ``ScrollForm`` and
place the form's widgets in ``form.content``. No image or third-party imports
are required. Labels placed inside a card use ``Card.TLabel`` or its variants.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk


BG = "#F4FAF7"
WHITE = "#FFFFFF"
NAVY = "#073B2B"
INK = TEXT = "#123A2A"
MUTED = "#587568"
ACCENT = ORANGE = "#007A50"  # Keep the historic alias for existing imports.
ACCENT_DARK = DARK_ORANGE = "#00613F"
ACCENT_SOFT = "#E4F7EC"
BRIGHT = "#02EF5F"
BORDER = "#D8E8DF"
SUCCESS = "#007A50"
WARNING = "#895400"
ERROR = "#B42332"
SOFT = "#EEF5F1"
_UPDATE_LOOKUP_GATE = threading.BoundedSemaphore(1)


def _font_family(root: tk.Misc) -> str:
    """Prefer the Windows UI face, with readable Linux/macOS fallbacks."""
    available = set(tkfont.families(root))
    if sys.platform == "win32" or "Segoe UI" in available:
        return "Segoe UI"
    for family in ("Noto Sans", "DejaVu Sans", "Liberation Sans", "Helvetica"):
        if family in available:
            return family
    return str(tkfont.nametofont("TkDefaultFont", root=root).actual("family"))


def apply_theme(root: tk.Misc) -> None:
    """Apply a readable, white-dominant theme to this Tk interpreter.

    Spacing is defined through padding, not fixed widget heights, so controls
    follow the interpreter's font scaling. ``ORANGE`` remains an alias for the
    emerald accent for older callers.
    """
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    family = _font_family(root)
    root._minianydesk_ui_font = family
    base = (family, 10)
    small = (family, 9)
    bold = (family, 9, "bold")
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        tkfont.nametofont(name, root=root).configure(family=family, size=10)
    tkfont.nametofont("TkHeadingFont", root=root).configure(weight="bold")
    root.option_add("*TCombobox*Listbox.font", base)
    root.option_add("*TCombobox*Listbox.background", WHITE)
    root.option_add("*TCombobox*Listbox.foreground", INK)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", WHITE)
    try:
        root.configure(background=BG)
    except tk.TclError:
        pass  # A ttk parent can share the theme without a background option.

    style.configure(".", font=base, background=BG, foreground=INK)
    style.configure("TFrame", background=BG)
    style.configure("App.TFrame", background=BG)
    style.configure("Card.TFrame", background=WHITE, borderwidth=1,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    relief="solid")
    style.configure("CardBody.TFrame", background=WHITE, borderwidth=0,
                    relief="flat")
    style.configure("TLabel", background=BG, foreground=INK, font=base)
    style.configure("Title.TLabel", font=(family, 23, "bold"), foreground=NAVY)
    style.configure("Heading.TLabel", font=(family, 12, "bold"), foreground=NAVY)
    style.configure("Muted.TLabel", foreground=MUTED, font=small)
    style.configure("Status.TLabel", foreground=NAVY, font=(family, 10, "bold"))
    style.configure("Card.TLabel", background=WHITE, foreground=INK)
    style.configure("CardMuted.TLabel", background=WHITE, foreground=MUTED,
                    font=small)
    style.configure("CardHeading.TLabel", background=WHITE, foreground=NAVY,
                    font=(family, 12, "bold"))
    style.configure("CardStatus.TLabel", background=WHITE, foreground=NAVY,
                    font=(family, 10, "bold"))
    style.configure("Hero.TLabel", background=WHITE, foreground=NAVY,
                    font=(family, 16, "bold"))
    style.configure("CardEyebrow.TLabel", background=WHITE, foreground=ACCENT_DARK,
                    font=(family, 9, "bold"))
    style.configure("CardAccent.TLabel", background=WHITE, foreground=ACCENT_DARK,
                    font=(family, 10, "bold"))
    style.configure("Step.TLabel", background=WHITE, foreground=ACCENT_DARK,
                    font=(family, 10, "bold"))
    for name, color in (("Success", SUCCESS), ("Warning", WARNING), ("Error", ERROR)):
        style.configure(f"{name}.TLabel", background=BG, foreground=color,
                        font=(family, 10, "bold"))
        style.configure(f"Card{name}.TLabel", background=WHITE, foreground=color,
                        font=(family, 10, "bold"))
    style.configure("Soft.TFrame", background=SOFT, borderwidth=0)
    style.configure("Soft.TLabel", background=SOFT, foreground=MUTED,
                    font=small)
    style.configure("Hint.TLabel", background=ACCENT_SOFT, foreground=ACCENT_DARK,
                    font=small, padding=(10, 8))

    button = dict(font=bold, padding=(14, 9), borderwidth=1, relief="flat",
                  focusthickness=2, anchor="center")
    style.configure("TButton", **button, background=WHITE, foreground=INK,
                    bordercolor=BORDER, lightcolor=WHITE, darkcolor=WHITE,
                    focuscolor=BORDER)
    style.map("TButton", background=[("disabled", SOFT), ("pressed", BORDER),
                                     ("active", "#EEF9F2")],
              foreground=[("disabled", MUTED)],
              bordercolor=[("disabled", BORDER), ("focus", ACCENT), ("active", "#A7D9BA")])
    style.configure("Secondary.TButton", **button, background=WHITE,
                    foreground=INK, bordercolor=BORDER,
                    lightcolor=WHITE, darkcolor=WHITE, focuscolor=BORDER)
    style.map("Secondary.TButton", background=[("disabled", SOFT),
                                               ("pressed", BORDER),
                                               ("active", "#EEF9F2")],
              foreground=[("disabled", MUTED)],
              bordercolor=[("disabled", BORDER), ("focus", ACCENT), ("active", "#A7D9BA")])
    for name in ("Quiet.TButton", "Link.TButton"):
        style.configure(name, **dict(button, padding=(8, 6)), background=WHITE,
                        foreground=ACCENT_DARK, bordercolor=WHITE,
                        lightcolor=WHITE, darkcolor=WHITE, focuscolor=ACCENT)
        style.map(name, background=[("disabled", WHITE), ("pressed", ACCENT_SOFT),
                                    ("active", "#F0FBF4")],
                  foreground=[("disabled", MUTED)],
                  bordercolor=[("disabled", WHITE), ("focus", ACCENT),
                               ("active", "#BCE5CA"), ("!focus", WHITE)])
    style.configure("Password.TButton", **dict(button, padding=(12, 10)),
                    background=ACCENT_SOFT, foreground=ACCENT_DARK, bordercolor="#B9DDC7",
                    lightcolor=ACCENT_SOFT, darkcolor=ACCENT_SOFT, focuscolor=ACCENT)
    style.map("Password.TButton", background=[("disabled", BG),
                                               ("pressed", "#C9EDD7"),
                                               ("active", "#D9F4E3")],
              foreground=[("disabled", MUTED)],
              bordercolor=[("disabled", BORDER), ("focus", ACCENT), ("active", "#91CBA9")])
    for name in ("Primary.TButton", "Accent.TButton"):
        style.configure(name, **button, background=ACCENT, foreground=WHITE,
                        bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
                        focuscolor=WHITE)
        style.map(name, background=[("disabled", "#D9EAE0"),
                                    ("pressed", ACCENT_DARK),
                                    ("active", "#006B46")],
                  foreground=[("disabled", "#688A77"), ("!disabled", WHITE)],
                  bordercolor=[("disabled", "#D9EAE0"), ("focus", NAVY),
                               ("pressed", ACCENT_DARK), ("active", "#006B46")])
    style.configure("Danger.TButton", **button, background=WHITE,
                    foreground=ERROR, bordercolor="#EFCAD0",
                    lightcolor=WHITE, darkcolor=WHITE, focuscolor=ERROR)
    style.map("Danger.TButton", background=[("disabled", BG),
                                            ("pressed", "#FBE2E6"),
                                            ("active", "#FFF2F4")],
              foreground=[("disabled", MUTED)],
              bordercolor=[("disabled", BORDER), ("focus", ERROR), ("active", "#E5A8B2")])
    sidebar = dict(button, padding=(14, 11), anchor="w")
    style.configure("Sidebar.TButton", **sidebar, background=NAVY,
                    foreground=WHITE, bordercolor=NAVY,
                    lightcolor=NAVY, darkcolor=NAVY, focuscolor="#02EF5F")
    style.map("Sidebar.TButton", background=[("disabled", NAVY),
                                             ("pressed", "#10513B"),
                                             ("active", "#0A4733")],
              foreground=[("disabled", "#6D9C86"), ("!disabled", "#D7EEE1")],
              bordercolor=[("focus", "#02EF5F"), ("!focus", NAVY)])
    style.configure("SidebarSelected.TButton", **sidebar, background=BRIGHT,
                    foreground=NAVY, bordercolor=BRIGHT,
                    lightcolor=BRIGHT, darkcolor=BRIGHT, focuscolor=NAVY)
    style.map("SidebarSelected.TButton", background=[("disabled", "#BEDCCB"),
                                                     ("pressed", "#A7EDBF"),
                                                     ("active", "#C5F7D7")],
              foreground=[("disabled", MUTED), ("!disabled", NAVY)],
              bordercolor=[("focus", "#02EF5F"), ("!focus", BRIGHT)])

    style.configure("PageEyebrow.TLabel", background=BG, foreground=ACCENT_DARK,
                    font=(family, 9, "bold"))
    style.configure("Count.TLabel", background=ACCENT_SOFT, foreground=ACCENT_DARK,
                    font=(family, 9, "bold"), padding=(10, 6))

    style.configure("TEntry", font=base, padding=(8, 6), fieldbackground=WHITE,
                    foreground=INK, bordercolor=BORDER, lightcolor=BORDER,
                    darkcolor=BORDER, insertcolor=INK, borderwidth=1)
    style.map("TEntry", bordercolor=[("disabled", BORDER), ("invalid", ERROR), ("focus", ACCENT)],
              fieldbackground=[("disabled", SOFT), ("readonly", "#F8FCF9")],
              foreground=[("disabled", MUTED)])
    style.configure("Password.TEntry", padding=(14, 10), fieldbackground="#F5FCF7",
                    bordercolor="#AFD7BF", lightcolor="#AFD7BF", darkcolor="#AFD7BF")
    style.map("Password.TEntry", bordercolor=[("disabled", BORDER), ("invalid", ERROR), ("focus", ACCENT)],
              fieldbackground=[("disabled", SOFT), ("!disabled", "#F5FCF7")],
              foreground=[("disabled", MUTED)])
    style.configure("TCombobox", font=base, padding=(8, 6),
                    fieldbackground=WHITE, background=WHITE, foreground=INK,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    arrowcolor=MUTED, arrowsize=13, borderwidth=1)
    style.map("TCombobox", bordercolor=[("disabled", BORDER), ("invalid", ERROR), ("focus", ACCENT)],
              fieldbackground=[("disabled", SOFT), ("readonly", WHITE)],
              foreground=[("disabled", MUTED), ("readonly", INK)],
              selectbackground=[("readonly", WHITE)],
              selectforeground=[("readonly", INK)])
    style.configure("TCheckbutton", font=small, background=BG, foreground=INK,
                    padding=(0, 4), focuscolor=ACCENT)
    style.map("TCheckbutton", background=[("active", BG)],
              foreground=[("disabled", MUTED)])
    style.configure("Card.TCheckbutton", background=WHITE)
    style.map("Card.TCheckbutton", background=[("active", WHITE)])
    style.configure("TLabelframe", background=BG, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, borderwidth=1)
    style.configure("TLabelframe.Label", background=BG, foreground=NAVY,
                    font=(family, 10, "bold"))
    style.configure("TSeparator", background=BORDER)
    # Tk's scaled font metrics keep rows legible on enlarged system displays.
    rowheight = max(40, tkfont.Font(root=root, font=small).metrics("linespace") + 18)
    style.configure("Treeview", font=small, rowheight=rowheight, background=WHITE,
                    fieldbackground=WHITE, foreground=INK, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, borderwidth=1)
    style.map("Treeview", background=[("selected", ACCENT_SOFT)],
              foreground=[("selected", ACCENT_DARK)])
    style.configure("Treeview.Heading", font=bold, background=BG,
                    foreground=MUTED, padding=(10, 8), bordercolor=BORDER,
                    lightcolor=BG, darkcolor=BG, relief="flat")
    style.map("Treeview.Heading", background=[("active", ACCENT_SOFT)])
    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 4, 0, 0))
    style.configure("TNotebook.Tab", font=bold, padding=(14, 8),
                    background=BG, foreground=MUTED, bordercolor=BORDER)
    style.map("TNotebook.Tab", background=[("selected", WHITE), ("active", ACCENT_SOFT)],
              foreground=[("selected", ACCENT_DARK), ("active", NAVY)])
    style.configure("Horizontal.TProgressbar", background=ACCENT,
                    troughcolor=ACCENT_SOFT, bordercolor=ACCENT_SOFT, lightcolor=ACCENT,
                    darkcolor=ACCENT, thickness=7)
    for name in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(name, background=BORDER, troughcolor=BG,
                        bordercolor=BG, lightcolor=BORDER, darkcolor=BORDER,
                        arrowcolor=MUTED, arrowsize=11, borderwidth=0)
        style.map(name, background=[("active", "#B3D5C0"),
                                    ("pressed", "#84B69A")])

    _apply_window_icon(root)


def card(parent: tk.Misc, padding: int | tuple = 18, **kwargs) -> ttk.Frame:
    """Return a white, lightly bordered container with consistent padding."""
    kwargs.setdefault("style", "Card.TFrame")
    return ttk.Frame(parent, padding=padding, **kwargs)


class ScrollForm(ttk.Frame):
    """A width-responsive vertical form; add children to ``content``.

    Wheel bindings belong to this form's toplevel and are removed on destroy.
    The pointer must be over this form, with no modal grab in another window.
    Text areas, tables, comboboxes and nested scroll forms keep their own wheel.
    """

    def __init__(self, parent: tk.Misc, **kwargs):
        background = kwargs.pop("background", BG)
        kwargs.setdefault("style", "App.TFrame")
        super().__init__(parent, **kwargs)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, background=background, highlightthickness=0,
                                borderwidth=0, yscrollincrement=18)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        self.content = tk.Frame(self.canvas, background=background, borderwidth=0)
        self._content_window = self.canvas.create_window(0, 0, window=self.content,
                                                          anchor="nw")
        self.content.bind("<Configure>", self._update_region)
        self.canvas.bind("<Configure>", self._resize_content)
        self._top = self.winfo_toplevel()
        self._windowing_system = str(self.tk.call("tk", "windowingsystem"))
        self._wheel_remainder = 0.0
        callbacks = {
            "<MouseWheel>": self._on_wheel,
            "<Button-4>": lambda event: self._on_wheel(event, -3),
            "<Button-5>": lambda event: self._on_wheel(event, 3),
        }
        self._wheel_bindings = {
            sequence: self._top.bind(sequence, callback, add="+")
            for sequence, callback in callbacks.items()
        }
        self.bind("<Destroy>", self._remove_wheel_bindings, add="+")

    def _update_region(self, _event=None):
        bounds = self.canvas.bbox(self._content_window)
        if bounds is not None:
            self.canvas.configure(scrollregion=bounds)

    def _resize_content(self, event):
        self.canvas.itemconfigure(self._content_window, width=max(1, event.width))
        self._update_region()

    def _owns_pointer(self) -> bool:
        try:
            grab = self.grab_current()
            if grab is not None and grab.winfo_toplevel() != self._top:
                return False
            x, y = self.winfo_pointerxy()
            widget = self.winfo_containing(x, y)
            while widget is not None:
                if widget is self:
                    return True
                if isinstance(widget, ScrollForm):
                    return False
                if widget is not self.canvas and widget.winfo_class() in {
                    "Text", "Listbox", "Treeview", "TCombobox", "Spinbox",
                    "TSpinbox", "Canvas",
                }:
                    return False
                widget = widget.master
        except (tk.TclError, KeyError):
            return False
        return False

    def _on_wheel(self, event, direction=None):
        if event.state & 0x1 or not self._owns_pointer():
            return None
        bounds = self.canvas.bbox(self._content_window)
        if bounds is None or bounds[3] - bounds[1] <= self.canvas.winfo_height():
            return None
        if direction is not None:
            units = direction
        elif event.num in (4, 5):
            units = -3 if event.num == 4 else 3
        elif self._windowing_system == "aqua":
            units = -int(event.delta)
        else:
            self._wheel_remainder += -event.delta / 120.0 * 3
            units = int(self._wheel_remainder)
            self._wheel_remainder -= units
        if units:
            self.canvas.yview_scroll(units, "units")
        return "break"

    def _remove_wheel_bindings(self, event):
        if event.widget is not self:
            return
        for sequence, callback_id in self._wheel_bindings.items():
            if callback_id:
                try:
                    self._top.unbind(sequence, callback_id)
                except tk.TclError:
                    pass
        self._wheel_bindings.clear()



def _brand_photo(parent: tk.Misc, filename: str, size: tuple[int, int]):
    """Load the shipped original artwork; Pillow is optional for the installer."""
    path = Path(__file__).resolve().parent / "assets" / "brand" / filename
    if not path.is_file():
        return None
    try:
        from PIL import Image, ImageTk
        with Image.open(path) as source:
            image = source.convert("RGBA")
            image.thumbnail(size, Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(image, master=parent)
    except (ImportError, OSError, tk.TclError):
        try:
            image = tk.PhotoImage(master=parent, file=str(path))
            factor = max(1, (image.width() + size[0] - 1) // size[0],
                         (image.height() + size[1] - 1) // size[1])
            return image.subsample(factor)
        except (OSError, tk.TclError):
            return None


def _apply_window_icon(root: tk.Misc) -> None:
    window = root.winfo_toplevel()
    if getattr(window, "_aliancadx_icon_applied", False):
        return
    photo = _brand_photo(window, "aliancadx-mark-256.png", (128, 128))
    if photo is not None:
        try:
            window.iconphoto(True, photo)
            window._aliancadx_window_icon = photo
            window._aliancadx_icon_applied = True
        except tk.TclError:
            pass
    icon = Path(__file__).resolve().parent / "assets" / "brand" / "aliancadx.ico"
    if sys.platform == "win32" and icon.is_file():
        try:
            window.iconbitmap(default=str(icon))
        except tk.TclError:
            pass


def _wordmark(parent: tk.Misc, size=(178, 42)) -> tk.Label:
    photo = _brand_photo(parent, "aliancadx-logo.png", size)
    family = getattr(parent.winfo_toplevel(), "_minianydesk_ui_font", "Segoe UI")
    label = tk.Label(parent, background=WHITE, borderwidth=0, highlightthickness=0)
    if photo is None:
        label.configure(text="aliançaDX", foreground=ACCENT, font=(family, 22, "bold"))
    else:
        label.configure(image=photo)
        label._aliancadx_artwork = photo
    return label


def page_brand(parent: tk.Misc, caption: str) -> ttk.Frame:
    """A restrained role label; the company artwork stays in the sidebar."""
    frame = ttk.Frame(parent, style="App.TFrame")
    title = ttk.Label(frame, text=caption, style="PageEyebrow.TLabel", justify="left")
    title.pack(fill="x", anchor="w")
    frame.bind("<Configure>", lambda event: title.configure(
        wraplength=max(100, event.width)), add="+")
    return frame


def brand(parent: tk.Misc, subtitle: str = "") -> tk.Frame:
    """Company identity for the forest-green sidebar, using the original logo."""
    family = getattr(parent.winfo_toplevel(), "_minianydesk_ui_font", "Segoe UI")
    frame = tk.Frame(parent, background=NAVY, pady=6)
    artwork = tk.Frame(frame, background=WHITE, padx=10, pady=11)
    artwork.pack(fill="x", pady=(0, 16))
    _wordmark(artwork).pack(anchor="w")
    tk.Label(frame, text="Acesso Remoto", background=NAVY, foreground=WHITE,
             font=(family, 14, "bold"), anchor="w").pack(fill="x")
    if subtitle:
        detail = tk.Label(frame, text=subtitle, background=NAVY, foreground=BRIGHT,
                          font=(family, 9, "bold"), anchor="w", justify="left", wraplength=200)
        detail.pack(fill="x", pady=(7, 0))
        frame.bind("<Configure>", lambda event: detail.configure(wraplength=max(120, event.width)), add="+")
    tk.Frame(frame, background="#245D45", height=1).pack(fill="x", pady=(19, 0))
    return frame


class UpdateDialog:
    """Manual, modeless update checks; only the UI thread ever touches Tk."""
    def __init__(self, parent, installed_version, role, *, checker=None, browser_open=None):
        import queue
        import webbrowser
        from updates import check_latest
        self.installed_version, self.role = installed_version, role
        self.checker = checker or check_latest
        self.browser_open = browser_open or webbrowser.open_new_tab
        self.results = queue.Queue(maxsize=1)
        self.generation = 0
        self.disposed = self.busy = False
        self.poll_job = None
        self.result = None
        self.window = tk.Toplevel(parent)
        self.window.title("AliançaDX · Buscar atualização")
        self.window.transient(parent)
        self.window.configure(background=BG)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", self.close)
        self.window.bind("<Destroy>", lambda event: self._dispose() if event.widget is self.window else None)
        _apply_window_icon(self.window)
        content = card(self.window, padding=22)
        content.pack(fill="both", expand=True, padx=14, pady=14)
        ttk.Label(content, text="Buscar atualização", style="CardHeading.TLabel").pack(anchor="w")
        caption = "Controlador Windows" if role == "controller" else "Hospedeiro Windows"
        ttk.Label(content, text=caption, style="CardMuted.TLabel").pack(anchor="w", pady=(4, 18))
        versions = ttk.Frame(content, style="CardBody.TFrame")
        versions.pack(fill="x")
        self.latest_var = tk.StringVar(self.window, value="—")
        for row, (label, value) in enumerate((("Versão instalada", installed_version), ("Versão mais recente", None))):
            ttk.Label(versions, text=label, style="CardMuted.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 24), pady=(0, 8))
            ttk.Label(versions, **({"text": value} if value is not None else {"textvariable": self.latest_var}),
                      style="CardStatus.TLabel").grid(row=row, column=1, sticky="w", pady=(0, 8))
        ttk.Separator(content).pack(fill="x", pady=(8, 16))
        self.title_var = tk.StringVar(self.window)
        self.message_var = tk.StringVar(self.window)
        self.status_label = ttk.Label(content, textvariable=self.title_var, style="CardStatus.TLabel", wraplength=400, justify="left")
        self.status_label.pack(fill="x")
        ttk.Label(content, textvariable=self.message_var, style="CardMuted.TLabel", wraplength=400, justify="left").pack(fill="x", pady=(7, 12))
        self.progress = ttk.Progressbar(content, mode="indeterminate", length=400)
        self.progress.pack(fill="x", pady=(0, 14))
        self.install_note_var = tk.StringVar(self.window)
        self.install_note_label = ttk.Label(content, textvariable=self.install_note_var, style="CardMuted.TLabel", wraplength=400, justify="left")
        self.install_note_label.pack(fill="x", pady=(0, 12))
        actions = ttk.Frame(content, style="CardBody.TFrame")
        actions.pack(fill="x")
        self.download_button = ttk.Button(actions, text="Baixar versão", style="Primary.TButton", command=self.download)
        self.retry_button = ttk.Button(actions, text="Buscar novamente", style="Secondary.TButton", command=self.start)
        self.retry_button.pack(side="left", padx=(0, 8))
        self.close_button = ttk.Button(actions, text="Fechar", style="Secondary.TButton", command=self.close)
        self.close_button.pack(side="right")
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = max(8, min(parent.winfo_rootx() + (parent.winfo_width()-width)//2, parent.winfo_screenwidth()-width-8))
        y = max(8, min(parent.winfo_rooty() + (parent.winfo_height()-height)//2, parent.winfo_screenheight()-height-8))
        self.window.geometry(f"+{x}+{y}")
        self.start()
        self.focus()

    @property
    def is_open(self):
        if self.disposed:
            return False
        try:
            return bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def focus(self):
        if self.is_open:
            self.window.lift()
            self.close_button.focus_set()

    def start(self):
        if self.disposed or self.busy:
            return False
        self.generation += 1
        token, target = self.generation, self.results
        checker, installed, role = self.checker, self.installed_version, self.role
        self.busy, self.result = True, None
        self.title_var.set("Buscando atualização…")
        self.message_var.set("Consultando a versão pública no GitHub. Suas sessões continuam abertas.")
        self.latest_var.set("—")
        self.install_note_var.set("")
        self.status_label.configure(style="CardStatus.TLabel")
        self.download_button.pack_forget()
        self.retry_button.configure(state="disabled")
        self.progress.pack(fill="x", pady=(0, 14), before=self.install_note_label)
        self.progress.start(12)
        def work():
            import queue
            from updates import UpdateResult
            if not _UPDATE_LOOKUP_GATE.acquire(blocking=False):
                result = UpdateResult("error", installed, None,
                    "Há uma busca em andamento. Aguarde um instante e tente novamente.", None)
            else:
                try:
                    result = checker(installed, role)
                except Exception:
                    result = UpdateResult("error", installed, None, "Não foi possível verificar agora. Tente novamente.", None)
                finally:
                    _UPDATE_LOOKUP_GATE.release()
            try:
                target.put_nowait((token, result))
            except queue.Full:
                pass
        threading.Thread(target=work, name="AliancaDX-update-check", daemon=True).start()
        self.poll_job = self.window.after(50, self._poll)
        return True

    def _poll(self):
        self.poll_job = None
        if self.disposed:
            return
        import queue
        try:
            token, result = self.results.get_nowait()
        except queue.Empty:
            self.poll_job = self.window.after(50, self._poll)
            return
        if token != self.generation:
            self.poll_job = self.window.after(50, self._poll)
            return
        self.busy = False
        self.progress.stop()
        self.progress.pack_forget()
        self.retry_button.configure(state="normal")
        self._show_result(result)

    def _show_result(self, result):
        from updates import UpdateResult, valid_download_url
        statuses = {"available": ("Nova versão disponível", "CardSuccess.TLabel"),
                    "current": ("Você está atualizado", "CardSuccess.TLabel"),
                    "ahead": ("Versão instalada mais recente", "CardStatus.TLabel"),
                    "offline": ("Sem conexão", "CardWarning.TLabel"),
                    "timeout": ("A consulta demorou demais", "CardWarning.TLabel"),
                    "limited": ("Limite de consultas atingido", "CardWarning.TLabel"),
                    "error": ("Não foi possível verificar", "CardError.TLabel")}
        if (not isinstance(result, UpdateResult) or not isinstance(result.status, str) or result.status not in statuses or
                result.installed_version != self.installed_version or
                result.status == "available" and not valid_download_url(result.download_url, result.latest_version, self.role)):
            result = UpdateResult("error", self.installed_version, None, "A resposta da atualização não pôde ser validada. Tente novamente.", None)
        self.result = result
        title, style = statuses[result.status]
        self.title_var.set(title)
        self.status_label.configure(style=style)
        self.message_var.set(result.message)
        self.latest_var.set(result.latest_version or "—")
        self.download_button.pack_forget()
        if result.status == "available":
            self.download_button.configure(text=f"Baixar versão {result.latest_version}")
            self.download_button.pack(side="left", padx=(0, 8), before=self.retry_button)
            self.install_note_var.set("O link será aberto no navegador. Instale quando suas sessões estiverem encerradas.")
        else:
            self.install_note_var.set("")

    def download(self):
        if self.disposed or self.busy or self.result is None:
            return False
        from updates import valid_download_url
        result = self.result
        if result.status != "available" or not valid_download_url(result.download_url, result.latest_version, self.role):
            return False
        try:
            if not self.browser_open(result.download_url):
                raise OSError("Browser unavailable")
        except Exception:
            self.message_var.set("Não foi possível abrir o navegador. Tente o botão de download novamente.")
            return False
        self.message_var.set("Link de download aberto no navegador. A versão instalada permanece a mesma até você instalar a nova versão.")
        return True

    def _dispose(self):
        if self.disposed:
            return
        self.disposed = True
        self.generation += 1
        if self.poll_job is not None:
            try:
                self.window.after_cancel(self.poll_job)
            except tk.TclError:
                pass
            self.poll_job = None

    def close(self, _event=None):
        self._dispose()
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        return "break"
