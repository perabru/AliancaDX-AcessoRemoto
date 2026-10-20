"""Actual Tk/TLS/UI integration using three official EasyTier Linux processes.

The only transport injection selects the verified runtime, a forced local relay,
and force_relay=True. No endpoint or network manager is simulated. This proves
local application/backend integration, not physical ISP traversal or Windows.
"""
from __future__ import annotations
import hashlib
import shutil
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--display", type=int, default=179)
    parser.add_argument("--runtime-dir", type=Path, required=True, help="Verified official EasyTier2.6.4 Linux core directory")
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    project = args.project.resolve()
    sys.path.insert(0, str(project))
    display = f"127.0.0.1:{args.display}"
    xvfb = subprocess.Popen(["Xvfb", f":{args.display}", "-screen", "0", "1920x1080x24", "-nolisten", "unix", "-nolisten", "local", "-listen", "tcp", "-ac"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    os.environ["DISPLAY"] = display
    root = host_app = control = None
    relay = None
    relay_temp = None
    managers = []
    report = {"platform": "Linux Tk/Xvfb", "real_windows_installer": False, "real_public_relay": False, "real_native_local_relay": True, "forced_relay": True,
              "transport": "actual Linux EasyTier 2.6.4 Host + Controller + forced local relay; real TLS, UI, frames and files", "checks": [], "screenshots": [], "errors": []}
    try:
        import tkinter as tk
        from tkinter import messagebox
        import mss
        from PIL import Image
        import controller
        import host
        import mesh
        from mesh_ui import pairing_address

        deadline = time.monotonic() + 4
        while root is None:
            try:
                root = tk.Tk()
            except tk.TclError:
                if time.monotonic() >= deadline:
                    raise
                threading.Event().wait(.02)
        root.report_callback_exception = lambda kind, error, trace: report["errors"].append(str(error))
        approvals = []
        def approve(title, text, **kwargs):
            approvals.append((title, kwargs.get("default")))
            return True

        relay_temp = tempfile.TemporaryDirectory(prefix="mda6-real-native-gui-")
        qa_dir = Path(relay_temp.name)
        runtime_dir = qa_dir / "runtime"
        runtime_dir.mkdir()
        source_runtime = args.runtime_dir.resolve()
        entries = []
        for name in ("easytier-core",):
            target = runtime_dir / name
            shutil.copy2(source_runtime / name, target)
            entries.append({"filename": name, "size": target.stat().st_size, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        (runtime_dir / "manifest.json").write_text(json.dumps({"schema": 1, "package": {"version": "2.6.4"}, "files": entries}))
        core = mesh.verify_runtime(runtime_dir)
        relay_port, relay_rpc = mesh._free_port(), mesh._free_port()
        relay_config = qa_dir / "relay.toml"
        relay_config.write_text(f'''instance_name = "MiniAnyDesk-UI-QA-Local-Relay"
listeners = ["tcp://127.0.0.1:{relay_port}"]
[network_identity]
network_name = "qa-{os.urandom(8).hex()}"
network_secret = "{os.urandom(32).hex()}"
[flags]
no_tun = true
enable_encryption = true
bind_device = false
relay_network_whitelist = "*"
disable_upnp = true
enable_ipv6 = false
[console_logger]
level = "off"
[file_logger]
level = "off"
''')
        relay_config.chmod(0o600)
        relay = subprocess.Popen([str(core), "--config-file", str(relay_config), "--disable-env-parsing", "--rpc-portal", f"127.0.0.1:{relay_rpc}", "--rpc-portal-whitelist", "192.0.2.1/32"], stdout=subprocess.DEVNULL, **mesh._child_options())
        start_deadline = time.monotonic() + 10
        while True:
            try:
                with socket.create_connection(("127.0.0.1", relay_port), timeout=.1):
                    break
            except OSError:
                if relay.poll() is not None or time.monotonic() > start_deadline:
                    raise AssertionError("Actual local relay did not listen")
                threading.Event().wait(.03)
        RealManager = mesh.MeshManager
        def Manager(role, config_dir, runtime_dir=None, on_status=None):
            manager = RealManager(role, config_dir, runtime_dir=qa_dir / "runtime", on_status=on_status,
                                  peers=[f"tcp://127.0.0.1:{relay_port}"], force_relay=True)
            managers.append(manager)
            return manager

        def pump(predicate, label, timeout=40):
            until = time.monotonic() + timeout
            while time.monotonic() < until:
                root.update()
                if report["errors"]:
                    raise AssertionError(report["errors"])
                if predicate():
                    report["checks"].append(label)
                    return
                threading.Event().wait(.01)
            raise AssertionError(label + "; " + (control.connection_var.get() if control else ""))

        def capture(window, name):
            window.lift()
            for _ in range(8):
                root.update()
                threading.Event().wait(.02)
            target = args.output_dir / name
            with mss.MSS(display=display) as grabber:
                shot = grabber.grab({"left": window.winfo_rootx(), "top": window.winfo_rooty(), "width": window.winfo_width(), "height": window.winfo_height()})
                Image.frombytes("RGB", shot.size, shot.rgb).save(target)
            report["screenshots"].append(str(target))

        with tempfile.TemporaryDirectory(prefix="minianydesk-no-login-gui-") as directory, patch.dict(os.environ, {"LOCALAPPDATA": directory}), patch.object(mesh, "MeshManager", Manager), patch.object(messagebox, "askyesno", approve), patch.object(messagebox, "showerror", lambda title, text, **kwargs: report["errors"].append(f"{title}: {text}")), patch.object(messagebox, "showwarning", lambda title, text, **kwargs: report["errors"].append(f"{title}: {text}")):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            host_app = host.HostApp(root)
            host_app.port.set(str(port))
            host_app.computer_name.set("Computador do laboratório")
            host_app.save_computer_name()
            pump(lambda: host_app.server is not None and host_app.mesh_panel.pairing is not None, "Host opens with TLS and account-free network preparation")
            pair = host_app.mesh_panel.pairing
            assert pair.fingerprint == host_app.server.fingerprint
            assert host_app.mesh_panel.code_entry.cget("show") == "*"
            host_app._navigate("Internet sem login")
            capture(root, "host-internet.png")
            control_window = tk.Toplevel(root)
            control_window.report_callback_exception = root.report_callback_exception
            control = controller.Controller(control_window, controller.Settings(Path(directory) / "controller" / "control.json"))
            assert control.selected_page == "connect"
            assert not control.mesh_panel.remember_var.get()
            control.mesh_panel.code_var.set(pair.code)
            assert control.mesh_panel.select_code()
            assert control.selected_page == "connect"
            assert control.address_var.get() == pairing_address(pair)
            control.password_var.set(host_app.password.get())
            control.name_var.set("Bruno")
            capture(control_window, "controller-connect.png")
            control.connect()
            pump(lambda: control.peer is not None and host_app.peer is not None, "Private code binds Host TLS; password and local consent authorize session")
            pump(lambda: control.current_image is not None, "Real Host desktop frame displayed through existing TLS protocol")
            assert approvals == [("Autorizar acesso remoto?", "no")]
            native_pids = [relay.pid] + [manager._process.pid for manager in managers if manager._process is not None]
            assert len(native_pids) == 3 and len(set(native_pids)) == 3
            assert all(manager.force_relay for manager in managers)
            endpoint = control.mesh_panel.manager.snapshot().endpoint
            assert endpoint and endpoint[1] != pair.port
            report["native_processes_live"] = len(native_pids)
            report["actual_forward_port_distinct_from_host"] = True
            report["checks"].append("Three actual native EasyTier processes use forced relay; app TLS enters a distinct forwarded endpoint")
            folder = Path(directory)
            controller_source, host_source = folder / "controller-source.bin", folder / "host-source.bin"
            host_received, controller_received = folder / "host-received.bin", folder / "controller-received.bin"
            controller_payload, host_payload = os.urandom(65537), os.urandom(98317)
            controller_source.write_bytes(controller_payload)
            host_source.write_bytes(host_payload)
            from tkinter import filedialog
            def choose_open(**kwargs):
                return str(host_source if kwargs.get("parent") is root else controller_source)
            def choose_save(**kwargs):
                return str(host_received if kwargs.get("parent") is root else controller_received)
            with patch.object(filedialog, "askopenfilename", choose_open), patch.object(filedialog, "asksaveasfilename", choose_save):
                control._send_file()
                pump(lambda: host_received.exists() and not control.file_active, "Controller file reaches Host through real overlay with local file consent")
                assert host_received.read_bytes() == controller_payload
                control._receive_file()
                pump(lambda: controller_received.exists() and not control.file_active, "Host file reaches Controller through real overlay with local file consent")
                assert controller_received.read_bytes() == host_payload
            report["file_bytes_controller_to_host"] = len(controller_payload)
            report["file_bytes_host_to_controller"] = len(host_payload)
            report["file_sha256_match_both_directions"] = True
            key = controller.pin_key(pairing_address(pair), pair.port)
            assert control.settings.data["pins"][key] == pair.fingerprint.lower()
            assert "127.0.0.1" not in control.settings.path.read_text()
            assert pair.code not in control.settings.path.read_text()
            assert not (control.mesh_panel.config_dir / "remembered").exists()
            entries = control.book.entries()
            assert len(entries) == 1 and entries[0]["address"] == pairing_address(pair)
            control.book.update(entries[0]["id"], name="Meu computador remoto")
            control._refresh_computer_rows()
            assert control.book.entries()[0]["name"] == "Meu computador remoto"
            report["checks"].append("Pins and editable computer names use stable public Host identity; secret not persisted by default")
            capture(control_window, "controller-session.png")
            control.disconnect()
            pump(lambda: host_app.peer is None, "Controller End terminates TLS and its owned network")
            control.mesh_panel.code_var.set(pair.code)
            control.mesh_panel.remember_var.set(True)
            assert control.mesh_panel.select_code()
            assert control.mesh_panel.load_remembered(pairing_address(pair)) == pair
            report["checks"].append("Explicit remember option roundtrips through protected local code storage")
            host_app.stop_listener()
            before = host_app.mesh_panel.manager._generation
            for _ in range(30):
                root.update()
                threading.Event().wait(.01)
            host_app.mesh_panel.prepare_access()
            assert host_app.server is None and host_app.mesh_panel.manager._generation == before
            assert not host_app.available_while_open.get()
            report["checks"].append("Host End stops owned network and status updates do not restart it")
            host_app.start_listener()
            pump(lambda: host_app.server is not None and not host_app.starting, "Host can deliberately restart")
            host_app.mesh_panel.renew_code()
            assert host_app.mesh_panel.pairing.public_id != pair.public_id
            assert approvals[-1] == ("Revogar código privado", "no")
            report["checks"].append("Explicit local revocation creates another private network identity")
            control.close()
            control = None
            host_app.close()
            host_app = root = None
        for manager in managers:
            manager.stop()
            assert manager.join(8), "Actual native transport failed to stop"
        report["owned_native_processes_stopped"] = all(manager._process is None for manager in managers)
        assert report["owned_native_processes_stopped"]
        report["result"] = "PASS"
    except Exception as error:
        report["result"] = "FAIL"
        report["errors"].append(str(error))
        raise
    finally:
        if control is not None:
            control.close()
        if host_app is not None:
            host_app.close()
        elif root is not None:
            root.destroy()
        for manager in managers:
            manager.stop()
            manager.join(5)
        if relay is not None:
            mesh._terminate(relay)
        if relay_temp is not None:
            relay_temp.cleanup()
        xvfb.terminate()
        try:
            xvfb.wait(timeout=3)
        except subprocess.TimeoutExpired:
            xvfb.kill()
        (args.output_dir / "actual-native-ui-relay.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"result": report.get("result"), "checks": len(report["checks"]), "errors": report["errors"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
