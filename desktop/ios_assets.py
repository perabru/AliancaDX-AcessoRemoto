"""Exact, source-pinned browser files shared by the Windows connector and PWA."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re


def browser_assets(project: Path | None = None) -> tuple[dict, ...]:
    project = Path(project) if project is not None else Path(__file__).resolve().parent
    manifest = json.loads((project / "ios_assets_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != 1 or manifest.get("version") != "7.6.1" or not isinstance(manifest.get("files"), list):
        raise RuntimeError("Manifesto dos arquivos do iPhone incompatível.")
    entries, names = [], set()
    for entry in manifest["files"]:
        name = entry.get("path")
        if (not isinstance(name, str) or not name or name in names or len(name) > 220
                or len(PurePosixPath(name).parts) > 8
                or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) or part in (".", "..")
                       for part in name.split("/"))
                or not isinstance(entry.get("bytes"), int) or isinstance(entry.get("bytes"), bool)
                or entry["bytes"] < 1
                or not isinstance(entry.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
            raise RuntimeError("Arquivo do iPhone inválido no manifesto.")
        names.add(name)
        entries.append(dict(entry))
    return tuple(entries)


def payload_path(project: Path, name: str) -> Path:
    """Resolve the same payload key in a public checkout or an installed copy."""
    project = Path(project)
    if not name.startswith("ios-pwa/"):
        return project / name
    relative = PurePosixPath(name.removeprefix("ios-pwa/"))
    if relative.is_absolute() or ".." in relative.parts or "\\" in name:
        raise RuntimeError("Caminho do iPhone inválido.")
    browser = project / "ios-pwa"
    if not browser.is_dir():
        browser = project.parent / "ios-pwa"
    path = browser.joinpath(*relative.parts)
    for part in (browser, *[browser.joinpath(*relative.parts[:index]) for index in range(1, len(relative.parts) + 1)]):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise RuntimeError("Os arquivos do iPhone não podem usar links.")
    return path


def verify_browser_assets(project: Path | None = None) -> tuple[str, ...]:
    project = Path(project) if project is not None else Path(__file__).resolve().parent
    entries = browser_assets(project)
    required = {"index.html", "bridge.html", "manifest.webmanifest", "sw.js"}
    if not required.issubset({entry["path"] for entry in entries}):
        raise RuntimeError("Os arquivos do aplicativo iPhone estão incompletos.")
    for entry in entries:
        path = payload_path(project, "ios-pwa/" + entry["path"])
        if (not path.is_file() or path.stat().st_size != entry["bytes"]
                or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]):
            raise RuntimeError("Arquivo do iPhone ausente ou alterado: " + entry["path"])
    return tuple("ios-pwa/" + entry["path"] for entry in entries)
