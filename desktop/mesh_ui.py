"""Native, account-free pairing controls. Network availability is not a session."""
from __future__ import annotations

from pathlib import Path
import queue
import re
import tkinter as tk
from tkinter import messagebox, ttk

from desktop_ui import card
import mesh


def pairing_address(pairing) -> str:
    """Public lookup label only: the private network token never enters settings."""
    return f"mesh-{pairing.public_id}.invalid"


def is_pairing_address(address) -> bool:
    return bool(isinstance(address, str) and re.fullmatch(r"mesh-[0-9a-f]{24}\.invalid", address))


def pairing_from_text(text):
    """Accept a code or the explicit invitation produced by the Host.

    Do not repair damaged codes, select between multiple invitations, or read a
    password from the clipboard. The transport's strict parser stays authoritative.
    """
    if not isinstance(text, str) or len(text) > 4096:
        raise mesh.MeshError("Cole o código ou um convite completo do Host.")
    value = text.strip()
    if value.upper().startswith("MDA6-") and "\n" not in value:
        return mesh.parse_pairing(value)
    lines = value.splitlines()
    if not lines or not lines[0].startswith(("MiniAnyDesk Host ", "AliançaDX Host ")):
        raise mesh.MeshError("Cole o código ou um convite completo do Host.")
    codes = [line.partition(":")[2].strip() for line in lines
             if line.startswith("Código privado:")]
    if len(codes) != 1:
        raise mesh.MeshError("Copie um único convite completo na janela do Host.")
    return mesh.parse_pairing(codes[0])


