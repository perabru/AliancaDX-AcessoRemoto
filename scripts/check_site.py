#!/usr/bin/env python3
"""Offline checks for missing assets, broken anchors and download destinations."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, unquote
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
DRIVE_IDS = {
    "1f7FFuWFtlwJS-dY2iNdpWAA0TY900pRg",
    "1KryJxq7vmQHovoY76n59m5cO7NILXOuR",
    "1cHQ3ASQqRthEv8ux9P06WgoJV41SyiAO",
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
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.append(a["id"])
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
    for file_id in DRIVE_IDS:
        expected = f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"
        if expected not in parser.links:
            errors.append(f"Missing supplied Drive link: {file_id}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Página OK: {len(parser.links)} links, {len(parser.ids)} âncoras, recursos locais presentes.")
    print("Validação estática; não substitui a inspeção visual em um navegador.")

if __name__ == "__main__":
    main()
