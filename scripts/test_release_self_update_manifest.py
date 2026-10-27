"""Audit the compiled installer surface without allowing exported receivers."""
import unittest
import mobile_release as release


def manifest(permissions=None, receiver=release.SELF_UPDATE_RECEIVER, exported='0'):
    permissions = sorted(release.SELF_UPDATE_PERMISSIONS) if permissions is None else permissions
    lines = ['E: manifest (line=1)']
    for permission in permissions:
        lines.extend(['  E: uses-permission (line=2)', f'    A: android:name(0x01010003)="{permission}" (Raw: "{permission}")'])
    lines.extend(['  E: application (line=7)', '    E: receiver (line=8)',
                  f'      A: android:name(0x01010003)="{receiver}" (Raw: "{receiver}")',
                  '      A: android:exported(0x01010010)=(type 0x12)0x' + exported,
                  '    E: activity (line=9)', '      A: android:name(0x01010003)="br.com.perabru.minianydesk.MainActivity"'])
    return '\n'.join(lines)


class SelfUpdateManifestTests(unittest.TestCase):
    def test_reviewed_compiled_installer_permissions_and_private_receiver_pass(self):
        report = release.verify_self_update_manifest(manifest())
        self.assertEqual(report['permissions'], sorted(release.SELF_UPDATE_PERMISSIONS))
        self.assertFalse(report['receiver_exported'])

    def test_missing_duplicate_or_extra_permission_is_rejected(self):
        permissions = sorted(release.SELF_UPDATE_PERMISSIONS)
        for changed in (permissions[:-1], permissions + [permissions[0]], permissions + ['android.permission.MANAGE_EXTERNAL_STORAGE']):
            with self.subTest(changed=changed), self.assertRaisesRegex(RuntimeError, 'permission inventory'):
                release.verify_self_update_manifest(manifest(permissions=changed))

    def test_exported_missing_or_renamed_installer_receiver_is_rejected(self):
        for text in (manifest(exported='ffffffff'), manifest(receiver='other.Receiver'),
                     manifest().replace('    E: receiver (line=8)', '    E: service (line=8)'),
                     manifest().replace('      A: android:exported(0x01010010)=(type 0x12)0x0\n', '')):
            with self.subTest(text=text), self.assertRaisesRegex(RuntimeError, 'receiver'):
                release.verify_self_update_manifest(text)

    def test_a_second_receiver_is_rejected(self):
        text = manifest() + '\n    E: receiver (line=10)\n      A: android:name(0x01010003)="other.Receiver"\n      A: android:exported(0x01010010)=(type 0x12)0x0'
        with self.assertRaisesRegex(RuntimeError, 'one private'):
            release.verify_self_update_manifest(text)


if __name__ == '__main__': unittest.main()
