"""Start the visible iPhone connector without putting credentials in argv."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

MAX_BOOTSTRAP = 16 * 1024


def launch_connector(bootstrap: dict | None = None):
    entry = Path(__file__).resolve().with_name("ios_connector.py")
    if not entry.is_file():
        raise RuntimeError("Atualize o AliançaDX para instalar o conector iPhone e iPad.")
    payload = None
    if bootstrap is not None:
        payload = (json.dumps(bootstrap, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(payload) > MAX_BOOTSTRAP:
            raise ValueError("Os dados locais do conector excedem o limite permitido.")
    argv = [sys.executable, str(entry)] + (["--bootstrap-stdin"] if payload is not None else [])
    process = subprocess.Popen(argv, cwd=entry.parent,
        stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
    if payload is not None:
        try:
            process.stdin.write(payload)
            process.stdin.close()
        except (OSError, BrokenPipeError):
            process.terminate()
            raise RuntimeError("Não foi possível abrir o conector iPhone e iPad.") from None
    return process
