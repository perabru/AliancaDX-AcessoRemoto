package br.com.perabru.minianydesk

import android.content.ContentResolver
import android.content.Context
import android.net.Uri
import org.json.JSONObject
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.FilterOutputStream
import java.io.OutputStream
import java.net.ServerSocket
import java.net.Socket
import java.nio.file.Files
import java.security.MessageDigest
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

private fun eventually(test: () -> Boolean) {
    val end = System.nanoTime() + TimeUnit.SECONDS.toNanos(2)
    while (!test()) { check(System.nanoTime() < end) { "Asynchronous cleanup did not complete" }; Thread.sleep(5) }
}

private class TransferGateSocket(private val actual: Socket) : Socket() {
    val entered = CountDownLatch(1); val release = CountDownLatch(1)
    private val sink = object : FilterOutputStream(actual.getOutputStream()) {
        override fun write(bytes: ByteArray, offset: Int, count: Int) {
            entered.countDown(); check(release.await(3, TimeUnit.SECONDS)); out.write(bytes, offset, count)
        }
    }
    override fun getInputStream() = actual.getInputStream()
    override fun getOutputStream(): OutputStream = sink
    override fun setSoTimeout(timeout: Int) { actual.soTimeout = timeout }
    override fun setKeepAlive(enabled: Boolean) { actual.keepAlive = enabled }
    override fun setTcpNoDelay(enabled: Boolean) { actual.tcpNoDelay = enabled }
    override fun shutdownInput() = actual.shutdownInput()
    override fun shutdownOutput() { release.countDown(); actual.shutdownOutput() }
    override fun close() { release.countDown(); actual.close() }
    override fun isClosed() = actual.isClosed
}

private class TransferFixture(
    slow: Boolean = false,
    ask: ((JSONObject, (Uri?) -> Unit) -> Unit)? = null,
    guard: (Boolean) -> Unit = {},
    status: ((TransferStatus) -> Unit)? = null,
    sharedCache: File? = null,
    prepareCache: ((File) -> Unit)? = null
) : AutoCloseable {
    val directory = Files.createTempDirectory("android-file-stability-").toFile()
    val cache = sharedCache ?: File(directory, "cache").apply { mkdirs() }
    val destination = File(directory, "fresh-document.bin")
    val source = File(directory, "source.bin").apply { writeBytes(ByteArray(1000) { it.toByte() }) }
    val resolver = ContentResolver()
    val selected = resolver.register(source)
    val remote: Socket
    val gate: TransferGateSocket?
    val peer: Peer
    val statuses = LinkedBlockingQueue<TransferStatus>()
    val transfers: FileTransfers
    init {
        prepareCache?.invoke(cache)
        val listener = ServerSocket(0)
        val local = Socket("127.0.0.1", listener.localPort)
        remote = listener.accept().apply { soTimeout = 2_000 }; listener.close()
        gate = if (slow) TransferGateSocket(local) else null
        peer = Peer(gate ?: local, policy = PeerPolicy(20, 60_000, 10_000, 2_000))
        transfers = FileTransfers(Context(cache, resolver), peer, { it() }, ask ?: { _, callback ->
            check(destination.createNewFile()); callback(resolver.register(destination))
        }, { statuses.offer(it); status?.invoke(it) }, { callback -> callback(selected) }, guard)
        peer.onMessage = { transfers.handle(it) }
        peer.onClose = { transfers.close() }
        peer.start()
    }
    fun send(message: JSONObject) = JsonWire.write(DataOutputStream(remote.getOutputStream()), message)
    fun read() = JsonWire.read(DataInputStream(remote.getInputStream()))
    fun offer(id: String = "d3159e03-9114-4b58-b24d-697b4525405a", bytes: ByteArray = byteArrayOf(1, 2, 3)) {
        val hash = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it.toInt() and 255) }
        send(JSONObject().put("type", "file_offer").put("id", id).put("name", "test.bin").put("size", bytes.size).put("sha256", hash))
    }
    fun receiveComplete() {
        val id = "d3159e03-9114-4b58-b24d-697b4525405a"
        offer(); check(read().getString("type") == "file_accept")
        send(JSONObject().put("type", "file_chunk").put("id", id).put("offset", 0).put("data", "AQID"))
        check(read().getString("type") == "file_ack")
        send(JSONObject().put("type", "file_end").put("id", id))
    }
    override fun close() {
        gate?.release?.countDown(); transfers.close(); peer.close(); remote.close(); directory.deleteRecursively()
    }
}

