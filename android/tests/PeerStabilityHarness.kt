package br.com.perabru.minianydesk

import org.json.JSONObject
import java.io.DataInputStream
import java.io.FilterInputStream
import java.io.FilterOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

private open class TestSocket(protected val actual: Socket) : Socket() {
    override fun getInputStream(): InputStream = actual.getInputStream()
    override fun getOutputStream(): OutputStream = actual.getOutputStream()
    override fun setSoTimeout(timeout: Int) { actual.soTimeout = timeout }
    override fun getSoTimeout(): Int = actual.soTimeout
    override fun setKeepAlive(enabled: Boolean) { actual.keepAlive = enabled }
    override fun setTcpNoDelay(enabled: Boolean) { actual.tcpNoDelay = enabled }
    override fun shutdownInput() = actual.shutdownInput()
    override fun shutdownOutput() = actual.shutdownOutput()
    override fun close() = actual.close()
    override fun isClosed(): Boolean = actual.isClosed
}

private class FragmentSocket(actual: Socket) : TestSocket(actual) {
    private var reads = 0
    private val source = object : FilterInputStream(actual.getInputStream()) {
        override fun read(bytes: ByteArray, offset: Int, count: Int): Int {
            reads++
            if (reads in setOf(3, 8, 22, 40)) throw SocketTimeoutException("temporary packet poll")
            return super.read(bytes, offset, minOf(1, count))
        }
    }
    override fun getInputStream(): InputStream = source
}

private class GateSocket(actual: Socket) : TestSocket(actual) {
    val entered = CountDownLatch(1)
    val release = CountDownLatch(1)
    private val sink = object : FilterOutputStream(actual.getOutputStream()) {
        override fun write(bytes: ByteArray, offset: Int, count: Int) {
            entered.countDown()
            check(release.await(3, TimeUnit.SECONDS)) { "test writer gate expired" }
            out.write(bytes, offset, count)
        }
        override fun write(value: Int) = write(byteArrayOf(value.toByte()), 0, 1)
    }
    override fun getOutputStream(): OutputStream = sink
    override fun shutdownOutput() { release.countDown(); super.shutdownOutput() }
    override fun close() { release.countDown(); super.close() }
}

private class FailPartialWriteSocket(actual: Socket) : TestSocket(actual) {
    var failed = false
    var writesAfterFailure = 0
    private val sink = object : FilterOutputStream(actual.getOutputStream()) {
        override fun write(bytes: ByteArray, offset: Int, count: Int) {
            if (failed) { writesAfterFailure++; error("packet range was replayed after partial write failure") }
            if (count > 16) {
                out.write(bytes, offset, 8)
                failed = true
                throw SocketTimeoutException("write may have transmitted a prefix")
            }
            out.write(bytes, offset, count)
        }
        override fun write(value: Int) = write(byteArrayOf(value.toByte()), 0, 1)
    }
    override fun getOutputStream(): OutputStream = sink
}

private fun socketPair(): Pair<Socket, Socket> {
    val listener = ServerSocket(0)
    val local = Socket("127.0.0.1", listener.localPort)
    val remote = listener.accept().apply { soTimeout = 2_000 }
    listener.close()
    return local to remote
}

private val normalPolicy = PeerPolicy(readPollMs = 20, heartbeatMs = 60_000, idleTimeoutMs = 3_000, enqueueTimeoutMs = 500)
private fun control(sequence: Int) = JSONObject().put("type", "control").put("sequence", sequence)
private fun read(remote: Socket): JSONObject = JsonWire.read(DataInputStream(remote.getInputStream()))
private fun send(remote: Socket, message: JSONObject) = JsonWire.write(java.io.DataOutputStream(remote.getOutputStream()), message)

