"""Manual Linux/Xvfb smoke test of the actual native Host and Controller.

Dialogs are answered explicitly by this test for temporary local test files.
This validates Tk/TLS/transfer integration, not Windows installation.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

PROJECT = Path(__file__).resolve().parents[1]
GUI_DEPS = PROJECT.parent / "native_build" / "gui_deps"
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(GUI_DEPS))


HOST_SCRIPT = r'''
import json, os, sys, threading
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, filedialog
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2])
import host
from host import HostApp
if sys.argv[5] == 'record':
    # Exercise decoded wire commands and release semantics without requiring a
    # platform-specific native input library in the Linux review environment.
    # This is deliberately opt-in and is never a physical-input proof.
    class RecordedInput:
        def __init__(self): self.held_keys=set(); self.held_buttons=set()
        def key(self,name,down):
            if down: self.held_keys.add(name)
            else: self.held_keys.discard(name)
        def button(self,name,down):
            if down: self.held_buttons.add(name)
            else: self.held_buttons.discard(name)
        def move(self,*a): pass
        def wheel(self,*a): pass
        def release(self): self.held_keys.clear(); self.held_buttons.clear()
    host.InputController = RecordedInput
folder=Path(sys.argv[3]); deny=[False]
messagebox.askyesno=lambda title,text,**kw: not(deny[0] and 'Receber arquivo' in title)
messagebox.showerror=lambda *a,**kw: print('HOST_ERROR',a,flush=True)
filedialog.asksaveasfilename=lambda **kw: str(folder/'host-received.bin')
filedialog.askopenfilename=lambda **kw: str(folder/'host-source.bin')
root=tk.Tk(); app=HostApp(root); root.geometry('720x780+1190+10')
app.port.set(sys.argv[4]); app.password.set('native-smoke-password'); app.start_listener()
def commands():
    for line in sys.stdin:
        command=json.loads(line)
        if command=='pause': app.dispatch(app.toggle_pause)
        elif command=='disconnect': app.dispatch(app.disconnect)
        elif command=='deny': deny[0]=True
        elif command=='allow': deny[0]=False
        elif command=='quit': app.dispatch(app.close); break
threading.Thread(target=commands,daemon=True).start()
def status():
    if app.shutdown.is_set(): return
    value={'ready':bool(app.server),'connected':bool(app.peer and not app.peer.closed),
           'localPaused':app.local_paused,'capture':app.last_screen,'status':app.status.get(),
           'keys':list(app.inputs.held_keys),'buttons':list(app.inputs.held_buttons),'log':app.log_box.get('1.0','end')[-1500:]}
    target=folder/'host-status.json'; temp=folder/'host-status.tmp'
    temp.write_text(json.dumps(value));temp.replace(target)
    root.after(50,status)
root.after(50,status);root.mainloop()
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record-input', action='store_true',
                        help='record decoded input commands; do not emit native keyboard/mouse events')
    args = parser.parse_args()
    print('INPUT_BACKEND:', 'recorded wire commands (native input not tested)' if args.record_input else 'native desktop input', flush=True)
    display = "127.0.0.1:109"
    xvfb = subprocess.Popen(["Xvfb", ":109", "-screen", "0", "1920x1080x24", "-nolisten", "unix", "-nolisten", "local", "-listen", "tcp", "-ac"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    os.environ["DISPLAY"] = display
    host_process = None
    root = None
    try:
        import tkinter as tk
        from tkinter import messagebox, filedialog
        deadline = time.monotonic() + 4
        while root is None:
            try:
                root = tk.Tk()
            except tk.TclError:
                if time.monotonic() > deadline:
                    raise RuntimeError("Xvfb did not start: " + xvfb.stderr.read().decode())
                time.sleep(.05)
        from controller import Controller, Settings, pin_key
        import mss
        from PIL import Image
        with tempfile.TemporaryDirectory(prefix="minianydesk-native-gui-") as directory:
            folder = Path(directory)
            errors = []
            dialogs = []
            root.report_callback_exception = lambda kind, error, traceback: errors.append(str(error))
            def approve(title, text, **kwargs):
                dialogs.append((title, text, kwargs.get("default")))
                return True
            messagebox.askyesno = approve
            messagebox.showerror = lambda *args, **kwargs: errors.append(str(args))
            messagebox.showwarning = lambda *args, **kwargs: errors.append(str(args))
            filedialog.askopenfilename = lambda **kwargs: str(folder / "controller-source.bin")
            filedialog.asksaveasfilename = lambda **kwargs: str(folder / "controller-received.bin")
            settings = Settings(folder / "control.json")
            app = Controller(root, settings)
            assert app.selected_page == "connect", "First use must show automatic access"
            root.geometry("1180x800+0+0")
            source = bytes(range(251)) * 1050
            (folder / "controller-source.bin").write_bytes(source)
            remote = bytes(range(250, -1, -1)) * 900
            (folder / "host-source.bin").write_bytes(remote)
            listener = socket.socket(); listener.bind(("127.0.0.1", 0)); port = listener.getsockname()[1]; listener.close()
            authority=folder/'xauthority'; authority.write_bytes(b'')
            environment = dict(os.environ, LOCALAPPDATA=str(folder / "state"), XAUTHORITY=str(authority))
            host_process = subprocess.Popen([sys.executable, "-c", HOST_SCRIPT, str(PROJECT), str(GUI_DEPS), str(folder), str(port), 'record' if args.record_input else 'native'],
                env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            def command(value):
                host_process.stdin.write(json.dumps(value) + "\n"); host_process.stdin.flush()
            def host_status():
                try: return json.loads((folder / "host-status.json").read_text())
                except (OSError, ValueError): return {}
            def wait(condition, message, seconds=8):
                deadline = time.monotonic() + seconds
                while not condition():
                    root.update()
                    if host_process.poll() is not None:
                        raise AssertionError("Host exited: " + host_process.stderr.read())
                    if time.monotonic() > deadline:
                        raise AssertionError(message + "; controller=" + app.connection_var.get() + "; host=" + str(host_status()) + "; errors=" + str(errors))
                    time.sleep(.01)
                root.update()
            wait(lambda: host_status().get("ready"), "Host listener must start")
            app._use_lan()
            app.address_var.set("127.0.0.1"); app.port_var.set(str(port)); app.password_var.set("native-smoke-password")
            app.connect()
            wait(lambda: bool(app.peer and app.current_image), "Native controller must receive an actual host screen", 12)
            assert any("Conferir certificado" in title and default == "no" for title, _, default in dialogs)
            assert host_status().get("connected")
            bounds = app.image_bounds
            assert bounds[0] >= 0 and bounds[1] >= 0
            assert bounds[0] + bounds[2] <= app.canvas.winfo_width()
            assert bounds[1] + bounds[3] <= app.canvas.winfo_height()
            assert app.current_screen["width"] == 1920 and app.current_screen["height"] == 1080
            assert app.monitor_ids == [0, 1]
            print("PASS: actual Host + Controller Tk windows connect over pinned TLS; whole desktop fits")
            app.monitor_combo.current(1); app._choose_monitor()
            wait(lambda: app.current_screen is not None and app.current_screen["monitorId"] == "1", "Individual monitor must render")
            app.monitor_combo.current(0); app._choose_monitor()
            wait(lambda: app.current_screen is not None and app.current_screen["monitorId"] == "all", "All monitors must render again")
            print("PASS: monitor selection switches individual and all desktop")
            app._request_sharing(False)
            wait(lambda: not app.sharing and app.current_image is None, "Remote pause must clear screen")
            app._request_sharing(True)
            wait(lambda: app.sharing and app.current_image is not None, "Remote resume must restore frame")
            command("pause")
            wait(lambda: app.local_paused and app.current_image is None, "Owner pause must remove stale frame")
            assert str(app.resume_button["state"]) == "disabled"
            command("pause")
            wait(lambda: not app.local_paused and app.current_image is not None, "Owner resume must restore frame")
            print("PASS: remote pause/resume and owner pause/resume preserve visible control")
            app.canvas.focus_force(); root.update()
            app._key_down(type("Event", (), {"keysym": "Control_L", "char": "", "keycode": 37})())
            wait(lambda: "ctrl" in host_status().get("keys", []), "Host must receive held Ctrl")
            app._release_input()
            wait(lambda: not host_status().get("keys"), "Blur-style release must clear host modifier")
            print("PASS: actual host input receives Ctrl and release clears held state")
            app._send_file()
            wait(lambda: (folder / "host-received.bin").exists(), "Controller upload must commit", 15)
            assert (folder / "host-received.bin").read_bytes() == source
            wait(lambda: not app.file_active, "Upload must complete")
            app._receive_file()
            wait(lambda: (folder / "controller-received.bin").exists(), "Controller request/download must commit", 15)
            assert (folder / "controller-received.bin").read_bytes() == remote
            wait(lambda: not app.file_active, "Download must complete")
            print("PASS: native file dialogs + bidirectional transfer preserve exact file bytes")
            command("deny")
            app._send_file()
            wait(lambda: not app.file_active and "recus" in app.file_var.get().lower(), "Owner denial must release transfer controls", 10)
            print("PASS: owner file denial completes without modifying destination")
            app._toggle_fullscreen(True); root.update(); assert app.fullscreen
            app._escape(); root.update(); assert not app.fullscreen
            wait(lambda: app.current_image is not None, 'A live frame must return after owner dialogs')
            root.geometry('1024x720+0+0'); root.update()
            wait(lambda: app.image_bounds[0]+app.image_bounds[2] <= app.canvas.winfo_width() and app.image_bounds[1]+app.image_bounds[3] <= app.canvas.winfo_height(), 'Resize must fit both dimensions')
            smaller_image_width = app.image_bounds[2]
            root.geometry('1180x800+0+0'); root.update()
            wait(lambda: app.current_image is not None and app.image_bounds[2] > smaller_image_width
                 and app.image_bounds[0]+app.image_bounds[2] <= app.canvas.winfo_width()
                 and app.image_bounds[1]+app.image_bounds[3] <= app.canvas.winfo_height(),
                 'A larger window must enlarge the live frame while fitting both dimensions')
            destination = Path(os.environ.get("MINIANYDESK_GUI_OUTPUT", str(PROJECT.parent)))
            destination.mkdir(parents=True, exist_ok=True)
            screenshot = destination / "native-controller-smoke.png"
            with mss.MSS(display=display) as capture:
                shot = capture.grab({"left": root.winfo_rootx(), "top": root.winfo_rooty(), "width": root.winfo_width(), "height": root.winfo_height()})
                Image.frombytes("RGB", shot.size, shot.rgb).save(screenshot)
            print("PASS: local fullscreen Escape exits; screenshot=" + str(screenshot))
            app.disconnect(); wait(lambda: not host_status().get("connected"), "Disconnect must close host peer")
            settings.data["pins"][pin_key("127.0.0.1", port)] = "0" * 64
            assert app.password_var.get() == "", "Disconnect must clear the session password"
            app.workspace.select(app.connect_page); root.update()
            app.password_var.set("native-smoke-password")
            app.connect_button.invoke(); wait(lambda: bool(app.peer and app.current_image), "Reconnect with changed pin must receive screen", 12)
            changed = [d for d in dialogs if "mudou" in d[0]]
            assert changed and "ANTERIOR:" in changed[-1][1] and "NOVO:" in changed[-1][1] and changed[-1][2] == "no"
            assert "password" not in json.loads(settings.path.read_text())
            assert not errors, errors
            print("PASS: changed fingerprint shows old/new and cancellation default; settings omit password; zero Tk errors")
            app.close(); root = None
            command("quit"); host_process.wait(timeout=4)
    finally:
        if root is not None:
            try: root.destroy()
            except Exception: pass
        if host_process and host_process.poll() is None:
            host_process.kill(); host_process.wait()
        if xvfb.poll() is None:
            xvfb.terminate(); xvfb.wait(timeout=3)


if __name__ == "__main__":
    main()
