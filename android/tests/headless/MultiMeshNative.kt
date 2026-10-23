package com.easytier.jni

import java.net.InetAddress
import java.net.InetSocketAddress
import java.security.KeyStore
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLServerSocket
import javax.net.ssl.SSLSocket

/** Test double for the exact upstream instance-name/retain-set contract. */
object EasyTierJNI {
    data class Instance(val name: String, val network: String, val socket: SSLServerSocket)
    val live = ConcurrentHashMap<String, Instance>()
    val failNextRun = AtomicBoolean(false)
    private val tls by lazy {
        val store = KeyStore.getInstance("PKCS12")
        java.io.FileInputStream(System.getProperty("mesh.test.keystore")).use { store.load(it, "test-only".toCharArray()) }
        val keys = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()).apply {
            init(store, "test-only".toCharArray())
        }
        SSLContext.getInstance("TLS").apply { init(keys.keyManagers, null, null) }
    }
    fun ensureLoaded() = Unit
    fun parseConfig(config: String): Int = if (config.contains("no_tun = true")) 0 else -1
    fun runNetworkInstance(config: String): Int {
        fun field(name: String) = Regex("(?m)^$name = \"([^\"]+)\"").find(config)!!.groupValues[1]
        val name = field("instance_name")
        if (live.containsKey(name)) return -1
        val port = field("bind_addr").substringAfterLast(':').toInt()
        val listener = (tls.serverSocketFactory.createServerSocket() as SSLServerSocket).apply {
            reuseAddress = true
            bind(InetSocketAddress(InetAddress.getByName("127.0.0.1"), port))
        }
        val instance = Instance(name, field("network_name"), listener)
        live[name] = instance
        Thread({
            while (!listener.isClosed) {
                try { (listener.accept() as SSLSocket).use { it.soTimeout = 1500; it.startHandshake() } }
                catch (_: Exception) { }
            }
        }, "mesh-test-forward").apply { isDaemon = true; start() }
        return if (failNextRun.getAndSet(false)) -1 else 0
    }
    fun retainNetworkInstance(names: Array<String>?): Int {
        val keep = names?.toSet().orEmpty()
        for ((name, instance) in live.entries.toList()) if (name !in keep) {
            if (live.remove(name, instance)) runCatching { instance.socket.close() }
        }
        return 0
    }
}
