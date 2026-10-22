package br.com.perabru.minianydesk

import org.json.JSONObject

/** Current Controller rules, separated from archived Android Host-only rules. */
object SessionRulesHarness {
    @JvmStatic fun main(args: Array<String>) {
        var cases = 0
        fun test(name: String, run: () -> Unit) { run(); cases++; println("PASS $name") }
        test("command snapshots and pointer barriers retain button wheel release order") {
            val owner = Any(); val queue = SessionCommandQueue<Any>(8)
            fun move(index: Int) = JSONObject().put("type", "input").put("action", "move").put("captureId", "current").put("sequence", index)
            repeat(1000) { check(queue.offer(owner, 1, move(it))) }
            val release = JSONObject().put("type", "input").put("action", "release")
            check(queue.offer(owner, 1, release)); repeat(1000) { check(queue.offer(owner, 1, move(it + 1000))) }
            val wheel = JSONObject().put("type", "input").put("action", "wheel").put("delta", 1)
            check(queue.offer(owner, 1, wheel)); wheel.put("delta", 99)
            check(queue.size() == 4)
            check(queue.poll()!!.message.getInt("sequence") == 999)
            check(queue.poll()!!.message.getString("action") == "release")
            check(queue.poll()!!.message.getInt("sequence") == 1999)
            check(queue.poll()!!.message.getInt("delta") == 1)
            check(queue.poll() == null)
        }
        test("pointer coalescing cannot cross owner generation or capture identity") {
            data class Owner(val id: Int)
            val first = Owner(1); val equivalent = Owner(1)
            val queue = SessionCommandQueue<Owner>(4)
            fun move(capture: String) = JSONObject().put("type", "input").put("action", "move").put("captureId", capture)
            check(queue.offer(first, 1, move("old")))
            check(queue.offer(equivalent, 1, move("old")))
            check(queue.offer(equivalent, 2, move("old")))
            check(queue.offer(equivalent, 2, move("new")))
            check(queue.size() == 4)
            check(queue.offer(equivalent, 2, move("new")))
            check(!queue.offer(first, 3, move("new")))
            val owners = (0..3).map { queue.poll()!! }
            check(owners[0].owner === first && owners[1].owner === equivalent)
            check(owners.map { it.generation } == listOf(1,1,2,2))
            queue.clear(); check(queue.size() == 0)
        }
        test("ASCII-sized reliable command batch keeps release in order") {
            val owner = Any(); val queue = SessionCommandQueue<Any>()
            repeat(3072) { index -> check(queue.offer(owner, 1, JSONObject().put("type", "input").put("action", "key").put("sequence", index))) }
            check(queue.offer(owner, 1, JSONObject().put("type", "input").put("action", "release")))
            repeat(3072) { check(queue.poll()!!.message.getInt("sequence") == it) }
            check(queue.poll()!!.message.getString("action") == "release")
            check(queue.poll() == null)
        }
        test("frame health expires waiting or displayed geometry and a new draw restores health") {
            check(!MainUiRules.frameExpired(6000, 0, 1))
            check(MainUiRules.frameExpired(6001, 0, 1))
            check(MainUiRules.frameExpired(9000, 3000, 1))
            check(!MainUiRules.frameExpired(9000, 8999, 1))
            check(!MainUiRules.frameExpired(100, 200, 1))
            check(!MainUiRules.frameExpired(10000, 0, 0))
        }
        test("only explicit releases bypass paused or stale input gating") {
            check(MainUiRules.inputIsRelease(JSONObject().put("type", "input").put("action", "release")))
            for (action in listOf("key", "button")) {
                check(MainUiRules.inputIsRelease(JSONObject().put("type", "input").put("action", action).put("down", false)))
                for (down in listOf<Any>(true, 0, "false"))
                    check(!MainUiRules.inputIsRelease(JSONObject().put("type", "input").put("action", action).put("down", down)))
            }
            check(!MainUiRules.inputIsRelease(JSONObject().put("type", "file_cancel").put("action", "release")))
            check(!MainUiRules.inputIsRelease(JSONObject().put("type", "input").put("action", "move")))
        }
        println("$cases controller state and command checks passed")
    }
}
