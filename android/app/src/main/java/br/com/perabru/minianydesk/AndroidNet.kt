package br.com.perabru.minianydesk

import android.annotation.TargetApi
import android.os.Build
import org.json.JSONObject
import org.json.JSONTokener
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.IOException
import java.io.EOFException
import java.net.InetSocketAddress
import java.net.InetAddress
import java.net.Socket
import java.net.SocketTimeoutException
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.ArrayDeque
import java.util.concurrent.Semaphore
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.ExecutionException
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.ScheduledThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import java.util.concurrent.atomic.AtomicReference
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLServerSocket
import javax.net.ssl.SSLSocket
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

internal object JsonWire {
    const val VERSION = "3.0.0"
    const val APP_VERSION = "7.2.0"
    const val STATUS_ALPN = "minianydesk-status/1"
    const val MAX_MESSAGE = 4 * 1024 * 1024

    fun encode(message: JSONObject): ByteArray {
        require(message.opt("type") is String) { "Mensagem inválida." }
        val bytes = message.toString().toByteArray(StandardCharsets.UTF_8)
        require(bytes.size in 1..MAX_MESSAGE) { "Mensagem excede o limite permitido." }
        return bytes
    }

    fun read(input: DataInputStream): JSONObject {
        val length = input.readInt()
        require(length in 1..MAX_MESSAGE) { "Mensagem excede o limite permitido." }
        val bytes = ByteArray(length)
        input.readFully(bytes)
        return decode(bytes)
    }

    fun decode(bytes: ByteArray): JSONObject {
        val text = StandardCharsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
            .decode(ByteBuffer.wrap(bytes)).toString()
        // Bound nesting before parsing so an untrusted packet cannot exhaust the stack.
        var depth = 0
        var quoted = false
        var escaped = false
        for (ch in text) {
            if (quoted) {
                if (escaped) escaped = false
                else if (ch == '\\') escaped = true
                else if (ch == '"') quoted = false
            } else {
                if (ch == '"') quoted = true
                else if (ch == '{' || ch == '[') {
                    depth++
                    require(depth <= 64) { "Mensagem muito complexa." }
                } else if (ch == '}' || ch == ']') depth--
            }
        }
        require(depth == 0 && !quoted) { "Mensagem inválida." }
        val tokenizer = JSONTokener(text)
        val parsed = tokenizer.nextValue()
        require(parsed is JSONObject && tokenizer.nextClean() == '\u0000') { "Mensagem inválida." }
        val message = parsed
        require(message.opt("type") is String) { "Mensagem inválida." }
        return message
    }

    fun write(output: DataOutputStream, bytes: ByteArray) {
        output.writeInt(bytes.size)
        output.write(bytes)
        output.flush()
    }

    fun write(output: DataOutputStream, message: JSONObject) = write(output, encode(message))

    fun tlsProtocols(supported: Array<String>): Array<String> =
        supported.filter { it == "TLSv1.2" || it == "TLSv1.3" }.toTypedArray()

    fun fingerprint(certificate: X509Certificate): String =
        MessageDigest.getInstance("SHA-256").digest(certificate.encoded)
            .joinToString("") { "%02X".format(it.toInt() and 255) }

    fun computerName(value: Any?): String {
        if (value !is String || value.length > 80 || value.any { it.code < 32 || it.code == 127 }) return ""
        return try {
            StandardCharsets.UTF_8.newEncoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).encode(java.nio.CharBuffer.wrap(value))
            value
        } catch (_: Exception) { "" }
    }
}

/** ALPN exists in the public Android API from 29; older devices send none. */
internal object AndroidAlpn {
    fun advertise(socket: SSLSocket): Boolean =
        if (Build.VERSION.SDK_INT >= 29) configure(socket) else false

    fun advertise(socket: SSLServerSocket): Boolean =
        if (Build.VERSION.SDK_INT >= 29) configure(socket) else false

