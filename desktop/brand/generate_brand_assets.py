#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Reproduce AliançaDX vector/raster/native icon assets. Requires Pillow only.

The supplied JPEG is read and copied verbatim. Its wordmark is never redrawn.
The two arc segments use identical geometry in SVG, Android VectorDrawable and
the supersampled raster renderer; no third-party font or stock artwork is used.
By default only this brand directory is written. Project integration is opt-in.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil

from PIL import Image, ImageDraw

BRAND = Path(__file__).resolve().parent
NEON = "#02EF5F"
EMERALD = "#009966"
WINDOWS_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def save_windows_icon(image: Image.Image, destination: Path) -> None:
    """Write classic DIB entries accepted by the Windows/Tk native loader."""
    image.save(destination, bitmap_format="bmp", sizes=[(size, size) for size in WINDOWS_ICON_SIZES])


def point(radius: float, angle: float) -> tuple[float, float]:
    return 54 + radius * math.cos(math.radians(angle)), 54 + radius * math.sin(math.radians(angle))


def xy(value: tuple[float, float]) -> str:
    return ",".join(f"{v:.5f}" for v in value)


def paths(outer: float) -> list[str]:
    inner = outer * 0.5
    left = (f"M{xy(point(outer,-103))}A{outer},{outer} 0 0,0 {xy(point(outer,103))}"
            f"L{xy(point(inner,103))}A{inner},{inner} 0 0,1 {xy(point(inner,-103))}Z")
    right = (f"M{xy(point(outer,-77))}A{outer},{outer} 0 0,1 {xy(point(outer,77))}"
             f"L{xy(point(inner,77))}A{inner},{inner} 0 0,0 {xy(point(inner,-77))}Z")
    return [left, right]


def svg(outer: float, background: bool = False) -> str:
    body = '<rect width="108" height="108" fill="#FFFFFF"/>\n' if background else ""
    body += "\n".join(f'<path d="{p}" fill="url(#brand-green)"/>' for p in paths(outer))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 108 108">\n'
            '<title>AliançaDX — símbolo de aliança</title>\n'
            '<desc>Anel geométrico com dois segmentos e aberturas em cima e embaixo, baseado na marca fornecida.</desc>\n'
            f'<defs><linearGradient id="brand-green" x1="{54-outer}" y1="54" x2="{54+outer}" y2="54" gradientUnits="userSpaceOnUse">'
            f'<stop offset="0" stop-color="{NEON}"/><stop offset="1" stop-color="{EMERALD}"/></linearGradient></defs>\n'
            + body + "\n</svg>\n")


def raster(size: int, outer: float, background: bool) -> Image.Image:
    # Sampling circular arcs preserves the SVG geometry at arbitrary output size.
    scale = 4
    side = size * scale
    mask = Image.new("L", (side, side), 0)
    draw = ImageDraw.Draw(mask)
    for start, end in ((-103, -257), (-77, 77)):
        angles = [start + (end-start)*i/512 for i in range(513)]
        polygon = [point(outer, a) for a in angles]
        polygon += [point(outer*0.5, a) for a in reversed(angles)]
        draw.polygon([(x*side/108, y*side/108) for x,y in polygon], fill=255)
    first = tuple(bytes.fromhex(NEON[1:])); last = tuple(bytes.fromhex(EMERALD[1:]))
    line = Image.new("RGB", (side, 1))
    low, high = (54-outer)*side/108, (54+outer)*side/108
    line.putdata([tuple(round(a+(b-a)*max(0,min(1,(x-low)/(high-low)))) for a,b in zip(first,last)) for x in range(side)])
    gradient = line.resize((side,side))
    canvas = Image.new("RGBA", (side,side), (255,255,255,255) if background else (0,0,0,0))
    canvas.paste(gradient, (0,0), mask)
    return canvas.resize((size,size), Image.Resampling.LANCZOS)


