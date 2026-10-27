#!/usr/bin/env python3
"""Pinned GitHub-only build inputs; never execute an installer or use Drive.

The owner-signed APK is gzip-compressed into bounded immutable Git blobs on a
dedicated input branch. The source release pins that branch's concrete commit
and manifest hash. Each chunk, compressed stream and reconstructed APK must
match its exact size/SHA-256 before atomic cache publication.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import urllib.parse
import urllib.request

REPOSITORY = "perabru/AliancaDX-AcessoRemoto"
RAW_ORIGIN = "https://raw.githubusercontent.com/" + REPOSITORY + "/"
CONTROLLER_FILENAME = "AliancaDX_Controlador_Setup_6.2.2.exe"
CONTROLLER = {"file": CONTROLLER_FILENAME, "bytes": 132193419,
              "sha256": "d9acdef77380618b3b8415e30602cbad268afc981f332e8ea3034ca1279592ba"}
CONTROLLER_URL = ("https://github.com/" + REPOSITORY +
                  "/releases/download/v6.2.2/AliancaDX_Controlador_Setup.exe")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
SHA = re.compile(r"[0-9a-f]{64}\Z")
CHUNK = re.compile(r"chunks/[0-9]{3}\.bin\Z")
MAX_MANIFEST = 64 * 1024
MAX_COMPRESSED = 64 * 1024 * 1024
MAX_APK = 100 * 1024 * 1024
MAX_CHUNK = 512 * 1024


class InputError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise InputError(message)


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def pin_row(row, *, maximum=MAX_APK):
    require(isinstance(row, dict) and type(row.get('bytes')) is int
            and 0 < row['bytes'] <= maximum and isinstance(row.get('sha256'), str)
            and SHA.fullmatch(row['sha256']), 'Invalid exact size/SHA-256 pin')
    return row


def matching(path, row):
    return (path.is_file() and not path.is_symlink()
            and path.stat().st_size == row['bytes'] and digest(path) == row['sha256'])


def allowed(url, *, commit=None, path=None):
    try:
        value = urllib.parse.urlsplit(url)
        safe = (value.scheme == 'https' and value.port in (None, 443)
                and value.username is None and value.password is None
                and not value.fragment)
    except ValueError as error:
        raise InputError('Malformed GitHub input URL') from error
    require(safe, 'Insecure or credential-bearing GitHub input URL')
    if commit is not None:
        require(url == RAW_ORIGIN + commit + '/' + path,
                'APK input left its exact immutable GitHub object URL')
    else:
        require(value.hostname in {'github.com', 'release-assets.githubusercontent.com',
                                   'objects.githubusercontent.com'}
                and (value.hostname != 'github.com' or url == CONTROLLER_URL),
                'Windows input left the exact GitHub release/CDN origins')


class Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def __init__(self, commit=None, path=None):
        super().__init__(); self.commit, self.path = commit, path

    def redirect_request(self, request, fp, code, message, headers, newurl):
        allowed(newurl, commit=self.commit, path=self.path)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def fetch(url, *, maximum, destination=None, row=None, opener=None, commit=None, path=None):
    allowed(url, commit=commit, path=path)
    opener = opener or urllib.request.build_opener(Redirects(commit, path))
    request = urllib.request.Request(url, headers={'Accept-Encoding': 'identity'})
    with opener.open(request, timeout=60) as response:
        allowed(response.geturl(), commit=commit, path=path)
        require('text/html' not in response.headers.get('Content-Type', '').lower(),
                'GitHub returned HTML instead of an immutable build input')
        length = response.headers.get('Content-Length')
        if length is not None:
            try: length = int(length)
            except (TypeError, ValueError) as error:
                raise InputError('Invalid GitHub input Content-Length') from error
            require(0 <= length <= maximum and (row is None or length == row['bytes']),
                    'GitHub input Content-Length differs from its bound')
        data, count, sha = [], 0, hashlib.sha256()
        while block := response.read(min(1 << 20, maximum + 1 - count)):
            count += len(block)
            require(count <= maximum, 'GitHub input exceeds its pinned bound')
            sha.update(block)
            if destination is None: data.append(block)
            else: destination.write(block)
        if row is not None:
            require(count == row['bytes'] and sha.hexdigest() == row['sha256'],
                    'GitHub input failed exact size/SHA-256 verification')
        return b''.join(data)


def publish(temporary, target, row):
    require(matching(temporary, row), 'Reconstructed input differs from its pin')
    try: os.link(temporary, target)
    except FileExistsError:
        require(matching(target, row), 'A different input cache appeared; not overwritten')


def controller(output, *, opener=None):
    output.mkdir(parents=True, exist_ok=True)
    target = output / CONTROLLER_FILENAME
    if target.exists() or target.is_symlink():
        require(matching(target, CONTROLLER), 'Existing Windows input differs from its pin')
        return target
    with tempfile.TemporaryDirectory(prefix='.github-controller-', dir=output) as name:
        temporary = Path(name) / CONTROLLER_FILENAME
        with temporary.open('xb') as stream:
            fetch(CONTROLLER_URL, maximum=CONTROLLER['bytes'], destination=stream,
                  row=CONTROLLER, opener=opener)
            stream.flush(); os.fsync(stream.fileno())
        publish(temporary, target, CONTROLLER)
    return target


def source_pin(pin):
    source = pin.get('github_input', {})
    require(isinstance(source, dict) and source.get('repository') == REPOSITORY
            and isinstance(source.get('commit'), str) and COMMIT.fullmatch(source['commit'])
            and source.get('manifest') == 'signed-apk.json'
            and isinstance(source.get('manifest_sha256'), str)
            and SHA.fullmatch(source['manifest_sha256']),
            'APK needs a pinned immutable GitHub input commit and manifest')
    return source


def manifest_bytes(data, pin):
    require(len(data) <= MAX_MANIFEST, 'APK input manifest exceeds its bound')
    source = source_pin(pin)
    require(hashlib.sha256(data).hexdigest() == source['manifest_sha256'],
            'APK input manifest differs from its committed SHA-256')
    try: manifest = json.loads(data)
    except (ValueError, UnicodeError) as error:
        raise InputError('Invalid APK input manifest JSON') from error
    row = pin_row(pin.get('artifact'))
    require(isinstance(manifest, dict) and manifest.get('schema') == 1
            and manifest.get('version') == pin.get('version')
            and manifest.get('compression') == 'gzip' and manifest.get('artifact') == row,
            'APK input manifest disagrees with the frozen release artifact')
    compressed = pin_row(manifest.get('compressed'), maximum=MAX_COMPRESSED)
    chunks = manifest.get('chunks')
    require(isinstance(chunks, list) and 1 <= len(chunks) <= 128,
            'APK input chunk inventory is missing or unbounded')
    names = []
    for index, chunk in enumerate(chunks):
        pin_row(chunk, maximum=MAX_CHUNK)
        name = chunk.get('path')
        require(isinstance(name, str) and CHUNK.fullmatch(name)
                and name == f'chunks/{index:03d}.bin', 'Unsafe or unordered APK input chunk')
        names.append(name)
    require(len(set(names)) == len(names)
            and sum(chunk['bytes'] for chunk in chunks) == compressed['bytes'],
            'APK input chunk inventory is incomplete')
    return manifest


def signed_apk(pin, output, *, opener=None):
    row = pin_row(pin.get('artifact')); source = source_pin(pin)
    require(re.fullmatch(r'AliancaDX_Acesso_Remoto_[0-9]+\.[0-9]+\.[0-9]+\.apk', row.get('file', '')),
            'Invalid reconstructed APK filename')
    output.mkdir(parents=True, exist_ok=True)
    target = output / row['file']
    if target.exists() or target.is_symlink():
        require(matching(target, row), 'Existing APK input differs from its pin')
        return target
    commit = source['commit']; path = source['manifest']
    data = fetch(RAW_ORIGIN + commit + '/' + path, maximum=MAX_MANIFEST,
                 opener=opener, commit=commit, path=path)
    manifest = manifest_bytes(data, pin)
    with tempfile.TemporaryDirectory(prefix='.github-apk-', dir=output) as name:
        folder = Path(name); compressed = folder / 'payload.gz'; temporary = folder / row['file']
        with compressed.open('xb') as stream:
            for chunk in manifest['chunks']:
                path = chunk['path']
                fetch(RAW_ORIGIN + commit + '/' + path, maximum=chunk['bytes'], destination=stream,
                      row=chunk, opener=opener, commit=commit, path=path)
        require(matching(compressed, manifest['compressed']), 'Compressed APK payload differs from its pin')
        try:
            with gzip.open(compressed, 'rb') as source_stream, temporary.open('xb') as output_stream:
                count = 0
                while block := source_stream.read(min(1 << 20, row['bytes'] + 1 - count)):
                    count += len(block)
                    require(count <= row['bytes'], 'Decompressed APK exceeds its pinned size')
                    output_stream.write(block)
                output_stream.flush(); os.fsync(output_stream.fileno())
        except (OSError, EOFError) as error:
            raise InputError('Invalid compressed APK payload') from error
        publish(temporary, target, row)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controller', action='store_true')
    parser.add_argument('--apk-pin', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(args.controller != bool(args.apk_pin), 'Select exactly one GitHub input')
    path = controller(args.output) if args.controller else signed_apk(json.loads(args.apk_pin.read_text()), args.output)
    print(json.dumps({'verified_input': str(path), 'bytes': path.stat().st_size, 'sha256': digest(path)}))


if __name__ == '__main__': main()
