"""Production geometry/touch-logic checks using JVM, plus Android API compile check.

The touch shim supplies only event/view primitives, no device, bitmap decoder or
Android rendering. These checks do not establish physical-device behavior.
Usage: python3 tests/viewport/run_viewport_checks.py [TOOLS_DIRECTORY]
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

project = Path(__file__).resolve().parents[2]
tools = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else project.parents[1] / "android_port_tools"
lib = tools / "gradle-8.11.1/lib"
stdlib = next(lib.glob("kotlin-stdlib-[0-9]*.jar"))
annotations = next(lib.glob("annotations-[0-9]*.jar"))
json_jar = tools / "json-20240303.jar"
android_jar = Path(os.environ.get("ANDROID_VIEWPORT_JAR", str(tools / "sdk/platforms/android-35/android.jar"))).resolve()
production = project / "app/src/main/java/br/com/perabru/minianydesk"
cp = os.pathsep.join(map(str, (stdlib, annotations, json_jar)))
compiler = ["java", "-cp", str(lib / "*"), "org.jetbrains.kotlin.cli.jvm.K2JVMCompiler",
            "-no-stdlib", "-no-reflect", "-jvm-target", "17"]

stubs = {
"android/content/Context.java": '''package android.content;
public class Context { public android.content.res.Resources getResources() { return new android.content.res.Resources(); } }''',
"android/content/res/Resources.java": '''package android.content.res;
public class Resources { public android.util.DisplayMetrics getDisplayMetrics() { return new android.util.DisplayMetrics(); } }''',
"android/util/AttributeSet.java": '''package android.util; public interface AttributeSet {}''',
"android/util/DisplayMetrics.java": '''package android.util; public class DisplayMetrics { public float density=1f; }''',
"android/util/TypedValue.java": '''package android.util;
public class TypedValue { public static final int COMPLEX_UNIT_SP=2; public static float applyDimension(int unit,float value,DisplayMetrics metrics){ return value; } }''',
"android/util/Base64.java": '''package android.util;
public class Base64 { public static final int DEFAULT=0; public static byte[] decode(String input,int flags){ return java.util.Base64.getDecoder().decode(input); } }''',
"android/graphics/Bitmap.java": '''package android.graphics;
public class Bitmap { public enum Config{ ARGB_8888 } private int width,height;public Bitmap(){this(1920,1080);}public Bitmap(int w,int h){width=w;height=h;}public int getWidth(){return width;}public int getHeight(){return height;}public void recycle(){} }''',
"android/graphics/BitmapFactory.java": '''package android.graphics;
public class BitmapFactory { public static int sourceWidth=1920,sourceHeight=1080;public static class Options { public boolean inJustDecodeBounds,inScaled; public int outWidth=1920,outHeight=1080,inSampleSize,inDensity,inTargetDensity; public String outMimeType="image/jpeg"; public Bitmap.Config inPreferredConfig; }
public static int boundsCount=0,decodeCount=0,lastSample=0;public static long decodeMillis=0;public static Bitmap.Config lastConfig;
public static void resetCounts(){boundsCount=0;decodeCount=0;lastSample=0;lastConfig=null;}
public static Bitmap decodeByteArray(byte[] bytes,int offset,int length,Options options){ if(options.inJustDecodeBounds){boundsCount++;options.outWidth=sourceWidth;options.outHeight=sourceHeight;return null;}decodeCount++;lastSample=options.inSampleSize;lastConfig=options.inPreferredConfig;android.os.SystemClock.advance(decodeMillis);return new Bitmap((sourceWidth+lastSample-1)/lastSample,(sourceHeight+lastSample-1)/lastSample); } }''',
"android/graphics/Rect.java": '''package android.graphics; public class Rect {}''',
"android/graphics/RectF.java": '''package android.graphics; public class RectF { public float left,top,right,bottom;public RectF(float a,float b,float c,float d){left=a;top=b;right=c;bottom=d;} }''',
"android/graphics/Color.java": '''package android.graphics; public class Color { public static int rgb(int r,int g,int b){return 0;} }''',
"android/graphics/Paint.java": '''package android.graphics;
public class Paint { public static final int ANTI_ALIAS_FLAG=1,FILTER_BITMAP_FLAG=2; public enum Align{ CENTER } public Paint(int flags){} private int color;private float textSize;private Align textAlign;private boolean filterBitmap;public boolean isFilterBitmap(){return filterBitmap;}public void setFilterBitmap(boolean value){filterBitmap=value;}
public int getColor(){return color;} public void setColor(int c){color=c;} public float getTextSize(){return textSize;} public void setTextSize(float s){textSize=s;} public Align getTextAlign(){return textAlign;} public void setTextAlign(Align a){textAlign=a;} public float ascent(){return 0;} public float descent(){return 0;} }''',
"android/graphics/Canvas.java": '''package android.graphics;
public class Canvas { public static RectF lastBitmapRect;public static boolean lastBitmapFiltered;public void drawCircle(float x,float y,float radius,Paint paint){} public void drawText(String text,float x,float y,Paint paint){} public void drawBitmap(Bitmap bitmap,Rect src,RectF dst,Paint paint){lastBitmapRect=dst;lastBitmapFiltered=paint.isFilterBitmap();} public int save(){return 1;} public boolean clipRect(int a,int b,int c,int d){return true;} public void restoreToCount(int count){} }''',
"android/os/Looper.java": '''package android.os; public class Looper { private static final Looper MAIN=new Looper(); public static Looper getMainLooper(){return MAIN;} public static Looper myLooper(){return MAIN;} }''',
"android/os/SystemClock.java": '''package android.os; public class SystemClock { private static long now=10000; public static long uptimeMillis(){return now;} public static void advance(long millis){now+=millis;} }''',
"android/os/Handler.java": '''package android.os;
public class Handler { private static final java.util.Map<Runnable,Long> callbacks=new java.util.LinkedHashMap<>(); private static final java.util.ArrayDeque<Runnable> posted=new java.util.ArrayDeque<>(); private static boolean deferPosts=false; public Handler(Looper looper){} public boolean post(Runnable action){if(deferPosts)posted.addLast(action);else action.run();return true;} public static void setDeferPosts(boolean defer){deferPosts=defer;} public static void runPosted(){while(!posted.isEmpty())posted.removeFirst().run();} public boolean postDelayed(Runnable action,long delay){callbacks.put(action,SystemClock.uptimeMillis()+delay);return true;} public void removeCallbacks(Runnable action){callbacks.remove(action);posted.remove(action);} public static void runDue(){for(Runnable r:new java.util.ArrayList<>(callbacks.keySet())){Long due=callbacks.get(r);if(due!=null&&due<=SystemClock.uptimeMillis()){callbacks.remove(r);r.run();}}} }''',
"android/view/ViewParent.java": '''package android.view; public interface ViewParent { void requestDisallowInterceptTouchEvent(boolean disallow); }''',
"android/view/ViewConfiguration.java": '''package android.view; public class ViewConfiguration { public static ViewConfiguration get(android.content.Context context){return new ViewConfiguration();} public int getScaledTouchSlop(){return 8;} public static int getLongPressTimeout(){return 500;} }''',
"android/view/MotionEvent.java": '''package android.view;
public class MotionEvent { public static final int ACTION_DOWN=0,ACTION_UP=1,ACTION_MOVE=2,ACTION_CANCEL=3,ACTION_POINTER_DOWN=5,ACTION_POINTER_UP=6; private final int action,index;private final int[] ids;private final float[] xs,ys;
public MotionEvent(int action,int index,int[] ids,float[] xs,float[] ys){this.action=action;this.index=index;this.ids=ids;this.xs=xs;this.ys=ys;} public int getActionMasked(){return action;} public int getActionIndex(){return index;} public int getPointerCount(){return ids.length;} public int getPointerId(int index){return ids[index];} public int findPointerIndex(int id){for(int i=0;i<ids.length;i++)if(ids[i]==id)return i;return -1;} public float getX(){return xs[0];} public float getY(){return ys[0];} public float getX(int index){return xs[index];} public float getY(int index){return ys[index];} }''',
"android/view/View.java": '''package android.view;
public class View { public static final int VISIBLE=0;private boolean focusable,focusableInTouchMode,clickable;private CharSequence contentDescription;private final android.content.Context context;
public View(android.content.Context context,android.util.AttributeSet attributes,int style){this.context=context;}
public android.content.res.Resources getResources(){return context.getResources();}public int getWidth(){return 640;}public int getHeight(){return 480;}public int getPaddingLeft(){return 0;}public int getPaddingRight(){return 0;}public int getPaddingTop(){return 0;}public int getPaddingBottom(){return 0;}
public boolean isFocusable(){return focusable;}public void setFocusable(boolean value){focusable=value;}public boolean isFocusableInTouchMode(){return focusableInTouchMode;}public void setFocusableInTouchMode(boolean value){focusableInTouchMode=value;}public boolean isClickable(){return clickable;}public void setClickable(boolean value){clickable=value;}
public CharSequence getContentDescription(){return contentDescription;}public void setContentDescription(CharSequence value){contentDescription=value;}
public boolean isShown(){return true;}public boolean isAttachedToWindow(){return true;}public boolean hasWindowFocus(){return true;}public int getWindowVisibility(){return VISIBLE;}public ViewParent getParent(){return null;}public void setBackgroundColor(int color){}public boolean requestFocus(){return true;}public void invalidate(){}
protected void onDraw(android.graphics.Canvas canvas){}protected void onSizeChanged(int width,int height,int oldWidth,int oldHeight){}public boolean onTouchEvent(MotionEvent event){return false;}public boolean performClick(){return true;}public void onWindowFocusChanged(boolean hasWindowFocus){}protected void onVisibilityChanged(View view,int visibility){}protected void onWindowVisibilityChanged(int visibility){}protected void onFocusChanged(boolean focus,int direction,android.graphics.Rect rect){}protected void onAttachedToWindow(){}protected void onDetachedFromWindow(){} }''',
}

with tempfile.TemporaryDirectory(prefix="viewport-checks-") as temporary:
    build = Path(temporary)
    geometry_jar = build / "geometry.jar"
    subprocess.run([*compiler, "-classpath", cp, str(production / "ScreenGeometry.kt"),
        str(production / "BitmapDecodePolicy.kt"), str(project / "tests/BitmapDecodePolicyHarness.kt"),
        str(project / "tests/GeometryHarness.kt"), str(project / "tests/viewport/ViewportHarness.kt"),
        "-d", str(geometry_jar)], check=True)
    for harness in ("GeometryHarnessKt", "ViewportHarnessKt"):
        subprocess.run(["java", "-cp", os.pathsep.join((str(geometry_jar), cp)),
            "br.com.perabru.minianydesk." + harness], check=True)
    subprocess.run(["java", "-cp", os.pathsep.join((str(geometry_jar), cp)),
        "br.com.perabru.minianydesk.BitmapDecodePolicyHarness"], check=True)
    stub_sources = []
    for name, content in stubs.items():
        path = build / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        stub_sources.append(str(path))
    stub_classes = build / "stubs"
    javac = tools / "jdk-17/bin/javac"
    java_compiler = [str(javac)] if javac.exists() else ["java", "com.sun.tools.javac.Main"]
    subprocess.run([*java_compiler, "-d", str(stub_classes), *stub_sources], check=True)
    touch_jar = build / "touch.jar"
    subprocess.run([*compiler, "-classpath", os.pathsep.join((str(stub_classes), cp)),
        str(production / "ScreenGeometry.kt"), str(production / "BitmapDecodePolicy.kt"), str(production / "PointerClicks.kt"), str(production / "RemoteScreenView.kt"),
        str(production / "StreamFeedback.kt"), str(production / "StreamSettings.kt"), str(project / "tests/viewport/ViewportTouchHarness.kt"),
        str(project / "tests/viewport/StreamPresentationHarness.kt"), "-d", str(touch_jar)], check=True)
    subprocess.run(["java", "-cp", os.pathsep.join((str(touch_jar), str(stub_classes), cp)),
        "br.com.perabru.minianydesk.ViewportTouchHarnessKt"], check=True)
    subprocess.run(["java", "-cp", os.pathsep.join((str(touch_jar), str(stub_classes), cp)),
        "br.com.perabru.minianydesk.StreamPresentationHarnessKt"], check=True)
    subprocess.run([*compiler, "-classpath", os.pathsep.join((str(android_jar), cp)),
        str(production / "ScreenGeometry.kt"), str(production / "BitmapDecodePolicy.kt"), str(production / "PointerClicks.kt"), str(production / "RemoteScreenView.kt"),
        "-d", str(build / "android-api.jar")], check=True)
    print("VIEWPORT_ANDROID_API_COMPILE_OK")
