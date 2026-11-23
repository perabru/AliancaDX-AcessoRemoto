package qa;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Base64;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import br.com.perabru.minianydesk.*;
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
import org.robolectric.shadows.ShadowSystemClock;
import org.robolectric.util.ReflectionHelpers;
import java.io.*;
import java.lang.reflect.Method;
import java.net.Socket;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;
import kotlin.Unit;
import static org.junit.Assert.*;

/** Real native viewer and ControllerSession watchdog with synthetic frames and a controlled clock. */
@RunWith(RobolectricTestRunner.class)
@Config(manifest=Config.NONE,sdk=35,qualifiers="mdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class StaticFrameHealthTest {
    final List<Scene> scenes=new ArrayList<>();
    @After public void close(){for(Scene scene:scenes)scene.owner.pause().stop().destroy();}
    static Object invoke(Object target,String name,Object...args)throws Exception{
        for(Method method:target.getClass().getDeclaredMethods())if((method.getName().equals(name)||method.getName().startsWith(name+"$"))&&method.getParameterCount()==args.length){method.setAccessible(true);return method.invoke(target,args);}
        throw new NoSuchMethodException(name);
    }
    static class Worker extends AbstractExecutorService {
        final ArrayDeque<Runnable> jobs=new ArrayDeque<>();boolean stopped;
        public void execute(Runnable task){jobs.add(task);}public void shutdown(){stopped=true;}
        public List<Runnable> shutdownNow(){stopped=true;List<Runnable> pending=new ArrayList<>(jobs);jobs.clear();return pending;}
        public boolean isShutdown(){return stopped;}public boolean isTerminated(){return stopped&&jobs.isEmpty();}
        public boolean awaitTermination(long timeout,TimeUnit unit){return isTerminated();}
        void decode()throws Exception{Thread worker=new Thread(jobs.remove(),"qa-retained-frame");worker.start();worker.join(10000);assertFalse(worker.isAlive());}
    }
    class Scene {
        final ActivityController<MainActivity> owner;final Object session;final RemoteScreenView screen;final Peer peer;final Worker worker=new Worker();
        int presented,frameInfo;
        Scene()throws Exception{
            owner=Robolectric.buildActivity(MainActivity.class).setup().visible();scenes.add(this);
            Object workspace=ReflectionHelpers.getField(owner.get(),"workspace");session=invoke(workspace,"getSelected");screen=ReflectionHelpers.getField(session,"screen");
            peer=new Peer(new Socket(){public InputStream getInputStream(){return new ByteArrayInputStream(new byte[0]);}public OutputStream getOutputStream(){return new ByteArrayOutputStream();}},message->Unit.INSTANCE,reason->Unit.INSTANCE,new PeerPolicy());
            ReflectionHelpers.setField(session,"peer",peer);ReflectionHelpers.setField(session,"sharing",true);ReflectionHelpers.setField(session,"networkSuspended",false);ReflectionHelpers.setField(session,"resumed",true);invoke(session,"showPage","session");
            ((ViewGroup)screen.getParent()).removeView(screen);owner.get().setContentView(screen,new FrameLayout.LayoutParams(640,360));Shadows.shadowOf(Looper.getMainLooper()).idle();layout();owner.windowFocusChanged(true);assertTrue(screen.hasWindowFocus());
            ((ExecutorService)ReflectionHelpers.getField(screen,"executor")).shutdownNow();ReflectionHelpers.setField(screen,"executor",worker);
            final kotlin.jvm.functions.Function3<JSONObject,Long,Long,Unit> previous=screen.getOnFramePresented();screen.setOnFramePresented((info,decode,render)->{presented++;return previous.invoke(info,decode,render);});
            final kotlin.jvm.functions.Function1<JSONObject,Unit> previousInfo=screen.getOnFrameInfo();screen.setOnFrameInfo(info->{frameInfo++;return previousInfo.invoke(info);});
            advance(100);frame(77,"retained-monitor");
        }
        void layout(){screen.measure(View.MeasureSpec.makeMeasureSpec(640,1073741824),View.MeasureSpec.makeMeasureSpec(360,1073741824));screen.layout(0,0,640,360);}
        void frame(long id,String capture)throws Exception{
            Bitmap source=Bitmap.createBitmap(640,360,Bitmap.Config.ARGB_8888);source.eraseColor(Color.rgb(14,110,61));ByteArrayOutputStream png=new ByteArrayOutputStream();assertTrue(source.compress(Bitmap.CompressFormat.PNG,100,png));source.recycle();
            screen.showFrame(new JSONObject().put("type","screen").put("streamVersion",1).put("frameId",id).put("captureId",capture).put("width",640).put("height",360).put("frameWidth",640).put("frameHeight",360).put("image",Base64.encodeToString(png.toByteArray(),Base64.NO_WRAP)));
            worker.decode();Shadows.shadowOf(Looper.getMainLooper()).idle();layout();Bitmap image=Bitmap.createBitmap(640,360,Bitmap.Config.ARGB_8888);screen.draw(new Canvas(image));image.recycle();Shadows.shadowOf(Looper.getMainLooper()).idle();
        }
        JSONObject heartbeat()throws Exception{return new JSONObject().put("type","screen_unchanged").put("streamVersion",1).put("frameId",77).put("captureId","retained-monitor");}
        int generation(){return ReflectionHelpers.getField(session,"sessionGeneration");}
        void receive(JSONObject message)throws Exception{invoke(session,"receive",message,generation());}
        long health(){return ReflectionHelpers.getField(session,"lastFrameDisplayed");}
        void advance(long milliseconds){ShadowSystemClock.advanceBy(Duration.ofMillis(milliseconds));}
        void tick(){((Runnable)ReflectionHelpers.getField(session,"frameHealthTick")).run();}
        void unchanged(JSONObject message)throws Exception{long before=health();advance(10);receive(message);assertEquals(before,health());}
        String state(){StringBuilder state=new StringBuilder("shown="+screen.isShown()+" focus="+screen.hasWindowFocus()+" retained=");try{state.append(screen.retainsUnchangedFrame(heartbeat()));}catch(Exception ignored){}for(String field:new String[]{"resumed","selected","sharing","captureAvailable","frameStale","networkSuspended","ownerPaused","remotePrompt","controllerPrompts","pendingMonitor","lastFrameDisplayed","frameWaitingSince"})state.append(" ").append(field).append("=").append((Object)ReflectionHelpers.getField(session,field));return state.toString();}
    }
    @Test public void validUnchangedChecksKeepStaticImageBeyondWatchdogWithoutNewDrawOrFeedback()throws Exception{
        Scene s=new Scene();assertNotNull(ReflectionHelpers.getField(s.session,"latestFrame"));assertEquals(1,s.presented);assertEquals(1,s.frameInfo);Object displayed=ReflectionHelpers.getField(s.screen,"displayed");
        ReflectionHelpers.setField(s.screen,"inputAllowed",false);
        for(int i=0;i<5;i++){long before=s.health();s.advance(2000);s.receive(s.heartbeat());assertTrue(s.state(),s.health()>before);s.tick();assertFalse((Boolean)ReflectionHelpers.getField(s.session,"frameStale"));assertSame(displayed,ReflectionHelpers.getField(s.screen,"displayed"));}
        assertEquals(1,s.presented);assertEquals(1,s.frameInfo);assertFalse((Boolean)ReflectionHelpers.getField(s.screen,"inputAllowed"));assertNotNull(ReflectionHelpers.getField(s.session,"latestFrame"));
    }
    @Test public void wrongIdentityGenerationAndPermissionStatesCannotExtendRetainedFrameHealth()throws Exception{
        Scene s=new Scene();s.unchanged(s.heartbeat().put("frameId",76));s.unchanged(s.heartbeat().put("frameId","77"));s.unchanged(s.heartbeat().put("captureId","other-monitor"));s.unchanged(s.heartbeat().put("streamVersion",2));
        long before=s.health();s.advance(10);invoke(s.session,"receive",s.heartbeat(),s.generation()-1);assertEquals(before,s.health());
        for(String field:new String[]{"selected","resumed","sharing","captureAvailable"}){ReflectionHelpers.setField(s.session,field,false);s.unchanged(s.heartbeat());ReflectionHelpers.setField(s.session,field,true);}
        for(String field:new String[]{"frameStale","networkSuspended","ownerPaused","remotePrompt"}){ReflectionHelpers.setField(s.session,field,true);s.unchanged(s.heartbeat());ReflectionHelpers.setField(s.session,field,false);}
        ReflectionHelpers.setField(s.session,"controllerPrompts",1);s.unchanged(s.heartbeat());ReflectionHelpers.setField(s.session,"controllerPrompts",0);
        AtomicInteger blocked=ReflectionHelpers.getField(s.session,"pendingBlocks");blocked.set(1);s.unchanged(s.heartbeat());blocked.set(0);
        ReflectionHelpers.setField(s.session,"pendingMonitor","1");s.unchanged(s.heartbeat());ReflectionHelpers.setField(s.session,"pendingMonitor",null);
        s.owner.windowFocusChanged(false);s.unchanged(s.heartbeat());s.owner.windowFocusChanged(true);
        s.peer.close("Test completed");s.unchanged(s.heartbeat());assertEquals(1,s.presented);assertEquals(1,s.frameInfo);
    }
    @Test public void lateHeartbeatCannotReviveExpiredClearedOrUndrawnImage()throws Exception{
        Scene s=new Scene();long before=s.health();s.advance(6001);s.receive(s.heartbeat());assertEquals(before,s.health());s.tick();assertTrue((Boolean)ReflectionHelpers.getField(s.session,"frameStale"));assertNull(ReflectionHelpers.getField(s.session,"latestFrame"));assertFalse(s.screen.retainsUnchangedFrame(s.heartbeat()));
        s.unchanged(s.heartbeat());assertNull(ReflectionHelpers.getField(s.screen,"displayed"));assertEquals(1,s.presented);
        s.frame(78,"new-monitor");assertEquals(2,s.presented);s.unchanged(s.heartbeat());assertEquals(2,s.frameInfo);
        s.screen.clearFrame();s.unchanged(new JSONObject().put("type","screen_unchanged").put("streamVersion",1).put("frameId",78).put("captureId","new-monitor"));assertNull(ReflectionHelpers.getField(s.screen,"displayed"));
    }
}
