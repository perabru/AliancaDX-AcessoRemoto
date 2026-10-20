package br.com.perabru.minianydesk

import android.content.ContentResolver
import android.content.Context
import android.net.Uri
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.File
import java.util.Base64
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import javax.imageio.ImageIO

/** JVM-only test entrypoint. Android providers are local test doubles, never APK code. */
object DesktopPortHarness {
    @JvmStatic fun main(args: Array<String>) {
        require(args.size == 7) { "mode host port pin source new-document cache" }
        val mode = args[0]
        val receivedScreen = AtomicReference<JSONObject?>()
        val screenReady = CountDownLatch(1)
        val ack = CountDownLatch(1)
        val transferReady = AtomicReference<FileTransfers?>()
        val transferDone = CountDownLatch(1)
        val transferSuccess = AtomicReference<Boolean?>()
        val early = java.util.concurrent.ConcurrentLinkedQueue<JSONObject>()
        val peer = try {
            AndroidNet.connect(args[1], args[2].toInt(), "12345678", "Android port test",
                trust = { fingerprint ->
                    println("TRUST:$fingerprint")
                    check(fingerprint == args[3]) { "Certificate identity changed" }
                    mode != "reject-pin"
                }, onMessage = { message ->
                    when (message.optString("type")) {
                        "screen" -> { receivedScreen.set(message); screenReady.countDown() }
                        "port_test_ack" -> ack.countDown()
                        else -> {
                            val transfers = transferReady.get()
                            if (transfers == null) early.offer(message) else transfers.handle(message)
                        }
                    }
                }, authenticationTimeoutMs = 12_000)
        } catch (rejected: SecurityException) {
            if (mode != "reject-pin") throw rejected
            println("PIN_REJECTED_BEFORE_LOGIN")
            return
        }
        check(mode != "reject-pin") { "A rejected identity was authenticated" }
        try {
            check(peer.computerName == "Desktop Python 6.1") { "Remote computer name lost" }
            if (mode == "session") {
                check(screenReady.await(5, TimeUnit.SECONDS)) { "Python frame not received" }
                val screen = receivedScreen.get()!!
                check(screen.getString("monitorId") == "all")
                check(screen.getJSONArray("monitors").length() == 2)
                val image = ImageIO.read(ByteArrayInputStream(Base64.getDecoder().decode(screen.getString("image"))))
                check(image.width == screen.getInt("frameWidth") && image.height == screen.getInt("frameHeight"))
                val fit = ScreenGeometry.fit(image.width, image.height, 1000, 700)!!
                val point = ScreenGeometry.normalizedPoint(fit, fit.right.toFloat(), fit.bottom.toFloat())!!
                val physical = ScreenGeometry.physicalPoint(screen.getInt("left"), screen.getInt("top"),
                    screen.getInt("width"), screen.getInt("height"), point)!!
                check(physical == Pair(1919, 1199)) { "Negative monitor edge mapping changed: $physical" }
                fun input(action: String): JSONObject = JSONObject().put("type", "input")
                    .put("action", action).put("captureId", screen.getString("captureId"))
                    .put("x", point.x).put("y", point.y)
                peer.send(input("move"))
                peer.send(input("button").put("button", "left").put("down", true))
                peer.send(input("button").put("button", "left").put("down", false))
                peer.send(input("key").put("key", "ctrl").put("down", true))
                peer.send(input("key").put("key", "c").put("down", true))
                peer.send(input("key").put("key", "c").put("down", false))
                peer.send(input("key").put("key", "ctrl").put("down", false))
                peer.send(input("wheel").put("delta", 2))
                peer.send(JSONObject().put("type", "input").put("action", "release"))
                peer.send(JSONObject().put("type", "monitor").put("id", "2"))
                peer.send(JSONObject().put("type", "monitor").put("id", "all"))
                peer.send(JSONObject().put("type", "sharing").put("enabled", false))
                peer.send(JSONObject().put("type", "sharing").put("enabled", true))
                peer.send(JSONObject().put("type", "port_test_complete"))
                check(ack.await(5, TimeUnit.SECONDS)) { "Python did not process all commands" }
                println("SESSION_OK:JPEG_GEOMETRY_POINTER_KEYBOARD_WHEEL_MONITORS_SHARING")
            } else {
                val cache = File(args[6]).apply { mkdirs() }
                val source = File(args[4])
                val destination = File(args[5])
                val resolver = ContentResolver()
                val selected = resolver.register(source)
                val transfers = FileTransfers(Context(cache, resolver), peer, { it() }, { _, choose ->
                    check(destination.createNewFile()) { "Receiver selection must be a NEW document" }
                    choose(resolver.register(destination))
                }, { status ->
                    if (status.success != null) {
                        transferSuccess.set(status.success)
                        transferDone.countDown()
                    }
                }, { choose -> choose(selected) })
                transferReady.set(transfers)
                while (true) transfers.handle(early.poll() ?: break)
                try {
                    when (mode) {
                        "send" -> check(transfers.sendFile(selected))
                        "request" -> check(transfers.receiveRequest())
                        "receive" -> peer.send(JSONObject().put("type", "port_test_ready"))
                        else -> error("Unknown mode")
                    }
                    check(transferDone.await(10, TimeUnit.SECONDS)) { "TLS transfer timed out" }
                    check(transferSuccess.get() == true) { "TLS transfer unsuccessful" }
                    // Let the queued protocol completion reach the sender before disconnect.
                    Thread.sleep(200)
                } finally { transfers.close() }
                check(cache.listFiles()?.isEmpty() == true) { "Private spool left behind" }
                println("TLS_TRANSFER_OK:$mode")
            }
        } finally { peer.close() }
    }
}
