"""GitHub APK chunks are inert bytes; verify bounds and atomic publication."""
from pathlib import Path
import copy
import gzip
import hashlib
import io
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import github_release_inputs as inputs


class Response(io.BytesIO):
    def __init__(self, data, url, *, content_type='application/octet-stream', length=None):
        super().__init__(data); self.url = url
        self.headers = {'Content-Type': content_type}
        if length is not None: self.headers['Content-Length'] = str(length)
    def geturl(self): return self.url


class Opener:
    def __init__(self, values): self.values = values; self.requests = []
    def open(self, request, timeout):
        self.requests.append(request.full_url)
        value = self.values[request.full_url]
        return Response(value, request.full_url)


class GitHubInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name); self.commit = 'a' * 40
        self.apk = b'PK inert owner-signed APK fixture; never installed' * 25
        self.payload = gzip.compress(self.apk, mtime=0)
        self.pin = {'version': '7.2.0', 'artifact': self.row(self.apk, file='AliancaDX_Acesso_Remoto_7.2.0.apk'),
                    'github_input': {'repository': inputs.REPOSITORY, 'commit': self.commit,
                                     'manifest': 'signed-apk.json', 'manifest_sha256': '0' * 64}}
        at = len(self.payload) // 2
        self.parts = [self.payload[:at], self.payload[at:]]
        self.manifest = {'schema': 1, 'version': '7.2.0', 'compression': 'gzip',
                         'artifact': self.pin['artifact'], 'compressed': self.row(self.payload),
                         'chunks': [self.row(value, path=f'chunks/{index:03d}.bin')
                                    for index, value in enumerate(self.parts)]}
        self.refresh()

    @staticmethod
    def row(data, **extra): return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), **extra}

    def refresh(self):
        self.body = json.dumps(self.manifest).encode()
        self.pin['github_input']['manifest_sha256'] = hashlib.sha256(self.body).hexdigest()
        self.values = {inputs.RAW_ORIGIN+self.commit+'/signed-apk.json': self.body}
        self.values.update({inputs.RAW_ORIGIN+self.commit+f'/chunks/{index:03d}.bin': value
                            for index, value in enumerate(self.parts)})

    def check(self): return inputs.signed_apk(self.pin, self.output, opener=Opener(self.values))

    def test_reconstructs_exact_pinned_apk_then_reuses_valid_cache_without_network(self):
        path = self.check(); self.assertEqual(path.read_bytes(), self.apk)
        cached = inputs.signed_apk(self.pin, self.output, opener=Opener({}))
        self.assertEqual(cached, path); self.assertEqual(list(self.output.iterdir()), [path])

    def test_mutable_branch_or_foreign_repo_manifest_is_rejected(self):
        for key, value in [('commit', 'main'), ('repository', 'other/repo'),
                           ('manifest', '../secret.json'), ('manifest_sha256', 'invalid')]:
            with self.subTest(key=key):
                pin = copy.deepcopy(self.pin); pin['github_input'][key] = value
                with self.assertRaises(inputs.InputError): inputs.signed_apk(pin, self.output, opener=Opener({}))
        self.assertEqual(list(self.output.iterdir()), [])

    def test_manifest_tamper_fails_before_any_chunk_is_fetched(self):
        self.values[inputs.RAW_ORIGIN+self.commit+'/signed-apk.json'] += b' '
        with self.assertRaisesRegex(inputs.InputError, 'manifest differs'): self.check()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_manifest_schema_compression_or_release_identity_cannot_drift(self):
        for key, value in [('schema', 2), ('compression', 'zip'), ('version', '7.1.0'),
                           ('artifact', self.row(b'other', file=self.pin['artifact']['file']))]:
            with self.subTest(key=key):
                altered = copy.deepcopy(self.manifest); altered[key] = value
                data = json.dumps(altered).encode(); pin = copy.deepcopy(self.pin)
                pin['github_input']['manifest_sha256'] = hashlib.sha256(data).hexdigest()
                with self.assertRaises(inputs.InputError): inputs.manifest_bytes(data, pin)

    def test_chunk_paths_must_be_exact_ordered_bounded_inventory(self):
        for value in ('../000.bin', 'chunks/001.bin', 'chunks/private.p12', 'https://evil.test/file'):
            with self.subTest(path=value):
                altered = copy.deepcopy(self.manifest); altered['chunks'][0]['path'] = value
                data = json.dumps(altered).encode(); pin = copy.deepcopy(self.pin)
                pin['github_input']['manifest_sha256'] = hashlib.sha256(data).hexdigest()
                with self.assertRaises(inputs.InputError): inputs.manifest_bytes(data, pin)
        altered = copy.deepcopy(self.manifest); altered['chunks'][0]['bytes'] = inputs.MAX_CHUNK + 1
        data = json.dumps(altered).encode(); pin = copy.deepcopy(self.pin)
        pin['github_input']['manifest_sha256'] = hashlib.sha256(data).hexdigest()
        with self.assertRaises(inputs.InputError): inputs.manifest_bytes(data, pin)

    def test_missing_or_oversized_inventory_fails(self):
        for chunks in ([], self.manifest['chunks'][:1], self.manifest['chunks']*65):
            altered = copy.deepcopy(self.manifest); altered['chunks'] = chunks
            data = json.dumps(altered).encode(); pin = copy.deepcopy(self.pin)
            pin['github_input']['manifest_sha256'] = hashlib.sha256(data).hexdigest()
            with self.assertRaises(inputs.InputError): inputs.manifest_bytes(data, pin)

    def test_chunk_hash_or_size_tamper_never_publishes_partial_apk(self):
        url = inputs.RAW_ORIGIN+self.commit+'/chunks/001.bin'; original = self.values[url]
        for value in (original+b'x', original[:-1], b'X'*len(original)):
            with self.subTest(size=len(value)):
                self.values[url] = value
                with self.assertRaises(inputs.InputError): self.check()
                self.assertEqual(list(self.output.iterdir()), [])

    def test_compressed_hash_is_checked_even_when_individual_chunks_match(self):
        self.manifest['compressed']['sha256'] = '0'*64; self.refresh()
        with self.assertRaisesRegex(inputs.InputError, 'Compressed APK'): self.check()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_gzip_bomb_stops_at_apk_pin_size(self):
        self.pin['artifact']['bytes'] = 10; self.refresh()
        with self.assertRaisesRegex(inputs.InputError, 'Decompressed APK exceeds'): self.check()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_corrupt_gzip_is_rejected_after_its_transport_hash_is_verified(self):
        self.payload = b'not a gzip stream'; self.parts = [self.payload[:5], self.payload[5:]]
        self.manifest['compressed'] = self.row(self.payload)
        self.manifest['chunks'] = [self.row(value, path=f'chunks/{i:03d}.bin') for i, value in enumerate(self.parts)]
        self.refresh()
        with self.assertRaisesRegex(inputs.InputError, 'compressed APK'): self.check()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_wrong_user_cache_and_symlinks_are_preserved(self):
        target = self.output/self.pin['artifact']['file']; target.write_bytes(b'user bytes')
        with self.assertRaisesRegex(inputs.InputError, 'Existing APK'): self.check()
        self.assertEqual(target.read_bytes(), b'user bytes')
        target.unlink(); source = self.output/'source'; source.write_bytes(self.apk); target.symlink_to(source)
        with self.assertRaises(inputs.InputError): self.check()
        self.assertTrue(target.is_symlink()); self.assertEqual(source.read_bytes(), self.apk)

    def test_raw_source_cannot_redirect_or_use_credentials_query_fragment_or_http(self):
        path = 'signed-apk.json'; good = inputs.RAW_ORIGIN+self.commit+'/'+path
        inputs.allowed(good, commit=self.commit, path=path)
        for url in (good+'?token=secret', good+'#x', good.replace('https:', 'http:'),
                    good.replace('raw.githubusercontent.com', 'evil.test'),
                    good.replace('https://', 'https://user:secret@')):
            with self.subTest(url=url), self.assertRaises(inputs.InputError):
                inputs.allowed(url, commit=self.commit, path=path)
        with self.assertRaises(inputs.InputError):
            inputs.Redirects(self.commit, path).redirect_request(None, None, 302, 'Found', {}, 'https://evil.test/file')

    def test_windows_origin_is_exact_release_or_its_https_cdn(self):
        inputs.allowed(inputs.CONTROLLER_URL)
        inputs.allowed('https://release-assets.githubusercontent.com/asset?signed=x')
        for url in (inputs.CONTROLLER_URL.replace('v6.2.2', 'latest'),
                    'https://drive.google.com/file', 'https://github.com/other/repo/file',
                    'https://evil.test/asset', 'https://objects.githubusercontent.com:444/file'):
            with self.subTest(url=url), self.assertRaises(inputs.InputError): inputs.allowed(url)

    def test_html_or_malformed_length_fails_before_output(self):
        url = inputs.RAW_ORIGIN+self.commit+'/signed-apk.json'
        for options in ({'content_type': 'text/html'}, {'length': 'invalid'}, {'length': inputs.MAX_MANIFEST+1}):
            class BadOpener:
                def open(inner, request, timeout): return Response(self.body, url, **options)
            with self.subTest(options=options), self.assertRaises(inputs.InputError):
                inputs.signed_apk(self.pin, self.output, opener=BadOpener())
        self.assertEqual(list(self.output.iterdir()), [])


if __name__ == '__main__': unittest.main()
