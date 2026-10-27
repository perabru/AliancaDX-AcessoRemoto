"""Signing-build argument validation and failure propagation, without Windows.

The compiler is replaced by a recorder. These tests validate the build
orchestration only; they do not certify a PE signature or execute an installer.
"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "signing_builder_under_test", Path(__file__).resolve().parents[1] / "installer/build_installers.py")
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class SigningBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "source"
        self.assets = self.root / "assets"
        self.output = self.root / "isolated output"
        (self.project / "installer").mkdir(parents=True)
        (self.assets / "wheelhouse").mkdir(parents=True)
        (self.project / "controller.py").write_text("# source-only test fixture\n")
        for name in ("README.txt", "installer/MiniAnyDesk.nsi", "installer/NOTICE.txt",
                     "installer/bootstrap.cmd", "installer/build_installers.py",
                     "installer/easytier_bundle_manifest.json", "installer/REBUILD.txt",
                     "installer/collect_easytier_licenses.py", "installer/repack_easytier_sources.py",
                     "assets/brand/aliancadx-header.bmp", "assets/brand/aliancadx-wizard.bmp"):
            (self.project / name).parent.mkdir(parents=True, exist_ok=True)
            (self.project / name).write_text("build fixture\n")
        python = b"pinned Python asset test fixture"
        wheel = b"pinned wheel test fixture"
        (self.assets / "python.exe").write_bytes(python)
        (self.assets / "wheelhouse/dependency.whl").write_bytes(wheel)
        manifest = {"python": {"file": "python.exe", "sha256": hashlib.sha256(python).hexdigest()},
                    "wheels": [{"file": "dependency.whl", "sha256": hashlib.sha256(wheel).hexdigest()}]}
        (self.project / "installer/assets_manifest.json").write_text(json.dumps(manifest))
        browser_entries = []
        for name in ("index.html", "bridge.html", "manifest.webmanifest", "sw.js"):
            data = (name + " browser fixture").encode()
            path = self.project / "ios-pwa" / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(data)
            browser_entries.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        (self.project / "ios_assets_manifest.json").write_text(json.dumps({"schema": 1, "version": "7.3.0", "files": browser_entries}))
        self.argv = ["build_installers.py", "--assets", str(self.assets), "--reuse-assets",
                     "--output-dir", str(self.output)]

    def run_builder(self, extra=(), compiler=None) -> None:
        with patch.object(sys, "argv", self.argv + list(extra)), \
                patch.object(builder, "PROJECT", self.project), \
                patch.object(builder, "PAYLOAD", ("controller.py",)), \
                patch.object(builder, "validate_easytier_bundle", return_value={}), \
                patch.object(builder, "call", side_effect=compiler) as call, \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.compiler = call
            builder.main()

    def test_require_signing_fails_before_compiler_when_one_hook_is_missing(self) -> None:
        with self.assertRaises(SystemExit) as error:
            self.run_builder(("--require-signing", "--installer-sign-command", 'sign "%1"'))
        self.assertEqual(error.exception.code, 2)
        self.compiler.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_invalid_signing_commands_never_reach_compiler(self) -> None:
        for command in ("sign without-placeholder", 'sign "%1"\nother', 'sign `%1`', 'sign %1\x00'):
            with self.subTest(command=command), self.assertRaises(SystemExit) as error:
                self.run_builder(("--uninstaller-sign-command", command))
            self.assertEqual(error.exception.code, 2)
            self.compiler.assert_not_called()

    def test_signing_hooks_preserve_quoted_path_and_write_only_to_selected_output(self) -> None:
        hook = 'powershell.exe -NoProfile -NonInteractive -File "C:\\Publisher Tools\\sign_file.ps1" -Path "%1"'

        def compile_fixture(*args):
            role = next(value.split("=", 1)[1] for value in args if value.startswith("-DROLE="))
            label = "Host" if role == "host" else "Controlador"
            (self.output / f"AliancaDX_{label}_Setup.exe").write_bytes(b"unexecuted compiler fixture")

        self.run_builder(("--require-signing", "--uninstaller-sign-command", hook,
                          "--installer-sign-command", hook), compile_fixture)
        self.assertEqual(self.compiler.call_count, 2)
        for call in self.compiler.call_args_list:
            self.assertIn("-DUNINSTALL_SIGN_COMMAND=" + hook, call.args)
            self.assertIn("-DINSTALLER_SIGN_COMMAND=" + hook, call.args)
            self.assertIn("-DOUTPUT_ROOT=" + str(self.output), call.args)
        self.assertFalse((self.project / "dist").exists())

    def test_failed_compiler_or_signer_stops_before_second_installer(self) -> None:
        def failed(*args):
            raise subprocess.CalledProcessError(1, args)

        with self.assertRaises(subprocess.CalledProcessError):
            self.run_builder((), failed)
        self.assertEqual(self.compiler.call_count, 1)
        self.assertFalse(list(self.output.glob("*.exe")))

    def test_signing_script_with_spaces_constructs_both_quoted_hooks_in_python(self) -> None:
        script = self.root / "Publisher Tools" / "sign_file.ps1"
        script.parent.mkdir()
        script.write_text("# Signing helper fixture; never executed.\n")

        def compile_fixture(*args):
            role = next(value.split("=", 1)[1] for value in args if value.startswith("-DROLE="))
            label = "Host" if role == "host" else "Controlador"
            (self.output / f"AliancaDX_{label}_Setup.exe").write_bytes(b"unexecuted compiler fixture")

        self.run_builder(("--signing-script", str(script), "--require-signing"), compile_fixture)
        expected = ('powershell.exe -NoProfile -NonInteractive -File "' + str(script.resolve())
                    + '" -Path "%1"')
        self.assertEqual(self.compiler.call_count, 2)
        for call in self.compiler.call_args_list:
            self.assertIn("-DUNINSTALL_SIGN_COMMAND=" + expected, call.args)
            self.assertIn("-DINSTALLER_SIGN_COMMAND=" + expected, call.args)


if __name__ == "__main__":
    unittest.main()
