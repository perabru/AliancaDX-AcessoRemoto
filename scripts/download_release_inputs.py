#!/usr/bin/env python3
"""Download only the pinned, public inputs for the AliançaDX 6.2.2 release.

No account, private API or browser cookies are used. Google Drive confirmation
HTML is parsed as data; it is never executed. Every binary must match its exact
size and SHA-256 before an atomic, non-overwriting publication to the cache.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
import http.cookiejar
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class Artifact:
    name: str
    filename: str
    file_id: str
    bytes: int
    sha256: str


ARTIFACTS = {
    "controller": Artifact("controller", "AliancaDX_Controlador_Setup_6.2.0.exe",
        "1f7FFuWFtlwJS-dY2iNdpWAA0TY900pRg", 132186817,
        "875a8dfb7c85c0efd2d20b04aa1f2e41173c17f2fa9a785aae80ce613ad35c5b"),
    "apk": Artifact("apk", "AliancaDX_Acesso_Remoto_6.2.2.apk",
        "1r7QwgsDlbiLbFPsD6zTuF3WQ9YFKWp9m", 34716022,
        "aafd1f38274da3e9580cf077e4f23b8c5ab79c8094c99b7b9cc1313c3298dd5f"),
}
FORM_HOSTS = {"drive.google.com", "drive.usercontent.google.com", "docs.google.com"}
MAX_HTML = 1 << 20
TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,512}\Z")


class DownloadError(RuntimeError):
    pass


def allowed_url(url: str, *, confirmation: bool = False) -> urllib.parse.SplitResult:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as error:
        raise DownloadError("Malformed download URL") from error
    try:
        valid_port = parsed.port in (None, 443)
    except ValueError:
        valid_port = False
    host = parsed.hostname or ""
    allowed_host = host in FORM_HOSTS or not confirmation and host.endswith(".googleusercontent.com")
    if (parsed.scheme != "https" or not allowed_host or not valid_port
            or parsed.username is not None or parsed.password is not None or parsed.fragment):
        raise DownloadError("Download destination is not an allowed HTTPS Google host")
    if confirmation and parsed.path not in ("/download", "/uc"):
        raise DownloadError("Unsupported Google Drive confirmation endpoint")
    return parsed


class GoogleRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def redirect_request(self, request, fp, code, message, headers, newurl):
        allowed_url(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


class ConfirmationHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.current = {"action": attributes.get("action", ""),
                            "method": (attributes.get("method") or "get").lower(), "fields": []}
            self.forms.append(self.current)
        elif tag == "input" and self.current is not None and attributes.get("type", "hidden").lower() == "hidden":
            if attributes.get("name"):
                self.current["fields"].append((attributes["name"], attributes.get("value") or ""))
        elif tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


def confirmation_candidate(action: str, fields: list[tuple[str, str]], base_url: str, file_id: str) -> str:
    parsed = allowed_url(urllib.parse.urljoin(base_url, action), confirmation=True)
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) + fields
    names = [name for name, _ in pairs]
    if len(names) != len(set(names)) or not set(names).issubset({"id", "export", "confirm", "uuid", "authuser"}):
        raise DownloadError("Ambiguous or unsupported confirmation fields")
    values = dict(pairs)
    if values.get("id") != file_id or values.get("export", "download") != "download":
        raise DownloadError("Confirmation does not refer to the pinned public file")
    if not TOKEN.fullmatch(values.get("confirm", "")):
        raise DownloadError("Missing or unsupported confirmation token")
    if "uuid" in values and not TOKEN.fullmatch(values["uuid"]):
        raise DownloadError("Unsupported confirmation identifier")
    if "authuser" in values and re.fullmatch(r"[0-9]{1,4}", values["authuser"]) is None:
        raise DownloadError("Unsupported account selector")
    values["export"] = "download"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(values), ""))


def confirmation_url(html: bytes, base_url: str, file_id: str, cookies=()) -> str:
    allowed_url(base_url)
    if len(html) > MAX_HTML:
        raise DownloadError("Google Drive returned oversized confirmation HTML")
    parser = ConfirmationHTML()
    parser.feed(html.decode("utf-8", "replace"))
    candidates = set()
    for form in parser.forms:
        if form["method"] != "get":
            continue
        try:
            candidates.add(confirmation_candidate(form["action"], form["fields"], base_url, file_id))
        except DownloadError:
            continue
    for href in parser.links:
        try:
            candidates.add(confirmation_candidate(href, [], base_url, file_id))
        except DownloadError:
            continue
    if not candidates:
        for cookie in cookies:
            if cookie.name.startswith("download_warning") and TOKEN.fullmatch(cookie.value):
                candidates.add("https://drive.google.com/uc?" + urllib.parse.urlencode(
                    {"export": "download", "id": file_id, "confirm": cookie.value}))
    if len(candidates) != 1:
        raise DownloadError("Public Google Drive download is unavailable or its confirmation is ambiguous")
    return candidates.pop()


def matching_file(path: Path, artifact: Artifact) -> bool:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != artifact.bytes:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest() == artifact.sha256


def download(artifact: Artifact, output: Path, *, opener=None, cookies=None) -> dict:
    if artifact.bytes <= 0 or re.fullmatch(r"[0-9a-f]{64}", artifact.sha256) is None:
        raise DownloadError("Release input pin is not frozen: " + artifact.filename)
    output.mkdir(parents=True, exist_ok=True)
    target = output / artifact.filename
    if target.exists() or target.is_symlink():
        if matching_file(target, artifact):
            return {"file": artifact.filename, "bytes": artifact.bytes, "sha256": artifact.sha256, "cached": True}
        raise DownloadError("Existing cache file differs from its pin; it was not overwritten: " + artifact.filename)
    if cookies is None:
        cookies = http.cookiejar.CookieJar()
    if opener is None:
        opener = urllib.request.build_opener(GoogleRedirects(), urllib.request.HTTPCookieProcessor(cookies))
    url = "https://drive.usercontent.google.com/download?" + urllib.parse.urlencode(
        {"id": artifact.file_id, "export": "download", "confirm": "t"})
    visited = set()
    temporary = None
    try:
        for _ in range(4):
            allowed_url(url)
            if url in visited:
                raise DownloadError("Google Drive confirmation loop")
            visited.add(url)
            request = urllib.request.Request(url, headers={"User-Agent": "AliancaDX-release-download/6.2.2",
                "Accept-Encoding": "identity"})
            with opener.open(request, timeout=60) as response:
                allowed_url(response.geturl())
                first = response.read(8192)
                content_type = response.headers.get("Content-Type", "").lower()
                if "text/html" in content_type or first.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                    html = first + response.read(MAX_HTML + 1 - len(first))
                    url = confirmation_url(html, response.geturl(), artifact.file_id, cookies)
                    continue
                length = response.headers.get("Content-Length")
                if length is not None:
                    try:
                        if int(length) != artifact.bytes:
                            raise DownloadError("Public file size differs from the pinned release input")
                    except ValueError as error:
                        raise DownloadError("Invalid public download length") from error
                descriptor, filename = tempfile.mkstemp(prefix=".aliancadx-download-", suffix=".part", dir=output)
                temporary = Path(filename)
                os.fchmod(descriptor, 0o644)
                digest = hashlib.sha256()
                count = 0
                with os.fdopen(descriptor, "wb") as stream:
                    block = first
                    while block:
                        count += len(block)
                        if count > artifact.bytes:
                            raise DownloadError("Public download exceeds its pinned size")
                        digest.update(block)
                        stream.write(block)
                        block = response.read(1 << 20)
                    stream.flush()
                    os.fsync(stream.fileno())
                if count != artifact.bytes or digest.hexdigest() != artifact.sha256:
                    raise DownloadError("Public download failed its exact size/SHA-256 pin")
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    if not matching_file(target, artifact):
                        raise DownloadError("A different cache file appeared; it was not overwritten")
                return {"file": artifact.filename, "bytes": count, "sha256": digest.hexdigest(), "cached": False}
        raise DownloadError("Google Drive exceeded the confirmation limit")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise DownloadError("Public download failed (" + type(error).__name__ + "): " + artifact.filename) from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", choices=("all", "controller", "apk"), default="all")
    args = parser.parse_args()
    try:
        selected = ARTIFACTS.values() if args.only == "all" else (ARTIFACTS[args.only],)
        results = [download(artifact, args.output) for artifact in selected]
    except DownloadError as error:
        parser.exit(1, "Release input download stopped: " + str(error) + "\n")
    print(json.dumps({"public_inputs_verified": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
