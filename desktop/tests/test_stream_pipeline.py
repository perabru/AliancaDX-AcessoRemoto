"""Negotiated flow window, automatic quality and pixel-exact capture fast path."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adaptive_stream import (AdaptiveStream, MAX_PIPELINED_FRAME_BYTES, PROFILES,
                             capture_rgb_image, encode_screen_image)


def stats(size=100_000, duration=.02, **extra):
    return {"pending": False, "writing": False, "lastFrameBytes": size,
            "lastFrameDuration": duration, **extra}


class PipelineTests(unittest.TestCase):
    def stream(self, **kwargs):
        s = AdaptiveStream(); s.synchronize(1)
        s.enable(1, kwargs.get('max_in_flight', 2), kwargs.get('ack_cumulative', True))
        return s

    def sample(self, s, now, cycle=.12, size=100_000, write=.02, **extra):
        self.assertTrue(s.ready(now, stats(size, write)))
        fields = s.frame_fields(now, size)
        self.assertIsNotNone(fields)
        self.assertTrue(s.feedback({"frameId": fields['frameId'], 'cumulative': True,
                                    'decodeMs': 3, 'renderMs': 4, **extra}, now + cycle))
        return now + cycle

    def warm(self, s=None):
        s = s or self.stream(); now = 1.
        for _ in range(3): now = self.sample(s, now) + .05
        return s, now

    def test_exact_capabilities_and_real_writer_timing_required(self):
        for cap, ack in ((True, True), (2., True), ('2', True), (2, 1), (2, 'true'), (2, False)):
            s, _ = self.warm(self.stream(max_in_flight=cap, ack_cumulative=ack))
            self.assertEqual(s.max_in_flight, 1)
        s = self.stream()
        for i in range(3):
            f = s.frame_fields(i, 100_000); s.feedback({'frameId': f['frameId'], 'cumulative': True}, i+.1)
        self.assertEqual(s.max_in_flight, 1)
        self.assertEqual(self.warm()[0].max_in_flight, 2)

    def test_two_known_reservations_and_no_third_or_future_ack(self):
        s, now = self.warm()
        a = s.frame_fields(now, 100_000); b = s.frame_fields(now+.01, 100_000)
        self.assertEqual(s.outstanding_count, 2)
        self.assertFalse(s.ready(now+.02, stats()))
        self.assertIsNone(s.frame_fields(now+.02, 100_000))
        self.assertFalse(s.feedback({'frameId': b['frameId']+1, 'cumulative': True}, now+.03))
        self.assertTrue(s.feedback({'frameId': b['frameId'], 'cumulative': True}, now+.04))
        self.assertEqual(s.outstanding_count, 0)
        self.assertFalse(s.feedback({'frameId': a['frameId'], 'cumulative': True}, now+.05))

    def test_non_boolean_cumulative_only_acknowledges_exact_frame(self):
        s, now = self.warm(); a=s.frame_fields(now, 100_000); b=s.frame_fields(now+.01,100_000)
        self.assertTrue(s.feedback({'frameId': b['frameId'], 'cumulative': 1}, now+.05))
        self.assertEqual(s.outstanding_count,1)
        self.assertEqual(s.outstanding_frame_id,a['frameId'])

    def test_large_new_candidate_collapses_before_a_second_large_frame(self):
        s, now = self.warm(); a=s.frame_fields(now,100_000)
        self.assertIsNone(s.frame_fields(now+.01,MAX_PIPELINED_FRAME_BYTES+1))
        self.assertEqual((s.max_in_flight,s.outstanding_count),(1,1))
        self.assertTrue(s.feedback({'frameId': a['frameId'], 'cumulative': True},now+.12))
        self.assertIsNotNone(s.frame_fields(now+.13,MAX_PIPELINED_FRAME_BYTES+1))
        self.assertEqual((s.max_in_flight,s.outstanding_count),(1,1))

    def test_waiting_or_slow_writer_collapses_and_cannot_open_capacity(self):
        s, now=self.warm(); s.frame_fields(now,100_000)
        self.assertFalse(s.ready(now+.26,stats())); self.assertEqual(s.max_in_flight,1)
        s, now=self.warm(); s.frame_fields(now,100_000)
        self.assertFalse(s.ready(now+.01,stats(writing=True)))
        self.assertFalse(s.ready(now+.17,stats(writing=True)))
        self.assertEqual(s.max_in_flight,1)
        s, now=self.warm(); self.sample(s,now,cycle=.3,write=.2)
        self.assertEqual(s.max_in_flight,1)

    def test_probe_retires_known_ids_once_and_resume_resets_capacity(self):
        s,now=self.warm(); a=s.frame_fields(now,100_000); b=s.frame_fields(now+.01,100_000)
        self.assertTrue(s.ready(now+2.01,stats()))
        probe=s.frame_fields(now+2.01,100_000)
        self.assertEqual(s.outstanding_count,1)
        self.assertFalse(s.feedback({'frameId': a['frameId'],'cumulative':True},now+2.02))
        self.assertFalse(s.feedback({'frameId': b['frameId'],'cumulative':True},now+2.02))
        for t in (5,10,100): self.assertFalse(s.ready(now+t,stats()))
        self.assertTrue(s.resume(1,now+101));self.assertEqual((s.outstanding_count,s.max_in_flight),(0,1))
        s.synchronize(2);self.assertFalse(s.feedback({'frameId':probe['frameId'],'cumulative':True},now+102))

    def test_quality_hysteresis_changes_one_tier_and_recovers_with_capacity(self):
        s=self.stream();before=s.profile
        now=self.sample(s,1,cycle=.9,write=.7)
        self.assertEqual(s.profile,PROFILES[2])
        for _ in range(3): now=self.sample(s,now+.001,cycle=.1,write=.02,decodeMs=100)
        self.assertEqual(s.profile,PROFILES[2])
        for _ in range(100): now=self.sample(s,now+.1,cycle=.05,write=.004)
        self.assertEqual(s.profile,PROFILES[0]);self.assertGreater(s.profile.quality,before.quality)

    def test_stable_high_rtt_fast_link_preserves_detail_slow_link_does_not(self):
        fast,slow=self.stream(),self.stream();nf=ns=1.
        for _ in range(80):
            nf=self.sample(fast,nf+.1,cycle=.25,write=.01)
            ns=self.sample(slow,ns+.1,cycle=.8,write=.55)
        self.assertEqual(fast.profile,PROFILES[0])
        self.assertEqual(slow.profile,PROFILES[-1])
        self.assertEqual(fast.max_in_flight,1) # RTT may limit smoothness; never fake a shorter cycle.

    def test_unaffordable_detail_upgrade_waits_for_faster_actual_writes(self):
        s=self.stream();now=1.
        for _ in range(80): now=self.sample(s,now+.1,cycle=.13,write=.06)
        self.assertEqual(s.profile,PROFILES[1])
        for _ in range(40): now=self.sample(s,now+.1,cycle=.13,write=.02)
        self.assertEqual(s.profile,PROFILES[0])

    def test_local_encoder_pressure_reduces_next_capture_without_manual_mode(self):
        s=self.stream();s.capture_finished(.2,1);self.assertEqual(s.profile,PROFILES[2])
        s.capture_finished(.2,1.1);self.assertEqual(s.profile,PROFILES[2])
        s.capture_finished(.2,2);self.assertEqual(s.profile,PROFILES[3])


class CapturePixelTests(unittest.TestCase):
    def test_bgra_fast_path_is_pixel_exact_and_does_not_read_python_rgb(self):
        from PIL import Image
        class Shot:
            size=(2,2)
            bgra=bytes((10,20,30,0,40,50,60,255,70,80,90,13,100,110,120,99))
            @property
            def rgb(self): raise AssertionError('slow RGB property read')
        image=capture_rgb_image(Shot(),Image)
        self.assertEqual(image.tobytes(),bytes((30,20,10,60,50,40,90,80,70,120,110,100)))
        image.close()

    def test_older_rgb_provider_keeps_its_existing_pixel_path(self):
        from PIL import Image
        image=capture_rgb_image(SimpleNamespace(size=(1,1),rgb=bytes((12,34,56))),Image)
        self.assertEqual(image.getpixel((0,0)),(12,34,56));image.close()

    def test_truncated_bgra_is_not_published_as_a_frame(self):
        from PIL import Image
        with self.assertRaises(ValueError):capture_rgb_image(SimpleNamespace(size=(2,2),bgra=b'bad'),Image)

    def test_high_entropy_frame_falls_back_before_any_transport_write(self):
        import random
        from PIL import Image
        image=Image.frombytes('RGB',(1920,1080),random.Random(72).randbytes(1920*1080*3))
        data,effective=encode_screen_image(image,PROFILES[0],Image)
        self.assertLessEqual(4*((len(data)+2)//3)+32768,MAX_PIPELINED_FRAME_BYTES)
        self.assertNotEqual(effective,PROFILES[0])
        self.assertLessEqual(image.width,effective.width);self.assertLessEqual(image.height,effective.height)
        stream=AdaptiveStream();stream._tier=0;stream.content_limited(effective,10)
        self.assertEqual(stream.profile,effective);self.assertEqual(stream.max_in_flight,1)
        image.close()

    def test_simple_desktop_text_retains_fullhd_and_selected_chroma(self):
        from PIL import Image,ImageDraw
        image=Image.new('RGB',(1920,1080),'white');draw=ImageDraw.Draw(image)
        draw.text((20,20),'AliançaDX - desktop com texto colorido',fill='red')
        data,effective=encode_screen_image(image,PROFILES[0],Image)
        self.assertIs(effective,PROFILES[0]);self.assertEqual(image.size,(1920,1080))
        self.assertLess(len(data),MAX_PIPELINED_FRAME_BYTES);image.close()

    def test_profile_quality_and_chroma_are_automatic_and_bounded(self):
        self.assertEqual([p.subsampling for p in PROFILES],[0,0,2,2,2])
        self.assertEqual(max(p.width for p in PROFILES),1920)
        self.assertTrue(all(a.quality>b.quality for a,b in zip(PROFILES,PROFILES[1:])))


if __name__=='__main__':unittest.main()
