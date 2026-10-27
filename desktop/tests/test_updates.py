"""Read-only update checks, strict publisher boundaries and safe failure states."""
import copy
from dataclasses import FrozenInstanceError
from email.message import Message
import io
import json
from pathlib import Path
import socket
import ssl
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPSHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import updates


def release(version="7.4.0", role="controller"):
    name = updates.ASSET_NAMES[role]
    return {"tag_name": "v" + version, "draft": False, "prerelease": False,
            "assets": [{"name": name, "size": 132_000_000, "state": "uploaded",
                        "browser_download_url": "https://github.com/" + updates.REPOSITORY + "/releases/download/v" + version + "/" + name}]}


class Response(io.BytesIO):
    def __init__(self, data, *, url=updates.LATEST_URL, status=200, headers=None):
        super().__init__(data)
        self.url, self.status = url, status
        self.headers = Message()
        for name, value in ((headers or {"Content-Type": "application/json"}).items()):
            if isinstance(value, tuple):
                for item in value:
                    self.headers[name] = item
            else:
                self.headers[name] = value
        self.read_calls = []

    def geturl(self):
        return self.url

    def read1(self, count=-1):
        self.read_calls.append(count)
        return super().read1(count)


def query(value, *, current="7.3.0", role="controller", **response_kwargs):
    data = value if isinstance(value, bytes) else json.dumps(value).encode()
    response = Response(data, **response_kwargs)
    opener = Mock(return_value=response)
    result = updates.check_latest(current, role, opener=opener)
    return result, response, opener


