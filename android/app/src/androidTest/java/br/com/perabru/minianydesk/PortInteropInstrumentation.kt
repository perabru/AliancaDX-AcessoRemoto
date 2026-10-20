package br.com.perabru.minianydesk

import android.app.Activity
import android.app.Instrumentation
import android.content.ContentValues
import android.content.ContentResolver
import android.content.Context
import android.content.ContextWrapper
import android.content.pm.ProviderInfo
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import android.provider.DocumentsContract
import android.util.Base64
import com.easytier.jni.EasyTierJNI
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/** Framework instrumentation: production Android runtime + real JNI + local forced relay. */
class PortInteropInstrumentation : Instrumentation() {
    private lateinit var arguments: Bundle
    private val report = JSONObject().put("scope", "Android emulator API35 x86_64, real native core and local forced relay")
        .put("physical_device", false).put("cross_isp", false).put("public_relays", false)
    private val passed = JSONArray()

    override fun onCreate(arguments: Bundle?) {
        this.arguments = arguments ?: Bundle()
        super.onCreate(arguments)
        start()
    }

    private fun stage(name: String) {
        sendStatus(1, Bundle().apply { putString("stage", name) })
    }
    private fun passed(name: String) { passed.put(name); stage("passed:$name") }
    private fun await(latch: CountDownLatch, seconds: Long, what: String) {
        check(latch.await(seconds, TimeUnit.SECONDS)) { "$what timeout" }
    }

