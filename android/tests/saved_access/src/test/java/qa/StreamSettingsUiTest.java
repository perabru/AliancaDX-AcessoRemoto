package qa;

import android.app.Activity;
import android.content.Context;
import android.content.res.Configuration;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Rect;
import android.os.Looper;
import android.os.SystemClock;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.widget.FrameLayout;
import android.widget.Button;
import android.widget.RadioButton;
import android.widget.TextView;
import br.com.perabru.minianydesk.*;
import org.json.JSONObject;
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
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.net.Socket;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;
import static org.junit.Assert.*;

/** Native production gear/panel and real ControllerSession lifecycle; synthetic PC, no physical-device claim. */
@RunWith(RobolectricTestRunner.class)
@Config(manifest=Config.NONE,sdk=35,qualifiers="mdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class StreamSettingsUiTest {
    final List<ActivityController<? extends Activity>> windows=new ArrayList<>();
    @After public void close(){for(ActivityController<? extends Activity> w:windows)w.pause().stop().destroy();windows.clear();}
    static Object invoke(Object target,String name,Object...args){try{for(Method m:target.getClass().getDeclaredMethods())if((m.getName().equals(name)||m.getName().startsWith(name+"$"))&&m.getParameterCount()==args.length){m.setAccessible(true);return m.invoke(target,args);}throw new NoSuchMethodException(name);}catch(Exception e){throw new AssertionError(name,e);}}
    static JSONObject frame(String mode)throws Exception{return new JSONObject().put("streamSettingsVersion",1).put("streamMode",mode);}
    class Scene {
        final int width,height; final FrameLayout root; final StreamSettingsButton gear; final StreamSettingsPanel panel; final StreamSettings settings=new StreamSettings("auto");
        int touches,releases,changes,closed;
        Scene(int width,int height,float font,boolean fullscreen)throws Exception{
            this.width=width;this.height=height;
            Context base=RuntimeEnvironment.getApplication();Configuration c=new Configuration(base.getResources().getConfiguration());c.fontScale=font;
            Context ctx=base.createConfigurationContext(c);root=new FrameLayout(ctx);root.setBackgroundColor(Color.rgb(29,55,43));root.setPadding(8,0,8,12);
            root.addView(new View(ctx){@Override protected void onDraw(Canvas canvas){canvas.drawColor(Color.rgb(30,63,48));android.graphics.Paint p=new android.graphics.Paint(3);p.setColor(Color.rgb(136,176,150));p.setTextSize(13);canvas.drawText("PC ilustrativo · ajustes Android nativos",16,160,p);}@Override public boolean onTouchEvent(MotionEvent e){if(e.getActionMasked()==0)touches++;return true;}},new FrameLayout.LayoutParams(-1,-1));
            panel=new StreamSettingsPanel(ctx,mode->{if(settings.select(mode)){changes++;settings.request(true);panelRender();}return kotlin.Unit.INSTANCE;},()->{closed++;panelHide();return kotlin.Unit.INSTANCE;});
            gear=new StreamSettingsButton(ctx,()->{releases++;return kotlin.Unit.INSTANCE;},()->{panel.render(settings);panel.showPanel();gearHide();return kotlin.Unit.INSTANCE;});
            FrameLayout.LayoutParams g=new FrameLayout.LayoutParams(48,48,Gravity.TOP|(fullscreen?Gravity.START:Gravity.END));g.topMargin=10;g.leftMargin=10;g.rightMargin=12;root.addView(gear,g);root.addView(panel,new FrameLayout.LayoutParams(-1,-1));
            ActivityController<Activity> w=Robolectric.buildActivity(Activity.class).setup().visible();windows.add(w);w.get().setContentView(root,new FrameLayout.LayoutParams(width,height));
            Shadows.shadowOf(Looper.getMainLooper()).idle();layout();
        }
        void gearHide(){gear.setVisibility(View.GONE);}void panelRender(){panel.render(settings);}void panelHide(){panel.hidePanel(false);gear.setVisibility(View.VISIBLE);}
        void layout(){root.measure(View.MeasureSpec.makeMeasureSpec(width,1073741824),View.MeasureSpec.makeMeasureSpec(height,1073741824));root.layout(0,0,width,height);}
        Rect bounds(View view){Rect b=new Rect(0,0,view.getWidth(),view.getHeight());root.offsetDescendantRectToMyCoords(view,b);return b;}
        void tap(float x,float y){long t=SystemClock.uptimeMillis();for(int a:new int[]{0,1}){MotionEvent e=MotionEvent.obtain(t,t,a,x,y,0);root.dispatchTouchEvent(e);e.recycle();}Shadows.shadowOf(Looper.getMainLooper()).idle();layout();}
        void tap(View v){Rect b=bounds(v);tap(b.exactCenterX(),b.exactCenterY());}
        void render(String name)throws Exception{Bitmap image=Bitmap.createBitmap(width,height,Bitmap.Config.ARGB_8888);root.draw(new Canvas(image));try(FileOutputStream out=new FileOutputStream(new File("output/"+name+".png"))){assertTrue(image.compress(Bitmap.CompressFormat.PNG,100,out));}image.recycle();}
    }
    static List<RadioButton> radios(View v){List<RadioButton> result=new ArrayList<>();if(v instanceof RadioButton)result.add((RadioButton)v);if(v instanceof ViewGroup)for(int i=0;i<((ViewGroup)v).getChildCount();i++)result.addAll(radios(((ViewGroup)v).getChildAt(i)));return result;}
    static String texts(View v){String s=v instanceof TextView?((TextView)v).getText().toString()+"\n":"";if(v instanceof ViewGroup)for(int i=0;i<((ViewGroup)v).getChildCount();i++)s+=texts(((ViewGroup)v).getChildAt(i));return s;}
    @Test public void nativeGearAndScrollablePanelFitPortraitLandscapeAndLargeFonts()throws Exception{
        for(float font:new float[]{1f,2f})for(int[] size:new int[][]{{320,568},{390,844},{568,320},{844,390}}){
            Scene s=new Scene(size[0],size[1],font,size[0]>size[1]);s.settings.observe(frame("auto"));s.settings.request(true);s.settings.observe(frame("auto"));s.render("settings73-gear-"+size[0]+"x"+size[1]+"-font"+Math.round(font*100));
            assertEquals(48,s.gear.getWidth());assertEquals(48,s.gear.getHeight());assertTrue(s.gear.getContentDescription().toString().contains("Imagem e resposta"));
            s.tap(s.gear);assertTrue(s.panel.isOpen());assertEquals(0,s.touches);assertTrue(s.releases>=1);
            View scroller=((ViewGroup)s.panel).getChildAt(0);Rect b=s.bounds(scroller);assertTrue(b.left>=8&&b.right<=size[0]-8&&b.top>=0&&b.bottom<=size[1]-12);
            List<RadioButton> options=radios(s.panel);assertEquals(3,options.size());assertTrue(options.get(0).isChecked());
            for(RadioButton option:options){assertTrue(option.getHeight()>=64&&option.getWidth()>=48);for(int i=0;i<option.getLayout().getLineCount();i++)assertEquals(0,option.getLayout().getEllipsisCount(i));}
            assertTrue(texts(s.panel).contains("Pode aumentar o atraso"));s.render("settings73-open-"+size[0]+"x"+size[1]+"-font"+Math.round(font*100));
            s.tap(9,3);assertEquals(1,s.closed);assertFalse(s.panel.isOpen());assertEquals(0,s.touches);
        }
    }
    static Button namedButton(View view,String title){if(view instanceof Button&&((Button)view).getText().toString().equals(title))return (Button)view;if(view instanceof ViewGroup)for(int i=0;i<((ViewGroup)view).getChildCount();i++){Button found=namedButton(((ViewGroup)view).getChildAt(i),title);if(found!=null)return found;}return null;}
    @Test public void nativeZoomButtonsAreAccessibleScrollableAndNeverClickThePc()throws Exception{
        Scene s=new Scene(390,844,1f,false);final int[] calls={0,0,0,0};s.panel.setOnFit(()->{calls[0]++;return kotlin.Unit.INSTANCE;});s.panel.setOnNative(()->{calls[1]++;return kotlin.Unit.INSTANCE;});s.panel.setOnZoomOut(()->{calls[2]++;return kotlin.Unit.INSTANCE;});s.panel.setOnZoomIn(()->{calls[3]++;return kotlin.Unit.INSTANCE;});
        s.panel.setViewInfo("Recebido 3840×2160 · Zoom 100%",true);s.settings.observe(frame("auto"));s.settings.request(true);s.settings.observe(frame("auto"));s.tap(s.gear);
        for(String title:new String[]{"Ajustar","100% · 1:1","−","+"}){Button button=namedButton(s.panel,title);assertNotNull(button);assertTrue(button.getHeight()>=48);assertNotNull(button.getContentDescription());button.requestRectangleOnScreen(new Rect(0,0,button.getWidth(),button.getHeight()),true);s.layout();s.tap(button);}
        assertArrayEquals(new int[]{1,1,1,1},calls);assertEquals(0,s.touches);assertTrue(texts(s.panel).contains("letras pequenas"));assertTrue(texts(s.panel).contains("Recebido 3840×2160"));s.render("settings75-native-zoom");
        s.panel.setViewInfo("Recebido 3840×2160 · Zoom 100%\nNo celular 1920×1080. Detalhe reduzido pelo limite do aparelho.",true);s.render("settings75-device-limit");assertTrue(texts(s.panel).contains("Detalhe reduzido pelo limite do aparelho"));
    }
    @Test public void oldHostShowsAutomaticAndUpdateNoticeAndCannotSelectUnsupportedModes()throws Exception{
        Scene s=new Scene(390,844,1f,false);s.settings.observe(new JSONObject().put("frameId",1));s.tap(s.gear);
        List<RadioButton> options=radios(s.panel);assertTrue(options.get(0).isChecked());assertFalse(options.get(1).isEnabled());assertFalse(options.get(2).isEnabled());
        s.tap(options.get(2));assertEquals(0,s.changes);assertEquals("auto",s.settings.getDisplayedMode());assertTrue(texts(s.panel).contains("Atualize o programa do PC"));assertEquals(0,s.touches);s.render("settings73-old-host");
        s.tap(s.panel.getCloseButton());assertEquals(1,s.closed);assertEquals(0,s.touches);
    }
    @Test public void modeClickWaitsForImageConfirmationAndNoTouchLeaksToPc()throws Exception{
        Scene s=new Scene(390,844,1f,false);s.settings.observe(frame("auto"));s.settings.request(true);s.settings.observe(frame("auto"));s.tap(s.gear);
        s.tap(radios(s.panel).get(2));assertEquals(1,s.changes);assertEquals("auto",s.settings.getAppliedMode());assertTrue(texts(s.panel).contains("Aguardando a imagem"));
        s.settings.observe(frame("quality"));s.panelRender();assertTrue(texts(s.panel).contains("Em uso: Mais nitidez"));assertEquals(0,s.touches);s.render("settings73-quality-confirmed");
    }
    @Test public void realControllerSessionBackClosesMenuFirstAndSessionStateDoesNotBleed()throws Exception{
        RuntimeEnvironment.getApplication().getSharedPreferences("native_connection",0).edit().clear().commit();
        ActivityController<MainActivity> owner=Robolectric.buildActivity(MainActivity.class).setup().visible();windows.add(owner);
        MainActivity activity=owner.get();Object workspace=ReflectionHelpers.getField(activity,"workspace");Object session=invoke(workspace,"getSelected");
        Peer peer=new Peer(new Socket(){public InputStream getInputStream(){return new ByteArrayInputStream(new byte[0]);}public OutputStream getOutputStream(){return new ByteArrayOutputStream();}},m->kotlin.Unit.INSTANCE,r->kotlin.Unit.INSTANCE,new PeerPolicy());
        ReflectionHelpers.setField(session,"peer",peer);ReflectionHelpers.setField(session,"sharing",true);ReflectionHelpers.setField(session,"networkSuspended",false);ReflectionHelpers.setField(session,"latestFrame",frame("auto"));
        invoke(session,"showPage","session");StreamSettings settings=ReflectionHelpers.getField(session,"streamSettings");settings.observe(frame("auto"));
        invoke(session,"toggleStreamSettings",true);StreamSettingsPanel panel=ReflectionHelpers.getField(session,"streamSettingsPanel");assertTrue(panel.isOpen());assertEquals(false,invoke(session,"inputReady"));
        assertEquals(true,invoke(session,"handleBack"));assertFalse(panel.isOpen());assertEquals(false,ReflectionHelpers.getField(session,"fullscreen"));
        invoke(session,"toggleStreamSettings",true);invoke(session,"onConfigurationChanged",new Configuration(activity.getResources().getConfiguration()));assertFalse(panel.isOpen());assertEquals(true,invoke(session,"inputReady"));assertEquals(true,ReflectionHelpers.getField(ReflectionHelpers.getField(session,"screen"),"inputAllowed"));
        invoke(session,"setFullscreen",true);invoke(session,"toggleStreamSettings",true);assertEquals(true,invoke(session,"handleBack"));assertEquals(true,ReflectionHelpers.getField(session,"fullscreen"));
        invoke(session,"handleBack");assertEquals(false,ReflectionHelpers.getField(session,"fullscreen"));
        settings.select("latency");invoke(activity,"newSession",(Object)null);Object second=invoke(workspace,"getSelected");assertNotNull(second);StreamSettings other=ReflectionHelpers.getField(second,"streamSettings");assertEquals("auto",other.getPreferredMode());assertEquals("latency",settings.getPreferredMode());
        invoke(session,"closeSessionTransport");assertFalse(settings.getCapable());assertNull(settings.getAppliedMode());assertFalse(panel.isOpen());
    }
}
