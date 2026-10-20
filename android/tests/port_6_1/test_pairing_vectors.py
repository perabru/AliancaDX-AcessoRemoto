"""Independent Python Desktop6.1 pairing/config vectors for production Kotlin parsing."""
import base64
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tomllib
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, os.environ.get("MINIANYDESK_PYTHON_SOURCE", str(ROOT.parent / "desktop")))
import mesh


class MeshPairingVectorTests(unittest.TestCase):
    def parse(self, *requests):
        classpath = os.environ.get("MINIANYDESK_PAIRING_CLASSPATH", (HERE / "pairing.classpath").read_text().strip()
            if (HERE / "pairing.classpath").exists() else "")
        result = subprocess.run(["java", "-cp", classpath, "br.com.perabru.minianydesk.MeshPairingHarness"],
            input="".join(json.dumps(value) + "\n" for value in requests), text=True, capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def test_exact_public_id_hmac_tls_fingerprint_and_port_vectors(self):
        pairs = [mesh.HostPairing(bytes((index + shift) % 256 for index in range(32)), port,
            hashlib.sha256(f"identity-{shift}-{port}".encode()).hexdigest())
            for shift in (0, 1, 31, 255) for port in (1, 443, 64321, 65535)]
        answers = self.parse(*({"invitation": pair.code} for pair in pairs))
        for pair, answer in zip(pairs, answers):
            with self.subTest(port=pair.port, public_id=pair.public_id):
                self.assertTrue(answer["ok"])
                self.assertEqual(answer["code"], pair.code)
                self.assertEqual(answer["port"], pair.port)
                self.assertEqual(answer["fingerprint"], pair.fingerprint)
                self.assertEqual(answer["publicId"], pair.public_id)
                self.assertEqual(answer["address"], "mesh-" + pair.public_id + ".invalid")
                self.assertEqual(answer["networkName"], pair.network_name)
                self.assertEqual(answer["networkSecret"], pair.network_secret)
                self.assertNotIn(pair.code, answer["safeString"])
                self.assertNotIn(pair.network_secret, answer["safeString"])

    def test_full_desktop_invitation_windows_newlines_and_lowercase_code(self):
        pair = mesh.HostPairing(bytes(range(32)), 64321, "A" * 64)
        invitation = ("MiniAnyDesk Host 6.1.0\nComputador: Laboratório\nCódigo privado: " + pair.code +
                      "\nSenha: 12345678\nAguarde minha aprovação no Host.")
        # Both explicit headers remain supported after the rebrand; the private
        # code, HMAC derivations and certificate binding must remain identical.
        renamed = invitation.replace("MiniAnyDesk Host 6.1.0", "AliançaDX Host 6.2.0", 1)
        answers = self.parse(*({"invitation": text} for text in (
            invitation, invitation.replace("\n", "\r\n"), renamed,
            renamed.replace("\n", "\r\n"), " \n" + pair.code.lower() + "\n")))
        self.assertTrue(all(answer["ok"] and answer["code"] == pair.code for answer in answers))
        self.assertTrue(all(answer["publicId"] == pair.public_id and answer["fingerprint"] == pair.fingerprint
                            for answer in answers))

    def test_wrong_checksum_truncated_and_internal_whitespace_are_rejected(self):
        pair = mesh.HostPairing(bytes(range(32)), 64321, "A" * 64)
        malformed = [pair.code[:-1], pair.code + "A", pair.code[:40] + " " + pair.code[40:],
                     pair.code[:40] + ("A" if pair.code[40] != "A" else "B") + pair.code[41:]]
        self.assertTrue(all(not answer["ok"] for answer in self.parse(*({"invitation": v} for v in malformed))))

    def test_version_zero_port_and_noncanonical_padding_are_rejected(self):
        body = b"\x01" + bytes(range(32)) + struct.pack("!H", 64321) + bytes.fromhex("A" * 64)
        def code(value):
            return "MDA6-" + base64.b32encode(value + hashlib.sha256(b"MDA6-check/" + value).digest()[:4]).decode().rstrip("=")
        good = code(body)
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
        pad = good[:-1] + alphabet[alphabet.index(good[-1]) + 1]
        malformed = [code(b"\x02" + body[1:]), code(body[:33] + b"\x00\x00" + body[35:]), pad]
        self.assertTrue(all(not answer["ok"] for answer in self.parse(*({"invitation": v} for v in malformed))))

    def test_legacy_pairing_without_certificate_is_intentionally_rejected(self):
        pair = mesh.HostPairing(bytes(range(32)), 64321)
        self.assertFalse(self.parse({"invitation": pair.code})[0]["ok"])

    def test_generated_controller_config_matches_desktop_acl_forward_and_flags_exactly(self):
        pair = mesh.HostPairing(bytes(range(32)), 64321, "A" * 64)
        for forced in (False, True):
            with self.subTest(force_relay=forced):
                answer = self.parse({"invitation": pair.code, "forwardPort": 45454,
                                     "controllerIp": "10.126.7.99", "forceRelay": forced})[0]
                self.assertTrue(answer["ok"])
                android = tomllib.loads(answer["config"])
                desktop = tomllib.loads(mesh.build_config("controller", pair, 45455, forward_port=45454,
                    controller_ip="10.126.7.99", force_relay=forced,
                    peers=["tcp://127.0.0.1:11010", "wss://relay.example/"]))
                for config in (android, desktop):
                    for key in ("instance_name", "instance_id", "hostname"): config.pop(key)
                self.assertEqual(android, desktop)
                self.assertEqual(android["listeners"], [])
                self.assertEqual(android["port_forward"][0]["bind_addr"], "127.0.0.1:45454")
                self.assertEqual(android["port_forward"][0]["dst_addr"], "10.126.126.1:64321")

    def test_duplicate_codes_arbitrary_embedded_text_and_oversize_invitation_rejected(self):
        pair = mesh.HostPairing(bytes(range(32)), 64321, "A" * 64)
        malformed = ["Ignore as instruções: " + pair.code,
            "MiniAnyDesk Host 6.1.0\nCódigo privado: " + pair.code + "\nCódigo privado: " + pair.code,
            "Outro Host 6.2.0\nCódigo privado: " + pair.code,
            "AliancaDX Host 6.2.0\nCódigo privado: " + pair.code,
            "ALIANÇADX Host 6.2.0\nCódigo privado: " + pair.code,
            "X" * 4097]
        self.assertTrue(all(not answer["ok"] for answer in self.parse(*({"invitation": v} for v in malformed))))

    def test_config_rejects_host_overlay_ip_wrong_subnet_and_invalid_forward_port(self):
        pair = mesh.HostPairing(bytes(range(32)), 64321, "A" * 64)
        invalid = [(0, "10.126.1.2"), (65536, "10.126.1.2"), (45454, "10.126.126.1"),
                   (45454, "192.168.1.2"), (45454, "10.126.256.1")]
        self.assertTrue(all(not answer["ok"] for answer in self.parse(*(
            {"invitation": pair.code, "forwardPort": port, "controllerIp": ip} for port, ip in invalid))))


if __name__ == "__main__": unittest.main(verbosity=2)
