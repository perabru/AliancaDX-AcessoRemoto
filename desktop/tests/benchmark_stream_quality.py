#!/usr/bin/env python3
"""Compare the historical 7.1 JPEG policy to the current 7.5 encoder policy.

Synthetic desktop pixels only: this is neither an internet speed test nor a
measurement on a physical Android/iPhone/Windows installation. Timing depends
on this CPU and Pillow build. The temporal network audit is separate.
"""
from __future__ import annotations
import argparse
from io import BytesIO
import json
from pathlib import Path
import platform
import random
import statistics
import sys
import time

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adaptive_stream import PROFILES, capture_rgb_image, encode_screen_image

BASELINE = (
    ('detail',1920,1080,65,20,2),('balanced',1280,720,55,12,2),
    ('economy',960,540,44,10,2),('low',640,360,32,6,2),('minimum',480,270,25,4,2),
)


def desktop_document():
    image=Image.new('RGB',(1920,1080),'#f3f5f7');draw=ImageDraw.Draw(image)
    font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',18)
    for y in range(20,1060,28):
        for color,x in (('#203d56',20),('#d91b29',650),('#087b32',1280)):
            draw.text((x,y),'AliancaDX 0123456789 Texto fino RGB',font=font,fill=color)
    return image


def median_ms(fn, iterations):
    values=[]
    for _ in range(iterations):
        started=time.perf_counter();result=fn();values.append((time.perf_counter()-started)*1000)
        if hasattr(result,'close'):result.close()
    return statistics.median(values)


def encode(source,width,height,quality,subsampling,iterations):
    target=source.copy()
    target.thumbnail((width,height),Image.Resampling.BICUBIC if quality>=76 else Image.Resampling.BILINEAR)
    def jpeg():
        with BytesIO() as out:
            target.save(out,'JPEG',quality=quality,subsampling=subsampling,optimize=False)
            return out.getvalue()
    elapsed=median_ms(jpeg,iterations);data=jpeg()
    with Image.open(BytesIO(data)) as decoded:
        diff=ImageStat.Stat(ImageChops.difference(target,decoded.convert('RGB')))
    target.close()
    return {'jpeg_bytes':len(data),'wire_bytes_estimate':4*((len(data)+2)//3)+32768,
            'encode_ms_median':elapsed,'compression_mean_absolute_rgb_error':statistics.mean(diff.mean)}


def run(iterations):
    image=desktop_document();rows=[]
    for baseline,current in zip(BASELINE,PROFILES):
        name,w,h,q,fps,sub=baseline
        a=encode(image,w,h,q,sub,iterations)
        b=encode(image,current.width,current.height,current.quality,current.subsampling,iterations)
        rows.append({'profile':name,'dimensions':[w,h],'baseline_7_1':{'quality':q,'subsampling':sub,**a},
                     'release_7_5':{'quality':current.quality,'subsampling':current.subsampling,**b}})
    # Convert the same BGRA bytes to RGB, with identical output pixels.
    width,height=image.size;rgb=image.tobytes();bgra=image.convert('RGBA').tobytes('raw','BGRA')
    class Shot:
        size=(width,height)
        @property
        def rgb(self):
            out=bytearray(width*height*3)
            out[::3]=bgra[2::4];out[1::3]=bgra[1::4];out[2::3]=bgra[::4]
            return bytes(out)
    Shot.bgra=bgra
    shot=Shot()
    before=median_ms(lambda:Image.frombytes('RGB',shot.size,shot.rgb),iterations)
    after=median_ms(lambda:capture_rgb_image(shot,Image),iterations)
    converted=capture_rgb_image(shot,Image);assert converted.tobytes()==rgb;converted.close()
    # A high-entropy FullHD frame still fits the authenticated 4MiB message cap.
    noise=Image.frombytes('RGB',image.size,random.Random(7200).randbytes(width*height*3))
    maximum=encode(noise,1920,1080,PROFILES[0].quality,PROFILES[0].subsampling,1)
    data,effective,size=encode_screen_image(noise,PROFILES[0],Image)
    bounded={'jpeg_bytes':len(data),'wire_bytes_estimate':4*((len(data)+2)//3)+32768,
             'actual_profile':effective.name,'dimensions':list(size)}
    assert bounded['wire_bytes_estimate']<=1024*1024
    noise.close();image.close()
    return {'version':'7.5.0','historical_baseline_version':'7.1.0',
            'scope':'Local synthetic pixel/JPEG/CPU benchmark; not physical-device or internet latency',
            'platform':platform.platform(),'python':platform.python_version(),'pillow':Image.__version__,
            'iterations':iterations,'profiles':rows,
            'bgra_to_rgb':{'pixel_exact':True,'baseline_ms_median':before,'raw_bgrx_ms_median':after},
            'fullhd_high_entropy_unbounded_candidate':maximum,
            'high_entropy_admitted_frame_budget':bounded,
            'sources':['https://python-mss.readthedocs.io/v10.2.0/examples.html',
                       'https://pillow.readthedocs.io/en/latest/reference/JpegPresets.html'],
            'tradeoff':'4:4:4 and higher JPEG quality improve colored text but increase bytes; automatic writer/ACK/CPU pressure may lower quality. No universal delay-reduction percentage is asserted.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--iterations',type=int,default=15);parser.add_argument('--output',type=Path)
    args=parser.parse_args();assert 1<=args.iterations<=100
    data=run(args.iterations);text=json.dumps(data,indent=2,ensure_ascii=False)+'\n'
    if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(text)
    print(text)
