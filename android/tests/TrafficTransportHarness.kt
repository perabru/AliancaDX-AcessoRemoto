package br.com.perabru.minianydesk

import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.FilterInputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/** Independent byte-accounting checks on production Peer, using real loopback sockets. */
private open class TrafficSocket(protected val actual: Socket) : Socket() {
    override fun getInputStream(): InputStream = actual.getInputStream()
    override fun getOutputStream(): OutputStream = actual.getOutputStream()
    override fun setSoTimeout(value: Int) { actual.soTimeout = value }
    override fun getSoTimeout(): Int = actual.soTimeout
    override fun setKeepAlive(value: Boolean) { actual.keepAlive = value }
    override fun setTcpNoDelay(value: Boolean) { actual.tcpNoDelay = value }
    override fun shutdownInput() = actual.shutdownInput()
    override fun shutdownOutput() = actual.shutdownOutput()
    override fun close() = actual.close()
    override fun isClosed(): Boolean = actual.isClosed
}

private class TrafficFragmentSocket(actual: Socket) : TrafficSocket(actual) {
    private var reads = 0
    private val stream = object : FilterInputStream(actual.getInputStream()) {
        override fun read(bytes: ByteArray, offset: Int, count: Int): Int {
            reads++
            if (reads in setOf(3, 9, 19, 31)) throw SocketTimeoutException("temporary read poll")
            return `in`.read(bytes, offset, minOf(count, 1))
        }
    }
    override fun getInputStream(): InputStream = stream
}

private class TrafficPartialFailureSocket(actual: Socket) : TrafficSocket(actual) {
    var failed = false
    var replayed = false
    private val sink = object : OutputStream() {
        override fun write(value: Int) = write(byteArrayOf(value.toByte()), 0, 1)
        override fun write(bytes: ByteArray, offset: Int, count: Int) {
            if (failed) { replayed = true; error("range replayed after unknown partial write") }
            if (count > 16) {
                actual.getOutputStream().write(bytes, offset, 8)
                failed = true
                throw SocketTimeoutException("unknown partial body write")
            }
            actual.getOutputStream().write(bytes, offset, count)
        }
    }
    override fun getOutputStream(): OutputStream = sink
}

private fun trafficSockets(): Pair<Socket, Socket> {
    val server = ServerSocket(0)
    val local = Socket("127.0.0.1", server.localPort)
    val remote = server.accept().apply { soTimeout = 2_000 }
    server.close()
    return local to remote
}

private val trafficPolicy = PeerPolicy(readPollMs = 20, heartbeatMs = 60_000,
    idleTimeoutMs = 3_000, enqueueTimeoutMs = 300)

private fun trafficPacket(message: JSONObject): ByteArray {
    val bytes = ByteArrayOutputStream()
    JsonWire.write(DataOutputStream(bytes), message)
    return bytes.toByteArray()
}

private fun trafficWait(label: String, condition: () -> Boolean) {
    val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(2)
    while (!condition()) {
        check(System.nanoTime() < deadline) { "Timed out: $label" }
        Thread.sleep(2)
    }
}

private fun fragmentedTraffic() {
    val (local, remote) = trafficSockets()
    val messages = LinkedBlockingQueue<JSONObject>()
    val peer = Peer(TrafficFragmentSocket(local), { messages.put(it) }, policy = trafficPolicy)
    try {
        check(peer.receivedBytes == 0L && peer.sentBytes == 0L)
        peer.start()
        val message = JSONObject().put("type", "control").put("text", "ação 日本語 Ελληνικά")
        val packet = trafficPacket(message)
        val output = remote.getOutputStream()
        output.write(packet, 0, 2); output.flush()
        trafficWait("fragmented header counted before JSON completes") { peer.receivedBytes == 2L }
        check(messages.isEmpty())
        output.write(packet, 2, 5); output.flush()
        trafficWait("partial body counted without double-counting header") { peer.receivedBytes == 7L }
        check(messages.isEmpty())
        output.write(packet, 7, packet.size - 7); output.flush()
        check(messages.poll(2, TimeUnit.SECONDS)?.toString() == message.toString())
        check(peer.receivedBytes == packet.size.toLong())
        check(peer.sentBytes == 0L && !peer.closed)
    } finally { peer.close(); remote.close() }
}