    override fun onStart() {
        var mesh: MeshTransport? = null
        var peer: Peer? = null
        var transfers: FileTransfers? = null
        try {
            val pairing = MeshPairing.parseCode(arguments.getString("pairing") ?: error("Missing lab pairing"))
            val relay = arguments.getString("relay") ?: error("Missing lab relay")
            require(relay.matches(Regex("tcp://(10\\.0\\.2\\.2|127\\.0\\.0\\.1):[0-9]{1,5}"))) {
                "Only a synthetic emulator lab relay is accepted"
            }
            report.put("outer_carrier", arguments.getString("carrier") ?: "emulator_guest_tcp")
            stage("android-keystore-vault")
            val vault = MeshCodeVault(targetContext)
            vault.save(pairing)
            check(vault.load(pairing.address)?.code == pairing.code)
            val stored = File(targetContext.filesDir, "remembered_codes/${pairing.publicId}.bin")
            val encrypted = stored.readBytes()
            check(!encrypted.toString(Charsets.ISO_8859_1).contains(pairing.code))
            vault.save(pairing)
            check(!stored.readBytes().contentEquals(encrypted)) { "Keystore reused an IV" }
            val corrupted = stored.readBytes().also { it[it.lastIndex] = (it.last().toInt() xor 1).toByte() }
            stored.writeBytes(corrupted)
            check(vault.load(pairing.address) == null) { "Tampered vault code was accepted" }
            vault.save(pairing)
            vault.forget(pairing.address)
            check(vault.load(pairing.address) == null && !stored.exists())
            passed("android_keystore_encrypted_roundtrip_random_iv_tamper_and_forget")
            stage("loading-native-and-starting-production-mesh")
            mesh = MeshTransport(targetContext, listOf(relay), true)
            mesh.start(pairing) { status -> stage("mesh:${status.state}") }
            val endpoint = mesh.awaitReady(120_000, AtomicBoolean())
            check(endpoint.address == "127.0.0.1")
            passed("production_mesh_real_native_forced_relay_tls_route")
            val screen = AtomicReference<JSONObject?>()
            val screenReady = CountDownLatch(1)
            val commandsDone = CountDownLatch(1)
            val transferOwner = AtomicReference<FileTransfers?>()
            val trustApproved = AtomicBoolean(false)
            peer = AndroidNet.connect(endpoint.address, endpoint.port, "12345678", "Android emulator native lab",
                trust = { fingerprint ->
                    check(fingerprint == pairing.fingerprint)
                    trustApproved.set(true)
                    true
                }, onMessage = { message ->
                    when (message.optString("type")) {
                        "screen" -> { screen.set(message); screenReady.countDown() }
                        "port_test_ack" -> commandsDone.countDown()
                        else -> transferOwner.get()?.handle(message)
                    }
                }, authenticationTimeoutMs = 40_000)
            check(trustApproved.get())
            check(peer.computerName == "Desktop Python 6.1")
            passed("android_tls_certificate_pin_then_password_and_host_consent")
            await(screenReady, 30, "JPEG frame")
            val frame = screen.get()!!
            val image = Base64.decode(frame.getString("image"), Base64.NO_WRAP)
            val bitmap = BitmapFactory.decodeByteArray(image, 0, image.size) ?: error("JPEG decode failed")
            check(bitmap.width == frame.getInt("frameWidth") && bitmap.height == frame.getInt("frameHeight"))
            bitmap.recycle()
            check(frame.getJSONArray("monitors").length() == 2)
            passed("real_android_jpeg_decode_and_multi_monitor_geometry")
            val activePeer = peer
            fun input(action: String): JSONObject = JSONObject().put("type", "input").put("action", action)
                .put("captureId", frame.getString("captureId")).put("x", 1.0).put("y", 1.0)
            activePeer.send(input("move"))
            activePeer.send(input("button").put("button", "left").put("down", true))
            activePeer.send(input("button").put("button", "left").put("down", false))
            activePeer.send(input("key").put("key", "ctrl").put("down", true))
            activePeer.send(input("key").put("key", "c").put("down", true))
            activePeer.send(input("key").put("key", "c").put("down", false))
            activePeer.send(input("key").put("key", "ctrl").put("down", false))
            activePeer.send(input("wheel").put("delta", 2))
            activePeer.send(JSONObject().put("type", "input").put("action", "release"))
            activePeer.send(JSONObject().put("type", "monitor").put("id", "2"))
            activePeer.send(JSONObject().put("type", "monitor").put("id", "all"))
            activePeer.send(JSONObject().put("type", "sharing").put("enabled", false))
            activePeer.send(JSONObject().put("type", "sharing").put("enabled", true))
            activePeer.send(JSONObject().put("type", "port_test_complete"))
            await(commandsDone, 30, "Host command dispatch")
            passed("android_pointer_keyboard_wheel_monitor_and_sharing_commands")

            // URI validation uses a registered system document authority, while every read/write
            // remains on our local synthetic provider through ContentResolver.wrap below.
            // No call is made to the external-storage provider or to user documents.
            val labAuthority = "com.android.externalstorage.documents"
            val root = DocumentsContract.buildDocumentUri(labAuthority, "root")
            // The release manifest needs no test-provider visibility declarations. Android's
            // real local provider wrapper supplies actual query/FD I/O without an exported provider.
            val provider = PortTestDocuments().apply {
                attachInfo(targetContext, ProviderInfo().apply {
                    authority = labAuthority
                    applicationInfo = targetContext.applicationInfo
                    name = PortTestDocuments::class.java.name
                })
            }
            val resolver = ContentResolver.wrap(provider)
            val transferContext = object : ContextWrapper(targetContext) {
                override fun getApplicationContext(): Context = this
                override fun getContentResolver(): ContentResolver = resolver
            }
            val suffix = System.nanoTime().toString()
            fun createDocument(name: String): Uri = resolver.insert(root,
                ContentValues().apply { put(OpenableColumns.DISPLAY_NAME, name) })!!
            val source = createDocument("source-$suffix.bin")
            check(DocumentsContract.isDocumentUri(transferContext, source)) { "Synthetic provider document URI was not recognized" }
            val bytes = ByteArray(256 * 257) { (it % 256).toByte() }
            resolver.openOutputStream(source, "w")!!.use { it.write(bytes) }
            val transferEvent = AtomicReference(CountDownLatch(1))
            val transferResult = AtomicReference<Boolean?>()
            val lastTransferMessage = AtomicReference("")
            val destination = AtomicReference<android.net.Uri?>()
            transfers = FileTransfers(transferContext, activePeer, { it() }, { _, choose ->
                val fresh = createDocument("received-$suffix.bin")
                check(DocumentsContract.isDocumentUri(transferContext, fresh)) { "Synthetic document URI was not classified as a document" }
                destination.set(fresh)
                choose(fresh)
            }, { status ->
                lastTransferMessage.set(status.message)
                if (status.success != null) {
                    transferResult.set(status.success)
                    transferEvent.get().countDown()
                }
            })
            transferOwner.set(transfers)
            check(transfers.sendFile(source))
            await(transferEvent.get(), 45, "Android to Python file")
            check(transferResult.get() == true) { lastTransferMessage.get() }
            passed("local_android_content_provider_to_python_multiblock_file")
            Thread.sleep(500)
            transferResult.set(null)
            transferEvent.set(CountDownLatch(1))
            check(transfers.receiveRequest())
            await(transferEvent.get(), 45, "Python to Android file")
            check(transferResult.get() == true) { lastTransferMessage.get() }
            val received = resolver.openInputStream(destination.get()!!)!!.use { it.readBytes() }
            check(received.contentEquals(bytes))
            passed("python_to_local_android_content_provider_multiblock_exact_bytes")
            Thread.sleep(500)
            transfers.close(); transfers = null
            activePeer.close(); peer = null
            mesh.close(); mesh = null
            stage("waiting-native-cleanup")
            val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(15)
            var empty = false
            while (System.nanoTime() < deadline) {
                val infos = EasyTierJNI.collectNetworkInfos(100)?.let(::JSONObject)
                if (infos?.optJSONObject("map")?.length() == 0) { empty = true; break }
                Thread.sleep(100)
            }
            check(empty) { "Native instance survived MeshTransport.close" }
            passed("production_mesh_close_removes_native_instance")
            report.put("result", "passed").put("passed", passed)
            finish(Activity.RESULT_OK, Bundle().apply { putString("report", report.toString()) })
        } catch (failure: Throwable) {
            // Test-only report, never dump pairing/config/arguments or native errors.
            report.put("result", "failed").put("passed", passed)
                .put("exception", failure.javaClass.simpleName)
                .put("detail", failure.message?.take(240) ?: "No detail")
                .put("failure_location", JSONArray(failure.stackTrace.take(4).map { "${it.className}.${it.methodName}:${it.lineNumber}" }))
            finish(Activity.RESULT_CANCELED, Bundle().apply { putString("report", report.toString()) })
        } finally {
            transfers?.close()
            peer?.close()
            mesh?.close()
        }
    }
}
