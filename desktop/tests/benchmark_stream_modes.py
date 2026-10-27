#!/usr/bin/env python3
"""Local synthetic pixel/CPU comparison of the three 7.3 priorities.

This is not an internet speed test or physical-device benchmark. Resolution,
compression and frame budgets differ deliberately, so JPEG error is reported
against each mode's own resized input, not as an across-resolution score.
"""
from io import BytesIO
import argparse
import json
from pathlib import Path
import platform
import random
import statistics
import sys
import time

from PIL import Image,ImageChops,ImageStat
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from adaptive_stream import STREAM_POLICIES,encode_screen_image
from benchmark_stream_quality import desktop_document


def measure(source,mode,tier,iterations):
    policy=STREAM_POLICIES[mode];durations=[]
    for _ in range(iterations):
        image=source.copy();start=time.perf_counter()
        data,effective=encode_screen_image(image,policy.profiles[tier],Image,mode)
        durations.append((time.perf_counter()-start)*1000)
        with Image.open(BytesIO(data)) as decoded:
            difference=ImageStat.Stat(ImageChops.difference(image,decoded.convert('RGB')))
        result={'requested_profile':policy.profiles[tier].name,'actual_profile':effective.name,
                'dimensions':list(image.size),'jpeg_quality':effective.quality,
                'jpeg_subsampling':effective.subsampling,'target_fps_ceiling':effective.fps,
                'jpeg_bytes':len(data),'wire_bytes_estimate':4*((len(data)+2)//3)+32768,
                'compression_rgb_mean_absolute_error_at_own_resolution':statistics.mean(difference.mean)}
        image.close()
    result['encode_and_resize_ms_median']=statistics.median(durations)
    assert result['wire_bytes_estimate']<=policy.frame_budget
    return result


def run(iterations):
    desktop=desktop_document()
    noise=Image.frombytes('RGB',desktop.size,random.Random(7300).randbytes(desktop.width*desktop.height*3))
    modes={}
    for mode,policy in STREAM_POLICIES.items():
        modes[mode]={'policy':{'frame_budget':policy.frame_budget,'upgrade_projected_write_seconds':policy.upgrade_write,
                              'window_max_write_seconds':policy.window_write,'window_max_cycle_seconds':policy.window_cycle,
                              'slow_write_seconds':policy.write_slow,'processing_budget_seconds':policy.processing_budget},
                     'initial_tier':measure(desktop,mode,1,iterations),
                     'best_tier_after_capacity_recovery':measure(desktop,mode,0,iterations),
                     'high_entropy_fallback':measure(noise,mode,0,1)}
    desktop.close();noise.close()
    return {'version':'7.3.0','scope':'Local synthetic pixels and CPU; no physical-device or internet-latency claims',
            'platform':platform.platform(),'python':platform.python_version(),'pillow':Image.__version__,
            'iterations':iterations,'modes':modes,
            'notes':['Auto retains the 7.2 profiles and adaptation thresholds.',
                     'Latency uses smaller JPEGs and lower image/write budgets; quality accepts more bytes and slower cycles for finer detail.',
                     'Every mode still downgrades automatically on CPU/network pressure; no fixed resolution is guaranteed.',
                     'Only a known authenticated peer may change mode; a mode switch preserves ACK ids, frame numbering and the one-probe bound.',
                     'Quality has a 2MiB encoded image budget, below the 4MiB protocol ceiling; an image above 1MiB cannot open a second in-flight slot.']}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--iterations',type=int,default=12);parser.add_argument('--output',type=Path)
    args=parser.parse_args();assert 1<=args.iterations<=100
    data=run(args.iterations);output=json.dumps(data,indent=2,ensure_ascii=False)+'\n'
    if args.output:args.output.parent.mkdir(exist_ok=True,parents=True);args.output.write_text(output)
    print(output)
