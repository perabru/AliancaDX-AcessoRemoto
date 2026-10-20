package br.com.perabru.minianydesk

import java.io.EOFException
import java.io.IOException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.Locale
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.ExecutionException
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ScheduledThreadPoolExecutor
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

data class PresenceResult(
    val state: String,
    val detail: String,
    val fingerprint: String? = null,
    val computerName: String? = null,
    val busy: Boolean? = null,
    val version: String? = null
)

/** Read-only availability: ALPN requests status; no application bytes are sent. */
object PresenceProbe {
    private const val MAX_STATUS_BYTES = 2048
    private val resolverId = AtomicInteger()
    private val resolvers = ThreadPoolExecutor(4, 4, 0, TimeUnit.MILLISECONDS,
        ArrayBlockingQueue<Runnable>(4), { task ->
            Thread(task, "MiniAnyDesk-status-dns-${resolverId.incrementAndGet()}").apply { isDaemon = true }
        }, ThreadPoolExecutor.AbortPolicy())
    private val deadlines = ScheduledThreadPoolExecutor(1) { task ->
        Thread(task, "MiniAnyDesk-status-deadline").apply { isDaemon = true }
    }.apply { removeOnCancelPolicy = true }

    // JVM regression seam; production leaves the platform resolver unchanged.
    @Volatile internal var addressResolver: (String) -> Array<InetAddress> = { InetAddress.getAllByName(it) }

