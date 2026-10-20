package br.com.perabru.minianydesk

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.os.Handler
import android.os.SystemClock
import android.view.MotionEvent
import org.json.JSONObject

/** Runs production touch logic with a tiny framework shim, not an emulator or Android device. */
fun main() {
    var count = 0
    fun test(name: String, body: () -> Unit) { body(); count++; println("OK:$name") }
    val sent = mutableListOf<JSONObject>()
    val view = RemoteScreenView(Context()).apply { send = { sent.add(it) }; setInputAllowed(true) }
    fun draw(capture: String = "pc-display", width: Int = 1920) {
        val decodedClass = RemoteScreenView::class.java.declaredClasses.first { it.simpleName == "Decoded" }
        val constructor = decodedClass.declaredConstructors.first().apply { isAccessible = true }
        val metadata = JSONObject().put("captureId", capture).put("width", width).put("height", 1080)
        val generation = RemoteScreenView::class.java.getDeclaredField("generation").apply { isAccessible = true }.get(view) as java.util.concurrent.atomic.AtomicLong
        val frame = constructor.newInstance(generation.get(), metadata, Bitmap(), 1920, 1080)
        RemoteScreenView::class.java.getDeclaredField("rendering").apply { isAccessible = true }.set(view, frame)
        RemoteScreenView::class.java.getDeclaredMethod("onDraw", Canvas::class.java).apply { isAccessible = true }.invoke(view, Canvas())
    }
    fun touch(action: Int, x: Float = 320f, y: Float = 240f, secondX: Float? = null, secondY: Float = y, index: Int = 0) {
        val ids = if (secondX == null) intArrayOf(7) else intArrayOf(7, 19)
        val xs = if (secondX == null) floatArrayOf(x) else floatArrayOf(x, secondX)
        val ys = if (secondX == null) floatArrayOf(y) else floatArrayOf(y, secondY)
        view.onTouchEvent(MotionEvent(action, index, ids, xs, ys))
    }
    fun buttonEvents() = sent.filter { it.optString("action") == "button" }
    fun checkReleased() {
        val held = mutableSetOf<String>()
        for (message in sent) when (message.optString("action")) {
            "release" -> held.clear()
            "button" -> if (message.optBoolean("down")) held.add(message.optString("button")) else held.remove(message.optString("button"))
        }
        check(held.isEmpty()) { "Remote button remained down: $held" }
    }
    fun reset() { view.releaseInput(); view.resetZoom(); sent.clear() }
    draw()
    sent.clear()
    test("short tap waits for release and sends one complete click") {
        touch(MotionEvent.ACTION_DOWN)
        check(buttonEvents().isEmpty())
        touch(MotionEvent.ACTION_UP)
        check(buttonEvents().map { it.optBoolean("down") } == listOf(true, false))
        checkReleased()
    }
    test("drag starts at touched point and releases at the final coordinate") {
        reset()
        touch(MotionEvent.ACTION_DOWN, 100f, 200f)
        touch(MotionEvent.ACTION_MOVE, 200f, 250f)
        check(buttonEvents().single().optBoolean("down"))
        touch(MotionEvent.ACTION_UP, 250f, 280f)
        val moves = sent.filter { it.optString("action") == "move" }
        check(moves.first().optDouble("x") < moves.last().optDouble("x"))
        check(buttonEvents().last().optBoolean("down") == false)
        checkReleased()
    }
    test("long press starts drag and cancellation releases immediately") {
        reset()
        touch(MotionEvent.ACTION_DOWN)
        SystemClock.advance(600)
        Handler.runDue()
        check(buttonEvents().single().optBoolean("down"))
        touch(MotionEvent.ACTION_CANCEL)
        checkReleased()
        val countAfterCancel = buttonEvents().size
        SystemClock.advance(1000); Handler.runDue()
        check(buttonEvents().size == countAfterCancel)
    }
    test("pinch zoom never produces a PC click and remaining finger stays suppressed") {
        reset()
        touch(MotionEvent.ACTION_DOWN, 220f, 240f)
        touch(MotionEvent.ACTION_POINTER_DOWN, 220f, 240f, 420f, index=1)
        touch(MotionEvent.ACTION_MOVE, 120f, 240f, 520f)
        check(view.zoomLabel == "200%")
        touch(MotionEvent.ACTION_POINTER_UP, 120f, 240f, 520f, index=1)
        touch(MotionEvent.ACTION_MOVE, 300f, 300f)
        touch(MotionEvent.ACTION_UP, 300f, 300f)
        check(buttonEvents().isEmpty())
        checkReleased()
    }
    test("starting a pinch during a held drag releases the PC mouse before zoom") {
        reset()
        touch(MotionEvent.ACTION_DOWN)
        touch(MotionEvent.ACTION_MOVE, 350f, 260f)
        check(buttonEvents().single().optBoolean("down"))
        touch(MotionEvent.ACTION_POINTER_DOWN, 250f, 240f, 450f, index=1)
        checkReleased()
        touch(MotionEvent.ACTION_MOVE, 150f, 240f, 550f)
        check(view.zoomLabel == "200%")
        touch(MotionEvent.ACTION_CANCEL)
        checkReleased()
    }
    test("two-finger panning retains target coordinate while moving a zoomed picture") {
        reset()
        touch(MotionEvent.ACTION_DOWN, 220f, 240f)
        touch(MotionEvent.ACTION_POINTER_DOWN, 220f, 240f, 420f, index=1)
        touch(MotionEvent.ACTION_MOVE, 120f, 240f, 520f)
        touch(MotionEvent.ACTION_MOVE, 160f, 270f, 560f, 270f)
        touch(MotionEvent.ACTION_CANCEL)
        sent.clear()
        touch(MotionEvent.ACTION_DOWN, 360f, 270f)
        touch(MotionEvent.ACTION_UP, 360f, 270f)
        val click = buttonEvents().first()
        check(kotlin.math.abs(click.optDouble("x") - 320.0 / 639.0) < 1e-8)
        check(kotlin.math.abs(click.optDouble("y") - 180.0 / 359.0) < 1e-8)
        checkReleased()
    }
    test("input pause releases held mouse and permits local zoom only") {
        reset()
        touch(MotionEvent.ACTION_DOWN)
        touch(MotionEvent.ACTION_MOVE, 350f, 260f)
        view.setInputAllowed(false)
        checkReleased()
        sent.clear()
        touch(MotionEvent.ACTION_DOWN, 220f, 240f)
        touch(MotionEvent.ACTION_POINTER_DOWN, 220f, 240f, 420f, index=1)
        touch(MotionEvent.ACTION_MOVE, 120f, 240f, 520f)
        touch(MotionEvent.ACTION_CANCEL)
        check(view.zoomLabel == "200%")
        check(buttonEvents().isEmpty())
        view.setInputAllowed(true)
        sent.clear()
        touch(MotionEvent.ACTION_MOVE, 300f, 300f)
        touch(MotionEvent.ACTION_UP, 300f, 300f)
        check(buttonEvents().isEmpty())
    }
    test("capture change cancels pending tap and held drag, and resets zoom") {
        reset()
        view.zoomIn()
        touch(MotionEvent.ACTION_DOWN)
        touch(MotionEvent.ACTION_MOVE, 350f, 260f)
        draw("different-monitor")
        checkReleased()
        check(view.zoomLabel == "100%")
        sent.clear()
        touch(MotionEvent.ACTION_UP, 350f, 260f)
        check(buttonEvents().isEmpty())
        touch(MotionEvent.ACTION_DOWN)
        draw("third-monitor")
        touch(MotionEvent.ACTION_UP)
        SystemClock.advance(1000); Handler.runDue()
        check(buttonEvents().isEmpty())
    }
    test("frame clearing cannot resurrect input from a held gesture") {
        reset()
        touch(MotionEvent.ACTION_DOWN)
        SystemClock.advance(600); Handler.runDue()
        check(buttonEvents().single().optBoolean("down"))
        view.clearFrame()
        checkReleased()
        sent.clear()
        touch(MotionEvent.ACTION_UP)
        SystemClock.advance(1000); Handler.runDue()
        check(buttonEvents().isEmpty())
        check(view.zoomLabel == "100%")
    }
    test("toolbar zoom releases drag and obeys limits") {
        draw()
        reset()
        touch(MotionEvent.ACTION_DOWN)
        touch(MotionEvent.ACTION_MOVE, 350f, 260f)
        repeat(20) { view.zoomIn() }
        check(view.zoomLabel == "600%")
        checkReleased()
        repeat(30) { view.zoomOut() }
        check(view.zoomLabel == "100%")
        checkReleased()
    }
    println("VIEWPORT_TOUCH_SHIM_OK:$count")
}
