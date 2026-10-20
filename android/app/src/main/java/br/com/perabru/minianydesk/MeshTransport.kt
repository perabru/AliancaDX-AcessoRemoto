package br.com.perabru.minianydesk

import android.content.Context
import android.os.Build
import com.easytier.jni.EasyTierJNI
import java.io.IOException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

data class MeshEndpoint(val address: String, val port: Int)
data class MeshStatus(val state: String, val message: String)

/** Owns one app-only tunnel. A ready route never grants permission to the PC. */
class MeshTransport private constructor(@Suppress("UNUSED_PARAMETER") context: Context,
                                       private val networkOptions: NetworkOptions?) : AutoCloseable {
    constructor(context: Context) : this(context, null)
    internal constructor(context: Context, peers: List<String>, forceRelay: Boolean) :
        this(context, NetworkOptions(peers.toList(), forceRelay))

    private data class NetworkOptions(val peers: List<String>, val forceRelay: Boolean)
    private class Run(val pairing: MeshPairing, val callback: (MeshStatus) -> Unit) {
        val cancelled = AtomicBoolean(false)
        val probe = AtomicReference<Socket?>(null)
        var endpoint: MeshEndpoint? = null
        var failure: IOException? = null
    }

    private val stateLock = Object()
    private val executor = Executors.newSingleThreadExecutor { task ->
        Thread(task, "MiniAnyDesk-private-network").apply { isDaemon = true }
    }
    private var active: Run? = null
    private var closed = false
    @Volatile var latestStatus = MeshStatus("stopped", "Conexão pela internet parada.")
        private set

    fun start(pairing: MeshPairing, onStatus: (MeshStatus) -> Unit) {
        val run: Run
        val previous: Run?
        synchronized(stateLock) {
            check(!closed) { "A conexão foi encerrada. Abra o aplicativo novamente." }
            previous = active
            previous?.cancelled?.set(true)
            run = Run(pairing, onStatus)
            active = run
            latestStatus = MeshStatus("starting", "Preparando a conexão privada…")
            stateLock.notifyAll()
        }
        previous?.probe?.getAndSet(null)?.let { runCatching { it.close() } }
        deliver(run, latestStatus)
        try { executor.execute { runNetwork(run) } }
        catch (_: RejectedExecutionException) { fail(run, "A conexão foi encerrada. Tente novamente.") }
    }

    fun awaitReady(timeoutMs: Long = 45_000, cancel: AtomicBoolean): MeshEndpoint {
        require(timeoutMs in 1..120_000)
        val deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMs)
        synchronized(stateLock) {
            val run = active ?: throw IOException("Cole o código do Host antes de conectar.")
            while (true) {
                if (closed || active !== run || cancel.get() || run.cancelled.get())
                    throw IOException("Conexão cancelada.")
                run.failure?.let { throw it }
                run.endpoint?.let { return it }
                val remaining = deadline - System.nanoTime()
                if (remaining <= 0) throw IOException("O Host ainda não foi encontrado. Confira se o PC está aberto, disponível e com internet; tente novamente.")
                try { stateLock.wait(minOf(100L, TimeUnit.NANOSECONDS.toMillis(remaining).coerceAtLeast(1))) }
                catch (_: InterruptedException) { Thread.currentThread().interrupt(); throw IOException("Conexão cancelada.") }
            }
        }
    }

    fun stop() {
        val run: Run?
        synchronized(stateLock) {
            run = active
            active = null
            run?.cancelled?.set(true)
            latestStatus = MeshStatus("stopped", "Conexão pela internet parada.")
            stateLock.notifyAll()
        }
        run?.probe?.getAndSet(null)?.let { runCatching { it.close() } }
        // The worker releases the native instance in finally. Never wait on the UI.
    }

    override fun close() {
        synchronized(stateLock) { if (closed) return; closed = true }
        stop()
        executor.shutdown()
    }

    private fun isCurrent(run: Run): Boolean = synchronized(stateLock) {
        !closed && active === run && !run.cancelled.get()
    }

    private fun publish(run: Run, status: MeshStatus, endpoint: MeshEndpoint? = null) {
        synchronized(stateLock) {
            if (!isCurrent(run)) return
            run.endpoint = endpoint
            latestStatus = status
            stateLock.notifyAll()
        }
        deliver(run, status)
    }

    private fun deliver(run: Run, status: MeshStatus) {
        if (isCurrent(run)) runCatching { run.callback(status) }
    }

    private fun fail(run: Run, message: String) {
        synchronized(stateLock) {
            if (!isCurrent(run)) return
            run.failure = IOException(message)
            run.endpoint = null
            latestStatus = MeshStatus("error", message)
            stateLock.notifyAll()
        }
        deliver(run, latestStatus)
    }

    private fun runNetwork(run: Run) {
        try {
            if (!isCurrent(run)) return
            val port = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).use { it.localPort }
            val random = SecureRandom()
            var overlayIp: String
            do { overlayIp = "10.126.${random.nextInt(254) + 1}.${random.nextInt(254) + 1}" }
            while (overlayIp == "10.126.126.1")
            val config = if (networkOptions == null) MeshConfig.build(run.pairing, port, overlayIp)
                else MeshConfig.build(run.pairing, port, overlayIp,
                    forceRelay = networkOptions.forceRelay, peers = networkOptions.peers)
            synchronized(nativeLock) {
                if (!isCurrent(run)) return
                EasyTierJNI.ensureLoaded()
                if (!isCurrent(run)) return
                check(EasyTierJNI.retainNetworkInstance(null) == 0)
                nativeOwner = run
                check(EasyTierJNI.parseConfig(config) == 0)
                if (!isCurrent(run)) return
                check(EasyTierJNI.runNetworkInstance(config) == 0)
            }
            val endpoint = MeshEndpoint("127.0.0.1", port)
            while (isCurrent(run)) {
                val verified = try { verifyHost(run, endpoint) }
                    catch (_: IdentityMismatch) {
                        fail(run, "A identidade do PC não corresponde ao código. Copie um convite novo diretamente no Host.")
                        return
                    }
                if (!isCurrent(run)) return
                publish(run,
                    if (verified) MeshStatus("ready", "PC encontrado. Confira a senha e aguarde a aprovação no Host.")
                    else MeshStatus("connecting", "Procurando o PC pela internet… mantenha o Host aberto."),
                    if (verified) endpoint else null)
                for (index in 0 until 20) {
                    if (!isCurrent(run)) return
                    Thread.sleep(100)
                }
            }
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        } catch (_: LinkageError) {
            fail(run, "O componente de rede não pôde abrir. Este APK precisa de Android de 64 bits; reinstale o pacote completo.")
        } catch (_: Exception) {
            // Native exception messages may contain configuration secrets. Do not
            // log or expose them, even on a failed startup.
            fail(run, "Não foi possível preparar a rede privada. Confira a internet e tente novamente.")
        } finally {
            run.probe.getAndSet(null)?.let { runCatching { it.close() } }
            synchronized(nativeLock) {
                if (nativeOwner === run) {
                    runCatching { EasyTierJNI.retainNetworkInstance(null) }
                    nativeOwner = null
                }
            }
        }
    }

    private class IdentityMismatch : IOException()

    private fun verifyHost(run: Run, endpoint: MeshEndpoint): Boolean {
        var socket: Socket? = null
        return try {
            val context = SSLContext.getInstance("TLS").apply {
                init(null, arrayOf<TrustManager>(object : X509TrustManager {
                    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
                    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
                    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
                }), SecureRandom())
            }
            val raw = Socket()
            socket = raw
            run.probe.set(raw)
            if (!isCurrent(run)) return false
            raw.connect(InetSocketAddress(endpoint.address, endpoint.port), 1200)
            raw.soTimeout = 1800
            val secure = context.socketFactory.createSocket(raw, endpoint.address, endpoint.port, true) as SSLSocket
            socket = secure
            run.probe.set(secure)
            secure.soTimeout = 1800
            secure.enabledProtocols = secure.supportedProtocols.filter { it == "TLSv1.2" || it == "TLSv1.3" }.toTypedArray()
            if (Build.VERSION.SDK_INT >= 29) {
                secure.sslParameters = secure.sslParameters.apply { applicationProtocols = arrayOf("minianydesk-status/1") }
            }
            secure.startHandshake()
            val actual = MessageDigest.getInstance("SHA-256").digest(secure.session.peerCertificates[0].encoded)
            val expected = run.pairing.fingerprint.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
            if (!MessageDigest.isEqual(actual, expected)) throw IdentityMismatch()
            // No login, password, or application bytes are sent by a route probe.
            isCurrent(run)
        } catch (error: IdentityMismatch) { throw error }
          catch (_: IOException) { false }
          finally { run.probe.compareAndSet(socket, null); runCatching { socket?.close() } }
    }

    private companion object {
        val nativeLock = Any()
        var nativeOwner: Any? = null
    }
}
