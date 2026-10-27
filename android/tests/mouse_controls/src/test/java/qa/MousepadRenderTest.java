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
import android.widget.FrameLayout;
import br.com.perabru.minianydesk.MouseControlButton;
import br.com.perabru.minianydesk.MouseControlsPad;
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
import java.io.File;
import java.io.FileOutputStream;
import java.util.ArrayList;
import java.util.EnumMap;
import java.util.List;
import static org.junit.Assert.*;

/** Exact production four-button pad; synthetic desktop, no physical-device claim. */
@RunWith(RobolectricTestRunner.class)
@Config(manifest=Config.NONE,sdk=35,qualifiers="mdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
public class MousepadRenderTest {
    private final List<ActivityController<Activity>> windows=new ArrayList<>();
    @After public void closeWindows(){for(ActivityController<Activity> w:windows)w.pause().stop().destroy();windows.clear();}
    private class Scene {
        final FrameLayout root;
        final MouseControlsPad pad;
        final EnumMap<MouseControlButton.Action,Integer> calls=new EnumMap<>(MouseControlButton.Action.class);
        int desktopDowns,desktopMoves,cancellations;
        final int width,height;
        Scene(int width,int height,int side,int bottom,float fontScale) {
            this.width=width;this.height=height;
            Context base=RuntimeEnvironment.getApplication();
            Configuration config=new Configuration(base.getResources().getConfiguration());config.fontScale=fontScale;
            Context context=base.createConfigurationContext(config);
            root=new FrameLayout(context);root.setPadding(side,0,side,bottom);root.setMotionEventSplittingEnabled(true);
            root.addView(new View(context){
                @Override protected void onDraw(Canvas canvas){
                    canvas.drawColor(Color.rgb(19,32,42));Paint p=new Paint(3);
                    p.setColor(Color.rgb(34,55,65));
                    for(int x=18;x<getWidth();x+=72)for(int y=58;y<getHeight();y+=70)
                        canvas.drawRoundRect(x,y,x+52,y+40,7,7,p);
                    p.setColor(Color.WHITE);p.setTextSize(14);canvas.drawText("AliançaDX · Prévia Android do mousepad",16,28,p);
                    p.setColor(Color.rgb(130,255,184));p.setTextSize(11);
                    canvas.drawText("Deslize em toda a tela. Use os botões para clicar.",16,46,p);
                    p.setColor(Color.WHITE);canvas.drawCircle(getWidth()*.44f,getHeight()*.46f,6,p);
                }
                @Override public boolean onTouchEvent(MotionEvent e){
                    if(e.getActionMasked()==MotionEvent.ACTION_DOWN)desktopDowns++;
                    if(e.getActionMasked()==MotionEvent.ACTION_MOVE)desktopMoves++;
                    return true;
                }
            },new FrameLayout.LayoutParams(-1,-1));
            pad=new MouseControlsPad(context,()->{cancellations++;return kotlin.Unit.INSTANCE;},action->{calls.put(action,calls.getOrDefault(action,0)+1);return kotlin.Unit.INSTANCE;});
            FrameLayout.LayoutParams p=new FrameLayout.LayoutParams(-2,-2,Gravity.BOTTOM|Gravity.END);p.rightMargin=14;p.bottomMargin=10;
            root.addView(pad,p);
            ActivityController<Activity> owner=Robolectric.buildActivity(Activity.class).setup().visible();windows.add(owner);owner.get().setContentView(root,new FrameLayout.LayoutParams(width,height));
            Shadows.shadowOf(Looper.getMainLooper()).idle();layout();
        }
        void layout(){root.measure(View.MeasureSpec.makeMeasureSpec(width,View.MeasureSpec.EXACTLY),View.MeasureSpec.makeMeasureSpec(height,View.MeasureSpec.EXACTLY));root.layout(0,0,width,height);}
        Rect bounds(View v){Rect r=new Rect(v.getLeft(),v.getTop(),v.getRight(),v.getBottom());View parent=(View)v.getParent();while(parent!=root){r.offset(parent.getLeft(),parent.getTop());parent=(View)parent.getParent();}return r;}
        void event(int action,float x,float y){long t=SystemClock.uptimeMillis();MotionEvent e=MotionEvent.obtain(t,t,action,x,y,0);root.dispatchTouchEvent(e);e.recycle();Shadows.shadowOf(Looper.getMainLooper()).idle();}
        void pointers(int action,int[] ids,float[] xs,float[] ys){
            MotionEvent.PointerProperties[] properties=new MotionEvent.PointerProperties[ids.length];
            MotionEvent.PointerCoords[] coords=new MotionEvent.PointerCoords[ids.length];
            for(int i=0;i<ids.length;i++){properties[i]=new MotionEvent.PointerProperties();properties[i].id=ids[i];properties[i].toolType=MotionEvent.TOOL_TYPE_FINGER;coords[i]=new MotionEvent.PointerCoords();coords[i].x=xs[i];coords[i].y=ys[i];coords[i].pressure=1;coords[i].size=1;}
            long t=SystemClock.uptimeMillis();MotionEvent e=MotionEvent.obtain(t,t,action,ids.length,properties,coords,0,0,1,1,0,0,0,0);root.dispatchTouchEvent(e);e.recycle();Shadows.shadowOf(Looper.getMainLooper()).idle();
        }
        void tap(View v){Rect r=bounds(v);event(0,r.exactCenterX(),r.exactCenterY());event(1,r.exactCenterX(),r.exactCenterY());}
        void render(String name)throws Exception{Bitmap b=Bitmap.createBitmap(width,height,Bitmap.Config.ARGB_8888);root.draw(new Canvas(b));try(FileOutputStream out=new FileOutputStream(new File("output/"+name+".png"))){assertTrue(b.compress(Bitmap.CompressFormat.PNG,100,out));}b.recycle();}
    }
    @Test public void fourProductionButtonsFitPortraitLandscapeInsetsAndLargeFonts()throws Exception{
        for(float scale:new float[]{1f,1.3f,2f})for(int[] size:new int[][]{{320,568,0,24},{390,844,0,24},{568,320,24,18},{844,390,32,18},{240,240,0,0}}){
            Scene s=new Scene(size[0],size[1],size[2],size[3],scale);
            assertEquals(4,s.pad.getButtons().size());assertTrue(s.pad.getLeft()>=size[2]);assertTrue(s.pad.getRight()<=size[0]-size[2]);assertTrue(s.pad.getTop()>=0);assertTrue(s.pad.getBottom()<=size[1]-size[3]);
            for(MouseControlButton b:s.pad.getButtons()){Rect r=s.bounds(b);assertTrue(r.left>=size[2]&&r.right<=size[0]-size[2]);assertTrue(r.top>=0&&r.bottom<=size[1]-size[3]);assertTrue(r.width()>=48&&r.height()>=48);assertNotNull(b.getContentDescription());}
            s.pad.setDragActive(size[0]>size[1]);s.render("mousepad71-"+size[0]+"x"+size[1]+"-font"+Math.round(scale*100));
        }
    }
    @Test public void dedicatedActionsAreIndependentAndDisabledTouchesNeverReachDesktop(){
        Scene s=new Scene(390,844,0,24,1f);
        for(MouseControlButton b:s.pad.getButtons()){s.tap(b);assertEquals(Integer.valueOf(1),s.calls.get(b.getMouseAction()));}
        assertEquals(0,s.desktopDowns);
        for(MouseControlButton b:s.pad.getButtons()){b.setEnabled(false);s.tap(b);assertFalse(b.performClick());assertEquals(Integer.valueOf(1),s.calls.get(b.getMouseAction()));}
        assertEquals(0,s.desktopDowns);s.event(0,15,220);s.event(1,15,220);assertEquals(1,s.desktopDowns);
    }
    @Test public void secondFingerClicksOverlayWhileFirstKeepsSlidingOnDesktop(){
        Scene s=new Scene(390,844,0,24,1f);Rect r=s.bounds(s.pad.getButtons().get(0));float bx=r.exactCenterX(),by=r.exactCenterY();
        s.pointers(MotionEvent.ACTION_DOWN,new int[]{0},new float[]{20},new float[]{220});
        s.pointers(MotionEvent.ACTION_POINTER_DOWN|(1<<8),new int[]{0,1},new float[]{20,bx},new float[]{220,by});
        s.pointers(MotionEvent.ACTION_MOVE,new int[]{0,1},new float[]{35,bx},new float[]{235,by});
        s.pointers(MotionEvent.ACTION_POINTER_UP|(1<<8),new int[]{0,1},new float[]{35,bx},new float[]{235,by});
        s.pointers(MotionEvent.ACTION_MOVE,new int[]{0},new float[]{50},new float[]{245});
        s.pointers(MotionEvent.ACTION_UP,new int[]{0},new float[]{50},new float[]{245});
        assertEquals(Integer.valueOf(1),s.calls.get(MouseControlButton.Action.LEFT));assertEquals(1,s.desktopDowns);assertTrue(s.desktopMoves>=2);assertEquals(0,s.cancellations);
    }
    @Test public void cancellingOverlayInvokesReleaseWithoutClicking(){
        Scene s=new Scene(390,844,0,24,1f);Rect r=s.bounds(s.pad.getButtons().get(3));
        s.event(MotionEvent.ACTION_DOWN,r.exactCenterX(),r.exactCenterY());s.event(MotionEvent.ACTION_CANCEL,r.exactCenterX(),r.exactCenterY());
        assertEquals(1,s.cancellations);assertFalse(s.calls.containsKey(MouseControlButton.Action.DRAG));assertEquals(0,s.desktopDowns);
    }
    @Test public void dragHasVisibleAccessibleLatchedAndReleasedState()throws Exception{
        Scene s=new Scene(568,320,24,18,1f);MouseControlButton drag=s.pad.getButtons().get(3);
        s.pad.setDragActive(true);assertTrue(drag.isSelected());assertEquals("Soltar botão esquerdo",drag.getText().toString());assertTrue(drag.getContentDescription().toString().contains("Arrastar ativo"));s.render("mousepad71-drag-active");
        s.pad.setDragActive(false);assertFalse(drag.isSelected());assertTrue(drag.getContentDescription().toString().contains("Ativar arrastar"));s.render("mousepad71-drag-released");
    }
}
