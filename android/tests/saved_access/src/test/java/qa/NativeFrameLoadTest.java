package qa;

import android.app.Activity;
import android.graphics.*;
import android.os.Looper;
import android.util.Base64;
import android.view.View;
import android.widget.FrameLayout;
import br.com.perabru.minianydesk.RemoteScreenView;
import br.com.perabru.minianydesk.StreamFeedback;
import kotlin.Unit;
import org.json.JSONObject;
import org.junit.After;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.Shadows;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;
import org.robolectric.util.ReflectionHelpers;
import java.io.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;
import static org.junit.Assert.*;

/** Actual native codecs/Canvas and production newest-only viewer; no phone/GPU or network benchmark. */
@RunWith(RobolectricTestRunner.class)
@Config(manifest=Config.NONE,sdk=35,qualifiers="mdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class NativeFrameLoadTest {
    final List<Scene> scenes=new ArrayList<>();
    @After public void close(){for(Scene scene:scenes)scene.owner.pause().stop().destroy();}
    static class Worker extends AbstractExecutorService {
        final ArrayDeque<Runnable> jobs=new ArrayDeque<>();boolean stopped;long wallNanos;int executions;
        public void execute(Runnable task){jobs.add(task);}public void shutdown(){stopped=true;}
        public List<Runnable> shutdownNow(){stopped=true;List<Runnable> pending=new ArrayList<>(jobs);jobs.clear();return pending;}
        public boolean isShutdown(){return stopped;}public boolean isTerminated(){return stopped&&jobs.isEmpty();}
        public boolean awaitTermination(long timeout,TimeUnit unit){return isTerminated();}
        void decode()throws Exception{
            assertEquals("Exactly one decoder task may be outstanding",1,jobs.size());Runnable task=jobs.remove();
            AtomicReference<Throwable> error=new AtomicReference<>();Thread worker=new Thread(()->{try{assertNotEquals(Thread.currentThread(),Looper.getMainLooper().getThread());long start=System.nanoTime();task.run();wallNanos+=System.nanoTime()-start;executions++;}catch(Throwable failure){error.set(failure);}},"qa-native-frame-load");
            worker.start();worker.join(15000);assertFalse("Native decoder did not finish",worker.isAlive());if(error.get()!=null)throw new AssertionError(error.get());
        }
    }
    class Scene {
        final ActivityController<Activity> owner;final RemoteScreenView view;final Worker worker=new Worker();
        final StreamFeedback feedback=new StreamFeedback();final List<JSONObject> acks=new ArrayList<>();final String capture;
        Scene(String capture){
            this.capture=capture;owner=Robolectric.buildActivity(Activity.class).setup().visible();scenes.add(this);view=new RemoteScreenView(owner.get());
            ((ExecutorService)ReflectionHelpers.getField(view,"executor")).shutdownNow();ReflectionHelpers.setField(view,"executor",worker);
            owner.get().setContentView(view,new FrameLayout.LayoutParams(640,360));Shadows.shadowOf(Looper.getMainLooper()).idle();layout();
            view.setOnFramePresented((info,decode,render)->{assertEquals(Looper.getMainLooper(),Looper.myLooper());JSONObject ack=feedback.presented(info,decode,render);if(ack!=null)acks.add(ack);return Unit.INSTANCE;});
        }
        void layout(){view.measure(View.MeasureSpec.makeMeasureSpec(640,1073741824),View.MeasureSpec.makeMeasureSpec(360,1073741824));view.layout(0,0,640,360);}
        void offer(String image,int width,int height,long id)throws Exception{view.showFrame(new JSONObject().put("type","screen").put("image",image).put("captureId",capture).put("width",width).put("height",height).put("frameWidth",width).put("frameHeight",height).put("streamVersion",1).put("frameId",id));}
        void decode()throws Exception{worker.decode();assertNull(((AtomicReference<?>)ReflectionHelpers.getField(view,"rawFrame")).get());assertTrue(worker.jobs.isEmpty());}
        void draw(){Shadows.shadowOf(Looper.getMainLooper()).idle();layout();Bitmap out=Bitmap.createBitmap(640,360,Bitmap.Config.ARGB_8888);view.draw(new Canvas(out));out.recycle();Shadows.shadowOf(Looper.getMainLooper()).idle();assertNull(((AtomicReference<?>)ReflectionHelpers.getField(view,"decodedFrame")).get());}
        Bitmap displayed(){return ReflectionHelpers.getField(ReflectionHelpers.getField(view,"displayed"),"bitmap");}
    }
    static String image(int width,int height,Bitmap.CompressFormat format,int color)throws Exception{
        Bitmap source=Bitmap.createBitmap(width,height,Bitmap.Config.ARGB_8888);Canvas canvas=new Canvas(source);canvas.drawColor(color);Paint text=new Paint(Paint.ANTI_ALIAS_FLAG);text.setColor(Color.rgb(6,44,90));text.setTypeface(Typeface.MONOSPACE);text.setTextSize(13);
        for(int y=20;y<height;y+=24)canvas.drawText("AliançaDX · movimento, letras 13px · 0123456789",20,y,text);
        ByteArrayOutputStream encoded=new ByteArrayOutputStream();assertTrue(source.compress(format,94,encoded));source.recycle();return Base64.encodeToString(encoded.toByteArray(),Base64.NO_WRAP);
    }
    static void report(String name,JSONObject report)throws Exception{
        report.put("scope","Native Android API35 codecs/Canvas on a test VM; synthetic scenes, no physical phone/GPU or internet timing");
        File target=new File("output/performance78-"+name+".json");try(Writer writer=new FileWriter(target)){writer.write(report.toString(2));}
        System.out.println("ANDROID_NATIVE_LOAD "+report);
    }
    @Test public void fourKFrameBurstKeepsOnlyNewestImageAndOneRealCumulativeAck()throws Exception{
        Scene s=new Scene("native-burst");String jpeg=image(3840,2160,Bitmap.CompressFormat.JPEG,Color.WHITE),png=image(3840,2160,Bitmap.CompressFormat.PNG,Color.rgb(230,250,239));
        for(int id=1;id<1000;id++)s.offer(jpeg,3840,2160,id);s.offer(png,3840,2160,1000);
        assertEquals(1,s.worker.jobs.size());assertTrue(s.acks.isEmpty());assertNull(ReflectionHelpers.getField(s.view,"displayed"));
        s.decode();assertTrue("Decode alone cannot grant presentation credit",s.acks.isEmpty());s.draw();assertEquals(1,s.acks.size());assertEquals(1000,s.acks.get(0).getLong("frameId"));assertTrue(s.acks.get(0).getBoolean("cumulative"));
        assertEquals(3840,s.displayed().getWidth());assertEquals(2160,s.displayed().getHeight());assertEquals(Color.rgb(230,250,239),s.displayed().getPixel(3000,2000));assertTrue(s.view.getViewInfoText().contains("sem perdas"));
        for(int i=0;i<30;i++)s.draw();assertEquals("Cursor/view redraws cannot duplicate a frame acknowledgement",1,s.acks.size());assertEquals(1,s.worker.executions);
        report("burst",new JSONObject().put("offered_frames",1000).put("decoder_tasks",1).put("acks",1).put("presented_id",1000).put("original_pixels",true).put("decode_task_wall_ms",s.worker.wallNanos/1e6));
    }
    @Test public void progressiveMotionAndPngRefinementStayCurrentWithoutDecodeOrAckBacklog()throws Exception{
        Scene s=new Scene("native-motion");String light=image(1920,1080,Bitmap.CompressFormat.JPEG,Color.WHITE),dark=image(1920,1080,Bitmap.CompressFormat.JPEG,Color.rgb(90,120,160));
        for(int id=1;id<=24;id++){
            s.offer(id%2==0?dark:light,1920,1080,id);s.decode();assertEquals(id-1,s.acks.size());s.draw();assertEquals(id,s.acks.size());assertEquals(id,s.acks.get(id-1).getLong("frameId"));assertFalse(s.view.getViewInfoText().contains("sem perdas"));
        }
        String exact=image(3840,2160,Bitmap.CompressFormat.PNG,Color.rgb(230,250,239));s.offer(exact,3840,2160,25);s.decode();s.draw();assertEquals(25,s.acks.size());assertEquals(3840,s.displayed().getWidth());assertTrue(s.view.getViewInfoText().contains("sem perdas"));
        report("motion",new JSONObject().put("progressive_jpeg_frames",24).put("final_native_png",true).put("decoder_tasks",25).put("acks",25).put("decode_task_wall_ms",s.worker.wallNanos/1e6));
    }
    @Test public void fourLoadedViewersRetainIndependentNewestFramesAndCredits()throws Exception{
        Scene[] viewers=new Scene[4];String[] pixels=new String[4];
        for(int session=0;session<4;session++){viewers[session]=new Scene("independent-"+session);pixels[session]=image(1920,1080,Bitmap.CompressFormat.PNG,Color.rgb(180+session*10,240-session*10,210));}
        for(int round=1;round<=100;round++)for(int session=0;session<4;session++)viewers[session].offer(pixels[session],1920,1080,session*1000+round);
        for(int session=0;session<4;session++){
            Scene s=viewers[session];s.decode();s.draw();assertEquals(1,s.acks.size());assertEquals(session*1000+100,s.acks.get(0).getLong("frameId"));assertEquals(Color.rgb(180+session*10,240-session*10,210),s.displayed().getPixel(1500,900));
            assertTrue(s.view.retainsUnchangedFrame(new JSONObject().put("type","screen_unchanged").put("streamVersion",1).put("frameId",session*1000+100).put("captureId",s.capture))==s.view.hasWindowFocus());
        }
        report("four-viewers",new JSONObject().put("viewers",4).put("offered_frames",400).put("decoder_tasks",4).put("acks",4).put("independent_pixels_and_frame_ids",true));
    }
}
