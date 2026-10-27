#!/usr/bin/env python3
"""Compare the exact 7.4 encoder with current native-scale desktop text.

Synthetic pixels, Pillow CPU timings and native-reference crops only. These
results are not network delay, physical-device FPS or a perceptual user study.
The checked-in crops use nearest-neighbor enlargement for honest inspection.
"""
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
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adaptive_stream import STREAM_POLICIES, encode_screen_image

BASELINE_COMMIT = "cf9cb920cd419eb02c45f97e7235c7fec45a369f"
BASELINE_PROFILES = {
    "auto": ((1920,1080,76,20,0), (1280,720,60,12,0), (960,540,46,10,2), (640,360,32,6,2), (480,270,25,4,2)),
    "quality": ((1920,1080,86,14,0), (1600,900,80,10,0), (1280,720,72,8,0), (800,450,48,6,2), (480,270,32,4,2)),
}
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
NAMES = ("detail", "balanced", "economy", "low", "minimum")


def desktop(size):
    """13-pixel source text stays 13px even on a 4K virtual desktop."""
    width, height = size
    image = Image.new("RGB", size, "#f3f5f8")
    draw = ImageDraw.Draw(image)
    mono = ImageFont.truetype(FONT_PATH, 13)
    small = ImageFont.truetype(FONT_PATH, 11)
    title = ImageFont.truetype(FONT_PATH, 18)
    draw.rectangle((0,0,width,52), fill="#092938")
    draw.text((22,15), "AliancaDX  |  Desktop de teste - texto nativo e cores", font=title, fill="white")
    panel_width = min(1200, width - 48)
    draw.rounded_rectangle((16,70,panel_width, min(height-30,1050)), radius=12, fill="white", outline="#ccd6df")
    segments = (("const ","#863ca6"), ("connection", "#125074"), (" = await ","#96350c"),
                ("session.open", "#005b9b"), ("({ code: ","#263d4c"), ("'ABC-123'", "#a31515"),
                (", secure: ","#263d4c"), ("true", "#007333"), (" });", "#263d4c"))
    for y in range(94, min(height-50,1030), 22):
        x = 32
        draw.text((x,y), f"{(y-94)//22+1:02} ", font=small, fill="#64758b"); x += 27
        for text, color in segments:
            draw.text((x,y), text, font=mono, fill=color)
            x += draw.textlength(text, font=mono)
    # A second text area exercises light text and colored glyphs on dark UI.
    if width > 1400:
        left = min(1250, width//2 + 80)
        draw.rounded_rectangle((left,70,width-20,min(height-30,1120)), radius=12, fill="#16242d")
        for y in range(96, min(height-50,1100), 24):
            draw.text((left+18,y), "$ status: conectado  RGB 0123456789  []{} != <= >=", font=mono,
                      fill=("#81e5c2" if (y//24)%2 else "#e7b165"))
    return image


def baseline_encode(source, mode, tier):
    """The pre-7.5 in-place fallback and resampling policy, unchanged."""
    image = source
    for index in range(tier, 5):
        width,height,quality,fps,subsampling = BASELINE_PROFILES[mode][index]
        method = (Image.Resampling.BICUBIC if mode == "quality" or index == 0 else Image.Resampling.BILINEAR)
        image.thumbnail((width,height), method)
        with BytesIO() as output:
            image.save(output, "JPEG", quality=quality, subsampling=subsampling, optimize=False)
            data = output.getvalue()
        if 4*((len(data)+2)//3)+32768 <= STREAM_POLICIES[mode].frame_budget:
            result = data, {"name":NAMES[index],"width":width,"height":height,"quality":quality,"fps":fps,"subsampling":subsampling}, image.size
            image.close()
            return result
    image.close()
    raise ValueError("Baseline could not fit its image budget")


def ssim_rgb_8x8(reference, candidate):
    """Mean RGB SSIM over nonoverlapping uniform 8x8 population windows."""
    height,width,_ = reference.shape
    height -= height%8; width -= width%8
    a = reference[:height,:width].astype(np.float64).reshape(height//8,8,width//8,8,3)
    b = candidate[:height,:width].astype(np.float64).reshape(height//8,8,width//8,8,3)
    ma,mb = a.mean(axis=(1,3)),b.mean(axis=(1,3))
    va,vb = (a*a).mean(axis=(1,3))-ma*ma,(b*b).mean(axis=(1,3))-mb*mb
    covariance = (a*b).mean(axis=(1,3))-ma*mb
    c1,c2 = (0.01*255)**2,(0.03*255)**2
    return float(np.mean(((2*ma*mb+c1)*(2*covariance+c2))/((ma*ma+mb*mb+c1)*(va+vb+c2))))


def inspect_crop(source, data):
    with Image.open(BytesIO(data)) as decoded:
        image = decoded.convert("RGB")
    if image.size != source.size:
        full = image.resize(source.size, Image.Resampling.LANCZOS)
        image.close(); image = full
    box = (32,94,672,454)
    actual = image.crop(box); reference = source.crop(box); image.close()
    a,b = np.asarray(reference),np.asarray(actual)
    mse = float(np.mean((a.astype(np.float64)-b.astype(np.float64))**2))
    metrics = {"crop_box_at_native_source_pixels":list(box),
               "rgb_psnr_db_against_native_source":None if mse == 0 else 10*math.log10(255**2/mse),
               "rgb_ssim_uniform_nonoverlap_8x8_against_native_source":ssim_rgb_8x8(a,b),
               "rgb_mean_absolute_error_against_native_source":float(np.mean(np.abs(a.astype(np.float64)-b)))}
    reference.close()
    return metrics, actual


def measure(source, mode, tier, iterations, old):
    values=[]
    for _ in range(iterations):
        # The old Host received a fresh RGB capture before encoding. Its test
        # fixture clone is not part of encoder CPU time; the new encoder's own
        # temporary copies, when necessary, remain inside the timed region.
        baseline_input=source.copy() if old else None
        start=time.perf_counter()
        if old:
            data,profile,size=baseline_encode(baseline_input,mode,tier)
        else:
            data,effective,size=encode_screen_image(source,STREAM_POLICIES[mode].profiles[tier],Image,mode)
            profile=asdict(effective)
        values.append((time.perf_counter()-start)*1000)
    metrics,crop=inspect_crop(source,data)
    result={"requested_tier":NAMES[tier],"actual_profile":profile,"encoded_dimensions":list(size),
            "jpeg_bytes":len(data),"wire_bytes_estimate":4*((len(data)+2)//3)+32768,
            "encode_and_resize_ms_median":statistics.median(values),**metrics}
    assert result["wire_bytes_estimate"] <= STREAM_POLICIES[mode].frame_budget
    return result,crop


def panel(source, before, after, path, title):
    box=(32,94,512,274)
    original=source.crop(box)
    # The metric crop has the same origin but is wider and taller.
    crops=(original,before.crop((0,0,480,180)),after.crop((0,0,480,180)))
    canvas=Image.new("RGB",(1000,1290),"#edf2f6")
    draw=ImageDraw.Draw(canvas);font=ImageFont.truetype(FONT_PATH,17)
    draw.text((20,12),title,font=font,fill="#17374a")
    labels=("Original - 13 px na captura", "7.4 - JPEG recebido", "7.5 - JPEG recebido")
    for i,(crop,label) in enumerate(zip(crops,labels)):
        y=50+i*410
        draw.text((20,y),label+" | ampliacao 2x sem filtro",font=font,fill="#17374a")
        canvas.paste(crop.resize((960,360),Image.Resampling.NEAREST),(20,y+32))
        crop.close()
    canvas.save(path);canvas.close()


def run(iterations, output):
    output.mkdir(parents=True,exist_ok=True)
    cases=[]
    selected={((1920,1080),"auto",1),((2560,1440),"quality",1),((3840,2160),"auto",0),((3840,2160),"quality",0)}
    for size in ((1920,1080),(2560,1440),(3840,2160)):
        source=desktop(size)
        for mode in ("auto","quality"):
            for tier in (1,0):
                before,before_crop=measure(source,mode,tier,iterations,True)
                after,after_crop=measure(source,mode,tier,iterations,False)
                case={"source_dimensions":list(size),"mode":mode,"tier":NAMES[tier],"release_7_4":before,"release_7_5":after}
                if (size,mode,tier) in selected:
                    name=f"comparison_{size[0]}x{size[1]}_{mode}_{NAMES[tier]}.png"
                    panel(source,before_crop,after_crop,output/name,f"{size[0]} x {size[1]} | {mode} | {NAMES[tier]}")
                    case["inspection_image"]=name
                cases.append(case);before_crop.close();after_crop.close()
        source.close()
    # The complexity bound is exercised on actual random 4K JPEGs as well.
    source=Image.frombytes("RGB",(3840,2160),random.Random(7500).randbytes(3840*2160*3))
    entropy={}
    for mode,policy in STREAM_POLICIES.items():
        start=time.perf_counter();data,actual,size=encode_screen_image(source,policy.profiles[0],Image,mode)
        entropy[mode]={"actual_profile":asdict(actual),"encoded_dimensions":list(size),
                       "jpeg_bytes":len(data),"wire_bytes_estimate":4*((len(data)+2)//3)+32768,
                       "encode_and_fallback_ms":(time.perf_counter()-start)*1000}
        assert entropy[mode]["wire_bytes_estimate"]<=policy.frame_budget
    source.close()
    report={"version":"7.5.0","baseline_commit":BASELINE_COMMIT,"scope":"Synthetic text/codedesktop pixels and local CPU only; no network/physical-device latency claim",
            "python":platform.python_version(),"pillow":Image.__version__,"numpy":np.__version__,"platform":platform.platform(),
            "iterations":iterations,"font_path":FONT_PATH,"source_font_pixels":{"code":13,"line_numbers":11},
            "metric_definition":"Both JPEGs reconstructed at the SAME native source size with Lanczos, then compared to the same 640x360 text crop; RGB PSNR and mean uniform nonoverlapping 8x8 RGB SSIM (population covariance). No cross-resolution-only JPEG compression score.",
            "policies":{mode:{**asdict(policy),"profiles":[asdict(p) for p in policy.profiles]} for mode,policy in STREAM_POLICIES.items()},
            "cases":cases,"high_entropy_4k_fallback":entropy,
            "limitations":["Synthetic source and a shared Linux CPU; platform screen scaling, network congestion, handheld zoom and actual display appearance are not measured.",
                           "Quality can retain native pixels up to 3840x2160 when content fits 2MiB wire budget and CPU/network feedback permits; higher entropy triggers fallback before enqueueing.",
                           "More detail can require more bytes or fewer updates; no claim of universal zero/lower delay.",
                           "SSIM here uses explicitly defined nonoverlapping RGB windows; it is not the Gaussian-window luminance SSIM from other libraries.",
                           "Comparison PNGs use only 2x nearest-neighbor enlargement; no sharpening, super-resolution or generated pixels."]}
    (output/"DESEMPENHO_7_5_0.json").write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return report


if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--iterations",type=int,default=5)
    parser.add_argument("--output",type=Path,default=Path(__file__).resolve().parents[1]/"docs"/"quality-7.5")
    args=parser.parse_args();assert 1<=args.iterations<=30
    result=run(args.iterations,args.output)
    print(json.dumps({"version":result["version"],"cases":len(result["cases"]),"output":str(args.output)},ensure_ascii=False))