    fun selected(socket: SSLSocket): String? =
        if (Build.VERSION.SDK_INT >= 29) selectedProtocol(socket) else null

    @TargetApi(29)
    private fun configure(socket: SSLSocket): Boolean = try {
        val parameters = socket.sslParameters
        parameters.applicationProtocols = arrayOf(JsonWire.STATUS_ALPN)
        socket.sslParameters = parameters
        true
    } catch (_: UnsupportedOperationException) { false }

    @TargetApi(29)
    private fun configure(socket: SSLServerSocket): Boolean = try {
        val parameters = socket.sslParameters
        parameters.applicationProtocols = arrayOf(JsonWire.STATUS_ALPN)
        socket.sslParameters = parameters
        true
    } catch (_: UnsupportedOperationException) { false }

    @TargetApi(29)
    private fun selectedProtocol(socket: SSLSocket): String? = try {
        socket.applicationProtocol?.takeIf { it.isNotEmpty() }
    } catch (_: UnsupportedOperationException) { null }
}

/** Tunable only for bounded regression tests; production uses these defaults. */
data class PeerPolicy(
    val readPollMs: Int = 1_000,
    val heartbeatMs: Long = 10_000,
    val idleTimeoutMs: Long = 120_000,
    val enqueueTimeoutMs: Long = 5_000
) {
    init {
        require(readPollMs > 0 && heartbeatMs > 0 && idleTimeoutMs > 0 && enqueueTimeoutMs > 0)
    }
}

/** Stateful framing: a temporary timeout never discards a partial header/body. */
internal class PacketReader(private val input: DataInputStream, private val received: (Int) -> Unit = {}) {
    private val header = ByteArray(4)
    private var headerOffset = 0
    private var body: ByteArray? = null
    private var bodyOffset = 0

    fun read(): JSONObject {
        while (headerOffset < header.size) {
            val count = input.read(header, headerOffset, header.size - headerOffset)
            if (count < 0) throw EOFException("O outro dispositivo encerrou a conexão.")
            if (count == 0) throw IOException("Leitura sem progresso.")
            received(count)
            headerOffset += count
        }
        if (body == null) {
            val length = ByteBuffer.wrap(header).int
            require(length in 1..JsonWire.MAX_MESSAGE) { "Mensagem excede o limite permitido." }
            body = ByteArray(length)
        }
        val bytes = body!!
        while (bodyOffset < bytes.size) {
            val count = input.read(bytes, bodyOffset, bytes.size - bodyOffset)
            if (count < 0) throw EOFException("O outro dispositivo encerrou a conexão.")
            if (count == 0) throw IOException("Leitura sem progresso.")
            received(count)
            bodyOffset += count
        }
        val message = JsonWire.decode(bytes)
        headerOffset = 0
        bodyOffset = 0
        body = null
        return message
    }
}

