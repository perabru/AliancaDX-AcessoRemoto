"""Explicit in-app Windows updates, using only this project's release assets.

The app streams a bounded file into its per-user private cache and validates
GitHub's SHA256 digest before handing off. A helper running on the *base*
Python waits for the old process to exit before NSIS replaces its venv. The
existing visible installer owns rollback and preserves saved user settings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from updates import ASSET_NAMES, UpdateResult, _header, _version, valid_download_url

MAX_INSTALLER_BYTES = 512 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 12.0
DOWNLOAD_DEADLINE_SECONDS = 30 * 60
CDN_HOSTS = frozenset(("release-assets.githubusercontent.com", "objects.githubusercontent.com"))
ROLES = {"controller": ("Controller", "controller.py"), "host": ("Host", "host.py")}


class UpdateCancelled(Exception):
    pass


class UpdateInstallError(ValueError):
    pass


def valid_install_metadata(result, role):
    return (isinstance(result, UpdateResult) and result.status == "available" and
            valid_download_url(result.download_url, result.latest_version, role) and
            type(result.download_size) is int and 2 <= result.download_size <= MAX_INSTALLER_BYTES and
            isinstance(result.sha256, str) and re.fullmatch(r"[0-9a-f]{64}", result.sha256, re.ASCII) is not None)


def _safe_path(path):
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise UpdateInstallError("A pasta da atualização não pode ser um atalho ou link.")


def install_target(role, *, platform=None, source=None):
    if (platform or sys.platform) != "win32" or role not in ROLES:
        raise UpdateInstallError("A instalação pelo aplicativo requer a versão instalada para Windows.")
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise UpdateInstallError("Não foi possível localizar sua instalação do Windows.")
    target = Path(local) / "Programs" / "MiniAnyDesk" / ROLES[role][0]
    source = Path(source) if source is not None else Path(__file__).resolve().parent
    for item in (Path(local), target.parent.parent, target.parent, target, source,
                 target / ".venv", target / ".venv" / "Scripts", target / ".minianydesk-app.json"):
        _safe_path(item)
    if source.resolve() != target.resolve():
        raise UpdateInstallError("Use o aplicativo instalado pelo Setup para atualizar por aqui.")
    try:
        marker = json.loads((target / ".minianydesk-app.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise UpdateInstallError("A instalação atual não pôde ser identificada. Reinstale pelo site oficial.") from None
    if not isinstance(marker, dict) or marker.get("application") != "MiniAnyDeskNative" or marker.get("role") != role:
        raise UpdateInstallError("A pasta não pertence a este aplicativo. Reinstale pelo site oficial.")
    if not (target / ".venv" / "Scripts" / "pythonw.exe").is_file():
        raise UpdateInstallError("O ambiente instalado está incompleto. Reinstale pelo site oficial.")
    return target


def _cdn_url(url):
    if not isinstance(url, str) or any(ord(c) < 33 for c in url):
        return False
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.hostname in CDN_HOSTS and parts.netloc == parts.hostname and
                parts.username is None and parts.password is None and not parts.fragment and bool(parts.path))
    except ValueError:
        return False


class _AssetRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _cdn_url(newurl):
            raise UpdateInstallError("O download foi redirecionado para um endereço não autorizado.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_asset(request, *, timeout):
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_alpn_protocols(["http/1.1"])
    return build_opener(HTTPSHandler(context=context), _AssetRedirects()).open(request, timeout=timeout)


def _cache_base():
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise UpdateInstallError("Não foi possível preparar a pasta da atualização.")
    base = Path(local) / "MiniAnyDesk" / "updates"
    for item in (Path(local), base.parent, base):
        _safe_path(item)
    base.mkdir(parents=True, exist_ok=True)
    return base


def cleanup_download(path):
    """Delete only an updater-owned random directory, never another user path."""
    if path is None:
        return
    path = Path(path)
    try:
        base = _cache_base().resolve()
        folder = path.parent
        _safe_path(folder)
        if (folder.parent.resolve() == base and folder.name.startswith("pending-") and
                path.name in ASSET_NAMES.values()):
            shutil.rmtree(folder)
    except (OSError, UpdateInstallError):
        pass


def verify_download(path, result, role):
    if not valid_install_metadata(result, role):
        raise UpdateInstallError("Esta atualização não possui uma verificação de integridade válida.")
    path = Path(path)
    _safe_path(path)
    if not path.is_file() or path.stat().st_size != result.download_size:
        raise UpdateInstallError("A atualização está incompleta. Tente novamente.")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise UpdateInstallError("O arquivo recebido não é um instalador Windows válido.")
        stream.seek(0)
        for block in iter(lambda: stream.read(256 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != result.sha256:
        raise UpdateInstallError("A verificação de integridade falhou. Nenhum arquivo foi instalado; tente novamente.")
    return path


def download_update(result, role, cancelled, progress, *, opener=None):
    if not valid_install_metadata(result, role):
        raise UpdateInstallError("O GitHub ainda não publicou a verificação desta atualização. Tente novamente mais tarde.")
    if cancelled.is_set():
        raise UpdateCancelled()
    base = _cache_base()
    folder = Path(tempfile.mkdtemp(prefix="pending-", dir=base))
    folder.chmod(0o700)
    destination = folder / ASSET_NAMES[role]
    partial = folder / "download.part"
    request = Request(result.download_url, headers={"User-Agent": "AliancaDX-Updater/1",
        "Accept": "application/octet-stream", "Accept-Encoding": "identity"}, method="GET")
    try:
        deadline = time.monotonic() + DOWNLOAD_DEADLINE_SECONDS
        with (opener or _open_asset)(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            if response.geturl() != result.download_url and not _cdn_url(response.geturl()):
                raise UpdateInstallError("O endereço final da atualização não foi autorizado.")
            if type(response.status) is not int or response.status != 200:
                raise UpdateInstallError("O GitHub não concluiu o download. Tente novamente.")
            encoding = _header(response.headers, "Content-Encoding")
            if encoding is not None and (not isinstance(encoding, str) or encoding.lower().strip() != "identity"):
                raise UpdateInstallError("O download usa uma codificação inesperada.")
            length = _header(response.headers, "Content-Length")
            if length is not None and (not isinstance(length, str) or
                    re.fullmatch(r"[0-9]{1,12}", length, re.ASCII) is None or int(length) != result.download_size):
                raise UpdateInstallError("O tamanho da atualização não corresponde ao publicado.")
            reader = getattr(response, "read1", response.read)
            received, digest = 0, hashlib.sha256()
            with partial.open("xb") as stream:
                partial.chmod(0o600)
                while True:
                    if cancelled.is_set():
                        raise UpdateCancelled()
                    if time.monotonic() >= deadline:
                        raise UpdateInstallError("O download demorou demais. Confira a conexão e tente novamente.")
                    chunk = reader(min(256 * 1024, result.download_size + 1 - received))
                    if not isinstance(chunk, bytes):
                        raise UpdateInstallError("O GitHub enviou uma resposta inválida.")
                    if cancelled.is_set():
                        raise UpdateCancelled()
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > result.download_size:
                        raise UpdateInstallError("A atualização ultrapassou o tamanho publicado.")
                    stream.write(chunk)
                    digest.update(chunk)
                    progress(received, result.download_size)
                stream.flush()
                os.fsync(stream.fileno())
            if received != result.download_size:
                raise UpdateInstallError("O download foi interrompido. Tente novamente.")
            if digest.hexdigest() != result.sha256:
                raise UpdateInstallError("A verificação de integridade falhou. Nenhum arquivo foi instalado; tente novamente.")
            partial.replace(destination)
            verify_download(destination, result, role)
        return destination
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        shutil.rmtree(folder, ignore_errors=True)
        raise UpdateInstallError("Não foi possível concluir o download. Confira a conexão e o espaço em disco; tente novamente.") from error
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def launch_update(path, result, role, *, source=None, popen=None):
    """Start the handoff before the UI closes, without executing a shell string."""
    target = install_target(role, source=source)
    verify_download(path, result, role)
    path = Path(path).resolve()
    if path.parent.parent != _cache_base().resolve() or not path.parent.name.startswith("pending-") or path.name != ASSET_NAMES[role]:
        raise UpdateInstallError("O arquivo não pertence ao download interno desta atualização.")
    base_python = Path(getattr(sys, "_base_executable", sys.executable))
    if base_python.name.lower() in ("python.exe", "pythonw.exe"):
        base_python = base_python.with_name("pythonw.exe")
    if not base_python.is_file() or ".venv" in base_python.parts:
        raise UpdateInstallError("O Python principal não foi encontrado para concluir a atualização.")
    plan = path.parent / "apply.json"
    plan.write_text(json.dumps({"role": role, "version": result.latest_version, "sha256": result.sha256,
        "size": result.download_size, "installer": str(path), "parent_pid": os.getpid()}), encoding="utf-8")
    plan.chmod(0o600)
    ready = path.parent / "helper.ready"
    helper = target / "update_install.py"
    process = (popen or subprocess.Popen)([str(base_python), str(helper), "--apply", str(plan)],
        cwd=target, close_fds=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.monotonic() + 5
    while not ready.is_file():
        if process.poll() is not None or time.monotonic() >= deadline:
            cancel_handoff(path, process)
            raise UpdateInstallError("Não foi possível iniciar a instalação. O aplicativo continua aberto; tente novamente.")
        time.sleep(.025)
    return process


def cancel_handoff(path, process):
    """Cancel a ready helper before closing the application or changing files."""
    abort = Path(path).parent / "helper.abort"
    abort.write_text("cancelled", encoding="ascii")
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        # No force-killing: the helper also verifies the file before running.
        return False
    return True


def _wait_parent(pid, timeout=90, *, abort_path=None):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE, never terminate.
    if not handle:
        if ctypes.get_last_error() == 87:  # Already exited.
            return
        raise UpdateInstallError("Não foi possível aguardar o aplicativo. Feche-o antes de atualizar.")
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if abort_path is not None and Path(abort_path).is_file():
                raise UpdateCancelled()
            status = kernel.WaitForSingleObject(handle, 100)
            if status == 0:
                return
            if status != 258:
                raise UpdateInstallError("Não foi possível aguardar o aplicativo. Tente novamente.")
        raise UpdateInstallError("O aplicativo ainda está aberto. Feche todas as janelas e tente novamente.")
    finally:
        kernel.CloseHandle(handle)


def apply_plan(plan_path, *, runner=None, launcher=None, wait_parent=None):
    """Helper entry; recheck all invariants after the old process is gone."""
    plan_path = Path(plan_path).resolve()
    if plan_path.name != "apply.json" or plan_path.parent.parent != _cache_base().resolve() or not plan_path.parent.name.startswith("pending-"):
        raise UpdateInstallError("O pedido de atualização não foi reconhecido.")
    _safe_path(plan_path)
    if plan_path.stat().st_size > 4096:
        raise UpdateInstallError("O pedido de atualização é inválido.")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    role = plan["role"]
    target = install_target(role)
    version = plan["version"]
    _version(version)
    current = json.loads((target / ".minianydesk-app.json").read_text(encoding="utf-8"))["version"]
    if _version(version) <= _version(current):
        raise UpdateInstallError("A atualização recebida já está instalada ou é mais antiga.")
    path = plan_path.parent / ASSET_NAMES[role]
    if str(path) != plan["installer"]:
        raise UpdateInstallError("O caminho da atualização foi alterado.")
    result = UpdateResult("available", current, version, "", "https://github.com/perabru/AliancaDX-AcessoRemoto/releases/download/v" + version + "/" + path.name,
                          plan["size"], plan["sha256"])
    verify_download(path, result, role)
    pid = plan["parent_pid"]
    if type(pid) is not int or not 1 <= pid <= 0xFFFFFFFF or pid == os.getpid():
        raise UpdateInstallError("O processo a atualizar não foi reconhecido.")
    (plan_path.parent / "helper.ready").write_text("ready", encoding="ascii")
    if wait_parent is None:
        _wait_parent(pid, abort_path=plan_path.parent / "helper.abort")
    else:
        wait_parent(pid)
    verify_download(path, result, role)
    completed = (runner or subprocess.run)([str(path), "/S", "/UPDATE"], cwd=target, check=False)
    if completed.returncode != 0:
        raise UpdateInstallError("A instalação não foi concluída. Consulte instalacao.log na pasta do aplicativo e tente novamente.")
    marker = json.loads((target / ".minianydesk-app.json").read_text(encoding="utf-8"))
    if marker.get("version") != version or marker.get("role") != role:
        raise UpdateInstallError("O instalador terminou sem confirmar a nova versão. Abra o aplicativo e tente novamente.")
    (launcher or subprocess.Popen)([str(target / ".venv" / "Scripts" / "pythonw.exe"), str(target / ROLES[role][1])],
                                  cwd=target, close_fds=True)
    cleanup_download(path)


def main():
    from app_identity import configure_process_identity
    configure_process_identity("updater")
    parser = argparse.ArgumentParser(description="Atualização interna AliançaDX")
    parser.add_argument("--apply", required=True)
    args = parser.parse_args()
    try:
        apply_plan(args.apply)
        return 0
    except UpdateCancelled:
        return 2
    except Exception as error:
        import tkinter as tk
        from tkinter import messagebox
        from desktop_ui import _apply_window_icon
        root = tk.Tk()
        root.withdraw()
        _apply_window_icon(root)
        messagebox.showerror("AliançaDX · Atualização", str(error) or "A atualização não foi concluída. Abra o aplicativo e tente novamente.", parent=root)
        root.destroy()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
