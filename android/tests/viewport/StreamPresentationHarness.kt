package br.com.perabru.minianydesk

import android.content.Context
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.os.Handler
import android.os.SystemClock
import org.json.JSONObject
import java.util.ArrayDeque
import java.util.concurrent.AbstractExecutorService
import java.util.concurrent.ExecutorService
import java.util.concurrent.TimeUnit

/** Production mailbox/draw feedback with controlled framework primitives.
 * The JPEG codec is a counted stub: durations below are logical queue timings,
 * never measurements of an Android device, real JPEG decoder or internet link. */
private class PausedDecoder : AbstractExecutorService() {
    val tasks = ArrayDeque<Runnable>()
    private var stopped = false
    override fun execute(task: Runnable) { check(!stopped); tasks.addLast(task) }
    override fun shutdown() { stopped = true }
    override fun shutdownNow(): MutableList<Runnable> { stopped = true; return tasks.toMutableList().also { tasks.clear() } }
    override fun isShutdown() = stopped
    override fun isTerminated() = stopped && tasks.isEmpty()
    override fun awaitTermination(timeout: Long, unit: TimeUnit) = isTerminated
    fun run() { while (tasks.isNotEmpty()) tasks.removeFirst().run() }
}

fun main() {
    var count = 0
    fun test(name: String, body: () -> Unit) { body(); count++; println("OK:$name") }
    val draw = RemoteScreenView::class.java.getDeclaredMethod("onDraw", Canvas::class.java).apply { isAccessible = true }
    fun message(id: Long) = JSONObject().put("type", "screen").put("image", "AA==")
        .put("captureId", "pc-desktop").put("width", 1920).put("height", 1080)
        .put("frameWidth", 1920).put("frameHeight", 1080).put("streamVersion", 1).put("frameId", id)
    fun scene(): Triple<RemoteScreenView, PausedDecoder, MutableList<JSONObject>> {
        val view = RemoteScreenView(Context())
        val worker = PausedDecoder()
        val field = RemoteScreenView::class.java.getDeclaredField("executor").apply { isAccessible = true }
        (field.get(view) as ExecutorService).shutdownNow(); field.set(view, worker)
        val feedback = StreamFeedback(); val acknowledgements = mutableListOf<JSONObject>()
        view.onFramePresented = { info, decode, render -> feedback.presented(info, decode, render)?.let { acknowledgements.add(it) } }
        return Triple(view, worker, acknowledgements)
    }
    test("a burst of 100 received frames decodes only the newest and acknowledges only its real draw") {
        val (view, worker, acknowledgements) = scene()
        BitmapFactory.resetCounts(); BitmapFactory.decodeMillis = 7
        Handler.setDeferPosts(true)
        for (id in 1L..100L) { SystemClock.advance(16); view.showFrame(message(id)) }
        check(worker.tasks.size == 1 && BitmapFactory.decodeCount == 0 && acknowledgements.isEmpty())
        worker.run()
        check(BitmapFactory.decodeCount == 1 && BitmapFactory.boundsCount == 1)
        check(acknowledgements.isEmpty()) { "Decode alone is not presentation" }
        SystemClock.advance(30)
        Handler.setDeferPosts(false); Handler.runPosted()
        check(acknowledgements.isEmpty()) { "Installing the bitmap alone is not presentation" }
        draw.invoke(view, Canvas())
        val ack = acknowledgements.single()
        check(ack.getLong("frameId") == 100L && ack.getBoolean("cumulative"))
        check(ack.getLong("decodeMs") == 7L && ack.getLong("renderMs") == 30L)
        println("STREAM_MAILBOX_BURST offered=100 decoded=1 presented=100 decodeMs=7 renderMs=30 logicalAgeMs=37")
        draw.invoke(view, Canvas()); check(acknowledgements.size == 1)
        view.clearFrame(); worker.shutdownNow(); BitmapFactory.decodeMillis = 0
    }
    test("the full HD decoded image keeps every source pixel and full color precision") {
        val (view, worker, _) = scene()
        BitmapFactory.resetCounts(); view.showFrame(message(1)); worker.run()
        check(BitmapFactory.decodeCount == 1 && BitmapFactory.lastSample == 1)
        check(BitmapFactory.lastConfig == android.graphics.Bitmap.Config.ARGB_8888)
        view.clearFrame(); worker.shutdownNow()
    }
    test("a paused or replaced capture cannot acknowledge an already decoded pending bitmap") {
        val (view, worker, acknowledgements) = scene()
        Handler.setDeferPosts(true); view.showFrame(message(5)); worker.run()
        view.clearFrame()
        Handler.setDeferPosts(false); Handler.runPosted(); draw.invoke(view, Canvas())
        check(acknowledgements.isEmpty())
        view.showFrame(message(6)); worker.run(); draw.invoke(view, Canvas())
        check(acknowledgements.single().getLong("frameId") == 6L)
        view.clearFrame(); worker.shutdownNow()
    }
    test("settings capability and confirmation come only from a current capture's actual draw") {
        val (view, worker, _) = scene()
        val settings = StreamSettings("quality")
        view.onFrameInfo = { settings.observe(it) }
        val stale = message(7).put("streamSettingsVersion", 1).put("streamMode", "quality")
        Handler.setDeferPosts(true); view.showFrame(stale); worker.run()
        view.clearFrame(); settings.reset()
        Handler.setDeferPosts(false); Handler.runPosted(); draw.invoke(view, Canvas())
        check(!settings.capable && settings.appliedMode == null && settings.request(true) == null)
        val current = message(8).put("streamSettingsVersion", 1).put("streamMode", "auto")
        view.showFrame(current); worker.run()
        check(!settings.capable) { "Receiving and decoding cannot announce capability" }
        draw.invoke(view, Canvas()); check(settings.capable && settings.appliedMode == "auto")
        check(settings.request(true)!!.getString("mode") == "quality" && settings.pending)
        view.showFrame(message(9).put("streamSettingsVersion", 1).put("streamMode", "quality")); worker.run()
        check(settings.pending) { "A requested mode must not be confirmed by decode alone" }
        draw.invoke(view, Canvas()); check(!settings.pending && settings.appliedMode == "quality")
        view.clearFrame(); worker.shutdownNow()
    }
    println("STREAM_PRESENTATION_SHIM_OK:$count")
}
