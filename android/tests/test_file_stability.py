"""Concurrency tests with production FileTransfers/Peer and explicit SAF doubles."""
import json
import os
import subprocess
import tempfile
from pathlib import Path
import unittest


@unittest.skipUnless(os.environ.get("MINIANYDESK_TRANSFER_CLASSPATH"), "Compile the Kotlin SAF harness first")
class FileStabilityTests(unittest.TestCase):
    def run_case(self, mode, *args):
        result = subprocess.run(["java", "-cp", os.environ["MINIANYDESK_TRANSFER_CLASSPATH"],
            "br.com.perabru.minianydesk.FileTransferStabilityHarness", mode, *map(str, args)], capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout.strip()), {"result": "passed", "case": mode})
    def test_cancel_during_saf_open_waits_to_delete_new_document_once(self): self.run_case("cancel-open")
    def test_close_during_blocked_saf_write_is_prompt_and_cleans_new_document(self): self.run_case("close-write")
    def test_cancel_and_close_do_not_wait_for_saturated_peer_outbox(self): self.run_case("blocked-network")
    def test_reentrant_status_and_late_prompts_preserve_shared_guard_count(self): self.run_case("reentrant-status-prompt")
    def test_failed_provider_cleanup_delivers_one_explicit_warning(self): self.run_case("cleanup-warning")
    def test_first_manager_removes_only_abandoned_owned_private_spools(self): self.run_case("abandoned-cache")
    def test_later_manager_sharing_cache_preserves_active_transfer_and_live_files(self): self.run_case("active-cache-owner")
    @unittest.skipUnless(hasattr(os, "mkfifo"), "A real FIFO descriptor requires a POSIX host")
    def test_valid_destination_may_be_a_file_output_stream_backed_by_pipe(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "saf-provider-pipe"
            os.mkfifo(path)
            self.run_case("provider-pipe", path)


if __name__ == "__main__": unittest.main()
