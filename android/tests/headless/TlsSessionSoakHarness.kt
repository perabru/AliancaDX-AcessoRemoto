package br.com.perabru.minianydesk

import android.content.ContentResolver
import android.content.Context
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/** Full TLS + file protocol session; local SAF selections are explicit JVM doubles. */
object TlsSessionSoakHarness {
    @JvmStatic fun main(args: Array<String>) {
        val directory = File(args[2])
        val duration = args[3].toLong()
        val selected = File(directory, "android-source.bin")
        val destination = File(directory, "android-destination.bin")
        val cache = File(directory, "android-cache").apply { mkdirs() }
        val resolver = ContentResolver()
        val frames = AtomicInteger(); val controls = AtomicInteger(); val successes = AtomicInteger()
        val closes = LinkedBlockingQueue<String>()
        val failures = LinkedBlockingQueue<String>()
        val initialized = CountDownLatch(1)
        lateinit var transfers: FileTransfers
        val peer = AndroidNet.connect("127.0.0.1", args[0].toInt(), "testpassword", "Android JVM soak",
            { pin -> pin == args[1] }, onMessage = { message ->
                check(initialized.await(5, TimeUnit.SECONDS))
                if (!transfers.handle(message)) when (message.getString("type")) {
                    "screen" -> { check(message.getString("captureId") == "qa-screen") { "Unexpected screen metadata: $message" }; frames.incrementAndGet() }
                    "input" -> {
                        val expected = controls.getAndIncrement()
                        check(message.getInt("sequence") == expected) { "Input order expected=$expected actual=$message" }
                    }
                }
            }, onClose = {
                closes.offer(it)
                if (initialized.await(5, TimeUnit.SECONDS)) transfers.close()
            })
        transfers = FileTransfers(Context(cache, resolver), peer, { it() }, { _, callback ->
            check(destination.createNewFile()); callback(resolver.register(destination))
        }, { status ->
            if (status.success == true && successes.incrementAndGet() == 1)
                peer.send(JSONObject().put("type", "qa_upload_done"))
            if (status.success == false) failures.offer(status.message)
        })
        initialized.countDown()
        val start = System.nanoTime()
        var sequence = 0
        try {
            check(transfers.sendFile(resolver.register(selected)))
            while ((System.nanoTime() - start) / 1_000_000 < duration) {
                check(!peer.closed) { "Unexpected TLS closure: $closes" }
                peer.send(JSONObject().put("type", "input").put("action", "key").put("key", "ctrl")
                    .put("down", false).put("captureId", "qa-screen").put("sequence", sequence++))
                Thread.sleep(50)
            }
            check(failures.isEmpty() && successes.get() == 2) { "Transfers failed: $failures; success=${successes.get()}" }
            check(frames.get() > 100 && controls.get() > 100)
            check(closes.isEmpty())
            val hash = MessageDigest.getInstance("SHA-256").digest(destination.readBytes())
                .joinToString("") { "%02x".format(it.toInt() and 255) }
            println(JSONObject().put("event", "soak").put("milliseconds", (System.nanoTime() - start) / 1_000_000)
                .put("sentControls", sequence).put("receivedControls", controls.get()).put("receivedFrames", frames.get())
                .put("successfulTransfers", successes.get()).put("destinationSha256", hash).put("unexpectedClosures", closes.size))
        } finally { transfers.close(); peer.close("QA session complete") }
        val deadline = System.nanoTime() + 2_000_000_000
        fun peerThreads() = Thread.getAllStackTraces().keys.count {
            it.isAlive && it.name in setOf("MiniAnyDesk-reader", "MiniAnyDesk-writer", "MiniAnyDesk-heartbeat") }
        while (peerThreads() > 0 && System.nanoTime() < deadline) Thread.sleep(10)
        check(peerThreads() == 0 && closes.size == 1)
        check(cache.listFiles().orEmpty().isEmpty())
        println(JSONObject().put("event", "closed").put("callbacks", closes.size).put("livePeerThreads", peerThreads()).put("cacheFiles", 0))
    }
}