private fun cancelDuringProvider(opening: Boolean) {
    TransferFixture().use { fixture ->
        val entered = CountDownLatch(1); val release = CountDownLatch(1)
        if (opening) fixture.resolver.beforeOpenOutput = {
            entered.countDown(); check(release.await(3, TimeUnit.SECONDS))
        } else fixture.resolver.transformOutput = { target ->
            object : FilterOutputStream(target) {
                override fun write(bytes: ByteArray, offset: Int, count: Int) {
                    entered.countDown(); check(release.await(3, TimeUnit.SECONDS)); out.write(bytes, offset, count)
                }
            }
        }
        try {
            fixture.receiveComplete(); check(entered.await(1, TimeUnit.SECONDS))
            val start = System.nanoTime()
            if (opening) fixture.transfers.cancel() else fixture.transfers.close()
            check(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start) < 250) { "Provider held the transfer state lock" }
            if (opening) check(fixture.resolver.deleteCalls.get() == 0) { "Deleted URI before its in-flight w-open completed" }
            release.countDown()
            eventually { !fixture.destination.exists() && fixture.resolver.deleteCalls.get() >= 1 && fixture.cache.listFiles().orEmpty().isEmpty() }
            check(fixture.resolver.deleteCalls.get() == 1) { "Fresh SAF document was deleted more than once" }
            check(!fixture.peer.closed)
        } finally { release.countDown() }
    }
}

private fun cancelWithBlockedNetwork() {
    for (close in listOf(false, true)) TransferFixture(slow = true).use { fixture ->
        fixture.peer.send(JSONObject().put("type", "control")); check(fixture.gate!!.entered.await(1, TimeUnit.SECONDS))
        repeat(128) { fixture.peer.send(JSONObject().put("type", "control").put("sequence", it)) }
        repeat(16) { fixture.peer.send(JSONObject().put("type", "file_cancel").put("id", "reserved-$it")) }
        check(fixture.transfers.sendFile(fixture.selected))
        eventually {
            Thread.getAllStackTraces().values.any { trace ->
                trace.any { it.className == Peer::class.java.name && it.methodName == "send" } &&
                    trace.any { it.className.startsWith(FileTransfers::class.java.name) }
            }
        }
        val start = System.nanoTime()
        if (close) fixture.transfers.close() else fixture.transfers.cancel()
        check(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start) < 250) { "Cancel/close waited for Peer.send" }
        fixture.transfers.close() // Interrupt the waiting outbox before releasing the socket fixture.
        eventually { fixture.cache.listFiles().orEmpty().isEmpty() }
        check(!fixture.peer.closed)
    }
}

private fun reentrantStatusAndLatePrompt() {
    val cancelled = CountDownLatch(1); val once = AtomicBoolean()
    lateinit var first: TransferFixture
    first = TransferFixture(status = { event ->
        if (event.active && once.compareAndSet(false, true)) {
            first.transfers.cancel(); first.transfers.close(); cancelled.countDown()
        }
    })
    first.use { check(it.transfers.receiveRequest()); check(cancelled.await(1, TimeUnit.SECONDS)); check(!it.peer.closed) }
    val count = AtomicInteger(); val callbacks = LinkedBlockingQueue<(Uri?) -> Unit>()
    val guard: (Boolean) -> Unit = { active -> check(count.addAndGet(if (active) 1 else -1) >= 0) }
    TransferFixture(ask = { _, callback -> callbacks.put(callback) }, guard = guard).use { old ->
        TransferFixture(ask = { _, callback -> callbacks.put(callback) }, guard = guard).use { current ->
            old.offer(); val oldCallback = callbacks.poll(1, TimeUnit.SECONDS) ?: error("old prompt missing")
            current.offer(); val currentCallback = callbacks.poll(1, TimeUnit.SECONDS) ?: error("new prompt missing")
            check(count.get() == 2)
            old.transfers.close(); check(count.get() == 2)
            oldCallback(null); check(count.get() == 1)
            oldCallback(null); check(count.get() == 1)
            currentCallback(null); check(count.get() == 0)
        }
    }
}

private fun providerCleanupFailureIsReportedOnce() {
    TransferFixture().use { fixture ->
        fixture.offer(); check(fixture.read().getString("type") == "file_accept")
        fixture.resolver.deleteAllowed = false
        fixture.transfers.cancel()
        eventually { fixture.statuses.any { it.cleanupRequired } }
        check(fixture.destination.exists()) { "The fixture must reproduce a document the provider refused to remove" }
        val event = fixture.statuses.single { it.cleanupRequired }
        check(!event.active && event.success != true)
        fixture.transfers.close()
        Thread.sleep(120) // Cross the status coalescing interval; no repeated cleanup warning is allowed.
        check(fixture.statuses.count { it.cleanupRequired } == 1)
        check(fixture.resolver.deleteCalls.get() == 1)
    }
}

