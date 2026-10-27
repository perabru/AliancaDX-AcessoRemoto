package qa;

import android.app.Activity;
import android.content.Context;
import android.content.res.Configuration;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Rect;
import android.os.Looper;
import android.os.SystemClock;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import br.com.perabru.minianydesk.FullscreenCommandsBar;
import br.com.perabru.minianydesk.MouseControlButton;
import br.com.perabru.minianydesk.MouseControlsPad;
import br.com.perabru.minianydesk.SessionCommandsVisibility;
import br.com.perabru.minianydesk.SessionTrafficRate;
import br.com.perabru.minianydesk.UiKit;
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
import static org.junit.Assert.*;
import java.io.File;
import java.io.FileOutputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.atomic.AtomicInteger;

/** Actual production Canvas components, plus ControllerSession's explicit layout recipe.
 * Uses synthetic desktop background; not an installed app or physical-device claim. */
@RunWith(RobolectricTestRunner.class)
@Config(manifest=Config.NONE,sdk=35,qualifiers="mdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class FullscreenSessionRenderTest {
    private final List<ActivityController<Activity>> windows=new ArrayList<>();
    @After public void closeWindows(){for(ActivityController<Activity> w:windows)w.pause().stop().destroy();windows.clear();}
    private class Scene {
        final FrameLayout root;
        final FullscreenCommandsBar commands;
        final LinearLayout mouse;
        final TextView badge;
        final Button reveal;
        final SessionCommandsVisibility visibility=new SessionCommandsVisibility();
        final AtomicInteger pcTouches=new AtomicInteger();
        final AtomicInteger releases=new AtomicInteger();
        final AtomicInteger exits=new AtomicInteger();
        final int width,height,side,bottom;
        Scene(int width,int height,int side,int bottom,float fontScale) {
            this.width=width;this.height=height;this.side=side;this.bottom=bottom;
            Context base=RuntimeEnvironment.getApplication();
            Configuration config=new Configuration(base.getResources().getConfiguration());config.fontScale=fontScale;
            Context context=base.createConfigurationContext(config);UiKit kit=new UiKit(context);
            root=new FrameLayout(context);root.setPadding(side,0,side,bottom);
            root.addView(new View(context){
                @Override protected void onDraw(Canvas canvas){
                    canvas.drawColor(Color.rgb(225,236,232));Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
                    p.setColor(Color.rgb(22,55,43));canvas.drawRect(0,0,getWidth(),44,p);
                    p.setColor(Color.WHITE);p.setTextSize(13);canvas.drawText("PC de teste · Canvas nativo",18,27,p);
                    for(int row=0;row<5;row++)for(int col=0;col<6;col++){
                        p.setColor(Color.rgb(175+row*9,204+col*4,189+row*6));
                        canvas.drawRoundRect(20+col*72,70+row*69,72+col*72,114+row*69,7,7,p);
                    }
                }
                @Override public boolean onTouchEvent(MotionEvent event){if(event.getActionMasked()==0)pcTouches.incrementAndGet();return true;}
            },new FrameLayout.LayoutParams(-1,-1));
            commands=new FullscreenCommandsBar(context,()->kotlin.Unit.INSTANCE,()->kotlin.Unit.INSTANCE,
                ()->{visibility.hide(true,true,true);updateVisibility();return kotlin.Unit.INSTANCE;},
                ()->{exits.incrementAndGet();return kotlin.Unit.INSTANCE;});
            FrameLayout.LayoutParams c=new FrameLayout.LayoutParams(-2,-2,Gravity.TOP|Gravity.END);c.topMargin=10;c.rightMargin=12;root.addView(commands,c);
            mouse=new MouseControlsPad(context,action->{releases.incrementAndGet();return kotlin.Unit.INSTANCE;});
            int preferred=0;
            for(MouseControlButton button:((MouseControlsPad)mouse).getButtons())
                preferred=Math.max(preferred,button.preferredHeightPx());
            FrameLayout.LayoutParams m=new FrameLayout.LayoutParams(-2,-2,Gravity.BOTTOM|Gravity.END);m.rightMargin=14;m.bottomMargin=10;root.addView(mouse,m);
            SessionTrafficRate rate=new SessionTrafficRate(12.34,1.23);
            String label=fontScale>=1.5f?String.format(Locale.forLanguageTag("pt-BR"),"Sessão ↓ %.2f Mb/s\n↑ %.2f Mb/s",12.34,1.23):rate.compactLabel();
            badge=kit.label(label,10f,Color.WHITE,false);badge.setSingleLine(false);badge.setMaxLines(2);badge.setPadding(9,6,9,6);badge.setClickable(true);
            badge.setBackground(kit.background(Color.argb(145,7,31,22),18,Color.argb(80,233,255,244),1));
            FrameLayout.LayoutParams b=new FrameLayout.LayoutParams(-2,-2,Gravity.BOTTOM|Gravity.START);b.leftMargin=10;b.topMargin=10;b.bottomMargin=preferred+4+20;root.addView(badge,b);
            reveal=kit.button("‹",UiKit.ButtonKind.SECONDARY,()->{visibility.restore();updateVisibility();return kotlin.Unit.INSTANCE;});
            reveal.setContentDescription("Mostrar comandos");reveal.setTextSize(25f);reveal.setMinWidth(48);reveal.setMinimumWidth(48);reveal.setMinHeight(56);reveal.setMinimumHeight(56);reveal.setPadding(0,0,0,3);reveal.setTextColor(Color.WHITE);
            reveal.setBackgroundTintList(null);reveal.setBackground(kit.background(Color.argb(125,35,40,43),16,Color.argb(90,255,255,255),1));
            reveal.setOnTouchListener((v,e)->{if(e.getActionMasked()==MotionEvent.ACTION_DOWN)releases.incrementAndGet();return false;});
            FrameLayout.LayoutParams r=new FrameLayout.LayoutParams(48,56,Gravity.CENTER_VERTICAL|Gravity.END);r.rightMargin=4;root.addView(reveal,r);
            updateVisibility();
            ActivityController<Activity> owner=Robolectric.buildActivity(Activity.class).setup().visible();windows.add(owner);owner.get().setContentView(root,new FrameLayout.LayoutParams(width,height));
            Shadows.shadowOf(Looper.getMainLooper()).idle();layout();
            b.bottomMargin=Math.max(mouse.getHeight(),preferred+4)+20;badge.setLayoutParams(b);layout();
        }
        void layout(){root.measure(View.MeasureSpec.makeMeasureSpec(width,View.MeasureSpec.EXACTLY),View.MeasureSpec.makeMeasureSpec(height,View.MeasureSpec.EXACTLY));root.layout(0,0,width,height);}
        void updateVisibility(){boolean hidden=visibility.getHidden();commands.setVisibility(hidden?View.GONE:View.VISIBLE);mouse.setVisibility(hidden?View.GONE:View.VISIBLE);badge.setVisibility(hidden?View.GONE:View.VISIBLE);reveal.setVisibility(hidden?View.VISIBLE:View.GONE);}
        Rect bounds(View view){
            // ViewGroup.offsetDescendantRectToMyCoords treats (0,0) as the
            // scrolled text CONTENT origin. Touch bounds exclude a Button's own
            // horizontal text scroll, particularly after a GONE->VISIBLE pass.
            Rect r=new Rect(view.getLeft(),view.getTop(),view.getRight(),view.getBottom());
            View parent=(View)view.getParent();
            while(parent!=root){r.offset(parent.getLeft()-parent.getScrollX(),parent.getTop()-parent.getScrollY());parent=(View)parent.getParent();}
            r.offset(-root.getScrollX(),-root.getScrollY());return r;
        }
        void render(String name)throws Exception{Bitmap image=Bitmap.createBitmap(width,height,Bitmap.Config.ARGB_8888);root.draw(new Canvas(image));try(FileOutputStream out=new FileOutputStream(new File("output640/"+name+".png"))){assertTrue(image.compress(Bitmap.CompressFormat.PNG,100,out));}image.recycle();}
        void tap(View view){Rect b=bounds(view);long now=SystemClock.uptimeMillis();for(int action:new int[]{MotionEvent.ACTION_DOWN,MotionEvent.ACTION_UP}){MotionEvent e=MotionEvent.obtain(now,now,action,b.exactCenterX(),b.exactCenterY(),0);root.dispatchTouchEvent(e);e.recycle();Shadows.shadowOf(Looper.getMainLooper()).idle();}layout();}
    }

    @Test public void productionCommandsAndBadgeFitAllFontsPortraitLandscapeAndSafeInsets()throws Exception {
        for(float scale:new float[]{1f,1.3f,2f})for(int[] size:new int[][]{{320,568,0,24},{390,844,0,24},{568,320,24,18},{844,390,32,18}}){
            Scene s=new Scene(size[0],size[1],size[2],size[3],scale);
            for(View v:new View[]{s.commands,s.mouse,s.badge}){Rect b=s.bounds(v);assertTrue("left "+b,b.left>=s.side);assertTrue("right "+b,b.right<=s.width-s.side);assertTrue("top "+b,b.top>=0);assertTrue("bottom "+b,b.bottom<=s.height-s.bottom);}
            assertFalse("Badge must not overlap mouse pads",Rect.intersects(s.bounds(s.badge),s.bounds(s.mouse)));
            assertFalse("Badge must not overlap fullscreen commands",Rect.intersects(s.bounds(s.badge),s.bounds(s.commands)));
            assertFalse("Fullscreen commands must not overlap mouse pads",Rect.intersects(s.bounds(s.commands),s.bounds(s.mouse)));
            for(int row=0;row<s.commands.getChildCount();row++){
                ViewGroup line=(ViewGroup)s.commands.getChildAt(row);
                for(int col=0;col<line.getChildCount();col++){
                    Button b=(Button)line.getChildAt(col);assertTrue(b.getWidth()>=48&&b.getHeight()>=48);
                    assertNotNull(b.getLayout());
                    for(int i=0;i<b.getLayout().getLineCount();i++){assertEquals("Never truncate command",0,b.getLayout().getEllipsisCount(i));assertTrue("Natural command width fits "+b.getText(),b.getLayout().getLineWidth(i)<=b.getWidth()-b.getCompoundPaddingLeft()-b.getCompoundPaddingRight()+1);}
                    assertTrue("Command text height fits",b.getLayout().getHeight()<=b.getHeight()-b.getCompoundPaddingTop()-b.getCompoundPaddingBottom());
                }
            }
            assertEquals(scale>=1.5f?2:1,s.badge.getLineCount());
            s.render("session-"+size[0]+"x"+size[1]+"-font"+Math.round(scale*100));
        }
    }

    @Test public void realNativeHideAndRevealPreserveRecoveryTargetAndSwallowTouches()throws Exception {
        Scene s=new Scene(320,568,0,24,2f);
        Button hide=(Button)((ViewGroup)s.commands.getChildAt(1)).getChildAt(0);s.tap(hide);
        assertTrue(s.visibility.getHidden());assertEquals(View.GONE,s.commands.getVisibility());assertEquals(View.GONE,s.badge.getVisibility());assertEquals(View.GONE,s.mouse.getVisibility());assertEquals(View.VISIBLE,s.reveal.getVisibility());
        Rect handle=s.bounds(s.reveal);assertEquals(48,handle.width());assertEquals(56,handle.height());assertTrue(handle.right<=320&&handle.left>=0);s.render("session-hidden-320-font200");
        s.tap(s.reveal);assertFalse(s.visibility.getHidden());assertEquals(View.VISIBLE,s.commands.getVisibility());assertEquals(View.GONE,s.reveal.getVisibility());assertEquals(1,s.releases.get());assertEquals(0,s.pcTouches.get());
        s.tap(s.commands.getExitButton());assertEquals(1,s.exits.get());assertEquals(0,s.pcTouches.get());
    }
}
