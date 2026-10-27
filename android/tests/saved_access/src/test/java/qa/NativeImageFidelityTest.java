package qa;

import android.app.Activity;
import android.graphics.*;
import android.os.Looper;
import android.util.Base64;
import android.view.View;
import android.widget.FrameLayout;
import br.com.perabru.minianydesk.RemoteScreenView;
import br.com.perabru.minianydesk.ScreenGeometry;
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
import static org.junit.Assert.*;

/** Real platform JPEG decode + native Canvas production viewer, synthetic text; no phone/PC claim. */
@RunWith(RobolectricTestRunner.class)
@Config(manifest=Config.NONE,sdk=35,qualifiers="mdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class NativeImageFidelityTest {
    final List<ActivityController<Activity>> windows=new ArrayList<>();
    @After public void close(){for(ActivityController<Activity> w:windows)w.pause().stop().destroy();}
    static class PausedWorker extends AbstractExecutorService {
        final ArrayDeque<Runnable> jobs=new ArrayDeque<>();boolean stopped;
        public void execute(Runnable task){jobs.add(task);}public void shutdown(){stopped=true;}public List<Runnable> shutdownNow(){stopped=true;List<Runnable> q=new ArrayList<>(jobs);jobs.clear();return q;}
        public boolean isShutdown(){return stopped;}public boolean isTerminated(){return stopped&&jobs.isEmpty();}public boolean awaitTermination(long t,TimeUnit u){return isTerminated();}
        void decode()throws Exception{Runnable task=jobs.remove();Thread thread=new Thread(task,"qa-native-jpeg");thread.start();thread.join(10000);assertFalse("Native fixture decoder did not finish",thread.isAlive());}
    }
    class Scene {
        final RemoteScreenView view;final PausedWorker worker=new PausedWorker();final int width,height;
        Scene(int width,int height){
            this.width=width;this.height=height;ActivityController<Activity> w=Robolectric.buildActivity(Activity.class).setup().visible();windows.add(w);
            view=new RemoteScreenView(w.get());((ExecutorService)ReflectionHelpers.getField(view,"executor")).shutdownNow();ReflectionHelpers.setField(view,"executor",worker);
            w.get().setContentView(view,new FrameLayout.LayoutParams(width,height));Shadows.shadowOf(Looper.getMainLooper()).idle();layout();
        }
        void layout(){view.measure(View.MeasureSpec.makeMeasureSpec(width,1073741824),View.MeasureSpec.makeMeasureSpec(height,1073741824));view.layout(0,0,width,height);}
        void frame(byte[] jpeg,int w,int h)throws Exception{
            view.showFrame(new JSONObject().put("type","screen").put("image",Base64.encodeToString(jpeg,Base64.NO_WRAP)).put("captureId","synthetic-colored-text")
                .put("width",w).put("height",h).put("frameWidth",w).put("frameHeight",h).put("streamVersion",1).put("frameId",1));
            worker.decode();Shadows.shadowOf(Looper.getMainLooper()).idle();layout();
        }
        Bitmap render(String name)throws Exception{Bitmap out=Bitmap.createBitmap(width,height,Bitmap.Config.ARGB_8888);view.draw(new Canvas(out));try(FileOutputStream stream=new FileOutputStream(new File("output/"+name+".png"))){assertTrue(out.compress(Bitmap.CompressFormat.PNG,100,stream));}return out;}
        Bitmap decoded(){Object displayed=ReflectionHelpers.getField(view,"displayed");return ReflectionHelpers.getField(displayed,"bitmap");}
    }
    static byte[] jpeg(int width,int height)throws Exception{
        Bitmap source=Bitmap.createBitmap(width,height,Bitmap.Config.ARGB_8888);Canvas canvas=new Canvas(source);canvas.drawColor(Color.rgb(250,252,251));Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);p.setTypeface(Typeface.MONOSPACE);p.setTextSize(13);
        for(int row=0;row<height;row+=22){
            p.setColor(Color.rgb(12,45,28));canvas.drawText("Texto 13 px · AliançaDX · 0123456789",width/2f-285,row+15,p);
            p.setColor(Color.rgb(215,20,40));canvas.drawText("RGB vermelho / azul",width/2f+95,row+15,p);
            p.setColor(Color.rgb(20,60,230));canvas.drawLine(width/2f-300,row+18,width/2f+300,row+18,p);
        }
        ByteArrayOutputStream stream=new ByteArrayOutputStream();assertTrue(source.compress(Bitmap.CompressFormat.JPEG,100,stream));source.recycle();return stream.toByteArray();
    }
    @Test public void actual4kJpegAndNativeCanvasPreserveEveryPixelInTheVisibleCrop()throws Exception{
        Scene s=new Scene(640,480);byte[] jpeg=jpeg(3840,2160);s.frame(jpeg,3840,2160);s.render("image75-fit-4k-text13").recycle();
        Bitmap decoded=s.decoded();assertEquals(3840,decoded.getWidth());assertEquals(2160,decoded.getHeight());assertEquals(Bitmap.Config.ARGB_8888,decoded.getConfig());
        assertEquals("17%",s.view.getPixelZoomLabel());assertFalse(s.view.getViewInfoText().contains("reduzido"));
        s.view.zoomToNativePixels();Bitmap output=s.render("image75-native-4k-text13");ScreenGeometry.Viewport viewport=ReflectionHelpers.getField(s.view,"displayedFit");
        assertEquals("100%",s.view.getPixelZoomLabel());assertEquals(3840d,viewport.getWidth(),0);assertEquals(2160d,viewport.getHeight(),0);
        assertEquals(Math.rint(viewport.getLeft()),viewport.getLeft(),0);assertEquals(Math.rint(viewport.getTop()),viewport.getTop(),0);
        assertFalse(((Paint)ReflectionHelpers.getField(s.view,"imagePaint")).isFilterBitmap());
        int[] expected=new int[640*480],actual=new int[640*480];decoded.getPixels(expected,0,640,(int)-viewport.getLeft(),(int)-viewport.getTop(),640,480);output.getPixels(actual,0,640,0,0,640,480);
        assertArrayEquals("1:1 must add no interpolation, translation or color reduction to decoded JPEG",expected,actual);
        s.view.zoomIn();s.render("image75-native135-4k-text13").recycle();assertEquals("135%",s.view.getPixelZoomLabel());
        s.view.resetZoom();s.render("image75-fit-restored").recycle();assertEquals("17%",s.view.getPixelZoomLabel());output.recycle();
    }
    @Test public void smallerJpegIsNeverEnlargedByDefaultFit()throws Exception{
        Scene s=new Scene(1280,720);s.frame(jpeg(640,360),640,360);Bitmap output=s.render("image75-small-native-fit");
        ScreenGeometry.Viewport viewport=ReflectionHelpers.getField(s.view,"displayedFit");assertEquals(640d,viewport.getWidth(),0);assertEquals(360d,viewport.getHeight(),0);assertEquals(320d,viewport.getLeft(),0);assertEquals(180d,viewport.getTop(),0);
        assertEquals("100%",s.view.getPixelZoomLabel());assertFalse(((Paint)ReflectionHelpers.getField(s.view,"imagePaint")).isFilterBitmap());output.recycle();
    }
}
