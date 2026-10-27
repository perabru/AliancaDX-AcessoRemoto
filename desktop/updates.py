"""Read-only discovery of public releases and their immutable asset digest.

Installation is an explicit UI action implemented by update_install.py.
"""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import json
import re
import socket
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


REPOSITORY = "perabru/AliancaDX-AcessoRemoto"
LATEST_URL = "https://api.github.com/repos/" + REPOSITORY + "/releases/latest"
API_VERSION = "2026-03-10"
REQUEST_TIMEOUT_SECONDS = 8.0
MAX_BODY_BYTES = 1024 * 1024
MAX_ASSETS = 256
ASSET_NAMES = {"controller": "AliancaDX_Controlador_Setup.exe", "host": "AliancaDX_Host_Setup.exe"}
_VERSION = re.compile(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\Z", re.ASCII)


@dataclass(frozen=True)
class UpdateResult:
    status: str
    installed_version: str
    latest_version: str | None
    message: str
    download_url: str | None = None
    download_size: int | None = None
    sha256: str | None = None


class _InvalidRelease(ValueError):
    pass


class _StrictRedirects(HTTPRedirectHandler):
    """The canonical, fixed /latest endpoint needs no redirects.

    Fail closed for both a different destination and a circular redirect.
    In particular a moved repository cannot silently select another publisher.
    """
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise _InvalidRelease("Unexpected update endpoint redirect")


def _open_release(request, *, timeout):
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_alpn_protocols(["http/1.1"])
    opener = build_opener(HTTPSHandler(context=context), _StrictRedirects())
    return opener.open(request, timeout=timeout)


def _version(value):
    if not isinstance(value, str) or len(value) > 32:
        raise _InvalidRelease("Invalid stable version")
    match = _VERSION.fullmatch(value)
    if match is None:
        raise _InvalidRelease("Invalid stable version")
    return tuple(int(part) for part in match.groups())


def valid_download_url(url, version, role):
    """Revalidate a browser target immediately before an explicit UI action."""
    if not isinstance(url, str):
        return False
    try:
        _version(version)
        name = ASSET_NAMES[role]
    except (ValueError, TypeError, KeyError):
        return False
    expected = "https://github.com/" + REPOSITORY + "/releases/download/v" + version + "/" + name
    return url == expected


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidRelease("Duplicate JSON field")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise _InvalidRelease("Nonfinite JSON number")


def _header(headers, name):
    if headers is None:
        return None
    values = headers.get_all(name) if hasattr(headers, "get_all") else None
    if values is not None:
        if len(values) != 1:
            raise _InvalidRelease("Ambiguous HTTP header")
        return values[0]
    if isinstance(headers, dict):
        matching = [v for k, v in headers.items() if isinstance(k, str) and k.lower() == name.lower()]
        if len(matching) > 1:
            raise _InvalidRelease("Ambiguous HTTP header")
        return matching[0] if matching else None
    return headers.get(name)


def _read_body(response, deadline):
    if response.geturl() != LATEST_URL:
        raise _InvalidRelease("Unexpected final endpoint")
    status = response.status
    if type(status) is not int or status != 200:
        raise _InvalidRelease("Unexpected HTTP status")
    content_type = _header(response.headers, "Content-Type")
    if (not isinstance(content_type, str) or
            content_type.split(";", 1)[0].strip().lower() not in ("application/json", "application/vnd.github+json")):
        raise _InvalidRelease("Unexpected content type")
    encoding = _header(response.headers, "Content-Encoding")
    if encoding is not None and (not isinstance(encoding, str) or encoding.strip().lower() != "identity"):
        raise _InvalidRelease("Unexpected content encoding")
    length = _header(response.headers, "Content-Length")
    if length is not None:
        if not isinstance(length, str) or re.fullmatch(r"[0-9]{1,10}", length, re.ASCII) is None:
            raise _InvalidRelease("Invalid response length")
        length = int(length)
        if not 1 <= length <= MAX_BODY_BYTES:
            raise _InvalidRelease("Response too large")
    reader = getattr(response, "read1", response.read)
    body = bytearray()
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError("Update deadline expired")
        chunk = reader(min(65536, MAX_BODY_BYTES + 1 - len(body)))
        if time.monotonic() >= deadline:
            raise TimeoutError("Update deadline expired")
        if not isinstance(chunk, bytes):
            raise _InvalidRelease("Invalid response bytes")
        if not chunk:
            break
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise _InvalidRelease("Response too large")
    if not body or length is not None and len(body) != length:
        raise _InvalidRelease("Incomplete response")
    return bytes(body)


def _release_asset(body, role):
    release = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    if not isinstance(release, dict) or release.get("draft") is not False or release.get("prerelease") is not False:
        raise _InvalidRelease("Release is not public and stable")
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise _InvalidRelease("Missing version tag")
    latest_version = tag[1:]
    latest = _version(latest_version)
    assets = release.get("assets")
    if not isinstance(assets, list) or not 1 <= len(assets) <= MAX_ASSETS:
        raise _InvalidRelease("Invalid asset inventory")
    name = ASSET_NAMES[role]
    candidates = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == name]
    if len(candidates) != 1:
        raise _InvalidRelease("Missing or ambiguous installer")
    asset = candidates[0]
    if asset.get("state") != "uploaded" or type(asset.get("size")) is not int or not 1 <= asset["size"] <= 4 * 1024 ** 3:
        raise _InvalidRelease("Installer is not completely uploaded")
    expected_url = "https://github.com/" + REPOSITORY + "/releases/download/" + tag + "/" + name
    if not valid_download_url(asset.get("browser_download_url"), latest_version, role):
        raise _InvalidRelease("Installer does not belong to this project release")
    digest = asset.get("digest")
    digest = digest[7:] if isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest, re.ASCII) else None
    return latest_version, latest, expected_url, asset["size"], digest