private fun providerFileDescriptorIsPipe(path: String) {
    TransferFixture().use { fixture ->
        val received = LinkedBlockingQueue<ByteArray>()
        val failure = LinkedBlockingQueue<Throwable>()
        val consumer = Thread {
            try { FileInputStream(path).use { received.put(it.readBytes()) } }
            catch (error: Throwable) { failure.put(error) }
        }.apply { isDaemon = true; start() }
        fixture.resolver.transformOutput = { target -> target.close(); FileOutputStream(path) }
        fixture.receiveComplete()
        check(fixture.read().getString("type") == "file_done") { "Valid bytes to a SAF pipe must not require fd.sync()" }
        check(received.poll(1, TimeUnit.SECONDS)?.contentEquals(byteArrayOf(1, 2, 3)) == true)
        consumer.join(1_000); check(!consumer.isAlive && failure.isEmpty())
        eventually { fixture.statuses.any { it.success == true } }
        check(fixture.resolver.deleteCalls.get() == 0)
        check(!fixture.peer.closed)
    }
}

private fun abandonedPrivateCacheIsNarrowlyRemoved() {
    TransferFixture(prepareCache = { cache ->
        File(cache, "minianydesk-send-crash.part").writeText("abandoned send")
        File(cache, "minianydesk-receive-crash.part").writeText("abandoned receive")
        File(cache, "keep.bin").writeText("unrelated cache")
        File(cache, "minianydesk-send-not-a-spool.txt").writeText("unrelated suffix")
        File(cache, "minianydesk-receive-directory.part").apply { mkdirs() }
            .resolve("keep.bin").writeText("directory contents")
    }).use { fixture ->
        check(!File(fixture.cache, "minianydesk-send-crash.part").exists())
        check(!File(fixture.cache, "minianydesk-receive-crash.part").exists())
        check(File(fixture.cache, "keep.bin").readText() == "unrelated cache")
        check(File(fixture.cache, "minianydesk-send-not-a-spool.txt").exists())
        check(File(fixture.cache, "minianydesk-receive-directory.part/keep.bin").exists())
    }
}

private fun anotherManagerNeverSweepsAnActiveSpool() {
    TransferFixture().use { first ->
        check(first.transfers.sendFile(first.selected))
        val offer = first.read()
        check(offer.getString("type") == "file_offer")
        val id = offer.getString("id")
        val spool = first.cache.listFiles().orEmpty().single { it.name.startsWith("minianydesk-send-") }
        val later = File(first.cache, "minianydesk-receive-created-in-this-process.part").apply { writeText("live owner") }
        TransferFixture(sharedCache = first.cache).use {
            check(spool.exists() && later.exists()) { "A later manager swept live process files" }
            first.send(JSONObject().put("type", "file_accept").put("id", id))
            val chunk = first.read()
            check(chunk.getString("type") == "file_chunk")
            check(java.util.Base64.getDecoder().decode(chunk.getString("data")).contentEquals(first.source.readBytes()))
            first.send(JSONObject().put("type", "file_ack").put("id", id).put("offset", first.source.length()))
            check(first.read().getString("type") == "file_end")
            first.send(JSONObject().put("type", "file_done").put("id", id))
            eventually { first.statuses.any { event -> event.success == true } && !spool.exists() }
            check(later.exists())
        }
        check(later.exists())
    }
}

object FileTransferStabilityHarness {
    @JvmStatic fun main(args: Array<String>) {
        when (args[0]) {
            "cancel-open" -> cancelDuringProvider(true)
            "close-write" -> cancelDuringProvider(false)
            "blocked-network" -> cancelWithBlockedNetwork()
            "reentrant-status-prompt" -> reentrantStatusAndLatePrompt()
            "cleanup-warning" -> providerCleanupFailureIsReportedOnce()
            "provider-pipe" -> providerFileDescriptorIsPipe(args[1])
            "abandoned-cache" -> abandonedPrivateCacheIsNarrowlyRemoved()
            "active-cache-owner" -> anotherManagerNeverSweepsAnActiveSpool()
            else -> error("Unknown file stability mode")
        }
        println(JSONObject().put("result", "passed").put("case", args[0]))
    }
}
