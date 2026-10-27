package qa;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.res.Configuration;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.os.Looper;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.TextView;
import br.com.perabru.minianydesk.*;
import kotlin.LazyKt;
import org.junit.After;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.Shadows;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;
import org.robolectric.util.ReflectionHelpers;
import java.io.*;
import java.net.Socket;
import java.lang.reflect.Method;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicLong;
import static org.junit.Assert.*;

/** Exact production updater/Activity with controlled release source. No live GitHub or phone claim. */
@RunWith(RobolectricTestRunner.class)
@Config(manifest=Config.NONE,sdk=35,qualifiers="mdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class AndroidUpdateDialogTest {
    final List<ActivityController<? extends Activity>> windows=new ArrayList<>();
    final List<AndroidUpdateDialog> checkers=new ArrayList<>();
    @After public void close(){for(AndroidUpdateDialog c:checkers)c.close();for(ActivityController<? extends Activity> w:windows)w.pause().stop().destroy();}
    static class PausedWorker extends AbstractExecutorService {
        final ArrayDeque<Runnable> jobs=new ArrayDeque<>();boolean closed;
        public void execute(Runnable task){if(closed)throw new RejectedExecutionException();jobs.add(task);}
        public void shutdown(){closed=true;}public List<Runnable> shutdownNow(){closed=true;List<Runnable> left=new ArrayList<>(jobs);jobs.clear();return left;}
        public boolean isShutdown(){return closed;}public boolean isTerminated(){return closed&&jobs.isEmpty();}public boolean awaitTermination(long t,TimeUnit u){return isTerminated();}
        void runOne()throws Exception{Runnable task=jobs.remove();Thread worker=new Thread(task,"qa-update-worker");worker.start();worker.join(3000);assertFalse("Fetcher must complete in controlled fixture",worker.isAlive());}
    }
    static class Source implements AndroidReleaseSource {
        int calls;boolean cancelled,onMain;AndroidReleaseUpdate.Result result=release("7.5.0",AndroidReleaseUpdate.Relation.AVAILABLE);AndroidUpdateFailure failure;
        public AndroidReleaseUpdate.Result fetch(String installed)throws AndroidUpdateFailure{calls++;onMain=Looper.myLooper()==Looper.getMainLooper();assertEquals("7.4.0",installed);if(failure!=null)throw failure;return result;}
        public void cancel(){cancelled=true;}
    }
    static AndroidReleaseUpdate.Result release(String version,AndroidReleaseUpdate.Relation relation){return new AndroidReleaseUpdate.Result("7.4.0",version,"v"+version,"https://github.com/perabru/AliancaDX-AcessoRemoto/releases/download/v"+version+"/AliancaDX_Acesso_Remoto_"+version+".apk",relation);}
    static Object invoke(Object target,String name,Object...args){try{for(Method m:target.getClass().getDeclaredMethods())if((m.getName().equals(name)||m.getName().startsWith(name+"$"))&&m.getParameterCount()==args.length){m.setAccessible(true);return m.invoke(target,args);}throw new NoSuchMethodException(name);}catch(Exception e){throw new AssertionError(name,e);}}
    static String text(View view){String s=view instanceof TextView?((TextView)view).getText().toString()+"\n":"";if(view instanceof ViewGroup)for(int i=0;i<((ViewGroup)view).getChildCount();i++)s+=text(((ViewGroup)view).getChildAt(i));return s;}
    static List<Button> buttons(View view){List<Button> out=new ArrayList<>();if(view instanceof Button)out.add((Button)view);if(view instanceof ViewGroup)for(int i=0;i<((ViewGroup)view).getChildCount();i++)out.addAll(buttons(((ViewGroup)view).getChildAt(i)));return out;}
    static Button button(View view,String prefix){for(Button b:buttons(view))if(b.getText().toString().startsWith(prefix))return b;throw new AssertionError("Missing "+prefix+" in "+text(view));}
    ActivityController<Activity> activity(){ActivityController<Activity> w=Robolectric.buildActivity(Activity.class).setup().visible();windows.add(w);return w;}
    class Fixture {
        final Activity activity;final Source source=new Source();final PausedWorker worker=new PausedWorker();final AtomicLong clock=new AtomicLong(1000);final AndroidUpdateDialog checker;
        Fixture(Activity activity){this.activity=activity;checker=new AndroidUpdateDialog(activity,"7.4.0",source,worker,()->clock.get());checkers.add(checker);}
        AlertDialog dialog(){return ReflectionHelpers.getField(checker,"dialog");}View root(){return dialog().getWindow().getDecorView();}
        void finishFetch()throws Exception{worker.runOne();Shadows.shadowOf(Looper.getMainLooper()).idle();}
        void render(String name,int width,int height)throws Exception{View root=root();root.measure(View.MeasureSpec.makeMeasureSpec(width,1073741824),View.MeasureSpec.makeMeasureSpec(height,1073741824));root.layout(0,0,width,height);Bitmap b=Bitmap.createBitmap(width,height,Bitmap.Config.ARGB_8888);root.draw(new Canvas(b));try(FileOutputStream out=new FileOutputStream(new File("output/"+name+".png"))){assertTrue(b.compress(Bitmap.CompressFormat.PNG,100,out));}b.recycle();}
    }
    @Test public void checkingIsManualDeduplicatedOffMainAndDownloadNeedsExplicitClick()throws Exception{
        Fixture f=new Fixture(activity().get());assertEquals(0,f.source.calls);assertEquals(0,f.worker.jobs.size());f.checker.show();f.checker.show();
        assertEquals(1,f.worker.jobs.size());assertTrue(text(f.root()).contains("Buscando atualização"));assertNull(Shadows.shadowOf(f.activity).getNextStartedActivity());f.render("update74-checking",390,700);
        f.finishFetch();assertEquals(1,f.source.calls);assertFalse(f.source.onMain);assertTrue(text(f.root()).contains("Instalada neste celular · 7.4.0"));assertTrue(text(f.root()).contains("Uma nova versão"));assertNull(Shadows.shadowOf(f.activity).getNextStartedActivity());
        button(f.root(),"Baixar versão").performClick();Intent opened=Shadows.shadowOf(f.activity).getNextStartedActivity();assertEquals(Intent.ACTION_VIEW,opened.getAction());assertEquals(f.source.result.getApkUrl(),opened.getDataString());assertTrue(opened.hasCategory(Intent.CATEGORY_BROWSABLE));assertNull(opened.getType());
        f.render("update74-available",390,700);
    }
    public static class NoBrowserActivity extends Activity {
        @Override public void startActivity(Intent intent){throw new android.content.ActivityNotFoundException("no browser fixture");}
    }
    @Test public void missingBrowserShowsAnErrorWithoutClosingTheDialog()throws Exception{
        ActivityController<NoBrowserActivity> w=Robolectric.buildActivity(NoBrowserActivity.class).setup().visible();windows.add(w);Fixture f=new Fixture(w.get());f.checker.show();f.finishFetch();
        button(f.root(),"Baixar versão").performClick();assertTrue(f.dialog().isShowing());assertTrue(text(f.root()).contains("Ative um navegador"));assertEquals(View.GONE,button(f.root(),"Baixar versão").getVisibility());assertNull(Shadows.shadowOf(f.activity).getNextStartedActivity());
    }
    @Test public void sameAndLocalNewerNeverOfferDowngradeOrOpenBrowser()throws Exception{
        for(AndroidReleaseUpdate.Relation relation:new AndroidReleaseUpdate.Relation[]{AndroidReleaseUpdate.Relation.CURRENT,AndroidReleaseUpdate.Relation.LOCAL_NEWER}){
            Fixture f=new Fixture(activity().get());f.source.result=release(relation==AndroidReleaseUpdate.Relation.CURRENT?"7.4.0":"7.3.0",relation);f.checker.show();f.finishFetch();
            assertEquals(View.GONE,button(f.root(),"Baixar versão").getVisibility());assertNull(Shadows.shadowOf(f.activity).getNextStartedActivity());assertTrue(text(f.root()).contains(relation==AndroidReleaseUpdate.Relation.CURRENT?"Você está atualizado":"Seu aplicativo é mais recente"));f.render("update74-"+relation.name().toLowerCase(Locale.ROOT),320,568);
        }
    }
    @Test public void offlineAndRateLimitCanRetryAfterTheBoundedCooldown()throws Exception{
        Fixture f=new Fixture(activity().get());f.source.failure=new AndroidUpdateFailure("Sem internet. Tente novamente.",60000);f.checker.show();f.finishFetch();
        assertTrue(text(f.root()).contains("Sem internet"));assertFalse(button(f.root(),"Tentar novamente").isEnabled());assertEquals(View.GONE,button(f.root(),"Baixar versão").getVisibility());f.render("update74-offline",390,700);
        f.clock.addAndGet(61000);Shadows.shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(61));assertTrue(button(f.root(),"Tentar novamente").isEnabled());
        f.source.failure=null;button(f.root(),"Tentar novamente").performClick();assertEquals(1,f.worker.jobs.size());f.finishFetch();assertEquals(2,f.source.calls);assertTrue(text(f.root()).contains("Uma nova versão"));
    }
    @Test public void destroyCancelsWorkerAndDiscardsAlreadyPostedResults()throws Exception{
        ActivityController<Activity> w=activity();Fixture f=new Fixture(w.get());f.checker.show();AlertDialog old=f.dialog();f.worker.runOne();
        f.checker.close();w.pause().stop().destroy();windows.remove(w);Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertTrue(f.source.cancelled);assertTrue(f.worker.closed);assertFalse(old.isShowing());assertNull(ReflectionHelpers.getField(f.checker,"dialog"));f.checker.show();assertNull(ReflectionHelpers.getField(f.checker,"dialog"));
    }
    @Test public void nativeDialogRemainsScrollableAndCloseableInPortraitLandscapeAndLargeFonts()throws Exception{
        for(float font:new float[]{1f,2f})for(int[] size:new int[][]{{320,568},{390,844},{568,320},{844,390}}){
            Activity a=activity().get();Configuration config=new Configuration(a.getResources().getConfiguration());config.fontScale=font;a.getResources().updateConfiguration(config,a.getResources().getDisplayMetrics());
            Fixture f=new Fixture(a);f.checker.show();f.finishFetch();f.render("update74-"+size[0]+"x"+size[1]+"-font"+Math.round(font*100),size[0],size[1]);
            Button close=f.dialog().getButton(AlertDialog.BUTTON_NEGATIVE);assertTrue(close.getHeight()>=48);android.graphics.Rect closeBounds=new android.graphics.Rect(0,0,close.getWidth(),close.getHeight());((ViewGroup)f.root()).offsetDescendantRectToMyCoords(close,closeBounds);assertTrue(closeBounds.top>=0&&closeBounds.bottom<=size[1]);assertEquals("Fechar",close.getText().toString());assertTrue(text(f.root()).contains("7.4.0"));close.performClick();Shadows.shadowOf(Looper.getMainLooper()).idle();assertFalse(f.dialog().isShowing());
        }
    }
    @Test public void realHomeAndSessionEntryPointsShareOneCheckerAndKeepThePcOpen()throws Exception{
        ActivityController<MainActivity> w=Robolectric.buildActivity(MainActivity.class).setup().visible();windows.add(w);MainActivity activity=w.get();assertFalse("App startup must not create an updater or make a query",((kotlin.Lazy<?>)ReflectionHelpers.getField(activity,"updateDialogOwner")).isInitialized());Fixture f=new Fixture(activity);
        ReflectionHelpers.setField(activity,"updateDialogOwner",LazyKt.lazy(()->f.checker));Object workspace=ReflectionHelpers.getField(activity,"workspace");Object session=invoke(workspace,"getSelected");
        Peer peer=new Peer(new Socket(){public InputStream getInputStream(){return new ByteArrayInputStream(new byte[0]);}public OutputStream getOutputStream(){return new ByteArrayOutputStream();}},m->kotlin.Unit.INSTANCE,r->kotlin.Unit.INSTANCE,new PeerPolicy());ReflectionHelpers.setField(session,"peer",peer);int generation=ReflectionHelpers.getField(session,"sessionGeneration");
        View home=ReflectionHelpers.getField(session,"chrome");Button entry=button(home,"Buscar\natualização");assertTrue(entry.getContentDescription().toString().contains("Buscar atualização"));entry.performClick();assertEquals(1,f.worker.jobs.size());assertFalse(peer.getClosed());
        f.dialog().dismiss();invoke(activity,"showUpdates");assertEquals(1,f.worker.jobs.size());assertFalse(peer.getClosed());f.finishFetch();assertFalse(peer.getClosed());assertEquals(generation,(int)ReflectionHelpers.getField(session,"sessionGeneration"));
        f.dialog().dismiss();invoke(activity,"showSessions");AlertDialog sessions=org.robolectric.shadows.ShadowAlertDialog.getLatestAlertDialog();assertEquals("Buscar atualização",sessions.getButton(AlertDialog.BUTTON_POSITIVE).getText().toString());sessions.getButton(AlertDialog.BUTTON_POSITIVE).performClick();Shadows.shadowOf(Looper.getMainLooper()).idle();assertTrue(f.dialog().isShowing());assertFalse(peer.getClosed());
    }
}