/** Separate Java TLS stream owners, bounded FIFO and a replaceable screen slot. */
class Peer(
    val sock: Socket,
    @Volatile var onMessage: (JSONObject) -> Unit = {},
    @Volatile var onClose: (String) -> Unit = {},
    private val policy: PeerPolicy = PeerPolicy()
) {
    private data class Pending(val bytes: ByteArray, val key: List<Any?>?)
    private val lock = Object()
    private val outgoing = ArrayDeque<Pending>()
    private var outgoingBytes = 0L
    private var frame: ByteArray? = null
    private var pingPending = false
    private var pongPending = false
    private var started = false
    private val ended = AtomicBoolean(false)
    private val readBytes = AtomicLong(0)
    private val writtenBytes = AtomicLong(0)
    /** Actual application-stream bytes after authentication, including JSON framing.
     * Excludes TLS/IP/mesh overhead and bytes only queued or replaced before writing. */
    val receivedBytes: Long get() = readBytes.get()
    val sentBytes: Long get() = writtenBytes.get()
    private val input = DataInputStream(sock.getInputStream())
    private val output = DataOutputStream(sock.getOutputStream())
    private var heartbeat: Thread? = null
    private var reader: Thread? = null
    private var writer: Thread? = null
    @Volatile private var lastReceivedAt = nowMs()
    @Volatile private var writeProgressAt = 0L
    val closed: Boolean get() = ended.get()
    var fingerprint: String = ""
        internal set
    var computerName: String = ""
        internal set

    fun start() {
        synchronized(lock) {
            if (started || closed) return
            started = true
            sock.soTimeout = policy.readPollMs
            try { sock.keepAlive = true } catch (_: Exception) { }
            try { sock.tcpNoDelay = true } catch (_: Exception) { }
            lastReceivedAt = nowMs()
            reader = daemon("MiniAnyDesk-reader") { readLoop() }
            writer = daemon("MiniAnyDesk-writer") { writeLoop() }
            heartbeat = daemon("MiniAnyDesk-heartbeat") { heartbeatLoop() }
            reader!!.start()
            writer!!.start()
            heartbeat!!.start()
        }
    }

    fun send(message: JSONObject) {
        val bytes = JsonWire.encode(message) // immutable snapshot
        val type = message.getString("type")
        val disposable = type == "input" && message.optString("action") == "move"
        val key = when {
            disposable -> listOf("move", message.opt("captureId"))
            type == "state" -> listOf("state", message.opt("sharing"), message.opt("localPaused"),
                message.opt("promptActive"), message.opt("captureAvailable"))
            else -> null
        }
        val reserved = (type in FILE_TYPES && bytes.size <= 128 * 1024) ||
            (type == "state" && bytes.size <= 64 * 1024)
        val countLimit = 128 + if (reserved) 16 else 0
        val byteLimit = MAX_QUEUED_CONTROL_BYTES + if (reserved) 512L * 1024 else 0
        val deadline = nowMs() + policy.enqueueTimeoutMs
        var congested = false
        synchronized(lock) {
            if (closed) throw IOException("Conexão encerrada.")
            when (type) {
                "screen" -> { frame = bytes; lock.notifyAll(); return }
                "ping" -> { pingPending = true; lock.notifyAll(); return }
                "pong" -> { pongPending = true; lock.notifyAll(); return }
            }
            if (type == "state" && message.opt("sharing") == false) frame = null
            val tail = outgoing.peekLast()
            if (key != null && tail != null && tail.key == key && outgoingBytes - tail.bytes.size + bytes.size <= byteLimit) {
                outgoing.removeLast()
                outgoing.addLast(Pending(bytes, key))
                outgoingBytes += bytes.size - tail.bytes.size
                lock.notifyAll()
                return
            }
            while (outgoing.size >= countLimit || outgoingBytes + bytes.size > byteLimit) {
                if (disposable) return // absolute positions can be superseded
                val remaining = deadline - nowMs()
                if (remaining <= 0 || !started || Thread.currentThread() === writer) {
                    congested = true
                    break
                }
                try { lock.wait(remaining) }
                catch (_: InterruptedException) {
                    Thread.currentThread().interrupt()
                    throw IOException("Envio cancelado.")
                }
                if (closed) throw IOException("Conexão encerrada.")
            }
            if (!congested) {
                outgoing.addLast(Pending(bytes, key))
                outgoingBytes += bytes.size
                lock.notifyAll()
            }
        }
        if (congested) {
            val reason = "Rede sem capacidade para os comandos pendentes. Verifique a rede e conecte novamente."
            close(reason)
            throw IOException(reason)
        }
    }

    private fun writeLoop() {
        try {
            while (!closed) {
                val bytes = synchronized(lock) {
                    while (!closed && !pongPending && !pingPending && outgoing.isEmpty() && frame == null) lock.wait()
                    when {
                        closed -> null
                        pongPending -> { pongPending = false; JsonWire.encode(JSONObject().put("type", "pong")) }
                        pingPending -> { pingPending = false; JsonWire.encode(JSONObject().put("type", "ping")) }
                        outgoing.isNotEmpty() -> outgoing.removeFirst().bytes.also {
                            outgoingBytes -= it.size; lock.notifyAll()
                        }
                        else -> frame.also { frame = null }
                    }
                } ?: return
                writeProgressAt = nowMs()
                output.writeInt(bytes.size)
                writtenBytes.addAndGet(4)
                writeProgressAt = nowMs()
                var offset = 0
                while (offset < bytes.size && !closed) {
                    val count = minOf(64 * 1024, bytes.size - offset)
                    // Java OutputStream writes the complete range or throws. Never
                    // replay a range after an exception with unknown partial progress.
                    output.write(bytes, offset, count)
                    writtenBytes.addAndGet(count.toLong())
                    offset += count
                    writeProgressAt = nowMs()
                }
                if (closed) return
                output.flush()
                writeProgressAt = 0L
            }
        } catch (exc: Exception) {
            close(exc.message ?: "Falha ao enviar mensagem.")
        } finally { writeProgressAt = 0L }
    }

    private fun readLoop() {
        val packets = PacketReader(input) { readBytes.addAndGet(it.toLong()) }
        try {
            while (!closed) {
                val message = try { packets.read() }
                    catch (_: SocketTimeoutException) { continue }
                lastReceivedAt = nowMs() // incomplete trickles do not keep a session alive
                when (message.getString("type")) {
                    "ping" -> send(JSONObject().put("type", "pong"))
                    "pong" -> Unit
                    else -> onMessage(message)
                }
            }
        } catch (exc: Exception) {
            close(exc.message ?: "Conexão encerrada.")
        }
    }

    private fun heartbeatLoop() {
        var nextPing = nowMs() + policy.heartbeatMs
        val poll = minOf(1_000L, policy.heartbeatMs, maxOf(5L, policy.idleTimeoutMs / 4))
        try {
            while (!closed) {
                Thread.sleep(poll)
                if (closed) return
                val now = nowMs()
                if (now - lastReceivedAt >= policy.idleTimeoutMs) {
                    close("Sem resposta do outro dispositivo por tempo prolongado. Verifique a rede e reconecte.")
                    return
                }
                val writing = writeProgressAt
                if (writing != 0L && now - writing >= policy.idleTimeoutMs) {
                    close("Envio sem progresso por tempo prolongado. Verifique a rede e reconecte.")
                    return
                }
                if (now >= nextPing) {
                    send(JSONObject().put("type", "ping"))
                    nextPing = now + policy.heartbeatMs
                }
            }
        } catch (_: InterruptedException) {
            // close wakes this sleeper immediately.
        } catch (exc: Exception) {
            close(exc.message ?: "Conexão encerrada.")
        }
    }

    fun close(reason: String = "Conexão encerrada.") {
        if (!ended.compareAndSet(false, true)) return
        synchronized(lock) {
            outgoing.clear(); outgoingBytes = 0L; frame = null
            pingPending = false; pongPending = false
            lock.notifyAll()
        }
        heartbeat?.let { if (it !== Thread.currentThread()) it.interrupt() }
        try { sock.shutdownInput() } catch (_: Exception) { }
        try { sock.shutdownOutput() } catch (_: Exception) { }
        try { sock.close() } catch (_: Exception) { }
        try { onClose(reason) } catch (_: Exception) { }
    }

    private fun daemon(name: String, block: () -> Unit) = Thread(block, name).apply { isDaemon = true }
    private fun nowMs() = System.nanoTime() / 1_000_000

    companion object {
        private const val MAX_QUEUED_CONTROL_BYTES = 8L * 1024 * 1024
        private val FILE_TYPES = setOf("file_offer", "file_accept", "file_chunk", "file_ack", "file_end",
            "file_done", "file_cancel", "file_request", "file_request_result", "file_request_cancel")
    }
}

