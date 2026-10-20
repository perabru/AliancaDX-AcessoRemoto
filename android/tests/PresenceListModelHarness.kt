package br.com.perabru.minianydesk

/** Meaningful state-machine checks runnable on a JVM without Android framework stubs. */
object PresenceListModelHarness {
    private val original = PresenceListModel.Item("one", "Office|pc.example|64321", "pc.example:64321")
    private fun model() = PresenceListModel().also { it.sync(listOf(original)) }
    private fun token(model: PresenceListModel, pin: String? = "pin", generation: Int = 1) =
        checkNotNull(model.begin("one", pin, generation))
    private fun expect(value: Boolean, message: String) { check(value) { message } }

    @JvmStatic fun main(args: Array<String>) {
        var cases = 0
        fun test(name: String, run: () -> Unit) {
            run()
            cases++
            println("PASS $name")
        }

        test("unchanged snapshot accepts the current result") {
            val m = model(); val t = token(m)
            m.sync(listOf(original))
            expect(m.finish(t, "pin", 1) == PresenceListModel.Finish(true, false, false), "Current result rejected")
            expect(!m.isPending("one") && m.pendingCount == 0, "Completed request remains pending")
        }
        test("rename invalidates while preserving the outstanding worker") {
            val m = model(); val t = token(m)
            m.sync(listOf(original.copy(revisionKey = "Renamed|pc.example|64321")))
            expect(m.begin("one", "pin", 1) == null && m.pendingCount == 1, "Rename started overlapping work")
            expect(m.finish(t, "pin", 1) == PresenceListModel.Finish(false, true, false), "Rename accepted old result")
            expect(token(m).revision != t.revision, "Rename revision unchanged")
        }
        test("address edit rejects old endpoint even with an unchanged revision key") {
            val m = model(); val t = token(m)
            m.sync(listOf(original.copy(endpoint = "new.example:64321")))
            expect(m.finish(t, "pin", 1) == PresenceListModel.Finish(false, true, false), "Address edit accepted old result")
            expect(token(m).endpoint == "new.example:64321", "New token retained old endpoint")
        }
        test("deletion retains in-flight bookkeeping and discards its callback") {
            val m = model(); val t = token(m)
            m.sync(emptyList())
            expect(m.entryCount == 0 && m.isPending("one"), "Deletion lost outstanding worker")
            expect(m.begin("one", "pin", 1, true) == null, "Deleted entry started a worker")
            expect(m.finish(t, "pin", 1) == PresenceListModel.Finish(false, false, true), "Deleted result was not discarded")
            expect(m.pendingCount == 0, "Deleted completed request leaked")
        }
        test("delete and re-add of the same ID cannot accept old work") {
            val m = model(); val t = token(m)
            m.sync(emptyList()); m.sync(listOf(original))
            expect(m.begin("one", "pin", 1) == null, "Re-add overlaps old worker")
            expect(m.finish(t, "pin", 1).let { !it.accepted && it.refresh && !it.unknown }, "Re-add accepted removed entry result")
            expect(token(m).revision > t.revision, "Re-add reused old revision")
        }
        test("pin changes and generation changes both reject old identity snapshots") {
            val m = model()
            expect(m.finish(token(m), "new-pin", 1) == PresenceListModel.Finish(false, true, false), "Changed pin accepted")
            expect(m.finish(token(m, "new-pin"), "new-pin", 2) == PresenceListModel.Finish(false, true, false), "Old generation accepted")
            val unknown = token(m, null, 2)
            expect(m.finish(unknown, null, 2).accepted, "Unknown-pin availability rejected")
        }
        test("session connect and close invalidate every alias for the endpoint") {
            val m = model()
            val alias = original.copy(id = "alias", revisionKey = "Alias")
            val other = original.copy(id = "other", endpoint = "other.example:64321")
            m.sync(listOf(original, alias, other)); val t = token(m)
            val a = checkNotNull(m.begin("alias", "pin", 1))
            val o = checkNotNull(m.begin("other", "pin", 1))
            expect(m.invalidateEndpoint(original.endpoint) == setOf("one", "alias"), "Aliases not invalidated together")
            expect(m.finish(t, "pin", 1).refresh && m.finish(a, "pin", 1).refresh, "Session accepted pre-connect probes")
            expect(m.finish(o, "pin", 1).accepted, "Unrelated endpoint invalidated")
            val closed = token(m)
            m.invalidateEndpoint(original.endpoint)
            expect(!m.finish(closed, "pin", 1).accepted, "Session close accepted stale availability")
        }
        test("pause rejects results without scheduling a background follow-up") {
            val m = model(); val t = token(m)
            m.begin("one", "pin", 1, true)
            expect(m.finish(t, "pin", 2, false) == PresenceListModel.Finish(false, false, false), "Background callback refreshed")
            val resumed = token(m, "pin", 3)
            expect(m.finish(resumed, "pin", 3).accepted, "Fresh foreground result rejected")
        }
        test("forced refreshes coalesce to one follow-up without duplicate overlap") {
            val m = model(); val t = token(m)
            repeat(20) { expect(m.begin("one", "pin", 1, true) == null, "Forced refresh overlaps worker") }
            expect(m.pendingCount == 1, "Forced refresh queued duplicate workers")
            expect(m.finish(t, "pin", 1) == PresenceListModel.Finish(true, true, false), "Forced follow-up missing")
            val followUp = token(m)
            expect(m.finish(followUp, "pin", 1) == PresenceListModel.Finish(true, false, false), "Force failed to coalesce")
        }
        test("ordinary repeated refresh does not create a follow-up") {
            val m = model(); val t = token(m)
            repeat(20) { expect(m.begin("one", "pin", 1) == null, "Normal refresh overlaps worker") }
            expect(!m.finish(t, "pin", 1).refresh, "Normal duplicate created follow-up")
        }
        test("a duplicate old callback never clears a newer unchanged-entry request") {
            val m = model(); val old = token(m)
            expect(m.finish(old, "pin", 1).accepted, "Initial result rejected")
            val current = token(m)
            expect(old.requestId != current.requestId, "Repeated request reused identity")
            expect(m.finish(old, "pin", 1) == PresenceListModel.Finish(false, false, false), "Duplicate callback accepted")
            expect(m.isPending("one") && m.pendingCount == 1, "Duplicate callback cleared newer pending")
            expect(m.finish(current, "pin", 1).accepted, "Duplicate callback damaged current request")
        }
        test("cancel deferred leaves the worker tracked and clears only requested follow-ups") {
            val m = model(); val t = token(m)
            m.begin("one", "pin", 1, true); m.cancelDeferred("one")
            expect(m.isPending("one"), "Cancellation lost running token")
            expect(!m.finish(t, "pin", 1).refresh, "Cancelled follow-up survived")
            val second = token(m)
            m.begin("one", "pin", 1, true); m.cancelDeferred()
            expect(!m.finish(second, "pin", 1).refresh, "Global cancellation failed")
        }
        test("duplicate IDs reject atomically without damaging existing work") {
            val m = model(); val t = token(m)
            var rejected = false
            try { m.sync(listOf(original, original.copy(endpoint = "other"))) }
            catch (_: IllegalArgumentException) { rejected = true }
            expect(rejected && m.entryCount == 1, "Duplicate IDs silently replaced entry")
            expect(m.finish(t, "pin", 1).accepted, "Rejected sync damaged current revision")
        }
        println("$cases presence-list state checks passed")
    }
}
