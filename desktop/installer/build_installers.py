"""Rebuild the two AliançaDX Acesso Remoto 7.4.0 x64 installers with pinned offline dependencies."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

PROJECT = Path(__file__).resolve().parents[1]
_IOS_SPEC = importlib.util.spec_from_file_location("aliancadx_ios_assets", PROJECT / "ios_assets.py")
assert _IOS_SPEC is not None and _IOS_SPEC.loader is not None
_ios_assets = importlib.util.module_from_spec(_IOS_SPEC)
_IOS_SPEC.loader.exec_module(_ios_assets)
payload_path = _ios_assets.payload_path
SOURCE_WHEELS = {"pyautogui", "pygetwindow", "pyscreeze", "pytweening", "mouseinfo", "pyrect"}
BRAND_FILES = ("aliancadx-logo.png", "aliancadx-logo-original.jpg", "aliancadx-mark-256.png",
               "aliancadx-app-256.png", "aliancadx.ico")
PAYLOAD = ("controller.py", "host.py", "updates.py", "net.py", "connection_metrics.py", "adaptive_stream.py", "ios_connector.py",
           "ios_launcher.py", "ios_assets.py", "ios_assets_manifest.json", "transfer.py", "desktop_geometry.py", "desktop_ui.py",
           "computer_book.py", "presence.py", "network_addresses.py", "mesh.py", "mesh_ui.py",
           "installer_python.py", "requirements.txt", "COMECE_AQUI.html", "GUIA_RAPIDO.txt",
           "CONFIANCA_WINDOWS.txt", "Abrir_AliancaDX_Host.cmd", "Abrir_AliancaDX_Controlador.cmd",
           *("assets/brand/" + name for name in BRAND_FILES),
           *("ios-pwa/" + entry["path"] for entry in _ios_assets.browser_assets(PROJECT)))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 17), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_easytier_bundle(assets: Path, project: Path = PROJECT) -> dict[str, str]:
    """Reject any unpinned binary, license, source archive or extra bundled file."""
    source = project / "installer" / "easytier_bundle_manifest.json"
    bundle = assets / "easytier_bundle"
    if source.read_bytes() != (bundle / "manifest.json").read_bytes():
        raise RuntimeError("The network bundle manifest differs from its pinned source. Build cancelled.")
    manifest = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise RuntimeError("Invalid network bundle manifest. Build cancelled.")
    package = manifest.get("package", {})
    entries = manifest.get("files", [])
    if not isinstance(package, dict) or not isinstance(entries, list):
        raise RuntimeError("Invalid network bundle manifest. Build cancelled.")
    if (manifest.get("schema") != 1 or package.get("type") != "minianydesk-easytier-userspace"
            or package.get("version") != "2.6.4" or package.get("architecture") != "amd64"):
        raise RuntimeError("Unsupported network bundle. Build cancelled.")
    hashes = {"manifest.json": sha256_file(source)}
    for item in entries:
        if not isinstance(item, dict):
            raise RuntimeError("Invalid network bundle entry. Build cancelled.")
        name, size, digest = item.get("filename"), item.get("size"), item.get("sha256")
        if (not isinstance(name, str) or not name or "/" in name or "\\" in name
                or name in (".", "..", "manifest.json") or name in hashes
                or not isinstance(size, int) or isinstance(size, bool) or size <= 0
                or not isinstance(digest, str) or len(digest) != 64
                or any(ch not in "0123456789abcdef" for ch in digest)):
            raise RuntimeError("Invalid network bundle entry. Build cancelled.")
        path = bundle / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != size or sha256_file(path) != digest:
            raise RuntimeError(f"Network bundle integrity failure: {name}")
        hashes[name] = digest
    actual = {path.name for path in bundle.iterdir()}
    required = {"easytier-core.exe", "Packet.dll", "NOTICE.txt", "LICENSE.txt",
                "THIRD_PARTY_LICENSES.txt", "PROVENANCE.json"}
    if not required.issubset(hashes):
        raise RuntimeError("The network bundle is missing its executables, licenses or provenance. Build cancelled.")
    if actual != set(hashes):
        raise RuntimeError("Unlisted or missing files in the network bundle. Build cancelled.")
    return {"easytier_bundle/" + name: digest for name, digest in hashes.items()}


def write_ios_include(project: Path, destination: Path) -> Path:
    """Generate exact-file NSIS operations; never recursively remove user files."""
    assets = _ios_assets.verify_browser_assets(project)
    directories = sorted({str(Path(name).parent).replace("\\", "/") for name in assets},
                         key=lambda value: (value.count("/"), value))
    for name in tuple(directories):
        for parent in Path(name).parents:
            if str(parent) != "." and str(parent).replace("\\", "/") not in directories:
                directories.append(str(parent).replace("\\", "/"))
    directories.sort(key=lambda value: (value.count("/"), value))
    lines = ["; Generated from the exact source-pinned iPhone asset manifest."]
    lines.append("!macro BackupIOSBrowser")
    for directory in directories:
        lines.append('  CreateDirectory "$BackupFolder\\' + directory.replace("/", "\\") + '"')
    for name in assets:
        windows = name.replace("/", "\\")
        parent = str(Path(name).parent).replace("/", "\\")
        lines.extend(['  IfFileExists "$INSTDIR\\' + windows + '" 0 +2',
                      '  CopyFiles /SILENT "$INSTDIR\\' + windows + '" "$BackupFolder\\' + parent + '"'])
    lines.extend(["!macroend", "!macro RestoreIOSBrowser"])
    for name in assets:
        windows = name.replace("/", "\\")
        parent = str(Path(name).parent).replace("/", "\\")
        lines.extend(['  ${If} ${FileExists} "$BackupFolder\\' + windows + '"',
                      '    CreateDirectory "$INSTDIR\\' + parent + '"',
                      '    CopyFiles /SILENT "$BackupFolder\\' + windows + '" "$INSTDIR\\' + parent + '"',
                      '  ${Else}', '    Delete "$INSTDIR\\' + windows + '"', '  ${EndIf}'])
    # Leaving an empty new directory after a failed update is harmless. Trying
    # to remove folders containing restored files would set NSIS Errors and
    # incorrectly report a successful rollback as a failure.
    lines.extend(["!macroend", "!macro InstallIOSBrowser"])
    for name in assets:
        parent = str(Path(name).parent).replace("/", "\\")
        origin = str(payload_path(project, name)).replace("$", "$$")
        if '"' in origin or "\n" in origin or "\r" in origin:
            raise RuntimeError("Unsupported NSIS source path")
        lines.extend(['  SetOutPath "$INSTDIR\\' + parent + '"', '  File "' + origin + '"'])
    lines.extend(["!macroend", "!macro RemoveIOSBrowser"])
    for name in assets:
        lines.append('  Delete "$INSTDIR\\' + name.replace("/", "\\") + '"')
    for directory in reversed(directories):
        lines.append('  RMDir "$INSTDIR\\' + directory.replace("/", "\\") + '"')
    lines.extend(["!macroend", ""])
    destination.write_text("\n".join(lines), encoding="utf-8")
    return destination


def call(*arguments: str) -> None:
    subprocess.run(arguments, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True, help="temporary directory outside the project")
    parser.add_argument("--reuse-assets", action="store_true", help="skip downloads and reuse validated build assets")
    parser.add_argument("--output-dir", type=Path, default=PROJECT / "dist",
                        help="installer output directory; use a local isolated directory for release builds")
    parser.add_argument("--makensis", default="makensis", help="NSIS compiler executable")
    parser.add_argument("--signing-script", type=Path,
                        help="existing PowerShell signing helper; used for both generated EXEs")
    parser.add_argument("--uninstaller-sign-command",
                        help="publisher signing command, with %%1 for the generated uninstaller path")
    parser.add_argument("--installer-sign-command",
                        help="publisher signing command, with %%1 for the generated installer path")
    parser.add_argument("--require-signing", action="store_true",
                        help="reject a build unless both signing commands are provided")
    args = parser.parse_args()
    if args.signing_script is not None:
        if args.uninstaller_sign_command is not None or args.installer_sign_command is not None:
            parser.error("--signing-script cannot be combined with raw signing commands")
        raw_path = str(args.signing_script)
        if any(ch in raw_path for ch in "\"'`$%\x00\r\n"):
            parser.error("signing script path cannot contain quotes, backticks, dollars, percent signs or control characters")
        try:
            signing_script = args.signing_script.resolve(strict=True)
        except (OSError, ValueError) as error:
            parser.error("signing script does not exist: " + str(error))
        if not signing_script.is_file():
            parser.error("signing script must be an existing file")
        if any(ch in str(signing_script) for ch in "\"'`$%\x00\r\n"):
            parser.error("resolved signing script path contains unsupported NSIS metacharacters")
        # Construct the nested quotes in Python instead of passing them through
        # PowerShell 5.1's native-command argument serialization.
        hook = ('powershell.exe -NoProfile -NonInteractive -File "' + str(signing_script)
                + '" -Path "%1"')
        args.uninstaller_sign_command = args.installer_sign_command = hook
    signing = (("UNINSTALL_SIGN_COMMAND", args.uninstaller_sign_command),
               ("INSTALLER_SIGN_COMMAND", args.installer_sign_command))
    if args.require_signing and not all(command for _, command in signing):
        parser.error("--require-signing needs both installer and uninstaller signing commands")
    signing_defines = []
    for name, command in signing:
        if command is None:
            continue
        # The command is operator-supplied build configuration, never runtime
        # input. Backticks delimit the NSIS command; reject delimiter injection.
        if not command.strip() or "%1" not in command or any(ch in command for ch in "\x00\r\n`"):
            parser.error("signing commands must contain %1 and no newlines, NUL or backticks")
        signing_defines.append(f"-D{name}={command}")
    output_dir = args.output_dir.resolve()
    for name in PAYLOAD:
        path = payload_path(PROJECT, name)
        if not path.is_file():
            raise RuntimeError(f"Missing application payload: {name}. Build cancelled.")
        if path.suffix == ".py":
            compile(path.read_bytes(), name, "exec")
    _ios_assets.verify_browser_assets(PROJECT)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((PROJECT / "installer" / "assets_manifest.json").read_text())
    assets = args.assets.resolve()
    wheelhouse = assets / "wheelhouse"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    python_info = manifest["python"]
    python_installer = assets / python_info["file"]
    if not args.reuse_assets:
        if not python_installer.exists():
            urllib.request.urlretrieve(python_info["url"], python_installer)
        versions = {w["name"].lower(): w["version"] for w in manifest["wheels"]}
        binary = [f"{name}=={version}" for name, version in versions.items() if name not in SOURCE_WHEELS]
        for version, abi in (("3.11", "cp311"), ("3.12", "cp312")):
            call(sys.executable, "-m", "pip", "download", "--dest", str(wheelhouse),
                 "--platform", "win_amd64", "--python-version", version,
                 "--implementation", "cp", "--abi", abi, "--only-binary=:all:", "--no-deps", *binary)
        call(sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheelhouse),
             *(f"{name}=={versions[name]}" for name in sorted(SOURCE_WHEELS)))
    if hashlib.sha256(python_installer.read_bytes()).hexdigest() != python_info["sha256"]:
        raise RuntimeError("The official Python installer hash does not match. Build cancelled.")
    for wheel in manifest["wheels"]:
        path = wheelhouse / wheel["file"]
        if not path.exists():
            raise RuntimeError(f"Missing bundled wheel: {path.name}")
        # Pure-source builds can have different ZIP timestamps. Reuse mode is
        # strictly checked against the precise shipped artifact manifest.
        if args.reuse_assets and hashlib.sha256(path.read_bytes()).hexdigest() != wheel["sha256"]:
            raise RuntimeError(f"Bundled wheel hash mismatch: {path.name}")
    expected_wheels = {wheel["file"] for wheel in manifest["wheels"]}
    unexpected_wheels = {path.name for path in wheelhouse.glob("*.whl")} - expected_wheels
    if unexpected_wheels:
        raise RuntimeError("Unlisted wheels in offline assets: " + ", ".join(sorted(unexpected_wheels)))
    network_hashes = validate_easytier_bundle(assets)
    inputs = (*PAYLOAD, "README.txt", "installer/MiniAnyDesk.nsi", "installer/NOTICE.txt",
              "installer/bootstrap.cmd", "installer/assets_manifest.json", "installer/build_installers.py",
              "installer/easytier_bundle_manifest.json", "installer/REBUILD.txt",
              "installer/collect_easytier_licenses.py", "installer/repack_easytier_sources.py",
              "assets/brand/aliancadx-header.bmp", "assets/brand/aliancadx-wizard.bmp")
    inputs += tuple(name for name in ("installer/sign_file.ps1", "installer/sign_release.ps1",
                                     "installer/verify_windows.ps1")
                    if (PROJECT / name).is_file())
    source_hashes = {name: sha256_file(payload_path(PROJECT, name)) for name in inputs}
    ios_include = write_ios_include(PROJECT, output_dir / "ios-browser-payload.nsh")
    for role, label in (("controller", "Controlador"), ("host", "Host")):
        call(args.makensis, "-V3", f"-DPROJECT_ROOT={PROJECT}", f"-DASSET_ROOT={assets}",
             f"-DOUTPUT_ROOT={output_dir}", f"-DROLE={role}", f"-DIOS_PAYLOAD_INCLUDE={ios_include}", *signing_defines,
             str(PROJECT / "installer" / "MiniAnyDesk.nsi"))
        if any(sha256_file(payload_path(PROJECT, name)) != digest
               for name, digest in source_hashes.items()):
            raise RuntimeError("Application source changed during packaging. Rebuild both installers.")
        if validate_easytier_bundle(assets) != network_hashes:
            raise RuntimeError("Network bundle changed during packaging. Rebuild both installers.")
        output = output_dir / f"AliancaDX_{label}_Setup.exe"
        print(output.name, "SHA256:", hashlib.sha256(output.read_bytes()).hexdigest())
    ios_include.unlink()
    if not signing_defines:
        print("Unsigned build: no Authenticode signing command was configured.")


if __name__ == "__main__":
    main()