internal object NetDeadlines {
    private val executor = ScheduledThreadPoolExecutor(1) { task ->
        Thread(task, "MiniAnyDesk-connection-deadline").apply { isDaemon = true }
    }.apply { removeOnCancelPolicy = true }

    fun arm(milliseconds: Long, close: () -> Unit) =
        executor.schedule({ close() }, milliseconds, TimeUnit.MILLISECONDS)
}

object AndroidNet {
    const val DEFAULT_PORT = 64321
    private val resolvers = ThreadPoolExecutor(2, 2, 30L, TimeUnit.SECONDS,
        ArrayBlockingQueue<Runnable>(4), { task ->
            Thread(task, "MiniAnyDesk-connect-dns").apply { isDaemon = true }
        }, ThreadPoolExecutor.AbortPolicy())
    @Volatile internal var addressResolver: (String) -> Array<InetAddress> = { InetAddress.getAllByName(it) }

    private fun resolve(host: String, cancelled: AtomicBoolean?, authenticationDeadline: Long): Array<InetAddress> {
        val octets = host.split('.')
        if (octets.size == 4 && octets.all { it.isNotEmpty() && it.all(Char::isDigit) && it.toIntOrNull() in 0..255 })
            return arrayOf(InetAddress.getByAddress(octets.map { it.toInt().toByte() }.toByteArray()))
        if (host.contains(':')) return arrayOf(InetAddress.getByName(host))
        val resolver = addressResolver
        val future = try { resolvers.submit<Array<InetAddress>> { resolver(host) } }
            catch (_: java.util.concurrent.RejectedExecutionException) { throw IOException("Há consultas de nome pendentes. Tente novamente.") }
        val deadline = minOf(authenticationDeadline, System.nanoTime() + TimeUnit.SECONDS.toNanos(8))
        try {
            while (true) {
                if (cancelled?.get() == true) throw IOException("Conexão cancelada.")
                val left = deadline - System.nanoTime()
                if (left <= 0) throw SocketTimeoutException("Tempo de consulta do endereço esgotado.")
                try { return future.get(minOf(left, TimeUnit.MILLISECONDS.toNanos(100)), TimeUnit.NANOSECONDS) }
                catch (_: TimeoutException) { }
            }
        } catch (error: ExecutionException) {
            throw (error.cause as? Exception ?: IOException("Endereço não encontrado."))
        } finally {
            if (!future.isDone) { future.cancel(false); resolvers.remove(future as Runnable) }
        }
    }

