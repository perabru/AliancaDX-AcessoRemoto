package br.com.perabru.minianydesk

import android.os.Build
import org.json.JSONObject
import java.net.InetAddress
import java.security.KeyStore
import java.security.cert.X509Certificate
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext

private fun emit(value: JSONObject) = synchronized(System.out) {
    println(value.toString())
    System.out.flush()
}

private fun resultJson(result: PresenceResult, elapsedMs: Long): JSONObject = JSONObject()
    .put("state", result.state).put("detail", result.detail)
    .put("fingerprint", result.fingerprint ?: JSONObject.NULL)
    .put("computerName", result.computerName ?: JSONObject.NULL)
    .put("busy", result.busy ?: JSONObject.NULL)
    .put("version", result.version ?: JSONObject.NULL).put("elapsedMs", elapsedMs)

private fun readField(server: AndroidHostServer, name: String): Any? =
    server.javaClass.getDeclaredField(name).apply { isAccessible = true }.get(server)

private fun runServer(args: Array<String>) {
    Build.VERSION.SDK_INT = args[3].toInt()
    val store = KeyStore.getInstance("PKCS12")
    java.io.FileInputStream(args[1]).use { store.load(it, "testpassword".toCharArray()) }
    val km = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm())
    km.init(store, "testpassword".toCharArray())
    val context = SSLContext.getInstance("TLS")
    context.init(km.keyManagers, null, null)
    val name = AtomicReference(args[2])
    val requests = AtomicInteger()
    val connections = AtomicInteger()
    val disconnects = AtomicInteger()
    val approvalRelease = CountDownLatch(1)
    val host = AndroidHostServer(0, "testpassword", context,
        onRequest = { visitor, _ ->
            requests.incrementAndGet()
            emit(JSONObject().put("event", "request").put("name", visitor))
            if (visitor == "hold") approvalRelease.await(30, TimeUnit.SECONDS)
            visitor != "deny"
        }, onConnect = { peer, _, _ ->
            connections.incrementAndGet()
            peer.onMessage = { message -> peer.send(message) }
        }, onDisconnect = { _, _ -> disconnects.incrementAndGet() },
        computerName = { name.get() })
    val port = host.start()
    emit(JSONObject().put("event", "ready").put("port", port)
        .put("fingerprint", JsonWire.fingerprint(store.getCertificate("host") as X509Certificate)))
    fun snapshot(): JSONObject {
        val lock = readField(host, "lock")!!
        return synchronized(lock) {
            @Suppress("UNCHECKED_CAST")
            val failed = readField(host, "failed") as Map<String, List<Long>>
            JSONObject().put("event", "snapshot").put("requests", requests.get())
                .put("connections", connections.get()).put("disconnects", disconnects.get())
                .put("failedAddresses", failed.size).put("failedAttempts", failed.values.sumOf { it.size })
                .put("pending", readField(host, "pending"))
                .put("active", (readField(host, "active") as? Peer)?.closed == false)
        }
    }
    try {
        while (true) {
            val line = readlnOrNull() ?: break
            when {
                line == "snapshot" -> emit(snapshot())
                line == "release" -> { approvalRelease.countDown(); emit(JSONObject().put("event", "released")) }
                line.startsWith("rename:") -> {
                    name.set(line.substringAfter(':'))
                    emit(JSONObject().put("event", "renamed"))
                }
                line == "restart" -> {
                    host.stop()
                    emit(JSONObject().put("event", "restarted").put("port", host.start()))
                }
                else -> break
            }
        }
    } finally {
        approvalRelease.countDown()
        host.stop()
    }
}

private fun runResolverTest(args: Array<String>) {
    val original = PresenceProbe.addressResolver
    val calls = AtomicInteger()
    val release = CountDownLatch(1)
    val entered = CountDownLatch(4)
    val workerNames = java.util.Collections.synchronizedSet(HashSet<String>())
    val executor = Executors.newFixedThreadPool(16)
    try {
        PresenceProbe.addressResolver = {
            calls.incrementAndGet()
            workerNames.add(Thread.currentThread().name)
            entered.countDown()
            release.await(5, TimeUnit.SECONDS)
            arrayOf(InetAddress.getByAddress(byteArrayOf(127, 0, 0, 1)))
        }
        val started = System.nanoTime()
        val pending = (0 until 16).map { index -> executor.submit<PresenceResult> {
            PresenceProbe.probe("blocked-$index.presence.invalid", args[1].toInt(), timeoutMs = 180)
        } }
        val results = pending.map { it.get(2, TimeUnit.SECONDS) }
        val elapsed = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started)
        val callsBeforeNumeric = calls.get()
        val numeric = PresenceProbe.probe("127.0.0.1", args[1].toInt(), args[2], 2_000)
        emit(JSONObject().put("event", "resolver").put("calls", calls.get())
            .put("callsBeforeNumeric", callsBeforeNumeric).put("workerThreads", workerNames.size)
            .put("allOffline", results.all { it.state == "offline" }).put("elapsedMs", elapsed)
            .put("numeric", resultJson(numeric, 0)))
    } finally {
        release.countDown()
        executor.shutdownNow()
        PresenceProbe.addressResolver = original
    }
    // Rejected DNS tasks must not kill the daemon workers or poison later work.
    PresenceProbe.addressResolver = { throw java.net.UnknownHostException("fixture invalid IDNA") }
    try {
        repeat(8) { PresenceProbe.probe("invalid-$it.presence.invalid", args[1].toInt(), timeoutMs = 200) }
        PresenceProbe.addressResolver = { arrayOf(InetAddress.getByAddress(byteArrayOf(127, 0, 0, 1))) }
        emit(JSONObject().put("event", "resolver_recovery")
            .put("result", resultJson(PresenceProbe.probe("healthy.presence.invalid", args[1].toInt(), args[2], 2_000), 0)))
    } finally { PresenceProbe.addressResolver = original }
}

fun main(args: Array<String>) {
    when (args[0]) {
        "probe", "probe-loop" -> {
            Build.VERSION.SDK_INT = args[5].toInt()
            val count = if (args[0] == "probe-loop") args[6].toInt() else 1
            repeat(count) {
                val started = System.nanoTime()
                try {
                    val result = PresenceProbe.probe(args[1], args[2].toInt(),
                        args[3].takeUnless { it == "-" }, args[4].toInt())
                    emit(resultJson(result, TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started)))
                } catch (error: IllegalArgumentException) {
                    emit(JSONObject().put("error", error.javaClass.simpleName).put("detail", error.message))
                }
            }
        }
        "auth-client" -> {
            val trustCalls = AtomicInteger()
            val peer = AndroidNet.connect("127.0.0.1", args[1].toInt(), "testpassword", "Android controlador",
                trust = { trustCalls.incrementAndGet(); it == args[2] })
            emit(JSONObject().put("event", "authenticated").put("computerName", peer.computerName)
                .put("fingerprint", peer.fingerprint).put("trustCalls", trustCalls.get())
                .put("closed", peer.closed))
            peer.close()
        }
        "server" -> runServer(args)
        "resolver" -> runResolverTest(args)
        else -> error("Unknown test mode")
    }
}
