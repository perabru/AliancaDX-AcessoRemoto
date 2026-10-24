#!/usr/bin/env python3
"""Offline checks for missing assets, broken anchors and download destinations."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, unquote
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
DOWNLOAD_URLS = {
    "ios-pwa/",
    "https://github.com/perabru/AliancaDX-AcessoRemoto/releases/download/v6.4.0/AliancaDX_iOS_PWA_6.4.0.zip",
    "https://github.com/perabru/AliancaDX-AcessoRemoto/releases/download/v6.4.0/AliancaDX_Controlador_Setup.exe",
    "https://github.com/perabru/AliancaDX-AcessoRemoto/releases/download/v6.4.0/AliancaDX_Host_Setup.exe",
    "https://github.com/perabru/AliancaDX-AcessoRemoto/releases/download/v6.4.0/AliancaDX_Acesso_Remoto_6.4.0.apk",
    "docs/manual/Manual_AliancaDX.pdf",
}

class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = []
        self.links = []
        self.assets = []
        self.errors = []
        self.h1 = 0
        self.lang = None
        self.title = False
        self.description = False
        self.references = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.append(a["id"])
        for attr in ("aria-controls", "aria-labelledby", "aria-describedby"):
            self.references.extend(a.get(attr, "").split())
        if tag == "label" and a.get("for"):
            self.references.append(a["for"])
        if tag == "html":
            self.lang = a.get("lang")
        if tag == "h1":
            self.h1 += 1
        if tag == "title":
            self.title = True
        if tag == "meta" and a.get("name") == "description":
            self.description = bool(a.get("content"))
        if tag == "a":
            href = a.get("href", "")
            self.links.append(href)
            if a.get("target") == "_blank":
                if not {"noopener", "noreferrer"} <= set(a.get("rel", "").split()):
                    self.errors.append("External new-tab link lacks noopener/noreferrer")
        if tag == "img":
            if "alt" not in a:
                self.errors.append("Image without alt attribute")
            self.assets.append(a.get("src", ""))
        if tag == "script" and "src" in a:
            self.assets.append(a["src"])
        if tag == "link" and a.get("rel") in {"icon", "apple-touch-icon", "stylesheet"}:
            self.assets.append(a.get("href", ""))


def main():
    parser = Page()
    parser.feed((ROOT / "index.html").read_text(encoding="utf-8"))
    errors = parser.errors
    if parser.lang not in {"pt-BR", "pt-br"}:
        errors.append("Page must declare Brazilian Portuguese")
    if parser.h1 != 1 or not parser.title or not parser.description:
        errors.append("Expected one h1, a title and a description")
    if any(n > 1 for n in Counter(parser.ids).values()):
        errors.append("Duplicate HTML IDs")
    for ref in parser.references:
        if ref not in parser.ids:
            errors.append(f"Missing label or accessibility target: {ref}")
    for href in parser.links:
        u = urlsplit(href)
        if href.startswith("#") and href[1:] not in parser.ids:
            errors.append(f"Missing anchor: {href}")
        if u.scheme in {"javascript", "http"}:
            errors.append("Unsafe or unencrypted link")
    for src in parser.assets:
        u = urlsplit(src)
        if not u.scheme and not u.netloc:
            asset = (ROOT / unquote(u.path)).resolve()
            if not asset.is_relative_to(ROOT) or not asset.is_file():
                errors.append(f"Missing local asset: {src}")
    for expected in DOWNLOAD_URLS:
        if expected not in parser.links:
            errors.append(f"Missing release or manual download: {expected}")
    required = {"instalar-ios", "app-ios", "instalar-android", "adx-device-dialog", "adx-device-usb", "adx-device-form", "adx-guide-title", "adx-guide-body", "adx-guide-back", "adx-guide-next", "adx-guide-close", "adx-guide-fallback"}
    missing = required - set(parser.ids)
    if missing:
        errors.append("Missing device-guide UI targets: " + ", ".join(sorted(missing)))
    for legacy in ("assets/usb-install-assistant.js", "assets/vendor/aliancadx-adb.bundle.js", "src/usb-adb-transport.js"):
        if (ROOT / legacy).exists():
            errors.append("Removed USB installer must not be present: " + legacy)
    for asset in parser.assets:
        if "usb-install-assistant" in asset or "aliancadx-adb" in asset:
            errors.append("Page still loads the removed USB installer")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Página OK: {len(parser.links)} links, {len(parser.ids)} âncoras, recursos locais presentes.")
    print("Validação estática; não substitui a inspeção visual em um navegador.")

if __name__ == "__main__":
    main()
