import base64
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import tomllib
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import mesh


class PairingTests(unittest.TestCase):
    def test_code_roundtrip_binds_token_port_and_tls(self):
        item = mesh.generate_pairing(54321, "a" * 64)
        self.assertEqual(mesh.parse_pairing(item.code), item)
        self.assertEqual(len(item.code), 119)
        self.assertEqual(mesh.parse_pairing(item.code.lower()).fingerprint, "A" * 64)
        self.assertEqual(mesh.parse_pairing(" \n" + item.code + "\n "), item)

    def test_identity_and_network_keys_are_domain_separated(self):
        item = mesh.generate_pairing()
        self.assertNotIn(item.network_secret, repr(item))
        self.assertNotIn(item.code, repr(item))
        self.assertNotEqual(item.network_secret[:32], item.network_name[5:])
        other = mesh.generate_pairing()
        self.assertNotEqual(item.network_name, other.network_name)
        self.assertNotEqual(item.public_id, other.public_id)
        self.assertEqual(mesh.pairing_public_id(item.code), item.public_id)

    def test_changes_included_in_code_keep_host_identity(self):
        item = mesh.generate_pairing(fingerprint="1" * 64)
        updated = dataclasses.replace(item, port=50200, fingerprint="2" * 64)
        self.assertEqual(item.public_id, updated.public_id)
        self.assertEqual(item.network_secret, updated.network_secret)
        self.assertNotEqual(item.code, updated.code)

    def test_mutation_and_noncanonical_padding_rejected(self):
        code = mesh.generate_pairing().code
        for index in (5, 20, 70, 117, 118):
            invalid = code[:index] + ("A" if code[index] != "A" else "B") + code[index + 1:]
            with self.subTest(index=index), self.assertRaises(mesh.MeshError):
                mesh.parse_pairing(invalid)

    def test_invalid_inputs(self):
        for invalid in (None, b"MDA6", "", "X" * 200, "MDA6-" + "0" * 114,
                        "MDA6-" + "A" * 114, mesh.generate_pairing().code.replace("-", "- ")):
            with self.subTest(value=type(invalid)), self.assertRaises(mesh.MeshError):
                mesh.parse_pairing(invalid)
        for port in (0, -1, 65536, True, "64321"):
            with self.subTest(port=port), self.assertRaises(mesh.MeshError):
                mesh.generate_pairing(port)
        with self.assertRaises(mesh.MeshError):
            mesh.generate_pairing(fingerprint="not-a-cert")

    def test_persist_preserve_update_and_rotate(self):
        with tempfile.TemporaryDirectory() as directory:
            first = mesh.load_or_create_pairing(directory, fingerprint="A" * 64)
            again = mesh.load_or_create_pairing(directory, fingerprint="A" * 64)
            self.assertEqual(first, again)
            changed = mesh.load_or_create_pairing(directory, 40001, "B" * 64)
            self.assertEqual(first.public_id, changed.public_id)
            self.assertNotEqual(first.code, changed.code)
            rotated = mesh.rotate_pairing(directory, 40001, "B" * 64)
            self.assertNotEqual(rotated.public_id, changed.public_id)
            self.assertEqual(mesh.load_pairing_code(Path(directory) / "host-pairing.json"), rotated.code)
            if os.name != "nt":
                self.assertEqual((Path(directory) / "host-pairing.json").stat().st_mode & 0o777, 0o600)
                self.assertEqual(Path(directory).stat().st_mode & 0o777, 0o700)

    def test_corrupt_identity_is_never_silently_regenerated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "host-pairing.json"
            path.write_text("broken")
            with self.assertRaises(mesh.MeshError):
                mesh.load_or_create_pairing(directory)
            self.assertEqual(path.read_text(), "broken")

    def test_malformed_vault_envelope_is_application_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "saved.json"
            for malformed in ("[]", "null", "1", "{}", '{"schema":1,"protection":"user-file","value":123}'):
                path.write_text(malformed)
                with self.subTest(value=malformed), self.assertRaises(mesh.MeshError):
                    mesh.load_pairing_code(path)

    def test_optional_controller_vault_and_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "saved.json"
            with self.assertRaises(FileNotFoundError):
                mesh.load_pairing_code(path)
            code = mesh.generate_pairing(fingerprint="C" * 64).code
            mesh.save_pairing_code(path, code)
            self.assertEqual(mesh.load_pairing_code(path), code)
            self.assertNotIn(code, path.read_text())

    @unittest.skipIf(os.name == "nt", "Unix symlink security test")
    def test_symlink_storage_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.write_text("unrelated")
            link = Path(directory) / "link"
            link.symlink_to(target)
            with self.assertRaises(mesh.MeshError):
                mesh.load_pairing_code(link)
            self.assertEqual(target.read_text(), "unrelated")


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.pair = mesh.generate_pairing(fingerprint="F" * 64)

    def config(self, role):
        return tomllib.loads(mesh.build_config(role, self.pair, 35001, 35002))

    def test_no_admin_network_or_broad_proxy_changes(self):
        for role in ("host", "controller"):
            with self.subTest(role=role):
                config = self.config(role)
                flags = config["flags"]
                self.assertTrue(flags["no_tun"])
                self.assertTrue(flags["enable_encryption"])
                self.assertFalse(flags["enable_exit_node"])
                self.assertFalse(flags["proxy_forward_by_system"])
                self.assertFalse(flags["accept_dns"])
                self.assertFalse(flags["enable_udp_broadcast_relay"])
                self.assertTrue(flags["disable_upnp"])
                self.assertEqual(flags["relay_network_whitelist"], "")
                for key in ("listeners", "exit_nodes", "routes"):
                    self.assertEqual(config[key], [])
                self.assertNotIn("proxy_network", config)
                self.assertNotIn("socks5_proxy", config)
                self.assertEqual(config["console_logger"]["level"], "off")
                self.assertEqual(config["file_logger"]["level"], "off")

    def test_acl_host_allows_only_its_tls_port_and_drops_all_other_protocols(self):
        chains = {x["chain_type"]: x for x in self.config("host")["acl"]["acl_v1"]["chains"]}
        self.assertEqual(chains[1]["default_action"], 2)
        self.assertEqual(chains[3]["default_action"], 2)
        self.assertNotIn("rules", chains[3])
        self.assertEqual(len(chains[1]["rules"]), 1)
        rule = chains[1]["rules"][0]
        self.assertEqual(rule["protocol"], 1)
        self.assertEqual(rule["action"], 1)
        self.assertEqual(rule["ports"], [str(self.pair.port)])
        self.assertEqual(rule["destination_ips"], [mesh.HOST_IP + "/32"])
        self.assertEqual(chains[2]["default_action"], 1)

    def test_controller_has_no_inbound_allow_rules_and_loopback_forward(self):
        config = self.config("controller")
        chains = {x["chain_type"]: x for x in config["acl"]["acl_v1"]["chains"]}
        self.assertEqual(chains[1]["default_action"], 2)
        self.assertNotIn("rules", chains[1])
        self.assertEqual(config["port_forward"], [{"proto": "tcp", "bind_addr": "127.0.0.1:35002",
                                                  "dst_addr": f"{mesh.HOST_IP}:{self.pair.port}"}])
        self.assertNotIn("port_forward", self.config("host"))

    def test_controller_ips_are_random_within_private_overlay(self):
        ips = {self.config("controller")["ipv4"] for _ in range(30)}
        self.assertGreater(len(ips), 25)
        self.assertNotIn(mesh.HOST_IP + "/16", ips)
        self.assertTrue(all(ip.startswith("10.126.") and ip.endswith("/16") for ip in ips))

    def test_force_relay_disables_direct_paths(self):
        config = tomllib.loads(mesh.build_config("controller", self.pair, 35001, 35002,
                                                peers=["tcp://127.0.0.1:35003"], force_relay=True))
        self.assertTrue(config["flags"]["disable_p2p"])
        self.assertTrue(config["flags"]["disable_udp_hole_punching"])
        self.assertTrue(config["flags"]["disable_tcp_hole_punching"])
        self.assertEqual(config["peer"], [{"uri": "tcp://127.0.0.1:35003"}])

    def test_invalid_peer_urls_do_not_inject_config(self):
        for invalid in ([], ["https://example.com"], ["tcp://user:pass@example.com:1"],
                        ['tcp://example.com:1\n[flags]'], ["tcp://example.com:65536"],
                        ["tcp://example.com:1/path"], ['wss://example.com/"'], ["wss://example.com/?token=a"]):
            with self.subTest(kind=str(invalid)[:20]), self.assertRaises(mesh.MeshError):
                mesh.build_config("host", self.pair, 35001, peers=invalid)

    def test_ambient_cli_config_and_logs_disabled(self):
        with patch.dict(os.environ, {"ET_DISABLE_ENCRYPTION": "true", "ET_CONFIG_SERVER": "bad",
                                     "ET_NETWORK_SECRET": "secret", "RUST_LOG": "debug"}):
            env = mesh._clean_env()
        self.assertFalse(any(name.startswith("ET_") for name in env))
        self.assertEqual(env["RUST_LOG"], "off")
        self.assertEqual(env["RUST_BACKTRACE"], "0")


