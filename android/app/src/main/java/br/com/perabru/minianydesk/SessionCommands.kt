package br.com.perabru.minianydesk

import org.json.JSONObject
import java.util.ArrayDeque

/** FIFO owned by one session. Only adjacent pointer moves can replace each other. */
internal class SessionCommandQueue<T>(private val limit: Int = 4096) {
    data class Command<T>(val owner: T, val generation: Int, val message: JSONObject)
    private val items = ArrayDeque<Command<T>>()
    @Synchronized fun offer(owner: T, generation: Int, message: JSONObject): Boolean {
        val snapshot = JSONObject(message.toString())
        val last = items.peekLast()
        if (isMove(snapshot) && last != null && last.owner === owner && last.generation == generation &&
            isMove(last.message) && last.message.optString("captureId") == snapshot.optString("captureId")) {
            items.removeLast(); items.addLast(Command(owner, generation, snapshot)); return true
        }
        if (items.size >= limit) return false
        items.addLast(Command(owner, generation, snapshot)); return true
    }
    @Synchronized fun poll(): Command<T>? = items.pollFirst()
    @Synchronized fun clear() = items.clear()
    @Synchronized fun size(): Int = items.size
    companion object {
        fun isMove(message: JSONObject) = message.optString("type") == "input" && message.optString("action") == "move"
    }
}

/** No network send runs on the Android main thread, including key releases. */
internal class SessionCommands(
    private val current: (Peer, Int) -> Boolean,
    private val failed: (Peer, Int, String) -> Unit
) {
    private val lock = Object()
    private val queue = SessionCommandQueue<Peer>()
    @Volatile private var stopped = false
    @Volatile private var failedOwner: Peer? = null
    @Volatile private var failedGeneration = -1
    private val worker = Thread({
        while (!stopped) {
            val command = synchronized(lock) {
                while (!stopped && queue.size() == 0) lock.wait()
                if (stopped) null else queue.poll()
            } ?: continue
            if (!current(command.owner, command.generation) || command.owner.closed ||
                (failedOwner === command.owner && failedGeneration == command.generation)) continue
            try { command.owner.send(command.message) }
            catch (error: Exception) {
                if (current(command.owner, command.generation)) {
                    command.owner.close(error.message ?: "A conexão foi interrompida.")
                    failed(command.owner, command.generation, error.message ?: "A conexão foi interrompida.")
                }
            }
        }
    }, "MiniAnyDesk-session-commands").apply { isDaemon = true; start() }

    fun offer(peer: Peer, generation: Int, message: JSONObject) {
        var overflow = false
        synchronized(lock) {
            if (stopped || !current(peer, generation) || (failedOwner === peer && failedGeneration == generation)) return
            if (!queue.offer(peer, generation, message)) {
                overflow = !SessionCommandQueue.isMove(message)
                if (overflow) { failedOwner = peer; failedGeneration = generation }
            }
            lock.notifyAll()
        }
        if (overflow) {
            // Never drop an ordered button/key/release silently. Terminate the congested session.
            Thread({
                if (current(peer, generation)) {
                    val reason = "A conexão não acompanhou os comandos. Conecte novamente."
                    peer.close(reason); failed(peer, generation, reason)
                }
            }, "MiniAnyDesk-session-congested").apply { isDaemon = true; start() }
        }
    }
    fun clear() = synchronized(lock) { queue.clear(); failedOwner = null; failedGeneration = -1; lock.notifyAll() }
    fun close() = synchronized(lock) { stopped = true; queue.clear(); lock.notifyAll() }
}
