package br.com.perabru.minianydesk

import android.content.ContentResolver
import android.content.Context
import android.net.Uri
import org.json.JSONObject
import java.io.File
import java.net.Socket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Test process connected to the real queued Peer with local content-provider doubles. */
object TransferHarness {
    @JvmStatic fun main(args: Array<String>) {
        require(args.size == 6) { "mode host port source destination cache" }
        val mode = args[0]
        val source = File(args[3])
        val destination = File(args[4])
        val cache = File(args[5]).apply { mkdirs() }
        val resolver = ContentResolver(mode != "send-unknown-size")
        val context = Context(cache, resolver)
        val selected = resolver.register(source)
        val finished = CountDownLatch(1)
        val peer = Peer(Socket(args[1], args[2].toInt()))
        fun newDestination(): Uri {
            check(destination.createNewFile()) { "Test destination must be a newly created document" }
            return resolver.register(destination)
        }
        val transfers = FileTransfers(context, peer, { it() }, { _, callback ->
            when (mode) {
                "refuse" -> callback(null)
                "receive-stale" -> Thread {
                    Thread.sleep(600)
                    callback(newDestination())
                }.apply { isDaemon = true; start() }
                else -> callback(newDestination())
            }
        }, { status ->
            println(JSONObject().put("event", "status").put("active", status.active)
                .put("message", status.message).put("name", status.name)
                .put("progress", status.progress).put("success", status.success ?: JSONObject.NULL))
            if (status.success != null) finished.countDown()
        }, { callback -> callback(selected) }, { guard ->
            println(JSONObject().put("event", "prompt_guard").put("active", guard))
        })
        peer.onMessage = { message ->
            if (!transfers.handle(message)) println(JSONObject().put("event", "other").put("message", message))
        }
        peer.onClose = { transfers.close(); finished.countDown() }
        peer.start()
        when (mode) {
            "send", "send-unknown-size" -> check(transfers.sendFile(selected))
            "request" -> check(transfers.receiveRequest())
        }
        val completed = finished.await(15, TimeUnit.SECONDS)
        // Allow the writer to flush file_done and a delayed, already opened picker to return.
        Thread.sleep(if (mode == "receive-stale") 900 else 150)
        transfers.close()
        peer.close()
        println(JSONObject().put("event", "finished").put("completed", completed)
            .put("cacheFiles", cache.listFiles()?.size ?: 0)
            .put("destinationExists", destination.exists()))
        check(completed) { "Transfer harness timeout" }
    }
}
