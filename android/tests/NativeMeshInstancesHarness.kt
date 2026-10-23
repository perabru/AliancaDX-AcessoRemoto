package br.com.perabru.minianydesk

import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.atomic.AtomicBoolean

internal object NativeMeshInstancesHarness {
    private class FakeApi : NativeMeshApi {
        val live = linkedSetOf<String>()
        var failRun = false
        var failParse = false
        var failRetain = false
        var onParse: (() -> Unit)? = null
        var loads = 0
        override fun ensureLoaded() { loads++ }
        override fun parse(config: String): Int { onParse?.invoke(); return if (failParse) -1 else 0 }
        override fun run(config: String): Int {
            live.add(config) // Includes a possible partial startup before an error.
            return if (failRun) -1 else 0
        }
        override fun retain(instanceNames: Array<String>): Int {
            if (failRetain) { failRetain = false; return -1 }
            live.retainAll(instanceNames.toSet())
            return 0
        }
    }

    fun runChecks(): Int {
        var checks = 0
        fun expect(value: Boolean) { check(value); checks++ }
        fun name() = "MiniAnyDesk-android-${UUID.randomUUID()}"
        val api = FakeApi()
        val registry = NativeMeshInstances(api)
        val a = name(); val b = name(); val c = name()
        expect(registry.start(a, a) { true } && registry.start(b, b) { true } && api.live == setOf(a, b))
        registry.release(a)
        expect(api.live == setOf(b))
        expect(runCatching { registry.start(b, b) { true } }.isFailure && api.live == setOf(b))
        api.failRun = true
        expect(runCatching { registry.start(c, c) { true } }.isFailure && api.live == setOf(b))
        api.failRun = false
        api.failParse = true
        expect(runCatching { registry.start(c, c) { true } }.isFailure && api.live == setOf(b))
        api.failParse = false
        val loads = api.loads
        expect(!registry.start(c, c) { false } && api.loads == loads && api.live == setOf(b))
        val current = AtomicBoolean(true)
        api.onParse = { current.set(false) }
        expect(!registry.start(c, c) { current.get() } && api.live == setOf(b))
        api.onParse = null
        registry.start(a, a) { true }
        api.failRetain = true
        expect(runCatching { registry.release(a) }.isFailure && api.live == setOf(a, b))
        registry.start(c, c) { true }
        expect(api.live == setOf(b, c)) // Cleanup retry removes only stopped A.
        registry.release(name())
        expect(api.live == setOf(b, c))
        expect(runCatching { registry.start("invalid", "invalid") { true } }.isFailure && api.live == setOf(b, c))
        registry.release(b); registry.release(c)
        expect(api.live.isEmpty())

        // Starts, closes and restarts from different per-session workers must not
        // take stale retain snapshots and delete another worker's live network.
        val names = (0 until 16).map { name() }
        val gate = CountDownLatch(1)
        val threads = names.map { id -> Thread { gate.await(); registry.start(id, id) { true } }.apply { start() } }
        gate.countDown(); threads.forEach { it.join() }
        expect(api.live == names.toSet())
        val replacements = (0 until 8).map { name() }
        val gate2 = CountDownLatch(1)
        val workers = names.take(8).map { id -> Thread { gate2.await(); registry.release(id) } } +
            replacements.map { id -> Thread { gate2.await(); registry.start(id, id) { true } } }
        workers.forEach { it.start() }; gate2.countDown(); workers.forEach { it.join() }
        expect(api.live == (names.drop(8) + replacements).toSet())
        (names.drop(8) + replacements).forEach { registry.release(it) }
        expect(api.live.isEmpty())
        println("Native registry: $checks checks passed")
        return checks
    }
}
