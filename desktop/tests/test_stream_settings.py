"""Authenticated priorities, preserved ACKs and exact applied-frame metadata."""
from pathlib import Path
from types import SimpleNamespace
import random
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from adaptive_stream import AdaptiveStream, PROFILES, STREAM_POLICIES, encode_screen_image
import host
from test_host_stability import Capture, Image, Peer, WaitScript, make_app, providers

IDLE={'pending':False,'writing':False,'lastFrameBytes':100_000,'lastFrameDuration':.01}


class StreamSettingsTests(unittest.TestCase):
    def test_default_auto_profiles_and_wire_fields(self):
        stream=AdaptiveStream()
        self.assertEqual(stream.mode,'auto');self.assertIs(stream.profiles,PROFILES)
        self.assertEqual(stream.frame_fields(0),{'streamProfile':'balanced','streamSettingsVersion':1,'streamMode':'auto'})

    def test_mode_and_version_validation_has_no_side_effects(self):
        stream=AdaptiveStream();stream.enable(1);pending=stream.frame_fields(1)
        for version in (True,False,1.,'1',0,2,None):
            self.assertFalse(stream.set_mode(version,'quality',2));self.assertEqual(stream.mode,'auto')
        for mode in (True,1,None,'Quality','AUTO','quality ','1080p',{},[]):
            self.assertFalse(stream.set_mode(1,mode,2));self.assertEqual(stream.mode,'auto')
        self.assertEqual(stream.outstanding_frame_id,pending['frameId'])
        self.assertFalse(stream.set_mode(1,'quality',float('nan')))

    def test_switch_preserves_ack_ids_and_applies_next_frame(self):
        stream=AdaptiveStream();stream.enable(1);old=stream.frame_fields(1,100_000)
        self.assertTrue(stream.set_mode(1,'quality',1.01))
        self.assertEqual(stream.outstanding_frame_id,old['frameId'])
        self.assertFalse(stream.ready(1.1,IDLE));self.assertIsNone(stream.frame_fields(1.1,100_000))
        self.assertTrue(stream.feedback({'frameId':old['frameId']},1.12))
        new=stream.frame_fields(1.13,100_000)
        self.assertGreater(new['frameId'],old['frameId'])
        self.assertEqual((new['streamSettingsVersion'],new['streamMode']),(1,'quality'))

    def test_switch_does_not_reset_or_repeat_missing_ack_probe(self):
        stream=AdaptiveStream();stream.enable(1);first=stream.frame_fields(0,100_000)
        self.assertTrue(stream.ready(2,IDLE));probe=stream.frame_fields(2,100_000)
        for i,mode in enumerate(('quality','latency','auto','quality')):
            self.assertTrue(stream.set_mode(1,mode,3+i));self.assertFalse(stream.ready(10+i,IDLE))
        self.assertEqual(stream.outstanding_frame_id,probe['frameId'])
        self.assertFalse(stream.feedback({'frameId':first['frameId']},20))
        self.assertTrue(stream.feedback({'frameId':probe['frameId']},20))

    def test_repeat_same_mode_is_idempotent_and_new_session_is_auto(self):
        stream=AdaptiveStream();stream.set_mode(1,'latency',1)
        stream._good_samples=8;stream._last_change=1
        self.assertTrue(stream.set_mode(1,'latency',2));self.assertEqual(stream._good_samples,8)
        self.assertEqual(stream._last_change,1);self.assertEqual(AdaptiveStream().mode,'auto')

    def test_priorities_change_real_image_budget_dimensions_and_quality(self):
        auto=STREAM_POLICIES['auto'];latency=STREAM_POLICIES['latency'];quality=STREAM_POLICIES['quality']
        self.assertLess(latency.frame_budget,auto.frame_budget);self.assertLess(auto.frame_budget,quality.frame_budget)
        self.assertLess(latency.profiles[1].width,auto.profiles[1].width)
        self.assertGreater(quality.profiles[1].quality,auto.profiles[1].quality)
        self.assertLess(latency.upgrade_write,auto.upgrade_write);self.assertLess(auto.upgrade_write,quality.upgrade_write)
        self.assertEqual(quality.frame_budget,2*1024*1024)
        self.assertEqual(max(p.width for p in quality.profiles),3840)

    def test_each_mode_still_degrades_and_recovers_automatically(self):
        for mode in STREAM_POLICIES:
            stream=AdaptiveStream();stream.enable(1);stream.set_mode(1,mode,0);now=1.
            for _ in range(12):
                stream.ready(now,{**IDLE,'lastFrameDuration':.8})
                frame=stream.frame_fields(now,100_000);stream.feedback({'frameId':frame['frameId']},now+.9);now+=1.
            self.assertEqual(stream.profile.name,'minimum',mode)
            for _ in range(110):
                stream.ready(now,{**IDLE,'lastFrameDuration':.005})
                frame=stream.frame_fields(now,20_000)
                stream.feedback({'frameId':frame['frameId'],'decodeMs':2,'renderMs':2},now+.05);now+=.25
            self.assertEqual(stream.profile.name,'detail',mode)

    def test_mode_switch_drains_two_reserved_frames_before_more_capture(self):
        stream=AdaptiveStream();stream.enable(1,2,True)
        for i in range(3):
            stream.ready(i,IDLE);frame=stream.frame_fields(i,100_000)
            stream.feedback({'frameId':frame['frameId'],'cumulative':True},i+.1)
        self.assertEqual(stream.max_in_flight,2)
        first=stream.frame_fields(3,100_000);last=stream.frame_fields(3.01,100_000)
        stream.set_mode(1,'latency',3.02)
        self.assertEqual((stream.max_in_flight,stream.outstanding_count),(1,2))
        self.assertFalse(stream.ready(3.03,IDLE))
        self.assertTrue(stream.feedback({'frameId':last['frameId'],'cumulative':True},3.04))
        self.assertEqual(stream.outstanding_count,0)
        self.assertFalse(stream.feedback({'frameId':first['frameId'],'cumulative':True},3.05))

    def test_large_quality_frame_cannot_open_second_transport_slot(self):
        stream=AdaptiveStream();stream.enable(1,2,True);stream.set_mode(1,'quality',0)
        fields=stream.frame_fields(1,1500_000)
        self.assertEqual(fields['streamMode'],'quality');self.assertEqual(stream.max_in_flight,1)
        self.assertFalse(stream.ready(1.01,IDLE));self.assertIsNone(stream.frame_fields(1.01,100_000))

    def test_real_encoded_noise_obeys_all_mode_budgets_and_protocol_limit(self):
        from PIL import Image as PillowImage
        source=PillowImage.frombytes('RGB',(1920,1080),random.Random(7300).randbytes(1920*1080*3))
        for mode,policy in STREAM_POLICIES.items():
            image=source.copy();data,effective,size=encode_screen_image(image,policy.profiles[0],PillowImage,mode)
            estimate=4*((len(data)+2)//3)+32768
            self.assertLessEqual(estimate,policy.frame_budget,mode);self.assertLess(estimate,4*1024*1024)
            self.assertIn(effective,policy.profiles);self.assertLessEqual(size[0],effective.width)
            self.assertEqual(image.size,source.size);image.close()
        source.close()


class HostStreamSettingsTests(unittest.TestCase):
    def test_only_current_authorized_peer_can_change_priority(self):
        app=make_app();app._message(Peer(),{'type':'stream_settings','version':1,'mode':'quality'})
        self.assertIsNone(getattr(app,'stream',None))
        app._message(app.peer,{'type':'stream_settings','version':True,'mode':'quality'})
        self.assertEqual(app.stream.mode,'auto')
        app._message(app.peer,{'type':'stream_settings','version':1,'mode':'quality'})
        self.assertEqual(app.stream.mode,'quality');self.assertTrue(app.sharing)
        self.assertEqual(app.generation,0)

    def test_state_advertises_capability_without_claiming_a_not_yet_applied_mode(self):
        app=make_app();app._message(app.peer,{'type':'stream_settings','version':1,'mode':'quality'});app._emit_state()
        state=app.peer.messages[-1]
        self.assertEqual(state['streamSettingsVersion'],1);self.assertNotIn('streamMode',state)

    def test_late_mode_switch_discards_old_encoding_and_retains_its_prior_ack(self):
        app=make_app();stream=app._stream_controller();stream.synchronize(app.generation);stream.enable(1,2,True)
        for i in range(3):
            stream.ready(i,IDLE);frame=stream.frame_fields(i,100_000)
            stream.feedback({'frameId':frame['frameId'],'cumulative':True},i+.1)
        pending=stream.frame_fields(3,100_000);previous_screen=app.last_screen
        class SwitchingImage(Image):
            def save(self,output,**options):
                app._message(app.peer,{'type':'stream_settings','version':1,'mode':'latency'})
                super().save(output,**options)
        def finish(event,delay):
            self.assertFalse(any(m['type']=='screen' for m in app.peer.messages))
            self.assertIs(app.last_screen,previous_screen)
            self.assertEqual(stream.outstanding_frame_id,pending['frameId'])
            self.assertTrue(stream.feedback({'frameId':pending['frameId'],'cumulative':True},3.12))
            event.set()
        app.shutdown=WaitScript(finish)
        with providers(Capture,SwitchingImage),patch.object(host.time,'monotonic',return_value=3.01):app._capture_loop()
        self.assertEqual(stream.mode,'latency')

    def test_new_authenticated_session_resets_priority_to_auto(self):
        app=make_app();app._message(app.peer,{'type':'stream_settings','version':1,'mode':'quality'})
        app.peer=None;new_peer=Peer()
        with patch('transfer.FileTransfers',return_value=SimpleNamespace(close=Mock())):
            app._connected(new_peer,'Visitante','127.0.0.1')
        self.assertEqual(app.stream.mode,'auto')

    def test_mode_is_confirmed_by_the_actual_next_encoded_frame(self):
        for mode in STREAM_POLICIES:
            app=make_app();app._message(app.peer,{'type':'stream_settings','version':1,'mode':mode})
            app.shutdown=WaitScript(lambda event,delay:event.set())
            with providers(Capture):app._capture_loop()
            frames=[m for m in app.peer.messages if m['type']=='screen']
            self.assertEqual(len(frames),1);self.assertEqual(frames[0]['streamMode'],mode)
            self.assertEqual(frames[0]['streamSettingsVersion'],1)
            self.assertTrue(app.sharing);self.assertFalse(app.local_paused)


if __name__=='__main__':unittest.main()
