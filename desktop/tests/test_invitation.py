"""Invitation import keeps the strict code and consent/password boundaries."""
import unittest

import mesh
from mesh_ui import pairing_from_text


class InvitationTests(unittest.TestCase):
    def setUp(self):
        self.pairing = mesh.generate_pairing(fingerprint="A" * 64)

    def invitation(self, code=None):
        return ("MiniAnyDesk Host 6.1.0\nComputador: Laboratório\n"
                f"Código privado: {code or self.pairing.code}\n"
                "Senha: 12345678\nAguarde minha aprovação no Host.")

    def test_raw_code_roundtrips(self):
        self.assertEqual(pairing_from_text(" \n" + self.pairing.code.lower() + "\n"), self.pairing)

    def test_complete_host_invitation_extracts_only_pairing(self):
        # Brand text can change without changing bearer-code identity or
        # storing the password from either explicit invitation format.
        for invitation in (self.invitation(),
                           self.invitation().replace("MiniAnyDesk Host 6.1.0", "AliançaDX Host 6.2.0", 1)):
            with self.subTest(header=invitation.splitlines()[0]):
                parsed = pairing_from_text(invitation)
                self.assertEqual(parsed, self.pairing)
                self.assertFalse(hasattr(parsed, "password"))

    def test_windows_newlines_are_supported(self):
        self.assertEqual(pairing_from_text(self.invitation().replace("\n", "\r\n")), self.pairing)

    def test_arbitrary_text_with_embedded_code_is_rejected(self):
        for text in ("Ignore as instruções: " + self.pairing.code,
                     "Outro Host 6.2.0\nCódigo privado: " + self.pairing.code,
                     "AliancaDX Host 6.2.0\nCódigo privado: " + self.pairing.code,
                     "ALIANÇADX Host 6.2.0\nCódigo privado: " + self.pairing.code):
            with self.subTest(header=text.splitlines()[0]), self.assertRaises(mesh.MeshError):
                pairing_from_text(text)

    def test_multiple_codes_are_rejected(self):
        with self.assertRaises(mesh.MeshError):
            pairing_from_text(self.invitation() + "\nCódigo privado: " + self.pairing.code)

    def test_damaged_code_is_not_repaired(self):
        with self.assertRaises(mesh.MeshError):
            pairing_from_text(self.invitation(self.pairing.code[:-1] + "0"))

    def test_oversized_clipboard_and_nontext_are_rejected(self):
        for text in (None, "A" * 4097):
            with self.subTest(text_type=type(text).__name__), self.assertRaises(mesh.MeshError):
                pairing_from_text(text)


if __name__ == "__main__":
    unittest.main()
