#!/usr/bin/env python3
"""Production 7.5 vs 7.6 synthetic pixels/CPU/bytes, never an internet benchmark."""
from __future__ import annotations
import argparse
from dataclasses import asdict
from io import BytesIO
import json
import math
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
import time
import types

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont, __version__ as PILLOW_VERSION

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
from adaptive_stream import STREAM_POLICIES, ScreenContentTracker, encode_screen_image, screen_text_candidate
from benchmark_stream_fidelity import desktop, ssim_rgb_8x8

BASELINE = "9a66467a77bb7c43bc7d56e5b6821bfc24adcf57"


def baseline_encoder():
    code = subprocess.check_output(["git", "show", BASELINE + ":desktop/adaptive_stream.py"], text=True,
                                   cwd=Path(__file__).resolve().parents[2])
    module = types.ModuleType("adx_75_baseline_for_benchmark")
    sys.modules[module.__name__] = module
    exec(compile(code, BASELINE + ":desktop/adaptive_stream.py", "exec"), module.__dict__)
    return module


def synthetic_photo(size):
    """Seeded continuous color/noise; illustrative content, not a camera photo."""
    width, height = size
    rng = np.random.default_rng(7600)
    y, x = np.mgrid[0:height, 0:width]
    base = np.stack((90 + 60 * np.sin(x / 180) + 35 * np.cos(y / 160),
                     115 + 65 * np.sin(y / 130) + 30 * np.cos(x / 200),
                     135 + 55 * np.sin((x + y) / 170)), axis=-1)
    noise = rng.normal(0, 15, (height, width, 3))
    return Image.fromarray(np.clip(base + noise, 0, 255).astype("uint8"), "RGB")



_capture_tracker = ScreenContentTracker()


def new_capture_pipeline(source, profile, image_module, mode, **options):
    """The actual new Host branches before encoding, including full text hash.

    These are changed-frame timing samples; no previous frame is remembered,
    so the tracker cannot skip encoding and make its cost disappear.
    """
    text = mode != "latency" and screen_text_candidate(source, image_module)
    native_candidate = (options.get("native_png_budget", 0) > 32 * 1024
                        or (source.width <= profile.width and source.height <= profile.height))
    if text and native_candidate:
        _capture_tracker.candidate(source, ("changed-capture",), time.monotonic())
    return encode_screen_image(source, profile, image_module, mode, **options, content_is_text=text)

def measure_pair(before, after, source, old_profile, new_profile, mode, iterations, **options):
    results = {"old": {"wall": [], "cpu": []}, "new": {"wall": [], "cpu": []}}
    for index in range(iterations):
        order = ("old", "new") if index % 2 == 0 else ("new", "old")
        for name in order:
            encode, profile, kwargs = ((before, old_profile, {}) if name == "old" else (after, new_profile, options))
            start = time.perf_counter(); cpu_start = time.thread_time()
            value = encode(source, profile, Image, mode, **kwargs)
            results[name]["cpu"].append((time.thread_time() - cpu_start) * 1000)
            results[name]["wall"].append((time.perf_counter() - start) * 1000)
            results[name]["value"] = value
    return results


