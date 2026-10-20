package br.com.perabru.minianydesk

import org.json.JSONObject
import java.net.InetAddress
import java.net.ServerSocket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

object ConnectionDeadlineHarness {
    @JvmStatic fun main(args: Array<String>) {
        when (args[0]) {
            "dns-cancel" -> {
                val original = AndroidNet.addressResolver
                val entered = CountDownLatch(1)
                val release = CountDownLatch(1)
                val done = CountDownLatch(1)
                val cancelled = AtomicBoolean()
                val opened = AtomicInteger()
                val failures = LinkedBlockingQueue<Throwable>()
                AndroidNet.addressResolver = { entered.countDown(); release.await(2, TimeUnit.SECONDS); arrayOf(InetAddress.getLoopbackAddress()) }
                try {
                    val thread = Thread {
                        try {
                            AndroidNet.connect("blocked.example", 9, "testpassword", "QA", { opened.incrementAndGet(); false },
                                cancelled = cancelled, authenticationTimeoutMs = 1_500)
                            error("Cancelled DNS returned a connection")
                        } catch (expected: java.io.IOException) {
                            if (!cancelled.get()) failures.offer(expected)
                        } catch (error: Throwable) {
                            failures.offer(error)
                        } finally { done.countDown() }
                    }.apply { isDaemon = true; start() }
                    check(entered.await(1, TimeUnit.SECONDS))
                    cancelled.set(true)
                    check(done.await(600, TimeUnit.MILLISECONDS)) { "DNS cancellation waited for the resolver" }
                    thread.join(1_000); check(opened.get() == 0 && failures.isEmpty()) { failures.toString() }
                } finally { release.countDown(); AndroidNet.addressResolver = original }
            }
            "dns-bounded-numeric" -> {
                val original = AndroidNet.addressResolver
                val release = CountDownLatch(1)
                val begin = CountDownLatch(1)
                val done = CountDownLatch(8)
                val calls = AtomicInteger()
                AndroidNet.addressResolver = {
                    calls.incrementAndGet(); release.await(2, TimeUnit.SECONDS)
                    arrayOf(InetAddress.getLoopbackAddress())
                }
                try {
                    repeat(8) { index -> Thread {
                        begin.await()
                        try { AndroidNet.connect("pool-$index.example", 9, "testpassword", "QA", { false }, authenticationTimeoutMs = 250) }
                        catch (_: Exception) { }
                        finally { done.countDown() }
                    }.apply { isDaemon = true; start() } }
                    begin.countDown(); check(done.await(1, TimeUnit.SECONDS))
                    check(calls.get() == 2) { "DNS workers were not bounded to2: ${calls.get()}" }
                    val server = ServerSocket(0).apply { soTimeout = 1_000 }
                    val numericDone = CountDownLatch(1)
                    try {
                        Thread {
                            try { AndroidNet.connect("127.0.0.1", server.localPort, "testpassword", "QA", { false }, authenticationTimeoutMs = 500) }
                            catch (_: Exception) { }
                            finally { numericDone.countDown() }
                        }.apply { isDaemon = true; start() }
                        server.accept().close() // ClientHello reaches TCP even while DNS workers remain blocked.
                        check(numericDone.await(1, TimeUnit.SECONDS))
                        check(calls.get() == 2)
                    } finally { server.close() }
                } finally { release.countDown(); AndroidNet.addressResolver = original }
            }
            "auth-trickle" -> {
                val started = System.nanoTime()
                val trust = AtomicInteger()
                try {
                    AndroidNet.connect("127.0.0.1", args[1].toInt(), "testpassword", "QA deadline",
                        { pin -> check(pin == args[2]); trust.incrementAndGet(); true }, authenticationTimeoutMs = 1_000)
                    error("Incomplete authentication exceeded the total deadline")
                } catch (expected: java.io.IOException) {
                    check(trust.get() == 1) { "Deadline failed before TLS/pin approval: $expected" }
                    check(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started) < 2_000) { "Trickle extended the total authentication deadline" }
                }
            }
            else -> error("Unknown connection test mode")
        }
        println(JSONObject().put("result", "passed").put("case", args[0]))
    }
}