class RuntimeTests(unittest.TestCase):
    def bundle(self, directory, windows=None):
        windows = os.name == "nt" if windows is None else windows
        names = ["easytier-core.exe", "Packet.dll"] if windows else ["easytier-core"]
        entries = []
        for name in names:
            path = Path(directory) / name
            path.write_bytes(b"fixture runtime" + name.encode())
            entries.append({"filename": name, "size": path.stat().st_size,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        manifest = {"schema": 1, "package": {"version": mesh.EASYTIER_VERSION}, "files": entries}
        (Path(directory) / "manifest.json").write_text(json.dumps(manifest))
        return manifest

    def test_all_payload_integrity_and_missing_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.bundle(directory)
            core = mesh.verify_runtime(directory)
            self.assertTrue(core.is_absolute())
            self.assertTrue(core.is_file())
            self.assertFalse(any(Path(directory).glob("easytier-cli*")))
            core.write_bytes(b"altered")
            with self.assertRaises(mesh.MeshError):
                mesh.verify_runtime(directory)
            core.unlink()
            with self.assertRaises(mesh.MeshError):
                mesh.verify_runtime(directory)

    def test_manifest_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.bundle(directory)
            manifest["files"][0]["filename"] = "../other"
            (Path(directory) / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(mesh.MeshError):
                mesh.verify_runtime(directory)

    def test_missing_manifest(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(mesh.MeshError):
            mesh.verify_runtime(directory)

    def test_windows_requires_loader_packet_dependency_without_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            self.bundle(directory, windows=True)
            # Patch only the module's platform lookup; pathlib keeps the real OS.
            with patch.object(mesh, "os", SimpleNamespace(name="nt")):
                self.assertEqual(mesh.verify_runtime(directory).name, "easytier-core.exe")
                (Path(directory) / "Packet.dll").unlink()
                with self.assertRaises(mesh.MeshError):
                    mesh.verify_runtime(directory)

    def test_windows_manifest_cannot_omit_loader_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.bundle(directory, windows=True)
            manifest["files"] = [item for item in manifest["files"] if item["filename"] != "Packet.dll"]
            (Path(directory) / "manifest.json").write_text(json.dumps(manifest))
            with patch.object(mesh, "os", SimpleNamespace(name="nt")), self.assertRaises(mesh.MeshError):
                mesh.verify_runtime(directory)


class LifecycleTests(unittest.TestCase):
    def test_controller_rejects_unpinned_codes_before_start(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = mesh.MeshManager("controller", directory)
            with self.assertRaises(mesh.MeshError):
                manager.start(mesh.generate_pairing())
            self.assertEqual(manager.snapshot().state, "stopped")

    def test_host_status_never_claims_public_online_from_running_process(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = mesh.MeshManager("host", directory)
            status = manager._observe(mesh.generate_pairing(), threading.Event())
            self.assertFalse(status.peer_online)
            self.assertFalse(status.public_connected)
            self.assertEqual(status.state, "connecting")

    def test_wait_ready_is_cancellable_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = mesh.MeshManager("controller", directory)
            manager._publish(mesh.MeshStatus("connecting", "test"))
            cancel = threading.Event()
            cancel.set()
            with self.assertRaisesRegex(mesh.MeshError, "cancelada"):
                manager.wait_ready(3, cancel)
            start = time.monotonic()
            with self.assertRaisesRegex(mesh.MeshTimeoutError, "ainda não encontrado") as caught:
                manager.wait_ready(.02)
            self.assertIsInstance(caught.exception, TimeoutError)
            self.assertIsInstance(caught.exception, mesh.MeshError)
            self.assertLess(time.monotonic() - start, .3)
            manager.stop()
            with self.assertRaisesRegex(mesh.MeshError, "parada"):
                manager.wait_ready(3)

    def test_same_pairing_start_is_idempotent_changed_port_waits_old_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = mesh.MeshManager("controller", directory)
            first = mesh.generate_pairing(fingerprint="A" * 64)
            launches, active, overlap = [], [], []
            entered = threading.Event()
            def run(pairing, stop, generation):
                if active:
                    overlap.append(True)
                active.append(generation)
                launches.append(pairing.port)
                entered.set()
                stop.wait(2)
                time.sleep(.03)
                manager._publish(mesh.MeshStatus("ready", "stale", ("127.0.0.1", 9)), generation)
                active.remove(generation)
            with patch.object(manager, "_run", side_effect=run):
                manager.start(first)
                self.assertTrue(entered.wait(1))
                initial_thread = manager._thread
                manager.start(first)
                self.assertIs(manager._thread, initial_thread)
                manager.start(dataclasses.replace(first, port=54320))
                deadline = time.monotonic() + 1
                while len(launches) < 2 and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertEqual(launches, [first.port, 54320])
                self.assertEqual(manager.snapshot().state, "starting")
                self.assertFalse(overlap)
                manager.stop()
                self.assertTrue(manager.join(1))
                self.assertEqual(manager.snapshot().state, "stopped")

    def test_stop_notification_cannot_be_overtaken_by_old_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            seen, entered, release = [], threading.Event(), threading.Event()
            def callback(status):
                if status.state == "ready":
                    entered.set()
                    release.wait(1)
                seen.append(status.state)
            manager = mesh.MeshManager("host", directory, on_status=callback)
            worker = threading.Thread(target=manager._publish,
                                      args=(mesh.MeshStatus("ready", "probe"), 0))
            worker.start()
            self.assertTrue(entered.wait(1))
            stopper = threading.Thread(target=manager.stop)
            stopper.start()
            release.set()
            worker.join(1)
            stopper.join(1)
            self.assertEqual(seen, ["ready", "stopped"])
            self.assertEqual(manager.snapshot().state, "stopped")

    def test_stale_callback_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            seen = []
            manager = mesh.MeshManager("host", directory, on_status=seen.append)
            manager._generation = 2
            manager._publish(mesh.MeshStatus("error", "old"), 1)
            self.assertFalse(seen)
            self.assertEqual(manager.snapshot().state, "stopped")


if __name__ == "__main__":
    unittest.main()