private fun fragments() {
    val (local, remote) = socketPair()
    val received = LinkedBlockingQueue<JSONObject>()
    val peer = Peer(FragmentSocket(local), { received.put(it) }, policy = normalPolicy)
    try {
        peer.start()
        val messages = listOf(JSONObject().put("type", "control").put("text", "ação Ελληνικά 日本語"),
            JSONObject().put("type", "input").put("action", "release"))
        messages.forEach { send(remote, it) }
        messages.forEach { check(received.poll(2, TimeUnit.SECONDS)?.toString() == it.toString()) }
        check(!peer.closed)
    } finally { peer.close(); remote.close() }
}

private fun pointerBarriers() {
    val (local, remote) = socketPair()
    val gate = GateSocket(local)
    val peer = Peer(gate, policy = normalPolicy)
    try {
        peer.send(control(-1)); peer.start(); check(gate.entered.await(1, TimeUnit.SECONDS))
        val expected = mutableListOf(control(-1))
        val barriers = listOf(
            JSONObject().put("type", "file_ack").put("id", "d3159e03-9114-4b58-b24d-697b4525405a").put("offset", 1),
            JSONObject().put("type", "input").put("action", "button").put("button", "left").put("down", true),
            JSONObject().put("type", "input").put("action", "wheel").put("delta", 1),
            JSONObject().put("type", "input").put("action", "release"))
        barriers.forEachIndexed { batch, barrier ->
            var last = JSONObject()
            repeat(300) { index ->
                last = JSONObject().put("type", "input").put("action", "move").put("captureId", "same")
                    .put("x", index / 299.0).put("y", .5).put("sequence", batch * 300 + index)
                peer.send(last)
            }
            expected.add(last); expected.add(barrier); peer.send(barrier)
        }
        for (capture in listOf("old", "new")) {
            val message = JSONObject().put("type", "input").put("action", "move").put("captureId", capture).put("x", 0).put("y", 0)
            expected.add(message); peer.send(message)
        }
        check(!peer.closed); gate.release.countDown()
        expected.forEach { check(read(remote).toString() == it.toString()) }
        check(!peer.closed)
    } finally { gate.release.countDown(); peer.close(); remote.close() }
}

private fun queuePressure() {
    val (local, remote) = socketPair()
    val gate = GateSocket(local)
    val peer = Peer(gate, policy = normalPolicy)
    val enqueued = CountDownLatch(1)
    val failures = LinkedBlockingQueue<Throwable>()
    try {
        peer.send(control(-1)); peer.start(); check(gate.entered.await(1, TimeUnit.SECONDS))
        repeat(128) { peer.send(control(it)) }
        peer.send(JSONObject().put("type", "input").put("action", "move").put("captureId", "discard").put("x", 0).put("y", 0))
        val release = JSONObject().put("type", "input").put("action", "release")
        val sender = Thread {
            try { peer.send(release) } catch (error: Throwable) { failures.put(error) }
            finally { enqueued.countDown() }
        }.apply { isDaemon = true; start() }
        check(!enqueued.await(40, TimeUnit.MILLISECONDS))
        gate.release.countDown(); check(enqueued.await(1, TimeUnit.SECONDS)); sender.join(1_000)
        check(failures.isEmpty()) { failures.toString() }
        check(read(remote).getInt("sequence") == -1)
        repeat(128) { check(read(remote).getInt("sequence") == it) }
        check(read(remote).toString() == release.toString()); check(!peer.closed)
    } finally { gate.release.countDown(); peer.close(); remote.close() }
}

