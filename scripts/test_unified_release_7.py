"""Exercise the identical publication boundaries using the isolated v7 profile."""
import unittest
import test_unified_release as fixtures
import unified_release as release


class ReleaseSevenTests(fixtures.UnifiedReleaseTests):
    profile = release.profile_for("7.0.0")

    def test_version_profile_does_not_change_historical_defaults(self):
        self.assertEqual(release.VERSION, "6.4.0")
        self.assertEqual(release.DEFAULT_PROFILE.version, "6.4.0")
        self.assertNotEqual(release.FILES, self.profile.files)
        self.assertIn("scripts/unified_release_7.py", self.profile.build_sources)
        self.assertIn("android/tools/apk_payload.py", self.profile.build_sources)
        self.assertIn(".github/workflows/release-7.0.0.yml", self.profile.build_sources)
        with self.assertRaises(RuntimeError): release.profile_for("7.6.0")

    def test_missing_saved_connection_class_claim_is_rejected(self):
        del self.pin["new_session_and_input_classes_in_apk"]["SavedComputerAccess"]
        self.write(self.root / "docs" / self.profile.pin, self.pin)
        with self.assertRaisesRegex(RuntimeError, "class inventory"):
            release.mobile.load_pin(self.root, self.profile.version)

    def test_missing_ci_dex_definition_audit_is_rejected(self):
        del self.report["artifacts"][-1]["dex_payload_verification"]
        self.write(self.output / release.REPORT, self.report)
        with self.assertRaisesRegex(RuntimeError, "DEX class definition"):
            self.check()


if __name__ == "__main__":
    unittest.main()
