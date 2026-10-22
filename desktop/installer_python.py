"""Visible, per-user Python installer for AliançaDX Controller and Host."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import struct
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
import uuid

VERSION = "6.2.1"
PUBLISHER = "AliançaDX — Bruno Michel Pera"
DISPLAY_NAME = "AliançaDX Acesso Remoto"
SOURCE = Path(__file__).resolve().parent
ROLES = {"controller": ("Controlador", "Controller", "controller.py"),
         "host": ("Host", "Host", "host.py")}
BRAND_FILES = ("aliancadx-logo.png", "aliancadx-logo-original.jpg", "aliancadx-mark-256.png",
               "aliancadx-app-256.png", "aliancadx.ico")
PAYLOAD = ("controller.py", "host.py", "net.py", "adaptive_stream.py", "transfer.py",
           "desktop_geometry.py", "desktop_ui.py", "computer_book.py", "presence.py", "network_addresses.py",
           "mesh.py", "mesh_ui.py",
           "installer_python.py", "requirements.txt", "COMECE_AQUI.html", "GUIA_RAPIDO.txt",
           "CONFIANCA_WINDOWS.txt", "Abrir_AliancaDX_Host.cmd", "Abrir_AliancaDX_Controlador.cmd",
           *("assets/brand/" + name for name in BRAND_FILES))
LEGACY_PAYLOAD = ("vpn.py", "vpn_ui.py", "vpn_setup.py", "auto_connect.py")


def bundle_files(folder: Path) -> tuple[str, ...]:
    """Validate the bundled userspace network component before installing it."""
    safe_private_folder(folder)
    manifest_path = folder / "manifest.json"
    safe_private_folder(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise RuntimeError("Manifesto do componente de rede inválido.")
    package = manifest.get("package", {})
    entries = manifest.get("files", [])
    if not isinstance(package, dict) or not isinstance(entries, list):
        raise RuntimeError("Manifesto do componente de rede inválido.")
    if (manifest.get("schema") != 1 or package.get("type") != "minianydesk-easytier-userspace"
            or package.get("version") != "2.6.4" or package.get("architecture") != "amd64"):
        raise RuntimeError("Componente de rede incompatível. Extraia novamente o pacote completo.")
    names = ["manifest.json"]
    for item in entries:
        if not isinstance(item, dict):
            raise RuntimeError("Manifesto do componente de rede inválido.")
        name, size, digest = item.get("filename"), item.get("size"), item.get("sha256")
        if (not isinstance(name, str) or not name or "/" in name or "\\" in name
                or name in (".", "..") or name in names
                or not isinstance(size, int) or isinstance(size, bool) or size <= 0
                or not isinstance(digest, str) or len(digest) != 64
                or any(ch not in "0123456789abcdef" for ch in digest)):
            raise RuntimeError("Manifesto do componente de rede inválido.")
        path = folder / name
        safe_private_folder(path)
        if not path.is_file() or path.stat().st_size != size or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Componente de rede ausente ou alterado: " + name)
        names.append(name)
    required = {"easytier-core.exe", "Packet.dll", "LICENSE.txt", "NOTICE.txt",
                "THIRD_PARTY_LICENSES.txt", "PROVENANCE.json"}
    if not required.issubset(names):
        raise RuntimeError("O pacote de rede está incompleto. Extraia novamente todos os arquivos.")
    if {path.name for path in folder.iterdir()} != set(names):
        raise RuntimeError("O pacote de rede contém arquivos não listados no manifesto.")
    return tuple(names)


CHECK = (
    "import sys,struct,tkinter,PIL,mss,pyautogui,cryptography; "
    "assert sys.platform=='win32'; assert struct.calcsize('P')==8; "
    "assert sys.version_info[:2] in ((3,11),(3,12)); "
    "assert sys.getwindowsversion().major==10; "
    "import net,transfer,desktop_geometry,desktop_ui,computer_book,presence,network_addresses,mesh,mesh_ui"
)


def install_path(role: str) -> Path:
    if role not in ROLES:
        raise ValueError("Papel de instalacao invalido.")
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("A pasta LOCALAPPDATA do Windows nao esta disponivel.")
    return Path(local) / "Programs" / "MiniAnyDesk" / ROLES[role][1]


def check_platform() -> None:
    if sys.platform != "win32" or struct.calcsize("P") != 8:
        raise RuntimeError("Este instalador requer Windows 10/11 x64 e Python de 64 bits.")
    if sys.version_info[:2] not in ((3, 11), (3, 12)):
        raise RuntimeError("Use Python 3.11 ou 3.12 de 64 bits com Tcl/Tk e pip.")
    if sys.getwindowsversion().major != 10:
        raise RuntimeError("Este pacote requer Windows 10 ou Windows 11.")


def safe_private_folder(folder: Path) -> None:
    if folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction()):
        raise RuntimeError(f"A pasta privada nao pode ser um link: {folder}")


def app_registry(role: str) -> str:
    return "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\MiniAnyDeskNative" + ROLES[role][1]


def cleanup_shortcuts(role: str, desktop: Path, programs: Path, *, legacy_only: bool = False) -> None:
    """Remove only this role's known links; leave other roles and user links."""
    display = ROLES[role][0]
    legacy_menu = programs / "MiniAnyDesk"
    current_menu = programs / DISPLAY_NAME
    links = [desktop / f"MiniAnyDesk {display}.lnk", programs / f"MiniAnyDesk {display}.lnk",
             legacy_menu / f"MiniAnyDesk {display}.lnk", legacy_menu / f"Desinstalar {display}.lnk"]
    if not legacy_only:
        links.extend((desktop / f"{DISPLAY_NAME} {display}.lnk", programs / f"{DISPLAY_NAME} {display}.lnk",
                      current_menu / f"{DISPLAY_NAME} {display}.lnk", current_menu / f"Desinstalar {display}.lnk"))
    for link in links:
        link.unlink(missing_ok=True)
    for folder in ((legacy_menu,) if legacy_only else (legacy_menu, current_menu)):
        try:
            folder.rmdir()
        except OSError:
            pass


