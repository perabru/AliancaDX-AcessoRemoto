"""Host identity survives fresh processes; no password/code is printed by the fixture.

Real production certificate and pairing persistence run in private temporary dirs.
On Linux the existing user-file branch is exercised, not Windows DPAPI. On Windows
the same subprocess fixture uses the real current-user DPAPI implementation.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


DESKTOP = Path(__file__).resolve().parents[1]
PROCESS = r'''
import hashlib,json,sys
from pathlib import Path
import mesh,net
state=Path(sys.argv[1]);port=int(sys.argv[2]);mode=sys.argv[3]
try:
    if mode == "validate-server":
        server=net.HostServer("127.0.0.1",0,"synthetic-test-password",
            lambda *a:False,lambda *a:None,lambda *a:None,state)
        print(json.dumps({"ok":True}));sys.exit(0)
    key,cert,pin=net._certificate(state)
    pairing=(mesh.rotate_pairing if mode == "rotate" else mesh.load_or_create_pairing)(state/"mesh",port,pin)
    digest=lambda value:hashlib.sha256(value).hexdigest()
    print(json.dumps({"ok":True,"port":pairing.port,"public_id":pairing.public_id,
        "code_sha256":digest(pairing.code.encode()),"token_sha256":digest(pairing.token),
        "network_name":pairing.network_name,"network_secret_sha256":digest(pairing.network_secret.encode()),
        "fingerprint":pin,"key_sha256":digest(key.read_bytes()),"cert_sha256":digest(cert.read_bytes())}))
except Exception as error:
    print(json.dumps({"ok":False,"error_type":type(error).__name__}))
'''


class HostIdentityRestartTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aliancadx-host-restart-fixture-")
        self.state = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def child(self, mode="load", port=64321, state=None):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(DESKTOP)
        process = subprocess.run([sys.executable, "-c", PROCESS, str(state or self.state), str(port), mode],
                                 env=env, cwd=DESKTOP, capture_output=True, text=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)
        value = json.loads(process.stdout)
        # Only identity digests/public labels are emitted, never bearer codes or passwords.
        self.assertNotIn("MDA6-", process.stdout)
        self.assertNotIn("synthetic-test-password", process.stdout)
        return value

    def test_fresh_host_process_reuses_code_network_and_tls_identity(self):
        first = self.child(); second = self.child()
        self.assertTrue(first["ok"])
        self.assertEqual(first, second)
        if os.name != "nt":
            for name in ("host-key.pem", "mesh/host-pairing.json"):
                self.assertEqual((self.state / name).stat().st_mode & 0o077, 0)

    def test_changed_port_updates_invitation_but_preserves_network_and_certificate(self):
        first = self.child(); changed = self.child(port=64322)
        self.assertEqual(changed["port"], 64322)
        self.assertNotEqual(first["code_sha256"], changed["code_sha256"])
        for field in ("public_id", "token_sha256", "network_name", "network_secret_sha256", "fingerprint", "key_sha256"):
            self.assertEqual(first[field], changed[field], field)
        self.assertEqual(changed, self.child(port=64322))

    def test_explicit_revocation_rotates_network_identity_without_replacing_tls_files(self):
        first = self.child(); changed = self.child("rotate")
        for field in ("public_id", "token_sha256", "network_name", "network_secret_sha256", "code_sha256"):
            self.assertNotEqual(first[field], changed[field], field)
        for field in ("fingerprint", "key_sha256", "cert_sha256"):
            self.assertEqual(first[field], changed[field], field)
        self.assertEqual(changed, self.child())

    def test_corrupt_saved_host_code_is_preserved_and_never_regenerated(self):
        first = self.child()
        path = self.state / "mesh/host-pairing.json"
        invalid = b'{"schema":1,"broken":'
        path.write_bytes(invalid)
        failed = self.child()
        self.assertEqual(failed, {"ok": False, "error_type": "MeshError"})
        self.assertEqual(path.read_bytes(), invalid)
        # Certificate generation did not run again while loading the bad pairing.
        import hashlib
        self.assertEqual(hashlib.sha256((self.state / "host-key.pem").read_bytes()).hexdigest(), first["key_sha256"])

    def test_an_incomplete_tls_identity_fails_without_replacing_the_remaining_key(self):
        first = self.child()
        (self.state / "host-cert.pem").unlink()
        failed = self.child()
        self.assertEqual(failed, {"ok": False, "error_type": "RuntimeError"})
        self.assertFalse((self.state / "host-cert.pem").exists())
        import hashlib
        self.assertEqual(hashlib.sha256((self.state / "host-key.pem").read_bytes()).hexdigest(), first["key_sha256"])

    def test_loss_of_both_tls_files_changes_fingerprint_and_code_but_keeps_existing_network_token(self):
        first = self.child()
        (self.state / "host-key.pem").unlink(); (self.state / "host-cert.pem").unlink()
        changed = self.child()
        self.assertNotEqual(first["fingerprint"], changed["fingerprint"])
        self.assertNotEqual(first["code_sha256"], changed["code_sha256"])
        self.assertEqual(first["public_id"], changed["public_id"])
        self.assertEqual(first["network_secret_sha256"], changed["network_secret_sha256"])
        self.assertEqual(changed, self.child())

    def test_invalid_private_key_is_not_silently_replaced_when_starting_the_host(self):
        self.child()
        invalid = b"invalid synthetic fixture key"
        (self.state / "host-key.pem").write_bytes(invalid)
        failed = self.child("validate-server")
        self.assertFalse(failed["ok"])
        self.assertEqual((self.state / "host-key.pem").read_bytes(), invalid)

    def test_new_profile_without_existing_state_creates_a_distinct_host_identity(self):
        first = self.child()
        with tempfile.TemporaryDirectory(prefix="aliancadx-other-profile-fixture-") as directory:
            changed = self.child(state=Path(directory))
        for field in ("public_id", "token_sha256", "network_secret_sha256", "fingerprint", "code_sha256"):
            self.assertNotEqual(first[field], changed[field], field)


if __name__ == "__main__":
    unittest.main()
