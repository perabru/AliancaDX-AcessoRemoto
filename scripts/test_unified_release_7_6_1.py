"""Exercise the identical publication boundaries using the isolated v7 profile."""
import unittest
import test_unified_release as fixtures
import unified_release as release


class ReleaseSevenSixOneTests(fixtures.UnifiedReleaseTests):
    profile = release.profile_for("7.6.1")

    def test_version_profile_does_not_change_historical_defaults(self):
        self.assertEqual(release.VERSION, "6.4.0")
        self.assertEqual(release.DEFAULT_PROFILE.version, "6.4.0")
        self.assertNotEqual(release.FILES, self.profile.files)
        self.assertIn("scripts/unified_release_7_6_1.py", self.profile.build_sources)
        self.assertIn("android/tools/apk_payload.py", self.profile.build_sources)
        self.assertIn("desktop/tests/windows_identity_smoke.py", self.profile.build_sources)
        self.assertIn("scripts/github_release_inputs.py", self.profile.build_sources)
        self.assertIn(".github/workflows/release-7.6.1.yml", self.profile.build_sources)
        with self.assertRaises(RuntimeError): release.profile_for("7.7.0")

    def test_missing_windows_identity_or_icon_is_rejected(self):
        for name in ("app_identity.py", "assets/brand/aliancadx.ico", "assets/brand/aliancadx-app-256.png"):
            with self.subTest(name=name):
                value = self.desktop["windows_embedded_source_sha256"].pop(name)
                self.write(self.output / release.WINDOWS_REPORT, self.desktop)
                with self.assertRaisesRegex(RuntimeError, "application identity or icon"):
                    self.check()
                self.desktop["windows_embedded_source_sha256"][name] = value

    def test_missing_windows_identity_or_icon_audit_is_rejected(self):
        for name in ("app_identity_module_included", "app_icon_included"):
            with self.subTest(name=name):
                self.desktop["artifacts"][0][name] = False
                self.write(self.output / release.WINDOWS_REPORT, self.desktop)
                with self.assertRaisesRegex(RuntimeError, "identity or icon audit"):
                    self.check()
                self.desktop["artifacts"][0][name] = True

    def test_missing_windows_in_app_updater_is_rejected(self):
        del self.desktop["windows_embedded_source_sha256"]["update_install.py"]
        self.write(self.output / release.WINDOWS_REPORT, self.desktop)
        with self.assertRaisesRegex(RuntimeError, "in-app updater module"):
            self.check()

    def test_missing_windows_in_app_updater_audit_is_rejected(self):
        self.desktop["artifacts"][0]["in_app_update_module_included"] = False
        self.write(self.output / release.WINDOWS_REPORT, self.desktop)
        with self.assertRaisesRegex(RuntimeError, "in-app updater audit"):
            self.check()

    def test_missing_saved_connection_class_claim_is_rejected(self):
        del self.pin["new_session_and_input_classes_in_apk"]["SavedComputerAccess"]
        self.write(self.root / "docs" / self.profile.pin, self.pin)
        with self.assertRaisesRegex(RuntimeError, "class inventory"):
            release.mobile.load_pin(self.root, self.profile.version)

    def test_missing_new_touchpad_class_claim_is_rejected(self):
        del self.pin["new_session_and_input_classes_in_apk"]["MouseControlsPad"]
        self.write(self.root / "docs" / self.profile.pin, self.pin)
        with self.assertRaisesRegex(RuntimeError, "class inventory"):
            release.mobile.load_pin(self.root, self.profile.version)

    def test_missing_adaptive_feedback_definition_is_rejected(self):
        del self.pin["new_session_and_input_classes_in_apk"]["StreamFeedback"]
        self.write(self.root / "docs" / self.profile.pin, self.pin)
        with self.assertRaisesRegex(RuntimeError, "class inventory"):
            release.mobile.load_pin(self.root, self.profile.version)

    def test_missing_settings_panel_definition_is_rejected(self):
        del self.pin["new_session_and_input_classes_in_apk"]["StreamSettingsPanel"]
        self.write(self.root / "docs" / self.profile.pin, self.pin)
        with self.assertRaisesRegex(RuntimeError, "class inventory"):
            release.mobile.load_pin(self.root, self.profile.version)

    def test_missing_settings_source_is_rejected(self):
        del self.pin["source_sha256"]["android/app/src/main/java/br/com/perabru/minianydesk/StreamSettingsPanel.kt"]
        self.write(self.root / "docs" / self.profile.pin, self.pin)
        with self.assertRaisesRegex(RuntimeError, "source inventory"):
            release.mobile.load_pin(self.root, self.profile.version)

    def test_missing_windows_updater_is_rejected(self):
        del self.desktop["windows_embedded_source_sha256"]["updates.py"]
        self.write(self.output / release.WINDOWS_REPORT, self.desktop)
        with self.assertRaisesRegex(RuntimeError, "updater module"):
            self.check()

    def test_missing_android_updater_definition_is_rejected(self):
        for name in ("AndroidReleaseUpdate", "AndroidReleaseUpdate$Result", "AndroidReleaseUpdate$Relation",
                     "AndroidReleaseFetcher", "AndroidReleaseSource", "AndroidUpdateFailure", "AndroidUpdateDialog"):
            with self.subTest(name=name):
                pin = dict(self.pin, new_session_and_input_classes_in_apk=dict(self.pin["new_session_and_input_classes_in_apk"]))
                del pin["new_session_and_input_classes_in_apk"][name]
                self.write(self.root / "docs" / self.profile.pin, pin)
                with self.assertRaisesRegex(RuntimeError, "class inventory"):
                    release.mobile.load_pin(self.root, self.profile.version)

    def test_missing_android_in_app_updater_definition_is_rejected(self):
        for name in ("AndroidApkDownload", "AndroidApkSource", "AndroidApkInstaller",
                     "AndroidApkInstallTarget", "AndroidInstallReceiver", "AndroidInstallState"):
            with self.subTest(name=name):
                pin = dict(self.pin, new_session_and_input_classes_in_apk=dict(self.pin["new_session_and_input_classes_in_apk"]))
                del pin["new_session_and_input_classes_in_apk"][name]
                self.write(self.root / "docs" / self.profile.pin, pin)
                with self.assertRaisesRegex(RuntimeError, "class inventory"):
                    release.mobile.load_pin(self.root, self.profile.version)

    def test_missing_android_in_app_updater_source_is_rejected(self):
        for name in ("AndroidApkDownload.kt", "AndroidApkInstaller.kt"):
            with self.subTest(name=name):
                pin = dict(self.pin, source_sha256=dict(self.pin["source_sha256"]))
                del pin["source_sha256"]["android/app/src/main/java/br/com/perabru/minianydesk/" + name]
                self.write(self.root / "docs" / self.profile.pin, pin)
                with self.assertRaisesRegex(RuntimeError, "source inventory"):
                    release.mobile.load_pin(self.root, self.profile.version)

    def test_missing_android_updater_source_is_rejected(self):
        del self.pin["source_sha256"]["android/app/src/main/java/br/com/perabru/minianydesk/AndroidReleaseFetcher.kt"]
        self.write(self.root / "docs" / self.profile.pin, self.pin)
        with self.assertRaisesRegex(RuntimeError, "source inventory"):
            release.mobile.load_pin(self.root, self.profile.version)

    def test_missing_native_bitmap_policy_definition_is_rejected(self):
        del self.pin["new_session_and_input_classes_in_apk"]["BitmapDecodePolicy"]
        self.write(self.root / "docs" / self.profile.pin, self.pin)
        with self.assertRaisesRegex(RuntimeError, "class inventory"):
            release.mobile.load_pin(self.root, self.profile.version)

    def test_missing_native_bitmap_policy_source_is_rejected(self):
        del self.pin["source_sha256"]["android/app/src/main/java/br/com/perabru/minianydesk/BitmapDecodePolicy.kt"]
        self.write(self.root / "docs" / self.profile.pin, self.pin)
        with self.assertRaisesRegex(RuntimeError, "source inventory"):
            release.mobile.load_pin(self.root, self.profile.version)

    def test_missing_windows_updater_audit_is_rejected(self):
        self.desktop["artifacts"][0]["updates_module_included"] = False
        self.write(self.output / release.WINDOWS_REPORT, self.desktop)
        with self.assertRaisesRegex(RuntimeError, "updater audit"):
            self.check()

    def test_missing_ci_dex_definition_audit_is_rejected(self):
        del self.report["artifacts"][-1]["dex_payload_verification"]
        self.write(self.output / release.REPORT, self.report)
        with self.assertRaisesRegex(RuntimeError, "DEX class definition"):
            self.check()

    def test_missing_android_installer_permission_audit_is_rejected(self):
        del self.report["artifacts"][-1]["self_update_manifest_verification"]
        self.write(self.output / release.REPORT, self.report)
        with self.assertRaisesRegex(RuntimeError, "permission and receiver audit"):
            self.check()

    def test_missing_android_installer_permission_policy_is_rejected(self):
        del self.pin["self_update_manifest_policy"]
        self.write(self.root / "docs" / self.profile.pin, self.pin)
        with self.assertRaisesRegex(RuntimeError, "manifest policy"):
            release.mobile.load_pin(self.root, self.profile.version)


if __name__ == "__main__":
    unittest.main()
