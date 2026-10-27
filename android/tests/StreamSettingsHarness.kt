package br.com.perabru.minianydesk

import org.json.JSONObject

object StreamSettingsHarness {
    @JvmStatic fun main(args: Array<String>) {
        var passed = 0
        fun test(title: String, check: () -> Unit) { check(); passed++; println("PASS $title") }
        fun frame(mode: String = "auto") = JSONObject().put("streamSettingsVersion", 1).put("streamMode", mode)
        test("old Host stays automatic, cannot send or claim saved quality") {
            val state = StreamSettings("quality")
            state.observe(JSONObject().put("frameId", 1))
            check(!state.capable && state.hasFrame && state.displayedMode == "auto" && state.appliedMode == null)
            check(!state.select("quality") && state.request(true) == null)
        }
        test("strict capability version and supported literal mode") {
            for (version in listOf<Any>("1", true, 2, 1.5, JSONObject.NULL)) {
                val state = StreamSettings(); state.observe(frame().put("streamSettingsVersion", version)); check(!state.capable)
            }
            for (mode in listOf<Any>("QUALITY", "quality ", 1, true, JSONObject.NULL)) {
                val state = StreamSettings(); state.observe(frame().put("streamMode", mode)); check(!state.capable)
            }
        }
        test("authentication plus advertised support required and exact contract emitted") {
            val state = StreamSettings("latency")
            check(state.request(true) == null); state.observe(frame())
            check(state.request(false) == null)
            val request = state.request(true)!!
            check(request.length() == 3 && request.getString("type") == "stream_settings" && request.getInt("version") == 1 && request.getString("mode") == "latency")
            check(state.request(true) == null && state.pending)
        }
        test("applied label follows frames, not a click or queued request") {
            val state = StreamSettings(); state.observe(frame()); state.request(true); state.observe(frame())
            check(!state.pending); check(state.select("quality")); state.request(true)
            check(state.appliedMode == "auto" && state.pending)
            state.observe(frame("auto")); check(state.pending)
            state.observe(frame("quality")); check(!state.pending && state.appliedMode == "quality")
        }
        test("quick reversal remains pending until a later frame confirms it") {
            val state = StreamSettings(); state.observe(frame()); state.request(true); state.observe(frame())
            state.select("quality"); state.request(true); state.select("auto"); state.request(true)
            check(state.pending); state.observe(frame("quality")); check(state.pending)
            state.observe(frame()); check(!state.pending)
        }
        test("reconnect removes old capability and reapplies saved mode only to new capable Host") {
            val state = StreamSettings("quality"); state.observe(frame()); state.request(true); state.observe(frame("quality"))
            state.reset(); check(!state.capable && !state.hasFrame && state.appliedMode == null && state.displayedMode == "auto")
            check(state.request(true) == null && state.preferredMode == "quality")
            state.observe(frame()); check(state.request(true)!!.getString("mode") == "quality")
        }
        test("multiple sessions own independent modes, acknowledgements and resets") {
            val first = StreamSettings(); val second = StreamSettings()
            first.observe(frame()); second.observe(frame()); first.select("latency"); first.request(true); first.observe(frame("latency"))
            check(second.preferredMode == "auto" && second.appliedMode == "auto")
            second.reset("quality"); check(first.preferredMode == "latency" && first.appliedMode == "latency")
        }
        test("invalid local preference falls back and unknown selection never transmits") {
            val state = StreamSettings("bogus"); state.observe(frame()); check(state.preferredMode == "auto")
            check(!state.select("bogus")); check(state.request(true)!!.getString("mode") == "auto")
        }
        println("STREAM_SETTINGS_CHECKS_PASSED=$passed")
    }
}