class MeshPanel(ttk.Frame):
    """Own one user-space transport, marshal its callbacks onto Tk's own thread."""
    def __init__(self, parent, *, role, config_dir, on_status=None, on_select=None, on_revoke=None, on_save=None, compact=False):
        super().__init__(parent, style="CardBody.TFrame" if compact else "App.TFrame")
        self.role = role
        self.config_dir = Path(config_dir)
        self.on_status = on_status
        self.on_select = on_select
        self.on_save = on_save
        self.on_revoke = on_revoke
        self.pairing = None
        self.host_enabled = False
        self.closed = False
        self.events = queue.Queue(maxsize=32)
        self.manager = mesh.MeshManager(role, self.config_dir, on_status=self._network_status)
        self.code_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Inicie o Host para preparar o código de conexão." if role == "host" else "Peça o código à pessoa no outro computador.")
        self.remember_var = tk.BooleanVar(value=False)
        self.saved_name_var = tk.StringVar()
        self.notice_var = tk.StringVar(value="Compartilhe o código somente com a pessoa autorizada. A senha e sua aprovação continuam necessárias." if role == "host" else "Sem conta e sem configurar o roteador. A sessão ainda exige senha e aprovação no Host.")
        body = ttk.Frame(self, style="CardBody.TFrame") if compact else card(self, padding=20)
        body.pack(fill="x")
        if not compact:
            ttk.Label(body, text="Conectar pela internet", style="CardHeading.TLabel").pack(anchor="w")
        self.description = ttk.Label(body, textvariable=self.notice_var, style="CardMuted.TLabel", wraplength=520, justify="left")
        if not compact:
            self.description.pack(fill="x", pady=(6, 16))
        ttk.Label(body, text="Código de conexão", style="Card.TLabel").pack(anchor="w", pady=(0, 6))
        row = ttk.Frame(body, style="CardBody.TFrame")
        row.pack(fill="x")
        row.columnconfigure(0, weight=1)
        self.code_entry = ttk.Entry(row, textvariable=self.code_var, show="*", width=1,
                                    state="readonly" if role == "host" else "normal")
        self.code_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8), ipady=4)
        self.show_button = ttk.Button(row, text="Mostrar", style="Secondary.TButton", command=self._toggle_code)
        self.show_button.grid(row=0, column=1)
        self.code_visible = False
        actions = ttk.Frame(body, style="CardBody.TFrame")
        actions.pack(fill="x", pady=(12, 0))
        if role == "host":
            self.copy_button = ttk.Button(actions, text="Copiar só o código" if compact else "Copiar código", style="Secondary.TButton" if compact else "Primary.TButton", command=self.copy_code, state="disabled")
            self.copy_button.pack(side="left")
            self.more_button = ttk.Button(actions, text="Mais opções", style="Secondary.TButton", command=self._toggle_options)
            self.more_button.pack(side="right")
            self.options = ttk.Frame(body, style="CardBody.TFrame")
            self.prepare_button = ttk.Button(self.options, text="Tentar rede novamente", style="Secondary.TButton", command=self.prepare_access, state="disabled")
            self.prepare_button.pack(fill="x", pady=(0, 8))
            self.renew_button = ttk.Button(self.options, text="Revogar código e gerar outro", style="Secondary.TButton", command=self.renew_code, state="disabled")
            self.renew_button.pack(fill="x")
        else:
            self.paste_button = ttk.Button(row, text="Colar", style="Secondary.TButton", command=self.paste_code)
            self.paste_button.grid(row=0, column=2, padx=(8, 0))
            self.remember_check = ttk.Checkbutton(body, text="Salvar este acesso em Meus computadores", variable=self.remember_var, style="Card.TCheckbutton")
            self.remember_check.pack(anchor="w", pady=(12, 0))
            self.save_notice = ttk.Label(body, text="No Windows, o código fica protegido no cofre deste usuário. A senha nunca é salva.", style="CardMuted.TLabel", wraplength=520, justify="left")
            self.save_notice.pack(fill="x", pady=(4, 8))
            ttk.Label(body, text="Nome do computador salvo (opcional)", style="Card.TLabel").pack(anchor="w")
            self.saved_name_entry = ttk.Entry(body, textvariable=self.saved_name_var, width=1)
            self.saved_name_entry.pack(fill="x", pady=(4, 0), ipady=4)
            self.select_button = ttk.Button(actions, text="Continuar com este código", style="Primary.TButton", command=self.select_code)
            self.select_button.pack(fill="x")
            self.code_entry.bind("<Return>", lambda _event: self.select_code())
            self.code_entry.bind("<<Paste>>", self._paste_event)
        self.status_label = ttk.Label(body, textvariable=self.status_var, style="CardMuted.TLabel", wraplength=520, justify="left")
        self.status_label.pack(fill="x", pady=(15, 0))
        relay_notice = ttk.Label(body, text="Mantenha os dois programas abertos e conectados à internet.", style="CardMuted.TLabel", wraplength=520, justify="left")
        if not compact:
            relay_notice.pack(fill="x", pady=(8, 0))
        body.bind("<Configure>", lambda event: [label.configure(wraplength=max(140, event.width - 40)) for label in (self.description, self.status_label, relay_notice, *([self.save_notice] if role != "host" else []))], add="+")
        self.job = self.after(100, self._drain)

    def _toggle_options(self):
        if self.options.winfo_manager():
            self.options.pack_forget()
            self.more_button.configure(text="Mais opções")
        else:
            self.options.pack(fill="x", pady=(12, 0), before=self.status_label)
            self.more_button.configure(text="Menos opções")

    def _paste_event(self, _event=None):
        self.paste_code()
        return "break"

    def paste_code(self):
        """Read clipboard only on the user's explicit paste action."""
        try:
            pairing = pairing_from_text(self.clipboard_get())
        except tk.TclError:
            self.status_var.set("A área de transferência está vazia. Copie o código no Host primeiro.")
            self.code_entry.focus_set()
            return False
        except (ValueError, mesh.MeshError):
            self.status_var.set("Não encontrei um código válido. No Host, use Copiar convite ou Copiar código.")
            self.code_entry.focus_set()
            return False
        self.code_var.set(pairing.code)
        self.hide_code()
        self.status_var.set("Código colado. Clique em Continuar para informar a senha.")
        getattr(self, "continue_focus", self.select_button).focus_set()
        return True

    def _network_status(self, status):
        if self.closed:
            return
        try:
            self.events.put_nowait(status)
        except queue.Full:
            try:
                self.events.get_nowait()
                self.events.put_nowait(status)
            except (queue.Empty, queue.Full):
                pass

    def _drain(self):
        self.job = None
        if self.closed:
            return
        latest = None
        for _ in range(32):
            try:
                latest = self.events.get_nowait()
            except queue.Empty:
                break
        if latest is not None:
            # A callback can be delivered after a newer Stop. Only the current
            # manager snapshot can describe the process owned by this panel.
            latest = self.manager.snapshot()
            self.status_var.set(latest.message)
            self.status_label.configure(style={"ready": "CardSuccess.TLabel",
                                               "error": "CardError.TLabel",
                                               "degraded": "CardWarning.TLabel"}.get(latest.state, "CardMuted.TLabel"))
            if self.on_status is not None:
                self.on_status(latest)
        self.job = self.after(100, self._drain)

    def _toggle_code(self):
        self.code_visible = not self.code_visible
        self.code_entry.configure(show="" if self.code_visible else "*")
        self.show_button.configure(text="Ocultar" if self.code_visible else "Mostrar")

    def hide_code(self):
        if self.code_visible:
            self._toggle_code()

    def copy_code(self):
        if self.pairing is None:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(self.pairing.code)
        except tk.TclError:
            self.status_var.set("Não foi possível copiar. Use Mostrar e selecione o código completo.")
            return
        self.status_var.set("Código copiado. Envie somente à pessoa autorizada.")

    def configure_host(self, port, fingerprint):
        if self.closed:
            return
        self.pairing = mesh.load_or_create_pairing(self.config_dir, port=port, fingerprint=fingerprint)
        self.host_enabled = True
        self.code_var.set(self.pairing.code)
        self.copy_button.configure(state="normal")
        self.prepare_button.configure(state="normal")
        self.renew_button.configure(state="normal")
        self.prepare_access()

    def renew_code(self):
        if self.closed or self.pairing is None or (self.role == "host" and not self.host_enabled):
            return
        if not messagebox.askyesno("Revogar código privado", "Encerrar a conexão atual e gerar outro código? O código antigo deixará de encontrar este Host. Compartilhe o novo código com quem poderá acessar.", default="no", parent=self.winfo_toplevel()):
            return
        if self.on_revoke is not None:
            self.on_revoke()
        self.manager.stop()
        try:
            self.pairing = mesh.rotate_pairing(self.config_dir, port=self.pairing.port, fingerprint=self.pairing.fingerprint)
        except (OSError, ValueError, mesh.MeshError) as error:
            self.status_var.set(f"Não foi possível gerar outro código. A rede foi parada. {str(error)[:160]}")
            return
        self.code_var.set(self.pairing.code)
        self.hide_code()
        self.prepare_access()

    def prepare_access(self):
        if self.closed or self.pairing is None or (self.role == "host" and not self.host_enabled):
            return
        self.status_var.set("Preparando rede privada sem login…")
        self.manager.start(self.pairing)

    def _vault_path(self, pairing):
        return self.config_dir / "remembered" / f"{pairing.public_id}.bin"

    def load_remembered(self, address):
        if not is_pairing_address(address):
            return None
        public_id = address[5:-8]
        try:
            code = mesh.load_pairing_code(self.config_dir / "remembered" / f"{public_id}.bin")
            pairing = mesh.parse_pairing(code)
            if pairing_address(pairing) != address:
                raise mesh.MeshError("O código no cofre não corresponde ao computador salvo. Use Atualizar código.")
            return pairing
        except FileNotFoundError:
            return None

    def select_pairing(self, pairing, *, remembered=False):
        if self.closed:
            return False
        if not getattr(pairing, "fingerprint", ""):
            messagebox.showerror("Código sem certificado", "Copie um código atualizado na janela do Host antes de conectar.", parent=self.winfo_toplevel())
            return False
        if self.on_select is not None and self.on_select(pairing) is False:
            return False
        self.pairing = pairing
        self.code_var.set(pairing.code)
        self.remember_var.set(remembered)
        self.hide_code()
        return True

    def select_code(self):
        try:
            pairing = pairing_from_text(self.code_var.get())
        except (ValueError, mesh.MeshError):
            self.status_var.set("Código incompleto ou inválido. Copie novamente na janela do Host.")
            messagebox.showerror("Código inválido", "Cole o código privado completo copiado no Host.", parent=self.winfo_toplevel())
            self.code_entry.focus_set()
            return False
        remember = self.remember_var.get()
        if not pairing.fingerprint:
            messagebox.showerror("Código sem certificado", "Copie um código atualizado na janela do Host antes de salvar ou conectar.", parent=self.winfo_toplevel())
            return False
        try:
            if remember:
                save = getattr(self, "on_save", None)
                if save is not None:
                    if save(pairing) is False:
                        return False
                else:
                    mesh.save_pairing_code(self._vault_path(pairing), pairing.code)
        except (OSError, ValueError, mesh.MeshError) as error:
            self.status_var.set("Não foi possível salvar este acesso. Confira o aviso e tente novamente; nenhuma conexão foi iniciada. Você pode desmarcar Salvar para usar só nesta janela.")
            messagebox.showerror("Acesso não salvo", str(error)[:360], parent=self.winfo_toplevel())
            return False
        if not self.select_pairing(pairing, remembered=remember):
            return False
        self.status_var.set("Acesso salvo. Informe a senha atual; ela nunca é gravada. Aguarde a aprovação no Host." if remember else "Código aceito só nesta janela. Informe a senha e aguarde a aprovação no Host.")
        return True

    def stop(self):
        self.manager.stop()
        self.hide_code()
        if self.role == "host":
            self.host_enabled = False
            self.prepare_button.configure(state="disabled")
            self.renew_button.configure(state="disabled")

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.job is not None:
            self.after_cancel(self.job)
            self.job = None
        self.manager.close()
        self.code_var.set("")
