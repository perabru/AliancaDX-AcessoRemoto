#!/usr/bin/env python3
"""Reproduce original-pixel quality checks with the production Host loop.

Requires Pillow12.3.0, DejaVu fonts and the baseline git object (v7.6.1).
The MSS provider and delivery ACKs are synthetic; CPU timings are local only.
"""
from __future__ import annotations
import argparse
import base64
from dataclasses import asdict
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
from io import BytesIO
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat, __version__

DESKTOP = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(DESKTOP), str(DESKTOP / "tests")]
import adaptive_stream as current
from test_refinement_quality import HostRefinementTests, dense_desktop
from test_lossless_stream import text_desktop


def load_baseline(ref):
    body = subprocess.check_output(["git", "show", f"{ref}:desktop/adaptive_stream.py"], cwd=DESKTOP)
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "baseline.py"; path.write_bytes(body)
        spec = spec_from_file_location("quality77_baseline", path)
        module = module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    return module, hashlib.sha256(body).hexdigest()


def decode(data):
    with Image.open(BytesIO(data)) as image:
        return image.convert("RGB")


def original_error(source, encoded):
    image = decode(encoded)
    try:
        expanded = image.resize(source.size, Image.Resampling.LANCZOS)
        try:
            difference = ImageChops.difference(source, expanded)
            result = sum(ImageStat.Stat(difference).mean) / 3
            difference.close()
            return result
        finally: expanded.close()
    finally: image.close()


def heavy_desktop():
    """Dense text plus one-pixel references, matching the independent audit."""
    source = Image.new("RGB", (3840, 2160), "#ffffff")
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 3840, 51), fill="#122e23")
    draw.text((22, 15), "AliançaDX · Documento e código · Fonte original",
              font=ImageFont.truetype("DejaVuSansMono.ttf", 18), fill="#ffffff")
    draw.rectangle((0, 52, 210, 2160), fill="#e8f0ec")
    font = ImageFont.truetype("DejaVuSansMono.ttf", 13)
    colors = ("#0057b7", "#bf1438", "#007147", "#1c2634")
    for column, x in enumerate(range(230, 3590, 860)):
        for row, y in enumerate(range(75, 2140, 22)):
            draw.text((x, y), f'{row:03d} const acesso = await pc.open({{ texto: "Árvore RGB 0123456789" }});',
                      font=font, fill=colors[(column + row) % 4])
    for x in range(25, 185):
        for y in range(80, 320):
            if (x + y) % 2 == 0: source.putpixel((x, y), (25, 36, 49))
    for y, color in ((380, (230, 20, 20)), (384, (10, 60, 230)), (388, (10, 150, 45))):
        draw.line((25, y, 185, y), fill=color, width=1)
    return source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-ref", default="v7.6.1")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--comparison", type=Path)
    args = parser.parse_args()
    baseline, baseline_sha = load_baseline(args.baseline_ref)
    rows = []; comparisons = []
    for name, factory, cycle in (("dense_4k_text", heavy_desktop, .05),
                                 ("ordinary_4k_text_350ms_cycle", lambda: text_desktop((3840, 2160)), .35)):
        source = factory()
        # Give7.6.1 its actual native opportunism on the fast link, rather
        # than compare a negotiated7.7 receiver against a JPEG-only client.
        baseline_budget = baseline.STREAM_POLICIES["auto"].frame_budget if cycle <= .22 else 0
        before, _, before_size = baseline.encode_screen_image(
            source, baseline.PROFILES[1], Image, allow_png=True, native_png_budget=baseline_budget)
        started = time.perf_counter()
        app, frames = HostRefinementTests().capture(source, elapsed=cycle, loops=30)
        elapsed_ms = (time.perf_counter() - started) * 1000
        after = base64.b64decode(frames[-1]["image"])
        final = decode(after)
        exact = source.size == final.size and source.tobytes() == final.tobytes()
        rows.append({"scene": name, "source_pixels": list(source.size), "baseline_received_pixels": list(before_size),
                     "baseline_bytes": len(before), "baseline_original_rgb_mae": original_error(source, before),
                     "baseline_native_png_wire_budget": baseline_budget,
                     "updated_received_pixels": list(final.size), "updated_bytes": len(after),
                     "updated_complete_json_bytes": len(json.dumps(frames[-1], ensure_ascii=False, separators=(",", ":")).encode()),
                     "updated_original_rgb_mae": original_error(source, after), "pixel_exact": exact,
                     "actual_screen_messages": len(frames),
                     "unchanged_pulses": sum(m["type"] == "screen_unchanged" for m in app.peer.messages),
                     "simulated_presentation_cycle_seconds": cycle, "local_30_capture_iterations_ms": elapsed_ms})
        if name == "dense_4k_text":
            old = decode(before).resize(source.size, Image.Resampling.LANCZOS)
            comparisons = [old.crop((20, 15, 640, 365)), final.crop((20, 15, 640, 365))]
            old.close()
        final.close(); source.close()
    legacy = []
    for name, source in (("text", text_desktop((1920, 1080))),
                         ("noise", Image.frombytes("RGB", (1280, 720), random.Random(7700).randbytes(1280 * 720 * 3)))):
        try:
            for mode in ("auto", "quality", "latency"):
                old = baseline.encode_screen_image(source, baseline.STREAM_POLICIES[mode].profiles[1], Image, mode)
                new = current.encode_screen_image(source, current.STREAM_POLICIES[mode].profiles[1], Image, mode)
                legacy.append({"scene": name, "mode": mode, "identical_bytes": old[0] == new[0], "same_size": old[2] == new[2]})
        finally: source.close()
    result = {"version": "7.7.0", "Pillow": __version__, "baseline_source_sha256": baseline_sha,
              "scope": "Synthetic desktop scenes and ACKs through real production Host code; no physical device or internet latency measurement.",
              "normal_wire_limits_unchanged": {mode: p.frame_budget for mode, p in current.STREAM_POLICIES.items()},
              "static_refinement_wire_limit": current.MAX_REFINEMENT_FRAME_BYTES,
              "readable_profiles": {mode: [asdict(p) for p in profiles] for mode, profiles in current.READABLE_PROFILES.items()},
              "scenes": rows, "legacy_JPEG_checks": legacy,
              "all_pass": all(r["pixel_exact"] for r in rows) and all(r["identical_bytes"] and r["same_size"] for r in legacy)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if args.comparison:
        canvas = Image.new("RGB", (1280, 438), "#f4faf7"); draw = ImageDraw.Draw(canvas)
        font = ImageFont.truetype("DejaVuSans.ttf", 19)
        draw.text((18, 12), "7.6.1 · prévia 1080p ampliada", font=font, fill="#123a2a")
        draw.text((658, 12), "7.7.0 · pixels originais sem perdas", font=font, fill="#007a50")
        for index, crop in enumerate(comparisons): canvas.paste(crop, (18 + index * 640, 48)); crop.close()
        draw.text((18, 410), "Recorte dos pixels recebidos · cena sintética · entrega simulada · mesmo conteúdo", font=ImageFont.truetype("DejaVuSans.ttf", 14), fill="#446557")
        args.comparison.parent.mkdir(parents=True, exist_ok=True); canvas.save(args.comparison)
    print(json.dumps({"all_pass": result["all_pass"], "Pillow": __version__, "scenes": rows}, ensure_ascii=False))
    raise SystemExit(0 if result["all_pass"] else 1)


if __name__ == "__main__": main()
