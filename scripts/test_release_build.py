"""Pure publication-integrity checks; no build, GitHub request or private key."""
from pathlib import Path
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location('release_build_under_test', Path(__file__).with_name('build_release.py'))
builder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = builder
SPEC.loader.exec_module(builder)


class ReleaseIntegrityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.commit = 'a' * 40
        self.contents = {
            'AliancaDX_Controlador_Setup.exe': b'public controller fixture',
            'AliancaDX_Host_Setup.exe': b'public host fixture',
            'AliancaDX_Acesso_Remoto_6.2.1.apk': b'public apk fixture',
            'release-build-verification.json': b'{}',
            'SHA256SUMS.txt': b'fixture hashes',
        }
        for name, data in self.contents.items():
            (self.folder/name).write_bytes(data)
        self.release = {
            'tagName': 'v6.2.1',
            'targetCommitish': self.commit,
            'assets': [{'name': name, 'size': len(data)} for name, data in self.contents.items()],
        }

    def test_uploaded_complete_set_accepts_exact_target_and_all_names_and_sizes(self):
        self.assertIsNone(builder.verify_uploaded(self.release, self.folder, self.commit))

    def test_uploaded_reordered_assets_are_equivalent(self):
        self.release['assets'].reverse()
        self.assertIsNone(builder.verify_uploaded(self.release, self.folder, self.commit))

    def test_uploaded_wrong_tag_or_target_is_rejected(self):
        for field, value in (('tagName', 'v6.2.0'), ('targetCommitish', 'main'),
                             ('targetCommitish', 'b' * 40)):
            release = {**self.release, field: value}
            with self.subTest(field=field, value=value), self.assertRaises(RuntimeError):
                builder.verify_uploaded(release, self.folder, self.commit)

    def test_uploaded_missing_asset_or_asset_list_is_rejected(self):
        for assets in (self.release['assets'][:-1], []):
            release = {**self.release, 'assets': assets}
            with self.subTest(asset_count=len(assets)), self.assertRaises(RuntimeError):
                builder.verify_uploaded(release, self.folder, self.commit)
        release = {key: value for key, value in self.release.items() if key != 'assets'}
        with self.assertRaises(RuntimeError):
            builder.verify_uploaded(release, self.folder, self.commit)

    def test_uploaded_wrong_size_or_name_is_rejected(self):
        for replacement in ({'name': self.release['assets'][0]['name'], 'size': 1},
                            {'name': 'unexpected.exe', 'size': self.release['assets'][0]['size']}):
            release = {**self.release, 'assets': [replacement, *self.release['assets'][1:]]}
            with self.subTest(replacement=replacement), self.assertRaises(RuntimeError):
                builder.verify_uploaded(release, self.folder, self.commit)

    def test_uploaded_extra_asset_is_rejected(self):
        release = {**self.release, 'assets': [*self.release['assets'], {'name': 'extra.txt', 'size': 3}]}
        with self.assertRaises(RuntimeError):
            builder.verify_uploaded(release, self.folder, self.commit)

    def test_uploaded_duplicate_asset_name_is_rejected(self):
        release = {**self.release, 'assets': [*self.release['assets'], self.release['assets'][0]]}
        with self.assertRaisesRegex(RuntimeError, 'Duplicate'):
            builder.verify_uploaded(release, self.folder, self.commit)

    def test_apk_same_size_tamper_is_rejected_before_opening_archive_or_signing_report(self):
        original = b'original pinned APK fixture'
        path = self.folder/'tampered.apk'
        path.write_bytes(b'X' + original[1:])
        with patch.object(builder, 'APK_BYTES', len(original)), \
                patch.object(builder, 'APK_SHA256', hashlib.sha256(original).hexdigest()), \
                patch.object(builder.zipfile, 'ZipFile', side_effect=AssertionError('Archive should not be opened')), \
                patch.object(Path, 'read_text', side_effect=AssertionError('Report should not be read')) as read_report:
            with self.assertRaisesRegex(RuntimeError, 'does not match'):
                builder.verify_apk(path)
            read_report.assert_not_called()

    def test_apk_valid_archive_pin_is_bound_to_committed_signing_report(self):
        path = self.folder/'valid-archive.apk'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('classes.dex', b'dex fixture, never installed')
        pin = hashlib.sha256(path.read_bytes()).hexdigest()
        for report in ({'apk_sha256': '0' * 64, 'bytes': path.stat().st_size},
                       {'apk_sha256': pin, 'bytes': path.stat().st_size + 1}):
            with self.subTest(report=report), \
                    patch.object(builder, 'APK_BYTES', path.stat().st_size), \
                    patch.object(builder, 'APK_SHA256', pin), \
                    patch.object(Path, 'read_text', return_value=json.dumps(report)), \
                    self.assertRaisesRegex(RuntimeError, 'different artifact pin'):
                builder.verify_apk(path)


class RecordGithubPinsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root/'project'
        self.output = self.root/'output'
        self.output.mkdir()
        self.commit = 'a'*40
        self.manifest_path = self.project/'desktop/installer/release_artifacts_6_2_1.json'
        self.manifest_path.parent.mkdir(parents=True)
        self.evidence_name = 'RELATORIO_INSTALADORES_GITHUB_6_2_1.json'
        self.evidence_path = self.project/'desktop/docs'/self.evidence_name
        self.sentinel = self.project/'untouched.txt'
        self.sentinel.write_bytes(b'preserve unrelated source')
        names = ('AliancaDX_Controlador_Setup.exe', 'AliancaDX_Host_Setup.exe')
        self.local_pins = [{'file': name, 'bytes': 123, 'sha256': ('b' if i==0 else 'c')*64}
                           for i,name in enumerate(names)]
        self.manifest_path.write_text(json.dumps({'version':'6.2.1','artifacts':self.local_pins,'additional_evidence':['local.json']}))
        windows=[]
        for name in names:
            path=self.output/name
            path.write_bytes(('fresh '+name).encode())
            windows.append({'file':name,'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                            'archive_crc_ok':True,'embedded_payload_matches_frozen_source':True})
        self.report={'version':'6.2.1','source_commit':self.commit,'source_dirty':False,'artifacts':windows,
                     'windows_embedded_source_sha256':{'adaptive_stream.py':'d'*64}}
        self.evidence={'version':'6.2.1','source_commit':self.commit,'artifacts':windows,
                       'embedded_source_sha256':self.report['windows_embedded_source_sha256']}
        self.write_reports()

    def write_reports(self):
        (self.output/'release-build-verification.json').write_text(json.dumps(self.report))
        (self.output/self.evidence_name).write_text(json.dumps(self.evidence))

    def snapshot(self):
        return {str(p.relative_to(self.project)):p.read_bytes() for p in self.project.rglob('*') if p.is_file()}

    def test_preserves_local_pins_appends_ci_pins_and_writes_only_two_metadata_paths(self):
        before=self.snapshot()
        builder.record_github_pins(self.output,self.project,self.commit)
        after=self.snapshot()
        changed={path for path in set(before)|set(after) if before.get(path)!=after.get(path)}
        self.assertEqual(changed, {'desktop/installer/release_artifacts_6_2_1.json','desktop/docs/'+self.evidence_name})
        manifest=json.loads(self.manifest_path.read_text())
        self.assertEqual(manifest['artifacts'][:2],self.local_pins)
        self.assertEqual(manifest['artifacts'][2:],[{key:row[key] for key in ('file','bytes','sha256')} for row in self.report['artifacts']])
        self.assertEqual(json.loads(self.evidence_path.read_text()),self.evidence)
        self.assertEqual(manifest['additional_evidence'],['local.json','../docs/'+self.evidence_name])
        builder.record_github_pins(self.output,self.project,self.commit)
        self.assertEqual(self.snapshot(),after)

    def test_dirty_revision_wrong_commit_or_incoherent_evidence_never_writes_metadata(self):
        before=self.snapshot()
        for field,value in (('source_dirty',True),('source_commit','f'*40)):
            original=self.report[field]
            self.report[field]=value;self.write_reports()
            with self.subTest(field=field),self.assertRaises(RuntimeError):
                builder.record_github_pins(self.output,self.project,self.commit)
            self.assertEqual(self.snapshot(),before)
            self.report[field]=original
        for field,value in (('source_commit','f'*40),('version','6.2.0'),('embedded_source_sha256',{})):
            original=self.evidence[field]
            self.evidence[field]=value;self.write_reports()
            with self.subTest(evidence_field=field),self.assertRaises(RuntimeError):
                builder.record_github_pins(self.output,self.project,self.commit)
            self.assertEqual(self.snapshot(),before)
            self.evidence[field]=original

    def test_same_size_artifact_tampering_never_writes_metadata(self):
        before=self.snapshot()
        path=self.output/self.report['artifacts'][0]['file']
        data=path.read_bytes();path.write_bytes(b'X'+data[1:])
        with self.assertRaisesRegex(RuntimeError,'changed after'):
            builder.record_github_pins(self.output,self.project,self.commit)
        self.assertEqual(self.snapshot(),before)


if __name__ == '__main__': unittest.main()
