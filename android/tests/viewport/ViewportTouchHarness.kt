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
    fun draw(capture: String = "pc-display", width: Int = 1920, frameId: Int? = null) {
        val decodedClass = RemoteScreenView::class.java.declaredClasses.first { it.simpleName == "Decoded" }
        val constructor = decodedClass.declaredConstructors.first().apply { isAccessible = true }
        val metadata = JSONObject().put("captureId", capture).put("width", width).put("height", 1080)
        if (frameId != null) metadata.put("streamVersion", 1).put("frameId", frameId)
        val generation = RemoteScreenView::class.java.getDeclaredField("generation").apply { isAccessible = true }.get(view) as java.util.concurrent.atomic.AtomicLong
        val frame = constructor.newInstance(generation.get(), metadata, Bitmap(), 1920, 1080, SystemClock.uptimeMillis() - 23, 7L)
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
    test("pacing feedback fires after draw once per exact frame with local timing") {
        val presented = mutableListOf<Triple<JSONObject, Long, Long>>()
        view.onFramePresented = { info, decodeMs, renderMs -> presented.add(Triple(info, decodeMs, renderMs)) }
        check(presented.isEmpty())
        draw(frameId = 1)
        check(presented.size == 1 && presented.single().first.getInt("frameId") == 1)
        check(presented.single().second == 7L && presented.single().third == 16L)
        RemoteScreenView::class.java.getDeclaredMethod("onDraw", Canvas::class.java).apply { isAccessible = true }.invoke(view, Canvas())
        check(presented.size == 1) { "Zoom/redraw must not acknowledge the same frame twice" }
        view.clearFrame()
        RemoteScreenView::class.java.getDeclaredMethod("onDraw", Canvas::class.java).apply { isAccessible = true }.invoke(view, Canvas())
        check(presented.size == 1) { "Cleared capture must not emit display feedback" }
        draw(frameId = 2)
        check(presented.size == 2)
        Handler.setDeferPosts(true)
        draw(frameId = 3)
        check(presented.size == 2)
        view.clearFrame()
        Handler.setDeferPosts(false); Handler.runPosted()
        check(presented.size == 2) { "A queued old draw callback cannot acknowledge a cleared capture" }
        draw(frameId = 4)
        check(presented.size == 3)
        view.onFramePresented = null
        sent.clear()
    }
    fun point(): ScreenGeometry.Point = RemoteScreenView::class.java.getDeclaredField("lastPoint")
        .apply { isAccessible = true }.get(view) as ScreenGeometry.Point
    fun same(actual: ScreenGeometry.Point, expected: ScreenGeometry.Point) {
        check(kotlin.math.abs(actual.x - expected.x) < 1e-9 && kotlin.math.abs(actual.y - expected.y) < 1e-9) {
            "Cursor moved unexpectedly: $actual != $expected"
        }
    }
    fun fresh() { draw("pad-test-$count"); reset() }
    test("touchpad down and short tap never reposition or click the cursor") {
        fresh()
        val before = point()
        touch(MotionEvent.ACTION_DOWN, 20f, 20f)
        same(point(), before)
        check(sent.isEmpty()) { "Touch-down must not send an absolute move" }
        touch(MotionEvent.ACTION_UP, 20f, 20f)
        same(point(), before)
        check(buttonEvents().isEmpty())
        checkReleased()
    }
    test("stationary long press never arms drag or creates a delayed click") {
        fresh()
        touch(MotionEvent.ACTION_DOWN, 320f, 240f)
        SystemClock.advance(2000); Handler.runDue()
        check(buttonEvents().isEmpty() && !view.isDragActive)
        touch(MotionEvent.ACTION_UP, 320f, 240f)
        check(buttonEvents().isEmpty())
        checkReleased()
    }
    test("one finger moves the cursor relatively without clicking") {
        fresh()
        val before = point()
        touch(MotionEvent.ACTION_DOWN, 100f, 200f)
        touch(MotionEvent.ACTION_MOVE, 180f, 240f)
        val after = point()
        check(after.x > before.x && after.y > before.y)
        touch(MotionEvent.ACTION_UP, 180f, 240f)
        same(point(), after)
        check(buttonEvents().isEmpty())
        check(sent.filter { it.optString("action") == "move" }.all { it.optString("captureId") == "pad-test-${count}" })
        checkReleased()
    }
    test("lifting and repositioning the finger keeps cursor and permits continued relative motion") {
        fresh()
        touch(MotionEvent.ACTION_DOWN, 100f, 200f)
        touch(MotionEvent.ACTION_MOVE, 140f, 220f)
        touch(MotionEvent.ACTION_UP, 140f, 220f)
        val first = point()
        touch(MotionEvent.ACTION_DOWN, 500f, 400f)
        same(point(), first)
        touch(MotionEvent.ACTION_MOVE, 540f, 420f)
        touch(MotionEvent.ACTION_UP, 540f, 420f)
        val second = point()
        check(kotlin.math.abs((second.x - first.x) - (first.x - .5)) < 1e-9)
        check(kotlin.math.abs((second.y - first.y) - (first.y - .5)) < 1e-9)
        check(buttonEvents().isEmpty())
    }
    test("letterbox margins also work as touchpad without an absolute cursor jump") {
        fresh()
        val before = point()
        // The fitted 16:9 picture occupies y=60..419 inside the 640x480 View.
        touch(MotionEvent.ACTION_DOWN, 100f, 10f)
        same(point(), before)
        touch(MotionEvent.ACTION_MOVE, 150f, 40f)
        touch(MotionEvent.ACTION_UP, 150f, 40f)
        check(point().x > before.x && point().y > before.y)
        check(buttonEvents().isEmpty())
    }
    test("coalesced release contributes its final relative delta and is never a click") {
        fresh()
        val before = point()
        touch(MotionEvent.ACTION_DOWN, 100f, 200f)
        touch(MotionEvent.ACTION_UP, 180f, 240f)
        check(point().x > before.x && point().y > before.y)
        check(buttonEvents().isEmpty())
        val finalMove = sent.last { it.optString("action") == "move" }
        check(finalMove.optDouble("x") == point().x && finalMove.optDouble("y") == point().y)
    }
    test("cursor clamps to every edge and can immediately move back from the edge") {
        fresh()
        touch(MotionEvent.ACTION_DOWN, 100f, 200f)
        touch(MotionEvent.ACTION_MOVE, 1e6f, 1e6f)
        same(point(), ScreenGeometry.Point(1.0, 1.0))
        touch(MotionEvent.ACTION_MOVE, 1e6f - 30f, 1e6f - 20f)
        check(point().x < 1.0 && point().y < 1.0)
        touch(MotionEvent.ACTION_MOVE, -1e6f, -1e6f)
        same(point(), ScreenGeometry.Point(0.0, 0.0))
        touch(MotionEvent.ACTION_MOVE, -1e6f + 30f, -1e6f + 20f)
        check(point().x > 0.0 && point().y > 0.0)
        touch(MotionEvent.ACTION_CANCEL)
        check(sent.filter { it.has("x") }.all { it.optDouble("x").isFinite() && it.optDouble("y").isFinite() &&
            it.optDouble("x") in 0.0..1.0 && it.optDouble("y") in 0.0..1.0 })
        checkReleased()
    }
    test("nonfinite touch coordinates never corrupt the cursor or emit nonfinite targets") {
        fresh()
        val before = point()
        touch(MotionEvent.ACTION_DOWN, 100f, 200f)
        touch(MotionEvent.ACTION_MOVE, Float.NaN, Float.POSITIVE_INFINITY)
        same(point(), before)
        touch(MotionEvent.ACTION_UP, 100f, 200f)
        check(point().x.isFinite() && point().y.isFinite())
        check(sent.filter { it.has("x") }.all { it.optDouble("x").isFinite() && it.optDouble("y").isFinite() })
        check(buttonEvents().isEmpty())
    }
    test("a stable held finger remains a pointer only after moving away and returning") {
        fresh()
        touch(MotionEvent.ACTION_DOWN, 320f, 240f)
        touch(MotionEvent.ACTION_MOVE, 360f, 260f)
        touch(MotionEvent.ACTION_MOVE, 320f, 240f)
        SystemClock.advance(1200); Handler.runDue()
        touch(MotionEvent.ACTION_UP, 320f, 240f)
        same(point(), ScreenGeometry.Point(.5, .5))
        check(buttonEvents().isEmpty())
        checkReleased()
    }
    test("explicit drag toggle holds the mouse across finger lifts and releases on second toggle") {
        fresh()
        val changes = mutableListOf<Boolean>()
        view.onDragChanged = { changes.add(it) }
        check(view.toggleDrag() && view.isDragActive)
        check(buttonEvents().map { it.optBoolean("down") } == listOf(true))
        touch(MotionEvent.ACTION_DOWN, 100f, 200f)
        touch(MotionEvent.ACTION_MOVE, 180f, 240f)
        touch(MotionEvent.ACTION_UP, 180f, 240f)
        check(view.isDragActive && buttonEvents().size == 1)
        val first = point()
        touch(MotionEvent.ACTION_DOWN, 20f, 20f)
        same(point(), first)
        touch(MotionEvent.ACTION_MOVE, 60f, 40f)
        touch(MotionEvent.ACTION_UP, 60f, 40f)
        check(view.isDragActive && point().x > first.x)
        check(view.toggleDrag() && !view.isDragActive)
        check(buttonEvents().map { it.optBoolean("down") } == listOf(true, false))
        check(changes == listOf(true, false))
        view.onDragChanged = null
        checkReleased()
    }
    test("drag cancellation releases immediately and cannot resume from an old finger") {
        fresh()
        check(view.toggleDrag())
        touch(MotionEvent.ACTION_DOWN)
        touch(MotionEvent.ACTION_MOVE, 350f, 260f)
        touch(MotionEvent.ACTION_CANCEL)
        check(!view.isDragActive)
        checkReleased()
        val before = point()
        sent.clear()
        touch(MotionEvent.ACTION_MOVE, 400f, 300f)
        touch(MotionEvent.ACTION_UP, 400f, 300f)
        same(point(), before)
        check(buttonEvents().isEmpty())
    }
    test("focus loss releases explicit drag and suppresses the original finger") {
        fresh()
        check(view.toggleDrag())
        touch(MotionEvent.ACTION_DOWN)
        view.onWindowFocusChanged(false)
        check(!view.isDragActive)
        checkReleased()
        sent.clear()
        val before = point()
        SystemClock.advance(1200); Handler.runDue()
        touch(MotionEvent.ACTION_MOVE, 400f, 300f)
        touch(MotionEvent.ACTION_UP, 400f, 300f)
        same(point(), before)
        check(buttonEvents().isEmpty())
    }
    test("pinch zoom never produces a PC click and remaining finger stays suppressed") {
        fresh()
        val before = point()
        touch(MotionEvent.ACTION_DOWN, 220f, 240f)
        touch(MotionEvent.ACTION_POINTER_DOWN, 220f, 240f, 420f, index=1)
        touch(MotionEvent.ACTION_MOVE, 120f, 240f, 520f)
        check(view.zoomLabel == "200%")
        touch(MotionEvent.ACTION_POINTER_UP, 120f, 240f, 520f, index=1)
        touch(MotionEvent.ACTION_MOVE, 300f, 300f)
        touch(MotionEvent.ACTION_UP, 300f, 300f)
        same(point(), before)
        check(buttonEvents().isEmpty())
        checkReleased()
    }
    test("starting pinch during explicit drag releases before local zoom") {
        fresh()
        check(view.toggleDrag())
        touch(MotionEvent.ACTION_DOWN)
        touch(MotionEvent.ACTION_MOVE, 350f, 260f)
        check(view.isDragActive)
        touch(MotionEvent.ACTION_POINTER_DOWN, 250f, 240f, 450f, index=1)
        check(!view.isDragActive)
        checkReleased()
        touch(MotionEvent.ACTION_MOVE, 150f, 240f, 550f)
        check(view.zoomLabel == "200%")
        touch(MotionEvent.ACTION_CANCEL)
        checkReleased()
    }
    test("two finger panning changes the picture without repositioning the cursor") {
        fresh()
        val before = point()
        touch(MotionEvent.ACTION_DOWN, 220f, 240f)
        touch(MotionEvent.ACTION_POINTER_DOWN, 220f, 240f, 420f, index=1)
        touch(MotionEvent.ACTION_MOVE, 120f, 240f, 520f)
        touch(MotionEvent.ACTION_MOVE, 160f, 270f, 560f, 270f)
        touch(MotionEvent.ACTION_CANCEL)
        same(point(), before)
        sent.clear()
        touch(MotionEvent.ACTION_DOWN, 360f, 270f)
        touch(MotionEvent.ACTION_UP, 360f, 270f)
        check(view.sendButtonTap("left"))
        val click = buttonEvents().first()
        check(click.optDouble("x") == before.x && click.optDouble("y") == before.y)
        checkReleased()
    }
    test("input pause releases mouse and keeps local zoom available without remote commands") {
        fresh()
        check(view.toggleDrag())
        touch(MotionEvent.ACTION_DOWN)
        touch(MotionEvent.ACTION_MOVE, 350f, 260f)
        view.setInputAllowed(false)
        check(!view.isDragActive)
        checkReleased()
        sent.clear()
        check(!view.toggleDrag())
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
    test("capture change releases drag and resets zoom and cursor without replaying the old gesture") {
        fresh()
        view.zoomIn()
        check(view.toggleDrag())
        touch(MotionEvent.ACTION_DOWN)
        touch(MotionEvent.ACTION_MOVE, 350f, 260f)
        draw("different-monitor")
        checkReleased()
        check(!view.isDragActive && view.zoomLabel == "100%")
        same(point(), ScreenGeometry.Point(.5, .5))
        sent.clear()
        touch(MotionEvent.ACTION_MOVE, 450f, 360f)
        touch(MotionEvent.ACTION_UP, 450f, 360f)
        check(buttonEvents().isEmpty())
        same(point(), ScreenGeometry.Point(.5, .5))
    }
    test("frame clearing cannot resurrect held input or accept fresh commands") {
        fresh()
        check(view.toggleDrag())
        touch(MotionEvent.ACTION_DOWN)
        view.clearFrame()
        checkReleased()
        check(!view.isDragActive)
        sent.clear()
        touch(MotionEvent.ACTION_UP)
        check(!view.toggleDrag() && !view.sendDoubleClick())
        SystemClock.advance(1000); Handler.runDue()
        check(buttonEvents().isEmpty() && view.zoomLabel == "100%")
    }
    test("toolbar zoom releases drag and obeys view limits") {
        fresh()
        check(view.toggleDrag())
        touch(MotionEvent.ACTION_DOWN)
        touch(MotionEvent.ACTION_MOVE, 350f, 260f)
        repeat(20) { view.zoomIn() }
        check(view.zoomLabel == "600%" && !view.isDragActive)
        checkReleased()
        repeat(30) { view.zoomOut() }
        check(view.zoomLabel == "100%")
        checkReleased()
    }
    test("visible left right and double clicks preserve point capture and complete strokes") {
        draw("click-controls"); reset()
        touch(MotionEvent.ACTION_DOWN, 100f, 200f); touch(MotionEvent.ACTION_UP, 160f, 240f)
        val target = point()
        sent.clear()
        check(view.sendDoubleClick())
        check(buttonEvents().map { it.optBoolean("down") } == listOf(true, false, true, false))
        check(buttonEvents().all { it.optString("button") == "left" && it.optString("captureId") == "click-controls" &&
            it.optDouble("x") == target.x && it.optDouble("y") == target.y })
        checkReleased(); sent.clear()
        check(view.sendButtonTap("right"))
        check(buttonEvents().map { it.optBoolean("down") } == listOf(true, false))
        check(buttonEvents().all { it.optString("button") == "right" })
        checkReleased(); sent.clear()
        check(view.sendButtonTap("left"))
        check(buttonEvents().map { it.optBoolean("down") } == listOf(true, false))
        check(buttonEvents().all { it.optString("button") == "left" })
        checkReleased()
    }
    test("overlay click during a gesture does not allow its delayed release to click again") {
        fresh()
        touch(MotionEvent.ACTION_DOWN, 220f, 240f)
        check(view.sendButtonTap("right"))
        check(buttonEvents().map { it.optBoolean("down") } == listOf(true, false))
        SystemClock.advance(1200); Handler.runDue()
        touch(MotionEvent.ACTION_UP, 220f, 240f)
        check(buttonEvents().size == 2)
        checkReleased()
    }
    test("simultaneous overlay click preserves an active touchpad anchor for continued motion") {
        fresh()
        touch(MotionEvent.ACTION_DOWN, 100f, 200f)
        touch(MotionEvent.ACTION_MOVE, 140f, 220f)
        val first = point()
        check(view.sendButtonTap("left"))
        touch(MotionEvent.ACTION_MOVE, 180f, 240f)
        touch(MotionEvent.ACTION_UP, 180f, 240f)
        val second = point()
        check(kotlin.math.abs((second.x - first.x) - (first.x - .5)) < 1e-9)
        check(kotlin.math.abs((second.y - first.y) - (first.y - .5)) < 1e-9)
        check(buttonEvents().map { it.optBoolean("down") } == listOf(true, false))
        checkReleased()
    }
    test("click action ends a held drag before issuing complete new strokes") {
        fresh()
        check(view.toggleDrag())
        check(view.sendButtonTap("right"))
        check(!view.isDragActive)
        val buttons = buttonEvents()
        check(buttons[0].optString("button") == "left" && buttons[0].optBoolean("down"))
        check(buttons[1].optString("button") == "left" && !buttons[1].optBoolean("down"))
        check(buttons.drop(2).map { it.optBoolean("down") } == listOf(true, false))
        checkReleased()
    }
    test("wheel uses persistent cursor position and never presses a mouse button") {
        fresh()
        val target = point()
        check(view.sendWheel(999))
        check(view.sendWheel(-999))
        val wheel = sent.filter { it.optString("action") == "wheel" }
        check(wheel.map { it.optInt("delta") } == listOf(20, -20))
        check(wheel.all { it.optDouble("x") == target.x && it.optDouble("y") == target.y })
        check(buttonEvents().isEmpty())
    }
    test("capture loss during explicit drag down cannot leave local or remote button held") {
        fresh()
        var cleared = false
        view.send = { command ->
            sent.add(command)
            if (!cleared && command.optString("action") == "button" && command.optBoolean("down")) {
                cleared = true; view.clearFrame()
            }
        }
        check(!view.toggleDrag())
        check(!view.isDragActive)
        checkReleased()
        view.send = { sent.add(it) }
        draw(); reset()
    }
    test("double click capture loss stops second click and releases immediately") {
        draw("click-cancel"); reset()
        var cleared = false
        view.send = { command ->
            sent.add(command)
            if (!cleared && command.optString("action") == "button" && command.optBoolean("down")) {
                cleared = true; view.clearFrame()
            }
        }
        check(!view.sendDoubleClick())
        check(buttonEvents().count { it.optBoolean("down") } == 1)
        checkReleased()
        view.send = { sent.add(it) }
        draw(); reset(); view.setInputAllowed(false); sent.clear()
        check(!view.sendDoubleClick() && !view.sendButtonTap("right"))
        check(sent.isEmpty())
        view.setInputAllowed(true)
    }
    println("VIEWPORT_TOUCH_SHIM_OK:$count")
}