    /** Blocking worker-only connect. Certificate approval precedes credentials. */
    fun connect(
        address: String,
        port: Int,
        password: String,
        name: String,
        trust: (String) -> Boolean,
        onMessage: (JSONObject) -> Unit = {},
        onClose: (String) -> Unit = {},
        cancelled: AtomicBoolean? = null,
        authenticationTimeoutMs: Long = 180_000
    ): Peer {
        require(password.length in 8..256) { "Use uma senha com 8 a 256 caracteres." }
        require(port in 1..65535) { "Porta inválida." }
        val displayName = JsonWire.computerName(name.take(80))
        require(displayName.isNotEmpty()) { "Informe um nome sem caracteres de controle." }
        require(authenticationTimeoutMs > 0) { "Prazo de conexão inválido." }
        val host = address.trim().removeSurrounding("[", "]")
        require(host.isNotEmpty() && host.length <= 253 && host.none { it.isWhitespace() || it.code < 32 || it.code == 127 }) { "Endereço inválido." }
        val finished = AtomicBoolean(false)
        val authenticationDeadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(authenticationTimeoutMs)
        val underlying = AtomicReference<Socket?>(null)
        val connection = AtomicReference<Socket?>(null)
        val expired = AtomicBoolean(false)
        val deadline = NetDeadlines.arm(authenticationTimeoutMs) {
            if (!finished.get()) {
                expired.set(true)
                try { underlying.get()?.close() } catch (_: Exception) { }
                try { connection.get()?.close() } catch (_: Exception) { }
            }
        }
        val cancellation = if (cancelled != null) Thread({
            while (!finished.get()) {
                if (cancelled.get()) {
                    try { underlying.get()?.close() } catch (_: Exception) { }
                    try { connection.get()?.close() } catch (_: Exception) { }
                    break
                }
                try { Thread.sleep(100) } catch (_: InterruptedException) { break }
            }
        }, "MiniAnyDesk-connect-cancel").apply { isDaemon = true; start() } else null
        fun checkCancelled() {
            if (cancelled?.get() == true) throw IOException("Conexão cancelada.")
            if (expired.get()) throw SocketTimeoutException("Tempo de conexão esgotado. Tente novamente.")
        }
        try {
            checkCancelled()
            val endpoints = resolve(host, cancelled, authenticationDeadline)
            checkCancelled()
            val connectDeadline = minOf(authenticationDeadline, System.nanoTime() + TimeUnit.SECONDS.toNanos(8))
            var raw: Socket? = null
            var lastError: IOException? = null
            for (endpoint in endpoints) {
                checkCancelled()
                val left = (connectDeadline - System.nanoTime()) / 1_000_000
                if (left <= 0) throw SocketTimeoutException("Tempo de conexão esgotado.")
                val candidate = Socket()
                underlying.set(candidate); connection.set(candidate)
                try {
                    candidate.connect(InetSocketAddress(endpoint, port), left.coerceAtMost(Int.MAX_VALUE.toLong()).toInt().coerceAtLeast(1))
                    raw = candidate
                    break
                } catch (error: IOException) {
                    lastError = error
                    try { candidate.close() } catch (_: Exception) { }
                }
            }
            val transport = raw ?: throw (lastError ?: IOException("Endereço não encontrado."))
            val context = SSLContext.getInstance("TLS")
            val pinTrust = object : X509TrustManager {
                override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
                override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) = Unit
                override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) = Unit
            }
            context.init(null, arrayOf<TrustManager>(pinTrust), SecureRandom())
            val tls = context.socketFactory.createSocket(transport, host, port, true) as SSLSocket
            connection.set(tls)
            tls.enabledProtocols = JsonWire.tlsProtocols(tls.supportedProtocols)
            tls.useClientMode = true
            tls.soTimeout = 8_000
            tls.startHandshake()
            checkCancelled()
            val certificate = tls.session.peerCertificates[0] as X509Certificate
            certificate.checkValidity()
            val fingerprint = JsonWire.fingerprint(certificate)
            if (!trust(fingerprint)) throw SecurityException("Identidade do hospedeiro não aprovada.")
            checkCancelled()
            tls.soTimeout = 100_000
            val input = DataInputStream(tls.inputStream)
            val output = DataOutputStream(tls.outputStream)
            JsonWire.write(output, JSONObject().put("type", "login").put("version", JsonWire.VERSION)
                .put("name", displayName).put("password", password))
            val answer = JsonWire.read(input)
            checkCancelled()
            if (answer.getString("type") != "auth_ok")
                throw SecurityException(answer.opt("message") as? String ?: "Conexão não autorizada.")
            val version = answer.opt("version")
            require(version == null || version == JsonWire.VERSION) { "Versão de conexão incompatível." }
            return Peer(tls, onMessage, onClose).apply {
                this.fingerprint = fingerprint
                computerName = JsonWire.computerName(answer.opt("computerName"))
                start()
            }
        } catch (exc: Exception) {
            try { connection.get()?.close() } catch (_: Exception) { }
            try { underlying.get()?.close() } catch (_: Exception) { }
            throw exc
        } finally {
            finished.set(true)
            deadline.cancel(false)
            cancellation?.interrupt()
        }
    }
}

