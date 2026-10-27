package br.com.perabru.minianydesk

import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.atomic.AtomicInteger

/** Production pacing rules on JVM. Does not claim Android rendering or an internet test. */
object StreamFeedbackHarness {
    @JvmStatic fun main(args: Array<String>) {
        var cases = 0
        fun test(name: String, run: () -> Unit) { run(); cases++; println("PASS $name") }
        fun frame(id: Any = 1, version: Any = 1) = JSONObject().put("streamVersion", version).put("frameId", id)
        test("optional ready keeps version one and negotiates a bounded cumulative two-frame window") {
            val ready = StreamFeedback.ready()
            check(ready.getString("type") == "stream_ready" && ready.getInt("version") == 1)
            check(ready.getInt("maxInFlight") == 2 && ready.getBoolean("ackCumulative"))
            check(ready.length() == 5)
            check(ready.getJSONArray("imageFormats").toString() == "[\"jpeg\",\"png\"]")
            check(StreamFeedback().presented(JSONObject().put("frameId", 1), 0, 0) == null)
        }
        test("only explicit numeric version and safe positive integer frame identity is acknowledged") {
            for (version in listOf<Any>("1", true, 0, 2, 1.5, JSONObject.NULL))
                check(StreamFeedback().presented(frame(version = version), 0, 0) == null) { version.toString() }
            for (id in listOf<Any>("1", true, 0, -1, 1.5, 9_007_199_254_740_992L, JSONObject.NULL))
                check(StreamFeedback().presented(frame(id), 0, 0) == null) { id.toString() }
            check(StreamFeedback().presented(frame(9_007_199_254_740_991L), 0, 0) != null)
        }
        test("duplicate old redraw and late frames do not release another frame in flight") {
            val feedback = StreamFeedback()
            check(feedback.presented(frame(5), 3, 12)?.getLong("frameId") == 5L)
            check(feedback.presented(frame(5), 3, 12) == null)
            check(feedback.presented(frame(4), 3, 12) == null)
            check(feedback.presented(frame(6), 3, 12)?.getLong("frameId") == 6L)
        }
        test("a new session can restart frame numbering without inheriting old acknowledgements") {
            val feedback = StreamFeedback()
            check(feedback.presented(frame(90), 1, 10) != null)
            feedback.reset()
            check(feedback.presented(frame(1), 1, 10)?.getLong("frameId") == 1L)
        }
        test("only the newest actually presented frame acknowledges a dropped predecessor") {
            val feedback = StreamFeedback()
            // The raw/decoded mailboxes may replace frame 1 before any draw.
            // No receipt/decode path manufactures an acknowledgement for it.
            val ack = feedback.presented(frame(2), 5, 7)!!
            check(ack.getLong("frameId") == 2L && ack.getBoolean("cumulative"))
            check(feedback.presented(frame(1), 1, 1) == null)
            check(feedback.presented(frame(2), 5, 7) == null)
            check(feedback.presented(frame(3), 4, 6)?.getBoolean("cumulative") == true)
        }
        test("viewer resume is explicit rate limited and resets only with a new session") {
            val feedback = StreamFeedback()
            check(feedback.resume(0)?.getString("type") == "stream_resume")
            check(feedback.resumeDelayMs(500) == 500L && feedback.resume(999) == null)
            check(feedback.resume(1_000)?.getInt("version") == 1)
            check(feedback.resume(1_000) == null && feedback.resumeDelayMs(1_001) == 999L)
            feedback.reset()
            check(feedback.resume(1_001) != null)
        }
        test("only bounded local durations and frame identity leave the viewer") {
            val source = frame(8).put("image", "large-screen-payload").put("private", "metadata")
            val ack = StreamFeedback().presented(source, -30, Long.MAX_VALUE)!!
            source.put("frameId", 900)
            check(ack.length() == 5 && ack.getString("type") == "stream_feedback" && ack.getBoolean("cumulative"))
            check(ack.getLong("frameId") == 8L && ack.getLong("decodeMs") == 0L && ack.getLong("renderMs") == 60_000L)
            check(!ack.has("image") && !ack.has("private"))
        }
        test("concurrent duplicate presentation produces exactly one acknowledgement") {
            val feedback = StreamFeedback()
            val start = CountDownLatch(1)
            val acknowledgements = AtomicInteger()
            val workers = (1..8).map { Thread {
                start.await()
                if (feedback.presented(frame(1), 0, 0) != null) acknowledgements.incrementAndGet()
            }.apply { start() } }
            start.countDown(); workers.forEach { it.join(2_000); check(!it.isAlive) }
            check(acknowledgements.get() == 1)
        }
        println("$cases stream feedback checks passed")
    }
}
