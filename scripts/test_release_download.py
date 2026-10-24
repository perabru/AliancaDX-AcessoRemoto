"""Pinned public-download behavior, without account access or live network."""
from pathlib import Path
import hashlib
import importlib.util
import io
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('release_inputs', Path(__file__).with_name('download_release_inputs.py'))
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class Response(io.BytesIO):
    def __init__(self, data, url, content_type='application/octet-stream', length=None):
        super().__init__(data)
        self.url = url
        self.headers = {'Content-Type': content_type}
        if length is not None:
            self.headers['Content-Length'] = str(length)
    def geturl(self): return self.url


class Opener:
    def __init__(self, responses): self.responses = responses; self.requests = []
    def open(self, request, timeout):
        self.requests.append(request.full_url)
        return self.responses.pop(0)


class PublicDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.body = b'MZ pinned installer fixture'
        self.artifact = module.Artifact('fixture', 'fixture.exe', 'EXPECTED_ID', len(self.body), hashlib.sha256(self.body).hexdigest())
        self.url = 'https://drive.usercontent.google.com/download?id=EXPECTED_ID&export=download&confirm=t'

    def form(self, file_id='EXPECTED_ID', action='https://drive.usercontent.google.com/download'):
        return (f'<form method="get" action="{action}"><input type="hidden" name="id" value="{file_id}">'
                '<input type="hidden" name="export" value="download"><input type="hidden" name="confirm" value="t">'
                '<input type="hidden" name="uuid" value="1234-abcd"></form>').encode()

    def test_confirmation_form_preserves_only_expected_public_file(self):
        url = module.confirmation_url(self.form(), self.url, 'EXPECTED_ID')
        self.assertIn('id=EXPECTED_ID', url)
        self.assertIn('uuid=1234-abcd', url)

    def test_non_google_insecure_credential_or_unusual_port_destination_rejected(self):
        for url in ('http://drive.google.com/uc', 'https://drive.google.com.evil.test/uc',
                    'https://evil.test/uc', 'https://user@drive.google.com/uc', 'https://drive.google.com:444/uc', 'https://[invalid'):
            with self.subTest(url=url), self.assertRaises(module.DownloadError): module.allowed_url(url)

    def test_redirect_handler_rejects_non_google_before_following(self):
        with self.assertRaises(module.DownloadError):
            module.GoogleRedirects().redirect_request(None, None, 302, 'Found', {}, 'https://evil.test/file')

    def test_mismatching_id_or_form_action_is_not_followed(self):
        for html in (self.form(file_id='WRONG'), self.form(action='https://evil.test/download'),
                     self.form(action='https://drive.usercontent.google.com/other'),
                     self.form(action='https://drive.usercontent.google.com/download?id=EXPECTED_ID')):
            with self.subTest(html=html), self.assertRaises(module.DownloadError):
                module.confirmation_url(html, self.url, 'EXPECTED_ID')

    def test_legacy_relative_confirmation_link_is_supported(self):
        html = b'<a href="/uc?export=download&amp;id=EXPECTED_ID&amp;confirm=ABC_123">Download</a>'
        self.assertIn('confirm=ABC_123', module.confirmation_url(html, 'https://drive.google.com/uc', 'EXPECTED_ID'))

    def test_ambiguous_confirmation_and_oversized_html_are_refused(self):
        other = self.form().replace(b'1234-abcd', b'5678-efgh')
        for html in (self.form() + other, b'x' * (module.MAX_HTML + 1), b'<html>Please sign in</html>'):
            with self.subTest(size=len(html)), self.assertRaises(module.DownloadError):
                module.confirmation_url(html, self.url, 'EXPECTED_ID')

    def test_binary_pin_is_verified_before_cache_publication_and_reused(self):
        opener = Opener([Response(self.body, self.url, length=len(self.body))])
        result = module.download(self.artifact, self.folder, opener=opener)
        self.assertFalse(result['cached'])
        self.assertEqual((self.folder/'fixture.exe').read_bytes(), self.body)
        cached = module.download(self.artifact, self.folder, opener=Opener([]))
        self.assertTrue(cached['cached'])
        self.assertEqual(len(opener.requests), 1)

    def test_wrong_existing_cache_is_preserved(self):
        path = self.folder/'fixture.exe'; path.write_bytes(b'user-owned existing file')
        with self.assertRaises(module.DownloadError): module.download(self.artifact, self.folder, opener=Opener([]))
        self.assertEqual(path.read_bytes(), b'user-owned existing file')

    def test_wrong_size_or_hash_never_leaves_output_or_partial(self):
        for body in (self.body+b'x', self.body[:-1], b'X'*len(self.body)):
            with self.subTest(body=body), self.assertRaises(module.DownloadError):
                module.download(self.artifact, self.folder, opener=Opener([Response(body, self.url)]))
            self.assertEqual(list(self.folder.iterdir()), [])

    def test_invalid_content_length_fails_without_creating_cache(self):
        for length in ('not-a-number', len(self.body)+1):
            with self.subTest(length=length), self.assertRaises(module.DownloadError):
                module.download(self.artifact, self.folder, opener=Opener([Response(self.body, self.url, length=length)]))
            self.assertEqual(list(self.folder.iterdir()), [])

    def test_html_confirmation_is_parsed_then_binary_pin_is_applied(self):
        opener = Opener([Response(self.form(), self.url, 'text/html'), Response(self.body, self.url)])
        module.download(self.artifact, self.folder, opener=opener)
        self.assertEqual(len(opener.requests), 2)
        self.assertIn('uuid=1234-abcd', opener.requests[1])

    def test_invalid_final_response_host_cannot_publish_bytes(self):
        with self.assertRaises(module.DownloadError):
            module.download(self.artifact, self.folder, opener=Opener([Response(self.body, 'https://evil.test/file')]))
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_publication_race_preserves_foreign_file(self):
        def appear(source, target):
            Path(target).write_bytes(b'foreign race file')
            raise FileExistsError()
        with patch.object(module.os, 'link', appear), self.assertRaises(module.DownloadError):
            module.download(self.artifact, self.folder, opener=Opener([Response(self.body, self.url)]))
        self.assertEqual((self.folder/'fixture.exe').read_bytes(), b'foreign race file')
        self.assertEqual(len(list(self.folder.iterdir())), 1)

    def test_archived_apk_uses_immutable_release_and_checks_its_pin(self):
        opener = Opener([Response(self.body, module.ARCHIVED_APK_URL, length=len(self.body))])
        with patch.dict(module.ARTIFACTS, {'apk': self.artifact}):
            result = module.download(self.artifact, self.folder, opener=opener)
        self.assertFalse(result['cached'])
        self.assertEqual(opener.requests, [module.ARCHIVED_APK_URL])
        self.assertEqual((self.folder/'fixture.exe').read_bytes(), self.body)

    def test_archived_apk_allows_only_exact_release_or_official_asset_cdn(self):
        module.allowed_archive_url(module.ARCHIVED_APK_URL)
        module.allowed_archive_url('https://release-assets.githubusercontent.com/github-production-release-asset/file?token=public')
        for url in ('https://github.com/another/repo/releases/download/v1/file.apk',
                    'https://evil.test/file', 'http://objects.githubusercontent.com/file',
                    'https://user@release-assets.githubusercontent.com/file',
                    'https://objects.githubusercontent.com:444/file'):
            with self.subTest(url=url), self.assertRaises(module.DownloadError):
                module.allowed_archive_url(url)

    def test_archive_redirect_is_checked_before_following(self):
        with self.assertRaises(module.DownloadError):
            module.ArchiveRedirects().redirect_request(None,None,302,'Found',{},'https://evil.test/file')

    def test_archive_tamper_html_or_wrong_length_never_publishes(self):
        for body,media,length in ((b'X'*len(self.body),'application/octet-stream',None),
                                  (self.body,'text/html',None),
                                  (self.body,'application/octet-stream',len(self.body)+1)):
            with self.subTest(media=media,length=length), patch.dict(module.ARTIFACTS, {'apk':self.artifact}), self.assertRaises(module.DownloadError):
                module.download(self.artifact,self.folder,opener=Opener([Response(body,module.ARCHIVED_APK_URL,media,length)]))
            self.assertEqual(list(self.folder.iterdir()),[])


if __name__ == '__main__': unittest.main()
