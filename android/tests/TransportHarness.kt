package br.com.perabru.minianydesk

import org.json.JSONObject
import java.io.DataInputStream
import java.net.ServerSocket
import java.net.Socket
import java.security.KeyStore
import java.security.cert.X509Certificate
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext

private fun checkThat(value: Boolean, text: String) { if (!value) error(text) }

private fun queueTests() {
    val listener = ServerSocket(0)
    val sender = Socket("127.0.0.1", listener.localPort)
    val receiver = listener.accept()
    listener.close()
    receiver.soTimeout = 2_000
    val input = DataInputStream(receiver.inputStream)
    val closed = AtomicInteger()
    val peer = Peer(sender, onClose = { closed.incrementAndGet() })
    val mutable = JSONObject().put("type", "input").put("seq", 1).put("text", "ação Ελληνικά")
    peer.send(JSONObject().put("type", "screen").put("captureId", "discarded"))
    peer.send(mutable)
    mutable.put("seq", 999)
    peer.send(JSONObject().put("type", "state").put("sharing", false))
    peer.start()
    val first = JsonWire.read(input)
    checkThat(first.getInt("seq") == 1 && first.getString("text") == "ação Ελληνικά", "snapshot/UTF8 failed")
    checkThat(JsonWire.read(input).getString("type") == "state", "control ordering failed")
    try { JsonWire.read(input); error("paused state leaked pending frame") }
    catch (_: java.net.SocketTimeoutException) { }
    peer.close(); peer.close()
    checkThat(closed.get() == 1, "close callback repeated")
    receiver.close()

    val l2 = ServerSocket(0)
    val s2 = Socket("127.0.0.1", l2.localPort)
    val r2 = l2.accept()
    l2.close()
    r2.soTimeout = 2_000
    val p2 = Peer(s2)
    repeat(400) { p2.send(JSONObject().put("type", "screen").put("captureId", "$it")) }
    repeat(4) { p2.send(JSONObject().put("type", "input").put("seq", it)) }
    p2.start()
    val in2 = DataInputStream(r2.inputStream)
    repeat(4) { checkThat(JsonWire.read(in2).getInt("seq") == it, "queued controls lost or reordered") }
    checkThat(JsonWire.read(in2).getString("captureId") == "399", "latest frame not coalesced")
    p2.close(); r2.close()

    val l3 = ServerSocket(0)
    val s3 = Socket("127.0.0.1", l3.localPort)
    val r3 = l3.accept()
    l3.close()
    val p3 = Peer(s3)
    repeat(128) { p3.send(JSONObject().put("type", "input").put("seq", it)) }
    try { p3.send(JSONObject().put("type", "input")); error("control queue is unbounded") }
    catch (_: java.io.IOException) { }
    checkThat(p3.closed, "congestion did not close peer")
    r3.close()
    println("QUEUE_OK")
}

fun main(args: Array<String>) {
    when (args[0]) {
        "queue" -> queueTests()
        "client" -> {
            val received = AtomicInteger()
            val done = CountDownLatch(1)
            val closed = CountDownLatch(1)
            val reject = args[5] == "reject"
            try {
                val peer = AndroidNet.connect("127.0.0.1", args[1].toInt(), args[3], args[4],
                    trust = { fingerprint -> checkThat(fingerprint == args[2], "fingerprint mismatch"); !reject },
                    onMessage = { msg ->
                        if (msg.getString("type") == "input") {
                            val next = received.getAndIncrement()
                            checkThat(msg.getInt("seq") == next, "interlanguage ordering failed")
                            checkThat(msg.getString("text") == "ação Ελληνικά 日本語", "unicode changed")
                            checkThat(msg.getString("captureId") == "capture-42", "capture metadata changed")
                        } else if (msg.getString("type") == "screen") {
                            checkThat(msg.getInt("left") == -1920 && msg.getInt("frameWidth") == 960,
                                "screen metadata changed")
                            done.countDown()
                        }
                    }, onClose = { closed.countDown() })
                checkThat(!reject, "rejected pin was authenticated")
                if (args[5] == "malformed") {
                    checkThat(closed.await(4, TimeUnit.SECONDS), "malformed packet did not close")
                    println("MALFORMED_CLOSED")
                } else {
                    repeat(40) { peer.send(JSONObject().put("type", "input").put("seq", it)
                        .put("text", "ação Ελληνικά 日本語").put("captureId", "capture-42")
                        .put("action", "key").put("key", "ctrl").put("down", false)
                        .put("x", 0.25).put("y", 0.75)) }
                    peer.send(JSONObject().put("type", "screen").put("left", -1920).put("top", 0)
                        .put("width", 1920).put("height", 1080).put("frameWidth", 960)
                        .put("frameHeight", 540).put("captureId", "capture-42").put("image", "AA=="))
                    checkThat(done.await(5, TimeUnit.SECONDS), "echo timed out")
                    checkThat(received.get() == 40, "ordered inputs lost")
                    println("CLIENT_OK")
                }
                peer.close()
            } catch (exc: SecurityException) {
                println("REJECTED:${exc.message}")
            }
        }
        "server" -> {
            val store = KeyStore.getInstance("PKCS12")
            java.io.FileInputStream(args[1]).use { store.load(it, "testpassword".toCharArray()) }
            val km = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm())
            km.init(store, "testpassword".toCharArray())
            val context = SSLContext.getInstance("TLS")
            context.init(km.keyManagers, null, null)
            val host = AndroidHostServer(0, "testpassword", context,
                onRequest = { name, _ -> println("REQUEST:$name"); System.out.flush(); name != "deny" },
                onConnect = { peer, _, _ -> peer.onMessage = { msg -> peer.send(msg) } },
                onDisconnect = { _, _ -> println("DISCONNECTED"); System.out.flush() })
            val port = host.start()
            val cert = store.getCertificate("host") as X509Certificate
            println("READY:$port:${JsonWire.fingerprint(cert)}"); System.out.flush()
            while (true) {
                when (readlnOrNull()) {
                    "restart" -> { host.stop(); println("RESTARTED:${host.start()}"); System.out.flush() }
                    else -> { host.stop(); return }
                }
            }
        }
    }
}
