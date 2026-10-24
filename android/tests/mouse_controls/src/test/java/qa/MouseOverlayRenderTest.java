package qa;

import android.app.Activity;
import android.content.Context;
import android.content.res.Configuration;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Rect;
import android.graphics.drawable.GradientDrawable;
import android.os.Looper;
import android.os.SystemClock;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import br.com.perabru.minianydesk.MouseControlButton;
import org.junit.Test;
import org.junit.After;
import org.junit.runner.RunWith;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.Robolectric;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.Shadows;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.GraphicsMode;
import static org.junit.Assert.*;
import java.io.File;
import java.io.FileOutputStream;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.ArrayList;
import java.util.List;

/** Production Android Canvas widget; synthetic viewer only, no real PC or physical device. */
@RunWith(RobolectricTestRunner.class)
@Config(manifest = Config.NONE, sdk = 35, qualifiers = "mdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class MouseOverlayRenderTest {
    private static final List<ActivityController<Activity>> WINDOWS=new ArrayList<>();
    @After public void closeSyntheticWindows(){
        for(ActivityController<Activity> window:WINDOWS)window.pause().stop().destroy();
        WINDOWS.clear();
    }
    private static class Scene {
        final AtomicInteger screenDown = new AtomicInteger();
        final AtomicInteger releases = new AtomicInteger();
        final AtomicInteger doubles = new AtomicInteger();
        final AtomicInteger rights = new AtomicInteger();
        final FrameLayout root;
        final LinearLayout pad;
        final MouseControlButton left;
        final MouseControlButton right;
        Scene(int width, int height, int sideInset, int bottomInset) {
            this(width,height,sideInset,bottomInset,1f);
        }
        Scene(int width, int height, int sideInset, int bottomInset,float fontScale) {
            Context base = RuntimeEnvironment.getApplication();
            Configuration config = new Configuration(base.getResources().getConfiguration());
            config.fontScale=fontScale;
            Context context=base.createConfigurationContext(config);
            root = new FrameLayout(context);
            root.setPadding(sideInset, 0, sideInset, bottomInset);
            View desktop = new View(context) {
                @Override protected void onDraw(Canvas canvas) {
                    canvas.drawColor(Color.rgb(225,236,232));
                    Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
                    p.setColor(Color.rgb(22,55,43)); canvas.drawRect(0, 0, getWidth(), 44, p);
                    p.setColor(Color.WHITE);p.setTextSize(13);canvas.drawText("Synthetic PC image • Native Android Canvas", 18, 27,p);
                    for (int row=0;row<4;row++) for(int col=0;col<4;col++) {
                        int x=20+col*72,y=70+row*69;
                        p.setColor(Color.rgb(175+row*9,204+col*4,189+row*6));
                        canvas.drawRoundRect(x,y,x+52,y+44,7,7,p);
                    }
                }
                @Override public boolean onTouchEvent(MotionEvent e) {
                    if(e.getActionMasked()==MotionEvent.ACTION_DOWN)screenDown.incrementAndGet();
                    return true;
                }
            };
            root.addView(desktop,new FrameLayout.LayoutParams(-1,-1));
            pad=new LinearLayout(context);pad.setOrientation(LinearLayout.HORIZONTAL);pad.setGravity(Gravity.CENTER_VERTICAL);pad.setClickable(true);
            // Mirrors UiKit.row(10) + ControllerSession.quickMouse padding exactly at mdpi.
            GradientDrawable divider=new GradientDrawable();divider.setColor(Color.TRANSPARENT);divider.setSize(10,10);
            pad.setDividerDrawable(divider);pad.setShowDividers(LinearLayout.SHOW_DIVIDER_MIDDLE);pad.setPadding(2,2,2,2);
            pad.setClipChildren(false);pad.setClipToPadding(false);
            left=new MouseControlButton(context,false,()->{releases.incrementAndGet();return kotlin.Unit.INSTANCE;});
            right=new MouseControlButton(context,true,()->{releases.incrementAndGet();return kotlin.Unit.INSTANCE;});
            left.setOnClickListener(v->doubles.incrementAndGet());right.setOnClickListener(v->rights.incrementAndGet());
            pad.addView(left,new LinearLayout.LayoutParams(left.preferredWidthPx(),left.preferredHeightPx()));
            LinearLayout.LayoutParams next=new LinearLayout.LayoutParams(right.preferredWidthPx(),right.preferredHeightPx());
            pad.addView(right,next);
            FrameLayout.LayoutParams params=new FrameLayout.LayoutParams(-2,-2,Gravity.BOTTOM|Gravity.END);
            params.rightMargin=14;params.bottomMargin=10;root.addView(pad,params);
            // A real attachment is required: detached View.post() queues clicks until attach.
            ActivityController<Activity> owner=Robolectric.buildActivity(Activity.class).setup().visible();
            WINDOWS.add(owner);owner.get().setContentView(root,new FrameLayout.LayoutParams(width,height));
            Shadows.shadowOf(Looper.getMainLooper()).idle();
            root.measure(View.MeasureSpec.makeMeasureSpec(width,View.MeasureSpec.EXACTLY),View.MeasureSpec.makeMeasureSpec(height,View.MeasureSpec.EXACTLY));
            root.layout(0,0,width,height);
        }
        void event(int action,float x,float y) {
            long now=SystemClock.uptimeMillis();MotionEvent e=MotionEvent.obtain(now,now,action,x,y,0);
            try{root.dispatchTouchEvent(e);}finally{e.recycle();}
            Shadows.shadowOf(Looper.getMainLooper()).idle();
        }
        void tap(float x,float y){event(MotionEvent.ACTION_DOWN,x,y);event(MotionEvent.ACTION_UP,x,y);}
        float x(MouseControlButton b){return pad.getLeft()+b.getLeft()+b.getWidth()/2f;}
        float y(MouseControlButton b){return pad.getTop()+b.getTop()+b.getHeight()/2f;}
        void render(String name) throws Exception {
            Bitmap bitmap=Bitmap.createBitmap(root.getWidth(),root.getHeight(),Bitmap.Config.ARGB_8888);
            root.draw(new Canvas(bitmap));
            try(FileOutputStream output=new FileOutputStream(new File("output/"+name+".png"))){assertTrue(bitmap.compress(Bitmap.CompressFormat.PNG,100,output));}
            bitmap.recycle();
        }
    }

    @Test public void portraitLandscapeAndSafeInsetsKeepBothPadsVisible() throws Exception {
        int[][] sizes={{320,568,0,0},{390,844,0,24},{844,390,32,18},{568,320,24,0},{240,240,0,0}};
        for(int[] size:sizes){
            Scene scene=new Scene(size[0],size[1],size[2],size[3]);
            assertTrue(scene.left.getWidth()>=48&&scene.left.getHeight()>=48);
            assertTrue(scene.right.getWidth()>=48&&scene.right.getHeight()>=48);
            assertTrue(scene.pad.getLeft()>=size[2]);
            assertTrue(scene.pad.getRight()<=size[0]-size[2]);
            assertTrue(scene.pad.getBottom()<=size[1]-size[3]);
            assertTrue(scene.pad.getTop()>=0);
            scene.render("native-mouse-"+size[0]+"x"+size[1]);
        }
    }

    @Test public void largeAccessibilityFontsKeepCaptionAndBothTargetsInsideAvailableViewer() throws Exception {
        for(float scale:new float[]{1f,1.3f,2f})for(int[] size:new int[][]{{320,568},{568,320}}){
            Scene scene=new Scene(size[0],size[1],0,18,scale);
            assertTrue(scene.pad.getLeft()>=0);
            assertTrue(scene.pad.getRight()<=size[0]);
            assertTrue(scene.pad.getBottom()<=size[1]-18);
            assertTrue(scene.left.getHeight()>=78);
            assertEquals("Both custom pads must share their top edge",scene.left.getTop(),scene.right.getTop());
            assertTrue(scene.left.getBottom()<=scene.pad.getHeight()-scene.pad.getPaddingBottom());
            assertTrue(scene.right.getBottom()<=scene.pad.getHeight()-scene.pad.getPaddingBottom());
            scene.render("native-font-"+Math.round(scale*100)+"-"+size[0]+"x"+size[1]);
        }
    }

    @Test public void padAndGapNeverDispatchMouseTouchToUnderlyingImage() {
        Scene scene=new Scene(390,844,0,24);
        scene.tap(scene.x(scene.left),scene.y(scene.left));
        assertEquals(1,scene.doubles.get());assertEquals(0,scene.rights.get());assertEquals(1,scene.releases.get());
        scene.tap(scene.x(scene.right),scene.y(scene.right));
        assertEquals(1,scene.doubles.get());assertEquals(1,scene.rights.get());assertEquals(2,scene.releases.get());
        scene.tap(scene.pad.getLeft()+(scene.left.getRight()+scene.right.getLeft())/2f,scene.y(scene.right));
        assertEquals(0,scene.screenDown.get());
        scene.left.setEnabled(false);scene.right.setEnabled(false);
        assertFalse(scene.left.performClick());assertFalse(scene.right.performClick());
        scene.tap(scene.x(scene.left),scene.y(scene.left));scene.tap(scene.x(scene.right),scene.y(scene.right));
        assertEquals(1,scene.doubles.get());assertEquals(1,scene.rights.get());assertEquals(0,scene.screenDown.get());
        scene.tap(30,180);assertEquals(1,scene.screenDown.get());
    }

    @Test public void draggingOffPadAndCancelNeverProduceClicks() {
        Scene scene=new Scene(390,844,0,24);
        scene.event(MotionEvent.ACTION_DOWN,scene.x(scene.left),scene.y(scene.left));
        scene.event(MotionEvent.ACTION_CANCEL,scene.x(scene.left),scene.y(scene.left));
        assertEquals(0,scene.doubles.get());assertFalse(scene.left.isPressed());assertEquals(0,scene.screenDown.get());
        scene.event(MotionEvent.ACTION_DOWN,scene.x(scene.right),scene.y(scene.right));
        scene.event(MotionEvent.ACTION_MOVE,10,10);scene.event(MotionEvent.ACTION_UP,10,10);
        assertEquals(0,scene.rights.get());assertFalse(scene.right.isPressed());assertEquals(0,scene.screenDown.get());
    }

    @Test public void nativeShaderIsTranslucentAndPressedMouseIsVisible() throws Exception {
        Scene scene=new Scene(390,844,0,24);
        Bitmap normal=Bitmap.createBitmap(64,78,Bitmap.Config.ARGB_8888);scene.left.draw(new Canvas(normal));
        int alpha=Color.alpha(normal.getPixel(32,52));
        assertTrue("Glass circle alpha must allow the PC image through: "+alpha,alpha>0&&alpha<255);
        scene.left.setPressed(true);scene.render("native-mouse-pressed");
        Bitmap pressed=Bitmap.createBitmap(64,78,Bitmap.Config.ARGB_8888);scene.left.draw(new Canvas(pressed));
        assertFalse("Pressed state must change actual Canvas output",normal.sameAs(pressed));
        scene.left.setPressed(false);scene.left.setEnabled(false);scene.right.setEnabled(false);scene.render("native-mouse-disabled");
        assertEquals("Clique duplo na posição do cursor desta conexão",scene.left.getContentDescription().toString());
        normal.recycle();pressed.recycle();
    }
}