def contrast(a: str, b: str) -> float:
    def luminance(color: str) -> float:
        rgb = [int(color[i:i+2],16)/255 for i in (1,3,5)]
        rgb = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in rgb]
        return sum(v*w for v,w in zip(rgb,(.2126,.7152,.0722)))
    x,y = sorted((luminance(a),luminance(b)))
    return round((y+.05)/(x+.05),2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--android-project", type=Path,
                        help="optional existing Android project root; explicitly refreshes its launcher resources")
    parser.add_argument("--desktop-project", type=Path,
                        help="optional existing desktop project root; explicitly refreshes assets/brand")
    args = parser.parse_args()
    android = None
    desktop = None
    if args.android_project is not None:
        android = args.android_project.resolve() / "app/src/main/res"
        if not android.is_dir():
            parser.error("--android-project must contain an existing app/src/main/res directory")
    if args.desktop_project is not None:
        desktop_project = args.desktop_project.resolve()
        if not desktop_project.is_dir():
            parser.error("--desktop-project must name an existing project directory")
        desktop = desktop_project / "assets/brand"
    original = BRAND / "aliancadx-logo-original.jpg"
    original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    (BRAND / "aliancadx-mark.svg").write_text(svg(44),encoding="utf-8")
    (BRAND / "aliancadx-app.svg").write_text(svg(31.5,True),encoding="utf-8")
    for size in (1024,256):
        raster(size,44,False).save(BRAND / f"aliancadx-mark-{size}.png")
        raster(size,31.5,True).save(BRAND / f"aliancadx-app-{size}.png")
    # Classic DIB entries let the bundled Tk select the actual 16/32px image
    # instead of resampling a PNG-backed icon through the Shell fallback.
    save_windows_icon(raster(1024,31.5,True), BRAND / "aliancadx.ico")
    Image.open(original).convert("RGB").save(BRAND / "aliancadx-logo.png")
    # NSIS artwork uses the same mark; no wordmark/font reconstruction.
    wizard = Image.new("RGB", (164,314), "#007A50")
    mark_mask = raster(112,44,False).getchannel("A")
    wizard.paste(Image.new("RGB", (112,112), "#FFFFFF"), (26,26), mark_mask)
    wizard.save(BRAND / "aliancadx-wizard.bmp")
    header = Image.new("RGB", (150,57), "#FFFFFF")
    logo = Image.open(original).convert("RGB")
    logo.thumbnail((144,47), Image.Resampling.LANCZOS)
    header.paste(logo, ((150-logo.width)//2,(57-logo.height)//2))
    header.save(BRAND / "aliancadx-header.bmp")
    if desktop is not None:
        desktop.mkdir(parents=True, exist_ok=True)
        for source in BRAND.iterdir():
            if source.is_file() and source.suffix in (".jpg", ".png", ".svg", ".ico", ".bmp"):
                shutil.copyfile(source, desktop / source.name)
    if android is not None:
        (android / "drawable-nodpi").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original,android / "drawable-nodpi/aliancadx_logo.jpg")
        raster(1024,31.5,True).save(android / "drawable-nodpi/ic_launcher_artwork.png")
        (android / "drawable/ic_launcher_background.xml").write_text('''<?xml version="1.0" encoding="utf-8"?>
<shape xmlns:android="http://schemas.android.com/apk/res/android" android:shape="rectangle">
    <solid android:color="#FFFFFF" />
</shape>
''',encoding="utf-8")
        for monochrome in (False,True):
            body = []
            for path in paths(31.5):
                if monochrome:
                    body.append(f'    <path android:fillColor="#FFFFFF" android:pathData="{path}" />')
                else:
                    body.append(f'''    <path android:pathData="{path}">
        <aapt:attr name="android:fillColor">
            <gradient android:type="linear" android:startX="22.5" android:startY="54"
                android:endX="85.5" android:endY="54" android:startColor="{NEON}" android:endColor="{EMERALD}" />
        </aapt:attr>
    </path>''')
            header = '''<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:aapt="http://schemas.android.com/aapt"
    android:width="108dp" android:height="108dp"
    android:viewportWidth="108" android:viewportHeight="108">
'''
            name = "ic_launcher_monochrome.xml" if monochrome else "ic_launcher_foreground.xml"
            (android / "drawable" / name).write_text(header+"\n".join(body)+"\n</vector>\n",encoding="utf-8")
        for directory,monochrome in (("mipmap-anydpi",False),("mipmap-anydpi-v33",True)):
            folder = android / directory; folder.mkdir(exist_ok=True)
            mono = '\n    <monochrome android:drawable="@drawable/ic_launcher_monochrome" />' if monochrome else ""
            (folder / "ic_launcher.xml").write_text('''<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@drawable/ic_launcher_background" />
    <foreground android:drawable="@drawable/ic_launcher_foreground" />'''+mono+'\n</adaptive-icon>\n',encoding="utf-8")
    data = {
        "name":"AliançaDX Acesso Remoto", "asset_version":"6.2.0",
        "original":{"file":original.name,"sha256":original_hash,"width":402,"height":94,"modified":False},
        "palette":{"neon":NEON,"emerald":EMERALD,"primary":"#007A50","white":"#FFFFFF","surface":"#F4FAF7","text":"#123A2A","muted":"#446557","border":"#CEE6D9"},
        "reference_samples":{"method":"Median RGB from interior JPEG pixels; JPEG compression and gradient cause variation.","neon":"#02EF5F","ring_emerald":"#0DAE68","wordmark":"#059866"},
        "contrast":{"white_on_primary":contrast("#FFFFFF","#007A50"),"text_on_white":contrast("#123A2A","#FFFFFF"),"muted_on_surface":contrast("#446557","#F4FAF7")},
        "usage":{"wordmark":"Use supplied original; never replace its lettering with another font.","neon":"Brand marks and decorative accents; use primary emerald for text/buttons on white.","android_safe_zone":"Ring diameter63dp in108dp foreground, fully inside66dp safe circle.","source":"User-supplied JPEG and newly authored geometric vector; no third-party fonts."}}
    (BRAND / "brand.json").write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if desktop is not None:
        shutil.copyfile(BRAND / "brand.json", desktop / "brand.json")
    assert hashlib.sha256(original.read_bytes()).hexdigest() == original_hash
    print(json.dumps({"original_sha256":original_hash,"contrast":data["contrast"],"android_logo":"R.drawable.aliancadx_logo" if android else None,"desktop_assets":str(desktop) if desktop else None},ensure_ascii=False))


if __name__ == "__main__":
    main()