class UpdateCheckTests(unittest.TestCase):
    def test_new_public_release_offers_exact_controller_asset_only(self):
        result, response, opener = query(release())
        self.assertEqual(result.status, "available")
        self.assertEqual(result.installed_version, "7.3.0")
        self.assertEqual(result.latest_version, "7.4.0")
        self.assertEqual(result.download_url, release()["assets"][0]["browser_download_url"])
        self.assertTrue(response.closed)
        self.assertEqual(opener.call_count, 1)

    def test_host_role_selects_host_installer_from_mixed_inventory(self):
        value = release()
        value["assets"].extend(release(role="host")["assets"])
        result, _, _ = query(value, role="host")
        self.assertEqual(result.status, "available")
        self.assertTrue(result.download_url.endswith("AliancaDX_Host_Setup.exe"))

    def test_equal_version_never_offers_a_redundant_download(self):
        result, _, _ = query(release(), current="7.4.0")
        self.assertEqual(result.status, "current")
        self.assertEqual(result.latest_version, "7.4.0")
        self.assertIsNone(result.download_url)

    def test_installed_newer_version_never_offers_a_downgrade(self):
        result, _, _ = query(release(), current="7.5.0")
        self.assertEqual(result.status, "ahead")
        self.assertIsNone(result.download_url)

    def test_comparison_uses_numeric_components_not_lexicographic_order(self):
        result, _, _ = query(release("7.10.0"), current="7.9.9")
        self.assertEqual(result.status, "available")
        self.assertEqual(result.latest_version, "7.10.0")
        result, _, _ = query(release("8.0.0"), current="7.999.999")
        self.assertEqual(result.status, "available")

    def test_only_public_literal_stable_flags_are_accepted(self):
        for field in ("draft", "prerelease"):
            for invalid in (None, True, 0, 1, "false", [], {}):
                with self.subTest(field=field, value=invalid):
                    value = release(); value[field] = invalid
                    result, _, _ = query(value)
                    self.assertEqual(result.status, "error")
                    self.assertIsNone(result.download_url)
            value = release(); del value[field]
            self.assertEqual(query(value)[0].status, "error")

    def test_only_canonical_stable_semver_tags_are_accepted(self):
        for tag in ("7.4.0", "V7.4.0", "v07.4.0", "v7.04.0", "v7.4", "v7.4.0-rc.1", "v7.4.0+build", "v7.4.0\n", "v７.4.0", "v7.4.0/else", None, True):
            with self.subTest(tag=tag):
                value = release(); value["tag_name"] = tag
                result, _, _ = query(value)
                self.assertEqual(result.status, "error")
                self.assertIsNone(result.download_url)

    def test_invalid_installed_version_or_role_never_starts_network(self):
        for current, role in (("unknown", "controller"), ("v7.4.0", "controller"), ("07.4.0", "controller"),
                              ("7.4.0", "android"), ("7.4.0", []), (None, "host"), (True, "host")):
            with self.subTest(current=current, role=role):
                opener = Mock()
                result = updates.check_latest(current, role, opener=opener)
                self.assertEqual(result.status, "error")
                opener.assert_not_called()

    def test_installer_url_is_exact_without_encoded_paths_or_other_publishers(self):
        good = release()["assets"][0]["browser_download_url"]
        variants = (good.replace("https:", "http:"), good.replace("github.com/", "github.com.evil.example/"),
                    good.replace("github.com/", "user:pass@github.com/"), good.replace("github.com/", "github.com:443/"),
                    good.replace("perabru/", "other/"), good.replace("AliancaDX-AcessoRemoto/", "another-repo/"),
                    good.replace("v7.4.0/", "v7.5.0/"), good.replace("releases/download/", "releases%2fdownload/"),
                    good.replace("Setup.exe", "Setup%2eexe"), good + "?token=value", good + "#fragment", good + "\n",
                    " " + good, "file:///tmp/app.exe", "javascript:alert(1)", None, True)
        for url in variants:
            with self.subTest(url=url):
                value = release(); value["assets"][0]["browser_download_url"] = url
                result, _, _ = query(value)
                self.assertEqual(result.status, "error")
                self.assertFalse(updates.valid_download_url(url, "7.4.0", "controller"))

    def test_asset_name_state_size_and_inventory_are_validated(self):
        for field, values in (("name", ("AliancaDX_Host_Setup.exe", "aliancadx_controlador_setup.exe", None)),
                              ("state", ("new", "uploading", True, None)),
                              ("size", (0, -1, True, "132000000", 4 * 1024 ** 3 + 1, None))):
            for invalid in values:
                with self.subTest(field=field, value=invalid):
                    value = release(); value["assets"][0][field] = invalid
                    self.assertEqual(query(value)[0].status, "error")
        for assets in (None, {}, [], [None], [release()["assets"][0]] * 2,
                       [release()["assets"][0]] * (updates.MAX_ASSETS + 1)):
            value = release(); value["assets"] = assets
            self.assertEqual(query(value)[0].status, "error")

    def test_duplicate_json_fields_are_rejected_even_inside_an_asset(self):
        bodies = (b'{"tag_name":"v7.4.0","tag_name":"v9.0.0","draft":false,"prerelease":false,"assets":[]}',
                  json.dumps(release()).replace('"size": 132000000', '"size": 132000000, "size": 1').encode())
        for body in bodies:
            self.assertEqual(query(body)[0].status, "error")

    def test_invalid_json_utf8_nonfinite_or_top_level_type_is_rejected(self):
        for body in (b"not JSON", b"\xff", b"[]", b"null", b'{"value": NaN}', b'{"value": Infinity}', b"", b"{}"):
            with self.subTest(body=body):
                result, response, _ = query(body)
                self.assertEqual(result.status, "error")
                self.assertIsNone(result.download_url)
                self.assertTrue(response.closed)

    def test_http_response_must_come_from_the_exact_https_api_endpoint(self):
        for url in (updates.LATEST_URL.replace("https:", "http:"), updates.LATEST_URL + "?page=1", updates.LATEST_URL + "#x",
                    "https://api.github.com/repos/other/project/releases/latest", "https://evil.example/release", None):
            with self.subTest(url=url):
                self.assertEqual(query(release(), url=url)[0].status, "error")
        for status in (201, 204, 304, True, "200"):
            self.assertEqual(query(release(), status=status)[0].status, "error")

    def test_content_type_and_encoding_must_be_unambiguous_json(self):
        for headers in ({"Content-Type": "text/html"}, {"Content-Type": "application/octet-stream"},
                        {"Content-Type": "application/json", "Content-Encoding": "gzip"},
                        {"Content-Type": ("application/json", "text/html")}):
            self.assertEqual(query(release(), headers=headers)[0].status, "error")
        for content_type in ("application/json; charset=utf-8", "Application/Json", "application/vnd.github+json"):
            self.assertEqual(query(release(), headers={"Content-Type": content_type})[0].status, "available")

    def test_invalid_or_ambiguous_content_length_fails_before_excessive_reads(self):
        for length in ("0", "-1", "hello", "1.0", str(updates.MAX_BODY_BYTES + 1), ("10", "11")):
            result, response, _ = query(release(), headers={"Content-Type": "application/json", "Content-Length": length})
            self.assertEqual(result.status, "error")
            self.assertEqual(response.read_calls, [])
        self.assertEqual(query(release(), headers={"Content-Type": "application/json", "Content-Length": "1"})[0].status, "error")
        body = json.dumps(release()).encode()
        self.assertEqual(query(body, headers={"Content-Type": "application/json", "Content-Length": str(len(body))})[0].status, "available")

    def test_body_without_content_length_is_still_bounded(self):
        result, response, _ = query(b"x" * (updates.MAX_BODY_BYTES + 100))
        self.assertEqual(result.status, "error")
        self.assertTrue(response.closed)
        self.assertLessEqual(sum(response.read_calls), updates.MAX_BODY_BYTES + 1)

    def test_manual_check_uses_one_read_only_request_and_no_credentials(self):
        _, _, opener = query(release())
        request = opener.call_args.args[0]
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertEqual(request.full_url, updates.LATEST_URL)
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(opener.call_args.kwargs["timeout"], updates.REQUEST_TIMEOUT_SECONDS)
        self.assertEqual(headers["accept-encoding"], "identity")
        self.assertEqual(headers["accept"], "application/vnd.github+json")
        self.assertNotIn("authorization", headers)
        self.assertNotIn("cookie", headers)

    def test_default_opener_keeps_certificate_and_hostname_validation(self):
        director = SimpleDirector()
        with patch.object(updates, "build_opener", return_value=director) as build:
            response = updates._open_release(Request(updates.LATEST_URL), timeout=3)
        self.assertIs(response, director.response)
        handlers = build.call_args.args
        https = next(handler for handler in handlers if isinstance(handler, HTTPSHandler))
        self.assertTrue(https._context.check_hostname)
        self.assertEqual(https._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(https._context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertTrue(any(isinstance(handler, updates._StrictRedirects) for handler in handlers))

    def test_redirect_handler_rejects_external_downgraded_and_circular_targets(self):
        handler = updates._StrictRedirects()
        for url in ("https://evil.example/", "http://api.github.com/", "file:///etc/passwd", updates.LATEST_URL):
            with self.assertRaises(ValueError):
                handler.redirect_request(Request(updates.LATEST_URL), None, 302, "moved", {}, url)

    def test_timeout_offline_and_tls_failures_have_distinct_safe_results(self):
        for error, status in ((TimeoutError(), "timeout"), (socket.timeout(), "timeout"),
                              (URLError(TimeoutError()), "timeout"), (URLError(socket.gaierror()), "offline"),
                              (ConnectionError(), "offline"), (ssl.SSLCertVerificationError(), "error"),
                              (URLError(ssl.SSLCertVerificationError()), "error"), (RuntimeError(), "error")):
            with self.subTest(error=type(error).__name__):
                result = updates.check_latest("7.3.0", "host", opener=Mock(side_effect=error))
                self.assertEqual(result.status, status)
                self.assertIsNone(result.download_url)
                self.assertIsNone(result.latest_version)

    def test_rate_limits_and_http_errors_are_distinguished_without_reading_error_body(self):
        for code, headers, status in ((429, {}, "limited"), (403, {"X-RateLimit-Remaining": "0"}, "limited"),
                                      (403, {"Retry-After": "60"}, "limited"), (403, {}, "error"),
                                      (404, {}, "error"), (500, {}, "error")):
            with self.subTest(code=code, headers=headers):
                body = io.BytesIO(b"untrusted remote explanation")
                error = HTTPError(updates.LATEST_URL, code, "HTTP failure", headers, body)
                result = updates.check_latest("7.3.0", "host", opener=Mock(side_effect=error))
                self.assertEqual(result.status, status)
                self.assertIsNone(result.download_url)
                self.assertNotIn("untrusted", result.message)
                self.assertTrue(body.closed)

    def test_response_deadline_prevents_continuous_reads_from_running_indefinitely(self):
        response = Response(b"x" * 100000)
        with patch.object(updates.time, "monotonic", side_effect=(0, 0, updates.REQUEST_TIMEOUT_SECONDS + 1)):
            result = updates.check_latest("7.3.0", "host", opener=Mock(return_value=response))
        self.assertEqual(result.status, "timeout")
        self.assertTrue(response.closed)
        self.assertEqual(len(response.read_calls), 1)

    def test_returned_results_are_immutable_and_only_newer_release_has_browser_url(self):
        for current, status in (("7.3.0", "available"), ("7.4.0", "current"), ("8.0.0", "ahead")):
            result = query(release(), current=current)[0]
            self.assertEqual(result.status, status)
            self.assertEqual(result.download_url is not None, status == "available")
            with self.assertRaises(FrozenInstanceError):
                result.status = "available"

    def test_public_url_helper_revalidates_role_version_and_exact_download_path(self):
        for role in ("controller", "host"):
            url = release(role=role)["assets"][0]["browser_download_url"]
            self.assertTrue(updates.valid_download_url(url, "7.4.0", role))
            self.assertFalse(updates.valid_download_url(url, "7.5.0", role))
            self.assertFalse(updates.valid_download_url(url, "7.4.0", "host" if role == "controller" else "controller"))
        for version, role in ((None, "host"), ("7.4.0", []), ("v7.4.0", "host"), ("7.4.0", "unknown")):
            self.assertFalse(updates.valid_download_url("https://github.com/", version, role))


class SimpleDirector:
    def __init__(self):
        self.response = object()
        self.open = Mock(return_value=self.response)


if __name__ == "__main__":
    unittest.main()