private fun queuedAndCoalescedTraffic() {
    val (local, remote) = trafficSockets()
    val peer = Peer(local, policy = trafficPolicy)
    try {
        var last = JSONObject()
        repeat(400) { index ->
            last = JSONObject().put("type", "input").put("action", "move")
                .put("captureId", "active-capture").put("x", index / 399.0).put("y", .5)
            peer.send(last)
        }
        val release = JSONObject().put("type", "input").put("action", "release")
        peer.send(release)
        check(peer.sentBytes == 0L) { "Queued bytes were reported as transmitted" }
        peer.start()
        val input = DataInputStream(remote.getInputStream())
        check(JsonWire.read(input).toString() == last.toString())
        check(JsonWire.read(input).toString() == release.toString())
        val expected = trafficPacket(last).size + trafficPacket(release).size
        trafficWait("only surviving coalesced packets counted") { peer.sentBytes == expected.toLong() }
        check(peer.receivedBytes == 0L)
    } finally { peer.close(); remote.close() }
}

private fun multiChunkTraffic() {
    val (local, remote) = trafficSockets()
    val peer = Peer(local, policy = trafficPolicy)
    try {
        val message = JSONObject().put("type", "log").put("message", "ç".repeat(90_000))
        val packet = trafficPacket(message)
        peer.start(); peer.send(message)
        check(JsonWire.read(DataInputStream(remote.getInputStream())).toString() == message.toString())
        trafficWait("UTF-8 byte count across multiple 64 KiB writes") { peer.sentBytes == packet.size.toLong() }
        check(peer.receivedBytes == 0L)
    } finally { peer.close(); remote.close() }
}

private fun failedWriteTraffic() {
    val (local, remote) = trafficSockets()
    val socket = TrafficPartialFailureSocket(local)
    val peer = Peer(socket, policy = trafficPolicy)
    try {
        peer.start()
        peer.send(JSONObject().put("type", "log").put("message", "x".repeat(500)))
        trafficWait("unknown partial output closes the connection") { peer.closed }
        check(socket.failed && !socket.replayed)
        // Only the completed 4-byte header is knowable. The throwing body write
        // may have emitted a prefix, which must neither be claimed nor replayed.
        check(peer.sentBytes == 4L) { "Failed write counted as a complete body" }
    } finally { peer.close(); remote.close() }
}

private fun independentPeersTraffic() {
    val (aLocal, aRemote) = trafficSockets()
    val (bLocal, bRemote) = trafficSockets()
    val a = Peer(aLocal, policy = trafficPolicy)
    val b = Peer(bLocal, policy = trafficPolicy)
    try {
        val first = JSONObject().put("type", "control").put("session", "A")
        val second = JSONObject().put("type", "control").put("session", "B-is-independent")
        val firstPacket = trafficPacket(first); val secondPacket = trafficPacket(second)
        a.start(); b.start()
        a.send(first)
        check(JsonWire.read(DataInputStream(aRemote.getInputStream())).toString() == first.toString())
        trafficWait("first session transmission") { a.sentBytes == firstPacket.size.toLong() }
        check(b.sentBytes == 0L && b.receivedBytes == 0L)
        bRemote.getOutputStream().write(secondPacket)
        trafficWait("second session reception") { b.receivedBytes == secondPacket.size.toLong() }
        check(a.receivedBytes == 0L && b.sentBytes == 0L)
    } finally { a.close(); b.close(); aRemote.close(); bRemote.close() }
}

object TrafficTransportHarness {
    @JvmStatic fun main(args: Array<String>) {
        when (args.single()) {
            "fragments" -> fragmentedTraffic()
            "coalesced-queue" -> queuedAndCoalescedTraffic()
            "multichunk-utf8" -> multiChunkTraffic()
            "partial-write" -> failedWriteTraffic()
            "independent-peers" -> independentPeersTraffic()
            else -> error("Unknown transport accounting case")
        }
        println("{\"result\":\"passed\",\"case\":\"${args.single()}\"}")
    }
}
