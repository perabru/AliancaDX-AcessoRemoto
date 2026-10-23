package br.com.perabru.minianydesk

import android.content.Context
import com.easytier.jni.EasyTierJNI
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager
import java.security.cert.X509Certificate

object MeshTransportHarness {
    @JvmStatic fun main(args: Array<String>) {
        val registryChecks = NativeMeshInstancesHarness.runChecks()
        var checks = 0
        fun expect(value: Boolean) { check(value); checks++ }
        fun waitFor(condition: () -> Boolean) {
            val deadline = System.nanoTime() + 6_000_000_000L
            while (!condition()) { check(System.nanoTime() < deadline) { "Timed out waiting for native lifecycle" }; Thread.sleep(10) }
        }
        val pairingA = MeshPairing.parseCode(args[0])
        val pairingB = MeshPairing.parseCode(args[1])
        val context = Context()
        val a = MeshTransport(context)
        val b = MeshTransport(context)
        val c = MeshTransport(context)
        val cancel = AtomicBoolean(false)
        try {
            a.start(pairingA) { }
            b.start(pairingB) { }
            val endpointA = a.awaitReady(8000, cancel)
            val endpointB = b.awaitReady(8000, cancel)
            expect(endpointA.address == "127.0.0.1" && endpointB.address == "127.0.0.1" && endpointA.port != endpointB.port)
            expect(EasyTierJNI.live.size == 2 && EasyTierJNI.live.values.map { it.network }.toSet().size == 2)
            val nameB = EasyTierJNI.live.values.single { it.socket.localPort == endpointB.port }.name
            expect(EasyTierJNI.live.keys.all { it.startsWith("MiniAnyDesk-android-") } && EasyTierJNI.live.keys.toSet().size == 2)
            a.stop()
            waitFor { EasyTierJNI.live.keys == setOf(nameB) }
            expect(b.latestStatus.state == "ready" && b.awaitReady(1000, cancel) == endpointB)
            // Prove B's real loopback TLS listener survived A's cleanup.
            val tls = SSLContext.getInstance("TLS").apply {
                init(null, arrayOf<TrustManager>(object : X509TrustManager {
                    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
                    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) = Unit
                    override fun getAcceptedIssuers() = emptyArray<X509Certificate>()
                }), null)
            }
            (tls.socketFactory.createSocket(endpointB.address, endpointB.port) as SSLSocket).use {
                it.soTimeout = 2000; it.startHandshake(); expect(it.session.peerCertificates.isNotEmpty())
            }
            EasyTierJNI.failNextRun.set(true)
            c.start(pairingA) { }
            expect(runCatching { c.awaitReady(6000, cancel) }.isFailure)
            waitFor { EasyTierJNI.live.keys == setOf(nameB) }
            expect(b.latestStatus.state == "ready")
            a.start(pairingA) { }
            val restarted = a.awaitReady(8000, cancel)
            expect(restarted != endpointB && EasyTierJNI.live.size == 2)
            b.close()
            waitFor { EasyTierJNI.live.size == 1 }
            expect(a.latestStatus.state == "ready" && a.awaitReady(1000, cancel) == restarted)
        } finally {
            a.close(); b.close(); c.close()
            waitFor { EasyTierJNI.live.isEmpty() }
        }
        expect(EasyTierJNI.live.isEmpty())
        println("Production MeshTransport with fake FFI and real loopback TLS: $checks checks passed")
        println("Total multi-network checks: ${registryChecks + checks}")
    }
}
