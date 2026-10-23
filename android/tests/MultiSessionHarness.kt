package br.com.perabru.minianydesk

/** Executes production ownership and click policies, with no Android framework calls. */
object MultiSessionHarness {
    @JvmStatic fun main(args: Array<String>) {
        var count = 0
        fun test(name: String, run: () -> Unit) { run(); count++; println("PASS $name") }
        test("four sessions remain owned when selection changes and fifth allocation is rejected") {
            val workspace = SessionWorkspace<Any>()
            val all = (1..4).map { workspace.add { Any() }!! }
            check(workspace.add { error("Factory must not run at capacity") } == null)
            for ((id, owner) in all) { check(workspace.select(id)); check(workspace.selected === owner) }
            check(workspace.size == 4)
            for ((id, owner) in all) check(workspace.owns(id, owner))
            check(!workspace.select(999)); check(workspace.selected === all.last().second)
        }
        test("closing a background owner preserves active connection and stale identities cannot replace it") {
            val workspace = SessionWorkspace<Any>()
            val first = workspace.add { Any() }!!; val second = workspace.add { Any() }!!
            workspace.select(second.first)
            check(workspace.remove(first.first) === first.second)
            check(workspace.selected === second.second)
            val replacement = workspace.add { Any() }!!
            check(replacement.first != first.first)
            check(!workspace.owns(first.first, replacement.second))
            check(!workspace.owns(second.first, Any()))
            check(workspace.remove(second.first) === second.second && workspace.selected == null)
        }
        test("document result remains bound to launching owner after active tab changes") {
            val workspace = SessionWorkspace<Any>()
            val first = workspace.add { Any() }!!; val second = workspace.add { Any() }!!
            val requests = SessionDocumentRequests<Any>()
            workspace.select(first.first)
            val request = requests.reserve(first.second, 103)
            workspace.select(second.first)
            val result = requests.complete(request)!!
            check(result.owner === first.second && result.originalCode == 103)
            check(workspace.selected === second.second)
            check(requests.complete(request) == null)
        }
        test("closing the original picker owner rejects late results and cannot consume another picker") {
            val first = Any(); val second = Any(); val requests = SessionDocumentRequests<Any>()
            val stale = requests.reserve(first, 104)
            check(runCatching { requests.reserve(second, 103) }.isFailure)
            requests.cancel(second)
            requests.cancel(first)
            val current = requests.reserve(second, 103)
            check(current != stale && requests.complete(stale) == null)
            check(requests.complete(current)!!.owner === second)
        }
        test("double click is move plus two ordered left strokes at the identical capture and point") {
            val point = ScreenGeometry.Point(.2, .8)
            val sequence = PointerClicks.sequence("left", 2, "capture-A", point)
            check(sequence.size == 5 && sequence.first().getString("action") == "move")
            check(sequence.drop(1).map { it.getBoolean("down") } == listOf(true, false, true, false))
            sequence.forEach { check(it.getString("captureId") == "capture-A" && it.getDouble("x") == .2 && it.getDouble("y") == .8) }
            check(sequence.drop(1).all { it.getString("button") == "left" })
        }
        test("right click contains one ordered stroke and malformed pointer snapshots are rejected") {
            val sequence = PointerClicks.sequence("right", 1, "capture-B", ScreenGeometry.Point(.5,.5))
            check(sequence.size == 3 && sequence[1].getBoolean("down") && !sequence[2].getBoolean("down"))
            check(sequence.drop(1).all { it.getString("button") == "right" })
            for (point in listOf(ScreenGeometry.Point(Double.NaN,0.0),ScreenGeometry.Point(1.1,0.0)))
                check(runCatching { PointerClicks.sequence("left",2,"capture",point) }.isFailure)
            check(runCatching { PointerClicks.sequence("left",3,"capture",ScreenGeometry.Point(.5,.5)) }.isFailure)
        }
        println("$count multiple-session and pointer checks passed")
    }
}
