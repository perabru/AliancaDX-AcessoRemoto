#!/usr/bin/env python3
"""Local PIL preparation timings; excludes Tk upload and network latency."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFont
from controller import fit_geometry, prepare_frame_image


def synthetic_screen(size=(1920, 1080)):
    image = Image.new("RGB", size, "#f7fafb")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 24)
    for row in range(28):
        color = ("#084fbd", "#00714d", "#a12359", "#25313e")[row % 4]
        draw.text((42, 30 + row * 36),
                  f"{row + 1:02d}  projeto.aliancadx -> tela nítida, mousepad e conexão autorizada", font=font, fill=color)
    return image


def median_ms(operation, samples=16):
    values = []
    for _ in range(samples):
        started = time.perf_counter()
        owned = operation()
        values.append((time.perf_counter() - started) * 1000)
        if owned is not None:
            owned.close()
    return round(statistics.median(values), 4)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with synthetic_screen() as original:
        rows = []
        for viewport in ((1280, 720), (1920, 1080), (2560, 1440)):
            bounds = fit_geometry(*viewport, *original.size)
            fitted_size = bounds[2:]
            old = median_ms(lambda: original.resize(fitted_size, Image.Resampling.BILINEAR))
            def worker():
                prepared = prepare_frame_image(original, viewport)
                return prepared[2] if prepared[2] is not original else None
            preparation = median_ms(worker)
            prepared = prepare_frame_image(original, viewport)
            def lookup():
                assert prepared[0] == viewport and prepared[2].size == fitted_size
                return None
            lookup_ms = median_ms(lookup)
            rows.append({"source": list(original.size), "viewport": list(viewport),
                         "displayed_size": list(fitted_size), "filter": "LANCZOS" if fitted_size != original.size else "original",
                         "old_ui_resize_median_ms": old,
                         "new_worker_preparation_median_ms": preparation,
                         "new_ui_cached_lookup_median_ms": lookup_ms,
                         "native_bitmap_reused": prepared[2] is original})
            if prepared[2] is not original:
                prepared[2].close()
        with synthetic_screen((3840, 2160)) as native:
            def original_bitmap():
                prepared = prepare_frame_image(native, (1280, 720), native=True)
                assert prepared[2] is native
            rows.append({"source": list(native.size), "viewport": [1280, 720], "mode": "native",
                         "new_worker_preparation_median_ms": median_ms(original_bitmap),
                         "native_bitmap_reused": True, "received_pixels_unchanged": True})
        result = {"samples_per_case": 16, "synthetic_screen": True,
                  "tk_upload_included": False, "network_latency_included": False,
                  "meaning": "Fit uses Lanczos reduction; 100% reuses received pixels. PIL timings exclude Tk upload, decode and network and are not end-to-end or physical-Windows timings.",
                  "rows": rows}
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