def _failure(status, installed, message):
    return UpdateResult(status, installed, None, message)


def _http_failure(error, installed):
    remaining = _header(error.headers, "X-RateLimit-Remaining")
    retry_after = _header(error.headers, "Retry-After")
    if error.code == 429 or error.code == 403 and (remaining == "0" or retry_after is not None):
        return _failure("limited", installed, "O GitHub atingiu o limite de consultas. Tente novamente mais tarde.")
    if error.code == 404:
        return _failure("error", installed, "Não foi encontrada uma versão pública para este programa no GitHub.")
    if error.code == 403:
        return _failure("error", installed, "O GitHub não permitiu consultar as atualizações. Tente novamente mais tarde.")
    return _failure("error", installed, "O GitHub está indisponível no momento. Tente novamente mais tarde.")


def check_latest(installed_version: str, role: str, *, opener=None) -> UpdateResult:
    """Check once, in the caller's worker thread, without changing app state.

    ``opener`` is an optional callable accepting ``(Request, timeout=seconds)``
    for isolated tests. Production always uses a certificate-validating HTTPS
    opener and rejects redirects. No credentials or machine names are sent.
    """
    installed = installed_version if isinstance(installed_version, str) else ""
    try:
        current = _version(installed)
        if role not in ASSET_NAMES:
            raise _InvalidRelease("Unknown application role")
    except (ValueError, TypeError):
        return _failure("error", installed, "Não foi possível identificar a versão instalada deste programa.")
    request = Request(LATEST_URL, headers={"Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION, "User-Agent": "AliancaDX-UpdateCheck/1",
        "Accept-Encoding": "identity", "Cache-Control": "no-cache"}, method="GET")
    try:
        deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
        with (opener or _open_release)(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = _read_body(response, deadline)
        latest_version, latest, url, size, digest = _release_asset(body, role)
        if latest > current:
            return UpdateResult("available", installed, latest_version,
                                f"A versão {latest_version} está disponível para este programa.", url, size, digest)
        if latest == current:
            return UpdateResult("current", installed, latest_version, "Você já está usando a versão mais recente.")
        return UpdateResult("ahead", installed, latest_version, "A versão instalada é mais recente que a última publicada.")
    except HTTPError as error:
        try:
            return _http_failure(error, installed)
        except (ValueError, TypeError):
            return _failure("error", installed, "Não foi possível validar a resposta do GitHub.")
        finally:
            try:
                error.close()
            except OSError:
                pass
    except (TimeoutError, socket.timeout):
        return _failure("timeout", installed, "A consulta demorou mais que o esperado. Tente novamente.")
    except URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            return _failure("timeout", installed, "A consulta demorou mais que o esperado. Tente novamente.")
        if isinstance(error.reason, ssl.SSLError):
            return _failure("error", installed, "A conexão segura com o GitHub não pôde ser validada. Tente novamente mais tarde.")
        return _failure("offline", installed, "Não foi possível acessar o GitHub. Confira sua conexão e tente novamente.")
    except ssl.SSLError:
        return _failure("error", installed, "A conexão segura com o GitHub não pôde ser validada. Tente novamente mais tarde.")
    except OSError:
        return _failure("offline", installed, "Não foi possível acessar o GitHub. Confira sua conexão e tente novamente.")
    except Exception:
        return _failure("error", installed, "Não foi possível conferir uma atualização válida no GitHub.")
