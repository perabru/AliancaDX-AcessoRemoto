package br.com.perabru.minianydesk

import org.json.JSONObject
import java.io.IOException
import java.io.DataInputStream
import java.net.ServerSocket
import java.net.Socket
import java.security.KeyStore
import java.security.cert.X509Certificate
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext

/** Extra production-transport checks, without Android framework test doubles. */
private fun queueByteTests() {
    fun socketPair(): Pair<Socket, Socket> {
        val listener = ServerSocket(0)
        val sender = Socket("127.0.0.1", listener.localPort)
        val receiver = listener.accept().apply { soTimeout = 2_000 }
        listener.close()
        return Pair(sender, receiver)
    }
    val large = JSONObject().put("type", "log").put("message", "a".repeat(3 * 1024 * 1024))
    val (firstSocket, firstReceiver) = socketPair()
    val closeCalls = AtomicInteger()
    val first = Peer(firstSocket, onClose = { closeCalls.incrementAndGet() })
    first.send(large)
    first.send(large)
    try { first.send(large); error("Queue exceeded its 8MiB budget") }
    catch (_: IOException) { }
    check(first.closed && closeCalls.get() == 1)
    firstReceiver.close()

    val (secondSocket, secondReceiver) = socketPair()
    val second = Peer(secondSocket)
    val input = DataInputStream(secondReceiver.getInputStream())
    second.start()
    repeat(4) {
        second.send(large)
        check(JsonWire.read(input).getString("message").length == 3 * 1024 * 1024)
        check(!second.closed) { "Dequeued packets still consumed the byte budget" }
    }
    second.close(); secondReceiver.close()
    println("BYTE_QUEUE_OK")
}

private fun rateLimitTests(storePath: String) {
    val store = KeyStore.getInstance("PKCS12")
    java.io.FileInputStream(storePath).use { store.load(it, "testpassword".toCharArray()) }
    val keys = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm())
    keys.init(store, "testpassword".toCharArray())
    val context = SSLContext.getInstance("TLS").apply { init(keys.keyManagers, null, null) }
    val requests = AtomicInteger()
    val host = AndroidHostServer(0, "testpassword", context,
        onRequest = { _, _ -> requests.incrementAndGet(); false },
        onConnect = { _, _, _ -> error("Denied visitor was connected") },
        onDisconnect = { _, _ -> })
    // Seed old/recent addresses instead of making 1024 needless TLS connections.
    val field = AndroidHostServer::class.java.getDeclaredField("failed").apply { isAccessible = true }
    @Suppress("UNCHECKED_CAST")
    val failed = field.get(host) as MutableMap<String, MutableList<Long>>
    val now = System.nanoTime() / 1_000_000
    repeat(1024) { failed["recent-$it"] = arrayListOf(now - it) }
    failed["stale"] = arrayListOf(now - 60_001)
    val certificate = store.getCertificate("host") as X509Certificate
    println("READY:${host.start()}:${JsonWire.fingerprint(certificate)}")
    System.out.flush()
    try {
        while (true) {
            when (readlnOrNull()) {
                "stats" -> {
                    println("STATS:${failed.size}:${failed.containsKey("stale")}:${failed.containsKey("recent-1023")}:${failed["127.0.0.1"]?.size ?: 0}:${requests.get()}")
                    System.out.flush()
                }
                "clear" -> { failed.clear(); println("CLEARED"); System.out.flush() }
                else -> return
            }
        }
    } finally { host.stop() }
}

fun main(args: Array<String>) {
    if (args[0] == "queue-bytes") { queueByteTests(); return }
    if (args[0] == "rate-limit") { rateLimitTests(args[1]); return }
    val port = args[0].toInt()
    val expectedPin = args[1]
    val mode = args[2]
    val cancelled = AtomicBoolean(mode == "cancel-pre")
    val trustCalls = AtomicInteger()
    val closedCalls = AtomicInteger()
    val closed = CountDownLatch(1)
    val holder = AtomicReference<Peer?>()
    fun cancelSoon() = Thread {
        Thread.sleep(250)
        cancelled.set(true)
    }.apply { isDaemon = true; start() }
    if (mode == "cancel-handshake") cancelSoon()
    val started = System.nanoTime()
    try {
        val peer = AndroidNet.connect("127.0.0.1", port, "testpassword", "Transport audit",
            trust = { pin ->
                check(pin == expectedPin) { "Unexpected certificate fingerprint" }
                trustCalls.incrementAndGet()
                when (mode) {
                    "cancel-trust" -> {
                        cancelSoon()
                        while (!cancelled.get()) Thread.sleep(10)
                        true
                    }
                    "cancel-auth" -> { cancelSoon(); true }
                    "pin-throw" -> throw IllegalStateException("Approval callback failed")
                    else -> true
                }
            },
            onMessage = { error("Malformed or closed peer unexpectedly delivered a message: $it") },
            onClose = {
                val current = holder.get()
                if (current != null) check(current.closed && current.sock.isClosed) { "Close callback preceded cleanup" }
                closedCalls.incrementAndGet()
                closed.countDown()
            }, cancelled = cancelled)
        holder.set(peer)
        check(!mode.startsWith("cancel") && mode != "pin-throw") { "Cancelled connect returned a peer" }
        check(closed.await(4, TimeUnit.SECONDS)) { "Peer did not close after invalid packet/EOF" }
        peer.close("Second close")
        check(closedCalls.get() == 1) { "Close callback repeated" }
        check(peer.closed && peer.sock.isClosed) { "Closed peer retained its socket" }
        try {
            peer.send(JSONObject().put("type", "input").put("action", "release"))
            error("Closed peer accepted a message")
        } catch (_: IOException) { }
        println("CLOSED_ONCE:$mode")
    } catch (error: Exception) {
        if (mode.startsWith("cancel")) {
            check(cancelled.get()) { "Failed before requested cancellation: $error" }
            check((System.nanoTime() - started) < TimeUnit.SECONDS.toNanos(2)) { "Cancellation remained blocked" }
            check(closedCalls.get() == 0) { "Unconnected peer emitted close callback" }
            check(trustCalls.get() == if (mode in setOf("cancel-auth", "cancel-trust")) 1 else 0)
            println("CANCELLED:$mode")
        } else if (mode == "pin-throw") {
            check(error.message == "Approval callback failed")
            check(trustCalls.get() == 1 && closedCalls.get() == 0)
            println("APPROVAL_ERROR_CLOSED")
        } else throw error
    }
}