    fun probe(address: String, port: Int, expectedFingerprint: String? = null, timeoutMs: Int = 3000): PresenceResult {
        var fingerprint: String? = null
        val connection = AtomicReference<Socket?>(null)
        val underlying = AtomicReference<Socket?>(null)
        var deadlineTask: java.util.concurrent.ScheduledFuture<*>? = null
        try {
            val host = address.trim().removeSurrounding("[", "]")
            require(host.isNotEmpty() && host.length <= 253 && host.none { it.code < 32 || it.code == 127 }) { "Endereço inválido." }
            require(port in 1..65535 && timeoutMs in 1..60000) { "Porta ou prazo de consulta inválido." }
            val expected = expectedFingerprint?.let { normalizeFingerprint(it) }
            val deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMs.toLong())
            val addresses = resolve(host, deadline)
            deadlineTask = deadlines.schedule({
                // Closing the transport also bounds a trickling TLS handshake.
                try { underlying.get()?.close() } catch (_: Exception) { }
                try { connection.get()?.close() } catch (_: Exception) { }
            }, remainingNanos(deadline), TimeUnit.NANOSECONDS)
            var raw: Socket? = null
            var lastError: IOException? = null
            for (endpoint in addresses) {
                val candidate = Socket()
                underlying.set(candidate)
                connection.set(candidate)
                try {
                    candidate.connect(InetSocketAddress(endpoint, port), remainingMillis(deadline))
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
            tls.useClientMode = true
            tls.enabledProtocols = JsonWire.tlsProtocols(tls.supportedProtocols)
            val advertised = AndroidAlpn.advertise(tls)
            tls.soTimeout = remainingMillis(deadline)
            tls.startHandshake()
            remainingNanos(deadline)
            fingerprint = JsonWire.fingerprint(tls.session.peerCertificates[0] as X509Certificate)
            if (expected != null && !MessageDigest.isEqual(expected.toByteArray(Charsets.US_ASCII),
                    fingerprint.toByteArray(Charsets.US_ASCII))) {
                return PresenceResult("unverified", "O certificado mudou; confira a identidade antes de conectar.", fingerprint)
            }
            if (!advertised || AndroidAlpn.selected(tls) != JsonWire.STATUS_ALPN) {
                // API26–28/legacy fallback: EOF before login is not an auth attempt.
                return if (expected != null)
                    PresenceResult("online", "Hospedeiro acessível com certificado conhecido; versão sem consulta de nome.", fingerprint)
                else PresenceResult("unverified", "Porta TLS acessível; a versão não confirma disponibilidade do AliançaDX.", fingerprint)
            }
            val message = JsonWire.decode(readStatus(tls, deadline))
            val name = message.opt("name") as? String
            val version = message.opt("version") as? String
            val busy = message.opt("busy") as? Boolean
            require(message.opt("type") == "status" && message.opt("agent") == "MiniAnyDesk" &&
                message.opt("online") is Boolean && message.opt("online") == true && busy != null &&
                name != null && JsonWire.computerName(name) == name &&
                version != null && version.length in 1..32 && JsonWire.computerName(version) == version) {
                "Resposta de disponibilidade inválida."
            }
            return PresenceResult("online", if (expected == null)
                "Hospedeiro acessível; identidade será conferida ao conectar"
                else if (busy) "Hospedeiro online, com uma sessão ou aprovação em andamento."
                else "Hospedeiro online e disponível.", fingerprint, name, busy, version)
        } catch (_: SocketTimeoutException) {
            return PresenceResult("offline", "Tempo de consulta esgotado; tente atualizar novamente.", fingerprint)
        } catch (_: TimeoutException) {
            return PresenceResult("offline", "Tempo de consulta esgotado; tente atualizar novamente.", fingerprint)
        } catch (_: RejectedExecutionException) {
            return PresenceResult("offline", "Há consultas de nome pendentes; tente atualizar novamente.", fingerprint)
        } catch (error: Exception) {
            return PresenceResult("offline", "O hospedeiro não respondeu a uma consulta TLS válida.", fingerprint)
        } finally {
            deadlineTask?.cancel(false)
            try { underlying.get()?.close() } catch (_: Exception) { }
            try { connection.get()?.close() } catch (_: Exception) { }
        }
    }

    private fun normalizeFingerprint(value: String): String {
        val normalized = value.filterNot { it.isWhitespace() || it == ':' }.uppercase(Locale.ROOT)
        require(normalized.length == 64 && normalized.all { it in '0'..'9' || it in 'A'..'F' }) { "Certificado salvo inválido." }
        return normalized
    }

    private fun resolve(host: String, deadline: Long): Array<InetAddress> {
        val octets = host.split('.')
        if (octets.size == 4 && octets.all { it.isNotEmpty() && it.all(Char::isDigit) && it.toIntOrNull() in 0..255 }) {
            return arrayOf(InetAddress.getByAddress(octets.map { it.toInt().toByte() }.toByteArray()))
        }
        if (host.contains(':')) return arrayOf(InetAddress.getByName(host)) // Literal IPv6 does not query DNS.
        val resolver = addressResolver
        val future = resolvers.submit<Array<InetAddress>> { resolver(host) }
        try {
            return future.get(remainingNanos(deadline), TimeUnit.NANOSECONDS)
        } catch (error: ExecutionException) {
            throw (error.cause as? Exception ?: IOException("Nome não encontrado."))
        } finally {
            if (!future.isDone) {
                future.cancel(false)
                resolvers.remove(future as Runnable)
            }
        }
    }

    private fun remainingNanos(deadline: Long): Long {
        val remaining = deadline - System.nanoTime()
        if (remaining <= 0) throw SocketTimeoutException("Tempo de consulta esgotado.")
        return remaining
    }

    private fun remainingMillis(deadline: Long): Int =
        ((remainingNanos(deadline) + 999999) / 1000000).coerceIn(1, Int.MAX_VALUE.toLong()).toInt()

    private fun exact(socket: SSLSocket, length: Int, deadline: Long): ByteArray {
        val bytes = ByteArray(length)
        val input = socket.inputStream
        var offset = 0
        while (offset < length) {
            socket.soTimeout = remainingMillis(deadline)
            val count = input.read(bytes, offset, length - offset)
            if (count < 0) throw EOFException("O hospedeiro não respondeu à consulta.")
            offset += count
        }
        return bytes
    }

    private fun readStatus(socket: SSLSocket, deadline: Long): ByteArray {
        val length = ByteBuffer.wrap(exact(socket, 4, deadline)).int
        require(length in 1..MAX_STATUS_BYTES) { "Resposta de disponibilidade inválida." }
        return exact(socket, length, deadline)
    }
}
