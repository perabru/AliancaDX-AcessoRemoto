package br.com.perabru.minianydesk

import org.json.JSONObject

/** Optional v1 pacing feedback, including the bounded v7.2 presentation window.
 * Older Hosts ignore the extra ready/feedback fields and retain one-frame pacing.
 * A received or decoded frame is never a displayed frame. */
internal class StreamFeedback {
    private var lastPresentedId = 0L
    private var lastResumeAt: Long? = null

    @Synchronized fun reset() { lastPresentedId = 0L; lastResumeAt = null }

    @Synchronized fun resumeDelayMs(now: Long): Long = lastResumeAt?.let {
        (RESUME_INTERVAL_MS - (now - it).coerceAtLeast(0)).coerceAtLeast(0)
    } ?: 0

    /** Only a real viewer return or discarded frame requests another presentation credit. */
    @Synchronized fun resume(now: Long): JSONObject? {
        if (resumeDelayMs(now) > 0) return null
        lastResumeAt = now
        return JSONObject().put("type", "stream_resume").put("version", VERSION)
    }

    /** Called only after the current capture was drawn, once per frame and session. */
    @Synchronized fun presented(info: JSONObject, decodeMs: Long, renderMs: Long): JSONObject? {
        if (number(info.opt("streamVersion")) != VERSION.toDouble()) return null
        val supplied = number(info.opt("frameId")) ?: return null
        if (supplied < 1 || supplied > MAX_FRAME_ID.toDouble() || supplied % 1.0 != 0.0) return null
        val frameId = supplied.toLong()
        if (frameId <= lastPresentedId) return null
        lastPresentedId = frameId
        return JSONObject().put("type", "stream_feedback").put("frameId", frameId)
            // Presentation of a newer frame also retires older discarded frames.
            // The Host only uses this after the matching capability negotiation.
            .put("cumulative", true)
            .put("decodeMs", decodeMs.coerceIn(0, MAX_PROCESSING_MS))
            .put("renderMs", renderMs.coerceIn(0, MAX_PROCESSING_MS))
    }

    companion object {
        const val VERSION = 1
        private const val MAX_FRAME_ID = 9_007_199_254_740_991L
        private const val MAX_PROCESSING_MS = 60_000L
        private const val RESUME_INTERVAL_MS = 1_000L
        fun ready(): JSONObject = JSONObject().put("type", "stream_ready").put("version", VERSION)
            .put("maxInFlight", 2).put("ackCumulative", true)
        private fun number(value: Any?): Double? = (value as? Number)?.toDouble()?.takeIf { it.isFinite() }
    }
}
