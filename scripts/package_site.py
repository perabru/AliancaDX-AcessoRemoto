#!/usr/bin/env python3
"""Package only the public pages and the explicit iOS PWA asset inventory."""
from pathlib import Path, PurePosixPath
import json
import shutil

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_site"


def ios_public_assets(root=ROOT):
    base = root / 'ios-pwa'
    manifest = base / 'public-assets.json'
    if manifest.is_symlink():
        raise ValueError('Unsafe iOS public inventory')
    values = json.loads(manifest.read_text(encoding='utf-8'))
    if (not isinstance(values, list) or not values or len(values) > 128
            or not all(isinstance(value, str) for value in values) or len(set(values)) != len(values)):
        raise ValueError('Invalid iOS public asset inventory')
    paths = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError('Invalid iOS asset path')
        path = PurePosixPath(value)
        if (path.is_absolute() or not path.parts or '\\' in value or ':' in value
                or any(part in ('', '.', '..') or part.startswith('.') for part in path.parts)
                or path.as_posix() != value
                or any(part in ('tests', 'node_modules', '__pycache__') for part in path.parts)
                or path.suffix.lower() in ('.p12', '.pfx', '.jks', '.keystore', '.env', '.py', '.exe', '.apk')):
            raise ValueError('Unsafe iOS asset path')
        source = base / value
        if (not source.is_file() or source.is_symlink() or any(parent.is_symlink() for parent in source.parents if parent != base.parent)
                or not source.resolve().is_relative_to(base.resolve())):
            raise ValueError('Missing or unsafe iOS asset: ' + value)
        paths.append((value, source))
    required = {'index.html', 'bridge.html', 'manifest.webmanifest', 'sw.js'}
    if not required.issubset(values):
        raise ValueError('Incomplete iOS PWA public inventory')
    return paths


def main():
    ios = ios_public_assets()
    manual = ROOT / 'docs/manual/Manual_AliancaDX.pdf'
    if (not manual.is_file() or manual.is_symlink()
            or manual.stat().st_size > 10 * 1024 * 1024
            or not manual.read_bytes().startswith(b'%PDF-')):
        raise ValueError('Missing or unsafe public user manual')
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    shutil.copy2(ROOT / 'index.html', OUT / 'index.html')
    manual_target = OUT / 'docs/manual/Manual_AliancaDX.pdf'
    manual_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manual, manual_target)
    shutil.copytree(ROOT / 'assets', OUT / 'assets')
    for name, source in ios:
        target = OUT / 'ios-pwa' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    shutil.copy2(ROOT / 'ios-pwa/public-assets.json', OUT / 'ios-pwa/public-assets.json')
    (OUT / '.nojekyll').write_text('', encoding='utf-8')
    total = sum(p.stat().st_size for p in OUT.rglob('*') if p.is_file())
    print(f'Página e PWA preparadas: {total:,} bytes em _site/')


if __name__ == '__main__':
    main()