/** Explicitly approved, single-session TLS host; callbacks execute on worker threads. */
class AndroidHostServer(
    private var port: Int,
    password: String,
    private val context: SSLContext,
    private val onRequest: (String, String) -> Boolean,
    private val onConnect: (Peer, String, String) -> Unit,
    private val onDisconnect: (Peer, String) -> Unit,
    private val computerName: () -> String = { "" },
    private val authenticationTimeoutMs: Long = 180_000
) {
    private val lock = Object()
    private val passwordHash: ByteArray
    private var workers = Semaphore(4)
    private val pendingSockets = HashSet<Socket>()
    private val failed = HashMap<String, MutableList<Long>>()
    private var running = false
    private var generation = 0L
    private var pending = false
    private var active: Peer? = null
    private var listener: SSLServerSocket? = null
    private var acceptThread: Thread? = null

    init {
        require(password.length in 8..256) { "Use uma senha com 8 a 256 caracteres." }
        require(port in 0..65535) { "Porta inválida." }
        require(authenticationTimeoutMs > 0) { "Prazo de autenticação inválido." }
        passwordHash = MessageDigest.getInstance("SHA-256").digest(password.toByteArray(StandardCharsets.UTF_8))
    }

    fun start(): Int = synchronized(lock) {
        if (running) return port
        val server = context.serverSocketFactory.createServerSocket() as SSLServerSocket
        try {
            server.reuseAddress = true
            server.enabledProtocols = JsonWire.tlsProtocols(server.supportedProtocols)
            AndroidAlpn.advertise(server)
            server.needClientAuth = false
            server.bind(InetSocketAddress(port), 4)
            server.soTimeout = 500
        } catch (exc: Exception) {
            server.close()
            throw exc
        }
        port = server.localPort
        listener = server
        generation++
        val epoch = generation
        val permits = Semaphore(4)
        workers = permits
        running = true
        acceptThread = Thread({ acceptLoop(server, epoch, permits) }, "MiniAnyDesk-host-accept").apply {
            isDaemon = true
            start()
        }
        port
    }

    private fun acceptLoop(server: SSLServerSocket, epoch: Long, permits: Semaphore) {
        while (true) {
            synchronized(lock) { if (!running || epoch != generation) return }
            val sock = try { server.accept() as SSLSocket }
                catch (_: SocketTimeoutException) { continue }
                catch (_: Exception) { return }
            if (!permits.tryAcquire()) { sock.close(); continue }
            synchronized(lock) {
                if (!running || epoch != generation) { permits.release(); sock.close(); return }
                pendingSockets.add(sock)
            }
            Thread({ handle(sock, epoch, permits) }, "MiniAnyDesk-host-login").apply { isDaemon = true; start() }
        }
    }

    private fun handle(sock: SSLSocket, epoch: Long, permits: Semaphore) {
        var ownsPending = false
        var peer: Peer? = null
        val deadline = NetDeadlines.arm(authenticationTimeoutMs) { try { sock.close() } catch (_: Exception) { } }
        try {
            sock.soTimeout = 8_000
            sock.startHandshake()
            val output = DataOutputStream(sock.outputStream)
            if (AndroidAlpn.selected(sock) == JsonWire.STATUS_ALPN) {
                val busy = synchronized(lock) {
                    if (!running || epoch != generation) return
                    pending || active?.closed == false
                }
                // ALPN alone asks for status. Exit before reading login or
                // changing failed attempts, pending approval, or active Peer.
                sock.soTimeout = 3_000
                JsonWire.write(output, JSONObject().put("type", "status").put("agent", "MiniAnyDesk")
                    .put("version", JsonWire.APP_VERSION).put("name", statusName())
                    .put("online", true).put("busy", busy))
                return
            }
            sock.soTimeout = 100_000
            val input = DataInputStream(sock.inputStream)
            val login = JsonWire.read(input)
            val password = login.opt("password") as? String
            val name = login.opt("name") as? String
            val ip = sock.inetAddress.hostAddress ?: ""
            val now = System.nanoTime() / 1_000_000
            val allowed = synchronized(lock) {
                val previous = failed.entries.iterator()
                while (previous.hasNext()) {
                    val entry = previous.next()
                    entry.value.removeAll { now - it >= 60_000 }
                    if (entry.value.isEmpty()) previous.remove()
                }
                val attempts = failed[ip] ?: ArrayList()
                val valid = attempts.size < 5 && login.opt("type") == "login" &&
                    password != null && password.length in 8..256 && name != null && name.length in 1..80 &&
                    JsonWire.computerName(name) == name &&
                    MessageDigest.isEqual(passwordHash, MessageDigest.getInstance("SHA-256")
                        .digest(password.toByteArray(StandardCharsets.UTF_8)))
                if (!valid && attempts.size < 5) {
                    attempts.add(now)
                    if (!failed.containsKey(ip) && failed.size >= MAX_FAILED_ADDRESSES) {
                        failed.minByOrNull { it.value.lastOrNull() ?: Long.MIN_VALUE }?.key?.let { failed.remove(it) }
                    }
                    failed[ip] = attempts
                }
                valid
            }
            if (!allowed) {
                JsonWire.write(output, JSONObject().put("type", "error")
                    .put("message", "Senha incorreta ou limite de tentativas atingido."))
                return
            }
            val busy = synchronized(lock) {
                if (!running || epoch != generation || pending || active?.closed == false) true
                else { pending = true; ownsPending = true; false }
            }
            if (busy) {
                JsonWire.write(output, JSONObject().put("type", "error")
                    .put("message", "Hospedeiro ocupado. Há uma sessão ou aprovação em andamento."))
                return
            }
            if (!onRequest(name!!, ip)) {
                JsonWire.write(output, JSONObject().put("type", "error")
                    .put("message", "O usuário do hospedeiro recusou a conexão."))
                return
            }
            synchronized(lock) {
                if (!running || epoch != generation) return
                val newPeer = Peer(sock)
                newPeer.onClose = { reason -> disconnected(newPeer, reason) }
                peer = newPeer
                active = newPeer
            }
            val acceptedPeer = peer!!
            onConnect(acceptedPeer, name, ip)
            val cancelled = synchronized(lock) { acceptedPeer.closed || !running || epoch != generation }
            if (cancelled) { acceptedPeer.close("Hospedeiro parou de receber conexões."); return }
            JsonWire.write(output, JSONObject().put("type", "auth_ok").put("version", JsonWire.VERSION)
                .put("computerName", statusName()).put("agentVersion", JsonWire.APP_VERSION))
            acceptedPeer.start()
        } catch (exc: Exception) {
            peer?.close(exc.message ?: "Conexão encerrada.")
        } finally {
            synchronized(lock) {
                if (ownsPending && epoch == generation) pending = false
                pendingSockets.remove(sock)
            }
            if (peer == null) try { sock.close() } catch (_: Exception) { }
            deadline.cancel(false)
            permits.release()
        }
    }

    private fun disconnected(peer: Peer, reason: String) {
        synchronized(lock) { if (active === peer) active = null }
        try { onDisconnect(peer, reason) } catch (_: Exception) { }
    }

    private fun statusName(): String = try { JsonWire.computerName(computerName()) } catch (_: Exception) { "" }

    fun stop() {
        val sockets: List<Socket>
        val server: SSLServerSocket?
        val peer: Peer?
        val thread: Thread?
        synchronized(lock) {
            running = false
            generation++
            pending = false
            server = listener
            listener = null
            peer = active
            active = null
            sockets = pendingSockets.toList()
            pendingSockets.clear()
            thread = acceptThread
        }
        try { server?.close() } catch (_: Exception) { }
        peer?.close("Hospedeiro parou de receber conexões.")
        for (sock in sockets) try { sock.close() } catch (_: Exception) { }
        if (thread !== Thread.currentThread()) try { thread?.join(1_000) } catch (_: InterruptedException) { }
    }

    companion object {
        private const val MAX_FAILED_ADDRESSES = 1024
    }
}