def shell_shortcut_folders() -> tuple[Path, Path]:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Shell Folders") as key:
        return tuple(Path(winreg.QueryValueEx(key, name)[0]) for name in ("Desktop", "Programs"))


class Installer:
    def __init__(self, role: str = "controller", from_nsis: bool = False, uninstall: bool = False):
        self.from_nsis = from_nsis
        self.uninstall_mode = uninstall
        self.result = 2
        self.busy = False
        self.cancelled = threading.Event()
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.process: subprocess.Popen | None = None
        self.closing = False
        self.committing = False
        from desktop_ui import apply_theme, card, ScrollForm, BG, WHITE, NAVY, ORANGE, BORDER

        self.root = tk.Tk()
        self.root.title(f"{DISPLAY_NAME} | Instalação {VERSION}")
        self.root.geometry("840x680")
        self.root.minsize(720, 600)
        apply_theme(self.root)
        family = self.root._minianydesk_ui_font
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.role = tk.StringVar(value=role)
        self.status = tk.StringVar(value=("Clique em Instalar para preparar este aplicativo." if from_nsis
                                         else "Escolha o aplicativo e clique em Instalar.") if not uninstall
                                  else "Confirme a remoção deste aplicativo.")

        header = ttk.Frame(self.root, padding=(26, 20, 26, 14))
        header.pack(fill="x")
        identity = ttk.Frame(header)
        identity.pack(fill="x")
        try:
            self.brand_logo = tk.PhotoImage(file=str(SOURCE / "assets/brand/aliancadx-logo.png")).subsample(2, 2)
            tk.Label(identity, image=self.brand_logo, bg=WHITE, borderwidth=0,
                     padx=8, pady=4).pack(side="left", padx=(0, 14))
        except (OSError, tk.TclError):
            ttk.Label(identity, text="AliançaDX", style="Title.TLabel").pack(side="left", padx=(0, 14))
        ttk.Label(identity, text="ACESSO REMOTO", style="Muted.TLabel").pack(side="left")
        ttk.Label(identity, text="VERSÃO " + VERSION, style="Muted.TLabel").pack(side="right")
        ttk.Label(header, text="Remover aplicativo" if uninstall else "Instalar neste computador",
                  style="Heading.TLabel").pack(anchor="w", pady=(12, 2))
        ttk.Label(header, text="Acesso entre redes sem conta. Seus dados salvos são preservados.",
                  style="Muted.TLabel").pack(anchor="w")

        form = ScrollForm(self.root)
        form.pack(fill="both", expand=True, padx=24, pady=(0, 14))
        panel = card(form.content, padding=20)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Aplicativo selecionado" if from_nsis or uninstall else "Escolha o aplicativo",
                  style="CardHeading.TLabel").pack(anchor="w", pady=(0, 8))
        choices = ttk.Frame(panel, style="Card.TFrame", borderwidth=0)
        choices.pack(fill="x")
        self.radios = []
        role_details = []
        for value, title, detail in (("controller", "Controlador", "Acesse e controle outro computador."),
                                     ("host", "Hospedeiro", "Compartilhe a tela deste computador.")):
            choice = card(choices, padding=(10, 8))
            choice.pack(side="left", fill="x", expand=True,
                        padx=(0, 6) if value == "controller" else (6, 0))
            radio = tk.Radiobutton(choice, text=title, variable=self.role, value=value,
                                   bg=WHITE, fg=NAVY, activebackground=WHITE,
                                   activeforeground=NAVY, selectcolor="#E4F4EB",
                                   highlightbackground=WHITE, highlightcolor=ORANGE, borderwidth=0,
                                   justify="left", anchor="w", font=(family, 10, "bold"),
                                   command=self.update_role)
            radio.pack(fill="x")
            detail_label = ttk.Label(choice, text=detail, style="CardMuted.TLabel", wraplength=285)
            detail_label.pack(anchor="w", padx=4)
            role_details.append(detail_label)
            self.radios.append(radio)
        self.path_label = ttk.Label(panel, text="", style="CardMuted.TLabel", wraplength=740)
        self.path_label.pack(fill="x", pady=(10, 12))
        self.update_role()
        ttk.Separator(panel).pack(fill="x", pady=(0, 12))

        info = "Instalação para seu usuário. Abra o aplicativo quando desejar utilizá-lo."
        if not uninstall:
            if not (SOURCE / "wheelhouse").is_dir():
                info += "\nModo fontes: requer internet para obter as dependências do PyPI."
            else:
                info += "\nPython e dependências incluídos. Ambiente privado para cada aplicativo."
            info += ("\nRede privada integrada, sem login e sem instalar um adaptador VPN."
                     "\nAbra o Host, copie o código de acesso e informe-o no Controlador. Ambos precisam de internet.")
        else:
            info += "\nConfigurações, certificados, computadores salvos e arquivos recebidos serão preservados."
        self.info_label = ttk.Label(panel, text=info, style="CardMuted.TLabel", wraplength=740)
        self.info_label.pack(fill="x", pady=(0, 14))
        status_label = ttk.Label(panel, textvariable=self.status, style="CardStatus.TLabel", wraplength=740)
        status_label.pack(fill="x", pady=(0, 7))
        self.progress = ttk.Progressbar(panel, mode="determinate", value=0, length=300)
        self.progress.pack(fill="x", pady=(0, 12))
        ttk.Label(panel, text="Detalhes da instalação", style="CardMuted.TLabel").pack(anchor="w", pady=(0, 5))
        details = ttk.Frame(panel, style="Card.TFrame", borderwidth=0)
        details.pack(fill="both", expand=True)
        self.log = tk.Text(details, height=5, font=("Consolas", 9), bg=BG, fg=NAVY,
                           relief="flat", borderwidth=0, highlightthickness=1,
                           highlightbackground=BORDER, state="disabled", wrap="word", padx=9, pady=7)
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(details, command=self.log.yview)
        scrollbar.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scrollbar.set)

        def wrap_labels(event) -> None:
            width = max(1, event.width - 44)
            for label in (self.path_label, self.info_label, status_label):
                label.configure(wraplength=width)
            for label in role_details:
                label.configure(wraplength=max(1, width // 2 - 36))

        panel.bind("<Configure>", wrap_labels)

        actions = ttk.Frame(self.root, padding=(24, 0, 24, 18))
        actions.pack(side="bottom", fill="x", before=form)
        ttk.Label(actions, text="Windows 10/11 | 64 bits", style="Muted.TLabel").pack(side="left")
        self.primary = ttk.Button(actions, text="Desinstalar" if uninstall else "Instalar",
                                  command=self.begin, style="Danger.TButton" if uninstall else "Primary.TButton")
        self.primary.pack(side="right")
        self.secondary = ttk.Button(actions, text="Fechar", command=self.close, style="Secondary.TButton")
        self.secondary.pack(side="right", padx=8)
        if not uninstall:
            self.network_help = ttk.Button(actions, text="Como funciona?", command=self.open_network_help,
                                       style="Secondary.TButton")
            self.network_help.pack(side="right", padx=(0, 8))
        if from_nsis or uninstall:
            for radio in self.radios:
                radio.configure(state="disabled")
        self.root.after(80, self.drain)

    def open_network_help(self) -> None:
        """Explain the included userspace network without a separate account."""
        messagebox.showinfo("Acesso pela internet sem login",
            "1. Instale o Host no computador que será acessado e o Controlador no outro.\n"
            "2. Abra o Host e copie o código de acesso apresentado.\n"
            "3. Cole o código no Controlador, informe a senha e conecte.\n"
            "4. Confirme a identidade e autorize cada sessão na janela do Host.\n\n"
            "Os componentes estão incluídos. A comunicação utiliza conexões de saída, "
            "tentativas diretas e retransmissores públicos. Internet e pelo menos um caminho "
            "de rede disponível continuam necessários.", parent=self.root)

    def update_role(self) -> None:
        try:
            self.path_label.configure(text="Pasta: " + str(install_path(self.role.get())))
        except RuntimeError:
            self.path_label.configure(text="Destino: pasta de programas do usuario no Windows.")

    def write(self, line: str) -> None:
        self.events.put(("log", line))

    def drain(self) -> None:
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "log":
                    self.log.configure(state="normal")
                    self.log.insert("end", text.rstrip() + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                    if text.startswith(("Preservando o ambiente", "Criando ambiente", "Instalando dependencias",
                                        "Baixando dependencias", "Verificando Python")):
                        self.status.set(text)
                elif kind == "done":
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=100)
                    self.busy = False
                    self.result = 0
                    if self.closing:
                        self.root.destroy()
                        return
                    self.status.set(text)
                    self.primary.configure(text="Continuar" if self.from_nsis else "Concluir", command=self.close, state="normal")
                    self.secondary.configure(text="Fechar", state="normal")
                elif kind == "committing":
                    self.status.set("Finalizando a instalacao...")
                    self.secondary.configure(state="disabled")
                elif kind == "error":
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=0)
                    self.busy = False
                    self.result = 1
                    if self.closing:
                        self.result = 2
                        self.root.destroy()
                        return
                    self.status.set("A operacao nao foi concluida.")
                    self.primary.configure(text="Tentar novamente", state="normal")
                    self.secondary.configure(text="Fechar", state="normal")
                    messagebox.showerror("AliançaDX", text, parent=self.root)
        except queue.Empty:
            pass
        self.root.after(80, self.drain)

    def run_command(self, argv: list[str], cwd: Path) -> None:
        if self.cancelled.is_set():
            raise RuntimeError("Instalacao cancelada.")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        with (cwd / "instalacao.log").open("a", encoding="utf-8") as logfile:
            process = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace", creationflags=flags,
                                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            self.process = process
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    self.write(line)
                    logfile.write(line)
                    logfile.flush()
                code = process.wait()
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
                if process.stdout:
                    process.stdout.close()
                self.process = None
        if self.cancelled.is_set():
            raise RuntimeError("Instalacao cancelada.")
        if code:
            raise RuntimeError("Uma etapa falhou. Consulte instalacao.log e feche o aplicativo antes de atualizar.")

    def begin(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.cancelled.clear()
        self.committing = False
        self.progress.configure(mode="indeterminate")
        self.progress.start(15)
        self.primary.configure(state="disabled")
        self.secondary.configure(text="Cancelar")
        for radio in self.radios:
            radio.configure(state="disabled")
        role = self.role.get()
        self.status.set(("Removendo " if self.uninstall_mode else "Preparando ") + ROLES[role][0] + "...")
        threading.Thread(target=self.worker, args=(role,), daemon=True).start()

    def worker(self, role: str) -> None:
        target = None
        backup_private = None
        original_files = {}
        copied_payload = False
        creating_private = False
        created_network_bundle = False
        created_brand_directories = []
        try:
            if self.cancelled.is_set():
                raise RuntimeError("Instalacao cancelada.")
            check_platform()
            target = install_path(role)
            safe_private_folder(target)
            if self.uninstall_mode:
                self.committing = True
                removed = self.uninstall(role, target)
                self.events.put(("done", "Aplicativo removido. Seus dados foram preservados." if removed
                                 else "Desinstalador Windows aberto. Conclua a remocao nessa janela."))
                return
            network_files = bundle_files(SOURCE / "easytier_bundle")
            for name in PAYLOAD:
                if not (SOURCE / name).is_file():
                    raise RuntimeError(f"Arquivo ausente: {name}. Extraia o pacote completo ou execute o EXE.")
                if name.endswith(".py"):
                    compile((SOURCE / name).read_bytes(), name, "exec")
            target.mkdir(parents=True, exist_ok=True)
            previous_marker = target / ".minianydesk-app.json"
            original_files[previous_marker.name] = previous_marker.read_bytes() if previous_marker.exists() else None
            if SOURCE.resolve() != target.resolve():
                copied_payload = True
                for relative in ("assets", "assets/brand"):
                    directory = target / relative
                    safe_private_folder(directory)
                    if not directory.exists():
                        directory.mkdir()
                        created_brand_directories.append(directory)
                for name in PAYLOAD:
                    safe_private_folder(target / name)
                    original_files[name] = (target / name).read_bytes() if (target / name).exists() else None
                    shutil.copy2(SOURCE / name, target / name)
                for name in ("README.txt", "NOTICE.txt"):
                    if (SOURCE / name).is_file():
                        shutil.copy2(SOURCE / name, target / name)
                if (SOURCE / "wheelhouse").is_dir():
                    shutil.copytree(SOURCE / "wheelhouse", target / "wheelhouse", dirs_exist_ok=True)
                source_network = SOURCE / "easytier_bundle"
                target_network = target / "easytier_bundle"
                safe_private_folder(target_network)
                created_network_bundle = not target_network.exists()
                target_network.mkdir(exist_ok=True)
                for filename in network_files:
                    origin = source_network / filename
                    destination = target_network / filename
                    safe_private_folder(destination)
                    name = "easytier_bundle/" + filename
                    original_files[name] = destination.read_bytes() if destination.exists() else None
                    shutil.copy2(origin, destination)
            bundle_files(target / "easytier_bundle")
            private = target / ".venv"
            safe_private_folder(private)
            python = private / "Scripts" / "python.exe"
            if private.exists():
                if not (private / "pyvenv.cfg").is_file():
                    raise RuntimeError("A pasta .venv existente nao e um ambiente privado valido.")
                if Path(sys.prefix).resolve() == private.resolve():
                    raise RuntimeError("Abra o instalador EXE ou use o Python principal para reparar esta instalacao.")
                self.write("Preservando o ambiente anterior ate confirmar a atualizacao...")
                backup_private = target / (".venv-backup-" + uuid.uuid4().hex)
                private.rename(backup_private)
            creating_private = True
            self.write("Criando ambiente Python privado...")
            self.run_command([sys.executable, "-m", "venv", str(private)], target)
            pip = [str(python), "-m", "pip", "--disable-pip-version-check", "--no-input"]
            wheelhouse = target / "wheelhouse"
            options = (["--no-index", "--find-links", str(wheelhouse), "--only-binary=:all:"]
                       if wheelhouse.is_dir() else ["--index-url", "https://pypi.org/simple", "--timeout", "45", "--retries", "3", "--prefer-binary"])
            self.write("Instalando dependencias incluidas..." if wheelhouse.is_dir() else "Baixando dependencias pelo PyPI...")
            self.run_command(pip + ["install", *options, "setuptools", "wheel"], target)
            self.run_command(pip + ["install", *options, "-r", str(target / "requirements.txt")], target)
            self.write("Verificando Python, dependencias e interface grafica...")
            self.run_command([str(python), "-c", CHECK], target)
            if self.cancelled.is_set():
                raise RuntimeError("Instalacao cancelada.")
            self.committing = True
            if self.cancelled.is_set():
                raise RuntimeError("Instalacao cancelada.")
            self.events.put(("committing", ""))
            marker = {"application": "MiniAnyDeskNative", "version": VERSION, "role": role,
                      "requirements_sha256": hashlib.sha256((target / "requirements.txt").read_bytes()).hexdigest(),
                      "base_python": str(getattr(sys, "_base_executable", sys.executable)),
                      "installer": "nsis" if self.from_nsis else "python"}
            (target / ".minianydesk-app.json").write_text(json.dumps(marker, indent=2), encoding="utf-8")
            if not self.from_nsis:
                self.shortcuts_and_registry(role, target)
            if backup_private:
                try:
                    shutil.rmtree(backup_private)
                    backup_private = None
                except OSError:
                    self.write("O ambiente anterior foi mantido como backup; feche o aplicativo antes da proxima atualizacao.")
            self.write("Instalacao concluida. Abra " + DISPLAY_NAME + " " + ROLES[role][0] + " pelo atalho.")
            self.events.put(("done", ROLES[role][0] + " instalado para este usuario." + " Rede integrada incluída, sem login."))
        except (OSError, RuntimeError, ValueError, SyntaxError, subprocess.SubprocessError) as error:
            if target and backup_private and backup_private.exists():
                try:
                    replacement = target / ".venv"
                    safe_private_folder(replacement)
                    if replacement.exists():
                        shutil.rmtree(replacement)
                    backup_private.rename(replacement)
                    self.write("Ambiente anterior restaurado.")
                except OSError as restore_error:
                    self.write("Nao foi possivel restaurar o ambiente automaticamente: " + str(restore_error))
            elif target and creating_private:
                try:
                    replacement = target / ".venv"
                    safe_private_folder(replacement)
                    if replacement.exists():
                        shutil.rmtree(replacement)
                except OSError as cleanup_error:
                    self.write("Nao foi possivel remover o ambiente incompleto: " + str(cleanup_error))
            if target:
                for name, data in original_files.items():
                    if name == ".minianydesk-app.json" or copied_payload:
                        try:
                            if data is None:
                                (target / name).unlink(missing_ok=True)
                            else:
                                (target / name).write_bytes(data)
                        except OSError:
                            self.write("Nao foi possivel restaurar o arquivo anterior: " + name)
                if created_network_bundle:
                    try:
                        (target / "easytier_bundle").rmdir()
                    except OSError:
                        pass
                for directory in reversed(created_brand_directories):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
            self.write("ERRO: " + str(error))
            self.events.put(("error", str(error)))

    def shortcuts_and_registry(self, role: str, target: Path) -> None:
        import winreg
        display, _, entry = ROLES[role]
        # Values travel through environment variables; no user-provided path is
        # interpolated into PowerShell code, and no execution policy is changed.
        code = ("$ErrorActionPreference='Stop'; $s=New-Object -ComObject WScript.Shell; "
                "$menu=Join-Path ([Environment]::GetFolderPath('Programs')) $env:MINI_MENU; "
                "[IO.Directory]::CreateDirectory($menu)|Out-Null; "
                "$dirs=@([Environment]::GetFolderPath('Desktop'),$menu); "
                "foreach($d in $dirs){$l=$s.CreateShortcut((Join-Path $d $env:MINI_SHORTCUT)); "
                "$l.TargetPath=Join-Path $env:MINI_ROOT '.venv\\Scripts\\pythonw.exe'; "
                "$l.Arguments='\"'+(Join-Path $env:MINI_ROOT $env:MINI_ENTRY)+'\"'; "
                "$l.WorkingDirectory=$env:MINI_ROOT; "
                "$l.IconLocation=(Join-Path $env:MINI_ROOT 'assets\\brand\\aliancadx.ico')+',0'; $l.Save(); "
                "if(!(Test-Path -LiteralPath (Join-Path $d $env:MINI_SHORTCUT))){throw 'Shortcut not created'}}")
        completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", code],
                                   capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
                                   env={**os.environ, "MINI_ROOT": str(target), "MINI_ENTRY": entry,
                                        "MINI_SHORTCUT": f"{DISPLAY_NAME} {display}.lnk", "MINI_MENU": DISPLAY_NAME}, timeout=60)
        if completed.returncode:
            raise RuntimeError("Nao foi possivel criar os atalhos. Consulte as regras do PowerShell neste computador.")
        cleanup_shortcuts(role, *shell_shortcut_folders(), legacy_only=True)
        base = Path(getattr(sys, "_base_executable", sys.executable)).with_name("pythonw.exe")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, app_registry(role)) as key:
            for name, value in {"DisplayName": f"{DISPLAY_NAME} {display}", "DisplayVersion": VERSION,
                                "Publisher": PUBLISHER, "InstallLocation": str(target),
                                "UninstallString": f'"{base}" "{target / "installer_python.py"}" --role {role} --uninstall'}.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)

    def uninstall(self, role: str, target: Path) -> bool:
        marker = json.loads((target / ".minianydesk-app.json").read_text(encoding="utf-8"))
        if marker.get("application") != "MiniAnyDeskNative" or marker.get("role") != role:
            raise RuntimeError("A pasta nao corresponde a uma instalacao reconhecida deste aplicativo.")
        if marker.get("installer") != "python" and (target / "Uninstall.exe").exists():
            subprocess.Popen([str(target / "Uninstall.exe")])
            self.write("O desinstalador Windows desta instalacao foi aberto.")
            return False
        directories = (".venv", "wheelhouse", "easytier_bundle", "vpn_bundle", "__pycache__")
        for directory in (*directories, "assets", "assets/brand"):
            safe_private_folder(target / directory)
        for directory in directories:
            folder = target / directory
            if folder.exists():
                shutil.rmtree(folder)
        for name in (*PAYLOAD, *LEGACY_PAYLOAD, "README.txt", "NOTICE.txt", "instalacao.log", ".minianydesk-app.json",
                     "bootstrap.cmd", "Uninstall.exe", "python-3.12.10-amd64.exe"):
            (target / name).unlink(missing_ok=True)
        for directory in ("assets/brand", "assets"):
            try:
                (target / directory).rmdir()
            except OSError:
                pass
        try:
            target.rmdir()
        except OSError:
            self.write("Arquivos adicionais foram preservados na pasta do aplicativo.")
        import winreg
        cleanup_shortcuts(role, *shell_shortcut_folders())
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, app_registry(role))
        except FileNotFoundError:
            pass
        return True

    def close(self) -> None:
        if self.busy:
            if self.committing:
                messagebox.showinfo("AliançaDX", "A operacao esta sendo finalizada. Aguarde a conclusao.", parent=self.root)
                return
            if not messagebox.askyesno("Cancelar instalacao", "Cancelar a operacao em andamento?", parent=self.root):
                return
            self.cancelled.set()
            self.closing = True
            process = self.process
            if process and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
            self.result = 2
            self.status.set("Cancelando e restaurando a instalacao anterior...")
            self.primary.configure(state="disabled")
            self.secondary.configure(state="disabled")
            return
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return self.result


def main() -> int:
    parser = argparse.ArgumentParser(description="Instalador Python visivel AliançaDX")
    parser.add_argument("--role", choices=ROLES, default="controller")
    parser.add_argument("--from-nsis", action="store_true", help="prepara o ambiente dentro do instalador EXE")
    parser.add_argument("--uninstall", action="store_true", help="remove somente este aplicativo")
    args = parser.parse_args()
    return Installer(args.role, args.from_nsis, args.uninstall).run()


if __name__ == "__main__":
    raise SystemExit(main())