private fun reservedStateAndFileAck() {
    val (local, remote) = socketPair()
    val gate = GateSocket(local)
    val responded = CountDownLatch(1)
    val peer = Peer(gate, policy = normalPolicy)
    val ack = JSONObject().put("type", "file_ack").put("id", "d3159e03-9114-4b58-b24d-697b4525405a").put("offset", 1)
    peer.onMessage = { peer.send(ack); responded.countDown() }
    try {
        peer.send(control(-1)); peer.start(); check(gate.entered.await(1, TimeUnit.SECONDS))
        repeat(128) { peer.send(control(it)) }
        val paused = JSONObject().put("type", "state").put("sharing", false).put("promptActive", true)
        val resumed = JSONObject().put("type", "state").put("sharing", true).put("promptActive", false)
        peer.send(paused); peer.send(resumed) // outside reader thread, as a real prompt guard does
        send(remote, JSONObject().put("type", "file_chunk").put("id", ack.getString("id")).put("offset", 0).put("data", "AA=="))
        check(responded.await(1, TimeUnit.SECONDS)); check(!peer.closed)
        gate.release.countDown()
        check(read(remote).getInt("sequence") == -1)
        repeat(128) { check(read(remote).getInt("sequence") == it) }
        for (message in listOf(paused, resumed, ack)) check(read(remote).toString() == message.toString())
    } finally { gate.release.countDown(); peer.close(); remote.close() }
}

private fun partialWriteDoesNotReplay() {
    val (local, remote) = socketPair()
    val broken = FailPartialWriteSocket(local)
    val closes = AtomicInteger()
    val closed = CountDownLatch(1)
    val peer = Peer(broken, onClose = { closes.incrementAndGet(); closed.countDown() }, policy = normalPolicy)
    try {
        peer.send(JSONObject().put("type", "control").put("text", "a".repeat(1000)))
        peer.start(); check(closed.await(1, TimeUnit.SECONDS)); peer.close()
        check(peer.closed && broken.failed && closes.get() == 1)
        check(broken.writesAfterFailure == 0)
    } finally { peer.close(); remote.close() }
}

private fun stalledWriterWithLivePongs() {
    val (local, remote) = socketPair()
    val gate = GateSocket(local)
    val reasons = LinkedBlockingQueue<String>()
    val peer = Peer(gate, onClose = { reasons.put(it) }, policy = PeerPolicy(10, 60_000, 160, 500))
    try {
        peer.send(control(1)); peer.start(); check(gate.entered.await(1, TimeUnit.SECONDS))
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(1)
        var pongs = 0
        while (!peer.closed && System.nanoTime() < deadline) {
            try { send(remote, JSONObject().put("type", "pong")); pongs++ } catch (_: Exception) { break }
            Thread.sleep(10)
        }
        check(peer.closed && pongs >= 3)
        val reason = reasons.poll(1, TimeUnit.SECONDS)
        check(reason?.contains("Envio sem progresso") == true) { "Expected TX timeout with live pongs=$pongs; actual=$reason" }
    } finally { gate.release.countDown(); peer.close(); remote.close() }
}

private fun heartbeatAndSilence() {
    val (local, remote) = socketPair()
    val closed = CountDownLatch(1)
    val peer = Peer(local, onClose = { closed.countDown() }, policy = PeerPolicy(10, 25, 140, 500))
    try {
        peer.start()
        val until = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(300)
        var pings = 0
        while (System.nanoTime() < until) {
            check(read(remote).getString("type") == "ping")
            send(remote, JSONObject().put("type", "pong")); pings++
        }
        check(pings >= 3 && !peer.closed)
        check(closed.await(600, TimeUnit.MILLISECONDS)) { "silent peer did not expire" }
    } finally { peer.close(); remote.close() }
}

object PeerStabilityHarness {
    @JvmStatic fun main(args: Array<String>) {
        when (args.single()) {
            "fragments" -> fragments()
            "pointer-barriers" -> pointerBarriers()
            "queue-pressure" -> queuePressure()
            "reserved-state-ack" -> reservedStateAndFileAck()
            "partial-write-failure" -> partialWriteDoesNotReplay()
            "tx-idle-pongs" -> stalledWriterWithLivePongs()
            "heartbeat-silence" -> heartbeatAndSilence()
            else -> error("Unknown test mode")
        }
        println(JSONObject().put("result", "passed").put("case", args.single()))
    }
}