def encoded_result(source, measured):
    data, actual, size = measured["value"]
    measurements = measured["cpu"]
    wire = 4 * ((len(data) + 2) // 3) + 32768
    with Image.open(BytesIO(data)) as decoded:
        decoded.load()
        pixels_equal = decoded.size == source.size and decoded.tobytes() == source.tobytes()
        raster = decoded.convert("RGB")
        image_format = decoded.format
    if raster.size != source.size:
        original_raster = raster
        raster = raster.resize(source.size, Image.Resampling.LANCZOS)
        original_raster.close()
    box = (24, 90, min(664, source.width), min(450, source.height))
    reference = np.asarray(source.crop(box))
    reconstructed = np.asarray(raster.crop(box))
    mse = float(np.mean((reference.astype(float) - reconstructed.astype(float)) ** 2))
    result = {"format": image_format, "encoded_bytes": len(data), "wire_bytes_estimate": wire,
              "encoded_size": list(size), "profile": asdict(actual), "cpu_ms_samples": measurements,
              "cpu_ms_median": statistics.median(measurements),
              "wall_ms_samples": measured["wall"], "wall_ms_median": statistics.median(measured["wall"]),
              "native_pixels_exact": pixels_equal,
              "text_crop_rgb_ssim_8x8": ssim_rgb_8x8(reference, reconstructed),
              "text_crop_rgb_psnr_db": None if mse == 0 else 10 * math.log10(255 ** 2 / mse)}
    result["encode_plus_wire_ms_estimate"] = {
        str(mbps): result["wall_ms_median"] + wire * 8 / (mbps * 1000) for mbps in (1, 5, 10, 100)}
    return result, raster


def comparison(source, baseline, current, path):
    box = (24, 90, min(664, source.width), min(450, source.height))
    labels = ("Original: texto de teste 13px", "7.5: imagem recebida", "7.6: PNG nativo sem perdas")
    images = (source, baseline, current)
    panel_width, panel_height = (box[2] - box[0]) * 2, (box[3] - box[1]) * 2
    output = Image.new("RGB", (panel_width, (panel_height + 52) * 3), "#edf2f7")
    draw = ImageDraw.Draw(output); font = ImageFont.truetype("DejaVuSans.ttf", 23)
    for index, (image, label) in enumerate(zip(images, labels)):
        top = index * (panel_height + 52)
        draw.text((16, top + 11), label, fill="#163444", font=font)
        crop = image.crop(box).resize((panel_width, panel_height), Image.Resampling.NEAREST)
        output.paste(crop, (0, top + 52)); crop.close()
    output.save(path)
    output.close()


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=5); args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    old = baseline_encoder(); rows = []
    for kind, size, mode, native in (("text_fullhd", (1920, 1080), "auto", False),
                                    ("text_4k", (3840, 2160), "auto", True),
                                    ("text_4k", (3840, 2160), "quality", True),
                                    ("text_4k_no_writer_headroom", (3840, 2160), "auto", False),
                                    ("synthetic_photo", (1920, 1080), "auto", False),
                                    ("changed_text_scroll", (3840, 2160), "auto", True)):
        source = synthetic_photo(size) if kind == "synthetic_photo" else desktop(size)
        if kind == "changed_text_scroll":
            shifted = ImageChops.offset(source, 0, 19); source.close(); source = shifted
        budget = STREAM_POLICIES[mode].frame_budget if native else 0
        measurements = measure_pair(old.encode_screen_image, new_capture_pipeline, source,
                                    old.STREAM_POLICIES[mode].profiles[1], STREAM_POLICIES[mode].profiles[1],
                                    mode, args.iterations, allow_png=True, native_png_budget=budget)
        before, before_raster = encoded_result(source, measurements["old"])
        after, after_raster = encoded_result(source, measurements["new"])
        row = {"content": kind, "source_size": list(size), "mode": mode,
               "native_writer_headroom_explicitly_simulated": native, "before_7_5": before, "after_7_6": after}
        if native and kind != "changed_text_scroll":
            png = args.output / ("comparison_" + mode + "_4k.png")
            comparison(source, before_raster, after_raster, png)
            row["comparison"] = png.name
        rows.append(row)
        source.close(); before_raster.close(); after_raster.close()
    static = []
    for size in ((1920, 1080), (3840, 2160)):
        source = desktop(size); tracker = ScreenContentTracker(); sent = 0; digest_ms = 0; digest_wall_ms = 0
        for index in range(60):
            now = index / 12
            start = time.perf_counter(); cpu_start = time.thread_time()
            signature = tracker.candidate(source, ("same-peer", 1), now)
            digest_ms += (time.thread_time() - cpu_start) * 1000
            digest_wall_ms += (time.perf_counter() - start) * 1000
            if signature is not None: tracker.published(signature, now); sent += 1
        before = next(row["before_7_5"] for row in rows if row["content"] == ("text_fullhd" if size[0] == 1920 else "text_4k") and row["mode"] == "auto")
        after = next(row["after_7_6"] for row in rows if row["content"] == ("text_fullhd" if size[0] == 1920 else "text_4k") and row["mode"] == "auto")
        static.append({"source_size": list(size), "candidate_captures": 60, "simulated_interval_seconds": 5,
                       "baseline_frames": 60, "new_frames": sent, "full_capture_sha256_cpu_ms_total": digest_ms, "full_capture_sha256_wall_ms_total": digest_wall_ms,
                       "before_wire_bytes": 60 * before["wire_bytes_estimate"],
                       "after_wire_bytes": sent * after["wire_bytes_estimate"],
                       "scope": "Deterministic capture timestamps with the production digest tracker. No socket or physical desktop."})
        source.close()
    result = {"version": "7.6.0", "baseline_commit": BASELINE, "iterations": args.iterations,
              "platform": platform.platform(), "pillow_version": PILLOW_VERSION, "numpy_version": np.__version__,
              "scope": "Local synthetic text/photo/changed-scroll pixel, CPU and byte measurements. Shared Linux CPU; not physical devices or internet latency.",
              "native_gate": "Production requires explicitly negotiated PNG, top two tiers, at most one cadence throttle and no probe, observed fast writer plus an actual presented-frame ACK within the mode cycle bound. The byte budget is the minimum of writer duration/bytes projection and the corresponding ACKed frame bytes multiplied by mode cycle limit / actual presentation cycle. No ACK or unknown frame bytes means no native expansion. The benchmark explicitly simulates that budget in marked rows.",
              "latency_estimate": "Median encode elapsed wall time + 8*(base64 image bytes + conservative32KiB metadata)/Mbps. Includes newHostclassifier/textdigest, excludes common OS capture/RGB conversion, ACK/RTT, receive/decode/draw and congestion. Native rows at1/5/10Mbps are illustrative arithmetic, not a claim native would pass the actual writer gate.",
              "timing_method": "Five interleaved old/new samples, alternating which runs first. CPU uses current-thread time.thread_time; elapsed uses perf_counter; both reported separately. New7.6 samples include the production Host classifier and fullRGBSHA256 text digest before encode; photo/video deliberately skip full digest. Cropping/metrics and common OS capture/RGB conversion excluded.",
              "quality_metric": "Same 640x360 native text crop after reconstructing received image at the source size; uniform8x8RGBSSIM. Native lossless PNG equality separately checks EVERY captured RGB pixel.",
              "rows": rows, "unchanged_scene": static,
              "limitations": ["No codec can provide zero network delay.", "Full PNG is selected for suitable flat/text UI only; photo/video retain JPEG.",
                              "No-headroom4Kauto retains the bounded1080p fallback; PNG cannot recover pixels lost to resizing.",
                              "A rapidly changing native PNG can use more bytes than a reduced JPEG, so native requires measured headroom and congestion still reduces it."]}
    (args.output / "DESEMPENHO_7_6_0.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"report": str(args.output / "DESEMPENHO_7_6_0.json"), "rows": [{"content":r["content"], "mode":r["mode"], "old": [r["before_7_5"]["encoded_size"], r["before_7_5"]["encoded_bytes"], round(r["before_7_5"]["cpu_ms_median"], 2)], "new": [r["after_7_6"]["encoded_size"], r["after_7_6"]["encoded_bytes"], round(r["after_7_6"]["cpu_ms_median"],2)], "exact":r["after_7_6"]["native_pixels_exact"]} for r in rows], "static":static}, ensure_ascii=False, indent=2))

if __name__ == "__main__": main()
