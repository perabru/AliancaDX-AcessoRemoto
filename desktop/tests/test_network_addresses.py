"""Headless regressions for address selection and connection diagnostics."""
import errno
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import network_addresses as addresses


def records(*values):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (value, 0)) for value in values]


class LocalAddressTests(unittest.TestCase):
    def discover(self, source, values):
        probe = Mock()
        probe.__enter__ = Mock(return_value=probe)
        probe.__exit__ = Mock(return_value=False)
        if isinstance(source, BaseException):
            probe.connect.side_effect = source
        else:
            probe.getsockname.return_value = (source, 12345)
        with patch.object(addresses.socket, "socket", return_value=probe), \
                patch.object(addresses.socket, "gethostname", return_value="host"), \
                patch.object(addresses.socket, "getaddrinfo", return_value=records(*values)) as lookup:
            result = addresses.local_addresses()
        probe.connect.assert_called_once_with(("192.0.2.1", 9))
        probe.send.assert_not_called()
        probe.sendto.assert_not_called()
        lookup.assert_called_once_with("host", None, socket.AF_INET)
        return result

    def test_default_route_precedes_other_network_addresses_and_apipa_is_last(self):
        self.assertEqual(self.discover("192.168.15.100", [
            "169.254.223.179", "192.168.15.100", "10.0.0.3", "172.20.1.4",
        ]), ["192.168.15.100", "10.0.0.3", "172.20.1.4", "169.254.223.179"])

    def test_vpn_and_public_interface_are_preserved_and_sorted_numerically(self):
        self.assertEqual(self.discover("100.90.12.4", [
            "192.168.2.10", "192.168.2.2", "100.90.12.4", "8.8.8.8", "10.0.0.1",
        ]), ["100.90.12.4", "8.8.8.8", "10.0.0.1", "192.168.2.2", "192.168.2.10"])

    def test_link_local_default_route_never_displaces_a_network_address(self):
        self.assertEqual(self.discover("169.254.4.8", ["169.254.3.1", "10.1.2.3"]),
                         ["10.1.2.3", "169.254.4.8", "169.254.3.1"])

    def test_apipa_only_is_reported_for_direct_local_connections(self):
        self.assertEqual(self.discover(OSError(errno.ENETUNREACH, "offline"), ["169.254.223.179"]),
                         ["169.254.223.179"])

    def test_invalid_nonroutable_and_duplicate_records_do_not_become_targets(self):
        self.assertEqual(self.discover("0.0.0.0", [
            "127.0.0.1", "224.0.0.1", "0.0.0.0", "255.255.255.255", "bad", "::1",
            "10.0.0.3", "10.0.0.3",
        ]), ["10.0.0.3"])

    def test_hostname_resolution_failure_preserves_route_source(self):
        probe = Mock()
        probe.__enter__ = Mock(return_value=probe)
        probe.__exit__ = Mock(return_value=False)
        probe.getsockname.return_value = ("192.168.15.100", 12345)
        with patch.object(addresses.socket, "socket", return_value=probe), \
                patch.object(addresses.socket, "getaddrinfo", side_effect=socket.gaierror("DNS")):
            self.assertEqual(addresses.local_addresses(), ["192.168.15.100"])

    def test_no_interfaces_returns_empty_without_requiring_internet(self):
        with patch.object(addresses.socket, "socket", side_effect=OSError("offline")), \
                patch.object(addresses.socket, "getaddrinfo", side_effect=socket.gaierror("offline")):
            self.assertEqual(addresses.local_addresses(), [])


class TargetTests(unittest.TestCase):
    def test_ipv4_lan_vpn_public_and_apipa_are_valid_individual_targets(self):
        for value in ("192.168.15.100", "10.0.0.1", "172.20.1.2", "100.90.0.4", "8.8.8.8", "169.254.1.2", "127.0.0.1"):
            with self.subTest(value=value):
                self.assertEqual(addresses.normalize_target("  " + value + "  "), value)

    def test_hostname_normalization_does_not_perform_dns(self):
        with patch.object(addresses.socket, "getaddrinfo") as lookup:
            self.assertEqual(addresses.normalize_target(" PC-Sala.LOCAL "), "pc-sala.local")
            self.assertEqual(addresses.normalize_target("ESTAÇÃO.local"), "xn--estao-dra1a.local")
            self.assertEqual(addresses.normalize_target("PC-Sala.local."), "pc-sala.local.")
        lookup.assert_not_called()

    def test_list_url_embedded_port_and_controls_are_rejected_before_connection(self):
        for value in ("169.254.1.2 / 192.168.1.2", "192.168.1.2,10.0.0.2",
                      "https://host", "host:64321", "host/path", "a b", "user@host",
                      "pc\n", "pc\t", "pc\x00", "pc\u200b", "host#part", "host?port=3"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                addresses.normalize_target(value)

    def test_unusable_ipv4_and_malformed_names_are_rejected(self):
        for value in ("", "...", ".host", "-host", "host-", "pc..local", "pc_name",
                      "999.1.2.3", "192.168.001.2", "0.0.0.0",
                      "224.0.0.1", "255.255.255.255", "1.2.3", "123", "a" * 64,
                      "a." * 128, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                addresses.normalize_target(value)

    def test_ipv6_has_an_explicit_supported_protocol_message(self):
        for value in ("::1", "2001:db8::1", "[fe80::1234]"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "IPv6"):
                addresses.normalize_target(value)

    def test_classification_distinguishes_apipa_lan_and_vpn_public_ranges(self):
        self.assertEqual(addresses.classify_address("169.254.223.179"), "link-local")
        for value in ("10.0.0.1", "172.20.1.2", "192.168.1.2"):
            self.assertEqual(addresses.classify_address(value), "local")
        for value in ("100.90.0.4", "8.8.8.8"):
            self.assertEqual(addresses.classify_address(value), "network")
        with self.assertRaises(ValueError):
            addresses.classify_address("hostname")


class ErrorHintTests(unittest.TestCase):
    def test_dns_timeout_refused_and_missing_route_have_actionable_distinct_hints(self):
        for error, expected in (
                (socket.gaierror(-2, "DNS"), "localizar"),
                (TimeoutError(), "não respondeu"),
                (ConnectionRefusedError(), "inicie o atendimento"),
                (OSError(errno.ENETUNREACH, "no route"), "rota"),
                (ConnectionResetError(), "interrompida")):
            with self.subTest(error=error):
                self.assertIn(expected, addresses.connection_error_hint(error))

    def test_windows_native_error_codes_have_the_same_diagnostics(self):
        for code, expected in ((10061, "recusou"), (10060, "não respondeu"), (10065, "rota")):
            error = OSError("network failure")
            error.winerror = code
            with self.subTest(code=code):
                self.assertIn(expected, addresses.connection_error_hint(error))

    def test_permission_failure_preserves_authorization_reason_without_retry_advice(self):
        message = addresses.connection_error_hint(PermissionError("Identidade não aprovada."), "169.254.1.2")
        self.assertIn("Acesso não autorizado", message)
        self.assertIn("Identidade não aprovada", message)
        self.assertNotIn("reconectar", message)
        self.assertNotIn("169.254", message)

    def test_apipa_failure_explains_limited_scope_and_other_error_detail_is_preserved(self):
        self.assertIn("169.254", addresses.connection_error_hint(TimeoutError(), "169.254.1.2"))
        self.assertEqual(addresses.connection_error_hint(ValueError("Porta inválida."), "host"), "Porta inválida.")


if __name__ == "__main__":
    unittest.main()
