package br.com.perabru.minianydesk

import java.io.File
import java.io.IOException
import java.nio.ByteBuffer
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import javax.crypto.SecretKey
import javax.crypto.spec.SecretKeySpec

/** Real production coordinator/book/AES-GCM with synthetic fixture storage and keys. */
private class AccessBookStore(var value: String? = null) : ComputerBook.Store {
    var fail = false
    var ignoreNext = false
    override fun read() = value
    override fun write(value: String?): Boolean {
        if (ignoreNext) { ignoreNext = false; return true }
        this.value = value
        return !fail
    }
}

private class AccessCipherStore : MeshCodeVault.Store {
    val values = HashMap<String, ByteArray>()
    var failNextWrite = false
    var failDelete = false
    override fun read(address: String) = values[address]?.copyOf()
    override fun write(address: String, bytes: ByteArray) {
        values[address] = bytes.copyOf()
        if (failNextWrite) { failNextWrite = false; throw IOException("fixture partial replacement failed") }
    }
    override fun delete(address: String) { if (failDelete) throw IOException("fixture delete failed"); values.remove(address) }
}

private class AccessKeys(var available: Boolean = true) : MeshCodeVault.KeyProvider {
    override fun existing(): SecretKey? = if (available) SecretKeySpec(ByteArray(32) { (it + 29).toByte() }, "AES") else null
    override fun create(): SecretKey { available = true; return existing()!! }
}

private class AccessFileBook(private val file: File) : ComputerBook.Store {
    override fun read() = if (file.isFile) file.readText() else null
    override fun write(value: String?): Boolean {
        if (value == null) { Files.deleteIfExists(file.toPath()); return true }
        atomic(file, value.toByteArray()); return true
    }
}

private class AccessFileCodes(private val directory: File) : MeshCodeVault.Store {
    private fun file(address: String) = File(directory, "$address.bin")
    override fun read(address: String) = file(address).takeIf { it.isFile }?.readBytes()
    override fun write(address: String, bytes: ByteArray) { atomic(file(address), bytes) }
    override fun delete(address: String) { Files.deleteIfExists(file(address).toPath()) }
}

private fun atomic(file: File, bytes: ByteArray) {
    val directory = requireNotNull(file.parentFile)
    directory.mkdirs()
    val temporary = Files.createTempFile(directory.toPath(), ".fixture-", ".tmp")
    try {
        Files.write(temporary, bytes)
        Files.move(temporary, file.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
    } finally { Files.deleteIfExists(temporary) }
}

private fun accessPairing(seed: Int = 0, port: Int = 64321, identity: Int = 170): MeshPairing {
    val body = byteArrayOf(1) + ByteArray(32) { (it + seed).toByte() } +
        ByteBuffer.allocate(2).putShort(port.toShort()).array() + ByteArray(32) { identity.toByte() }
    val payload = body + MessageDigest.getInstance("SHA-256").digest("MDA6-check/".toByteArray() + body).copyOf(4)
    val alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    var bits = 0; var buffer = 0
    val encoded = buildString {
        for (byte in payload) {
            buffer = (buffer shl 8) or (byte.toInt() and 255); bits += 8
            while (bits >= 5) { bits -= 5; append(alphabet[(buffer ushr bits) and 31]) }
        }
        if (bits > 0) append(alphabet[(buffer shl (5 - bits)) and 31])
    }
    return MeshPairing.parseCode("MDA6-$encoded")
}

private class AccessFixture {
    val registry = AccessBookStore()
    val ciphertext = AccessCipherStore()
    val keys = AccessKeys()
    val book = ComputerBook(registry)
    val vault = MeshCodeVault(ciphertext, keys)
    val access = SavedComputerAccess(book, vault)
}

private inline fun <reified T : Throwable> expectFailure(action: () -> Unit): T {
    try { action() } catch (error: Throwable) { check(error is T) { "$error" }; return error }
    error("Expected ${T::class.java.simpleName}")
}

object SavedComputerAccessHarness {
    @JvmStatic fun main(args: Array<String>) {
        if (args.isNotEmpty()) {
            val directory = File(args[1]); val book = ComputerBook(AccessFileBook(File(directory, "registry.json")))
            val vault = MeshCodeVault(AccessFileCodes(File(directory, "codes")), AccessKeys())
            val access = SavedComputerAccess(book, vault)
            when (args[0]) {
                "write-process" -> access.saveInvitation(accessPairing().code, chosenName = "PC preservado após reiniciar")
                "read-process" -> {
                    val record = book.entries().single()
                    val prepared = access.prepare(record.id)
                    check(record.name == "PC preservado após reiniciar" && record.lastSeen == null)
                    check(prepared.pairing?.code == accessPairing().code && prepared.source == SavedComputerAccess.CodeSource.PROTECTED)
                    check(!File(directory, "registry.json").readText().contains("MDA6-"))
                }
                else -> error("Unknown fixture process")
            }
            println("PASS fresh coordinator JVM ${args[0]}"); return
        }
        var cases = 0
        fun test(name: String, action: () -> Unit) { action(); cases++; println("PASS $name") }
        test("full CRLF Host invitation extracts exactly the friendly name and never stores its password") {
            val f = AccessFixture(); val pairing = accessPairing()
            val secret = "public-fixture-password"
            val text = "AliançaDX Host 7.0.0\r\nComputador: PC da sala\r\nCódigo privado: ${pairing.code}\r\nSenha: $secret\r\nAguarde minha aprovação."
            val saved = f.access.saveInvitation(text)
            check(saved.computer.name == "PC da sala" && saved.computer.lastSeen == null)
            check(saved.pairing.code == pairing.code)
            check(!f.registry.value!!.contains(secret) && !f.registry.value!!.contains(pairing.code))
            val raw = String(f.ciphertext.values.getValue(pairing.address), Charsets.ISO_8859_1)
            check(!raw.contains(secret) && !raw.contains(pairing.code))
        }
        test("saving before first access survives independent registry and vault reload") {
            val f = AccessFixture(); val saved = f.access.saveInvitation(accessPairing().code, chosenName = "Meu alias")
            val reloaded = SavedComputerAccess(ComputerBook(f.registry), MeshCodeVault(f.ciphertext, AccessKeys()))
            val prepared = reloaded.prepare(saved.computer.id)
            check(prepared.computer == saved.computer && prepared.source == SavedComputerAccess.CodeSource.PROTECTED)
            check(prepared.pairing?.code == saved.pairing.code)
        }
        test("unremembered in-memory code is usable only for that exact endpoint and is not persisted") {
            val f = AccessFixture(); val p = accessPairing(); val record = f.book.add("Sem lembrar", p.address, p.port)
            check(f.access.prepare(record.id, p).source == SavedComputerAccess.CodeSource.MEMORY_ONLY)
            check(f.access.prepare(record.id).source == SavedComputerAccess.CodeSource.MISSING)
            check(f.access.prepare(record.id, accessPairing(2)).source == SavedComputerAccess.CodeSource.MISSING)
            check(f.access.prepare(record.id, accessPairing(port = 1234)).source == SavedComputerAccess.CodeSource.MISSING)
            check(f.ciphertext.values.isEmpty())
        }
        test("changed private identity updates the chosen card while preserving its ID alias and history") {
            val f = AccessFixture(); val first = f.access.saveInvitation(accessPairing().code, chosenName = "Escritório")
            f.book.markSeen(first.computer.id, 10_000)
            val second = f.access.saveInvitation(accessPairing(5).code, first.computer.id)
            check(second.computer.id == first.computer.id && second.computer.name == "Escritório" && second.computer.lastSeen == 10_000L)
            check(f.book.entries() == listOf(second.computer) && f.vault.load(first.pairing.address) == null)
            check(f.access.prepare(second.computer.id).pairing?.code == second.pairing.code)
        }
        test("updated port or certificate retains the card and saves only the validated new code") {
            val f = AccessFixture(); val first = f.access.saveInvitation(accessPairing().code)
            val updated = accessPairing(port = 4444, identity = 187)
            val saved = f.access.saveInvitation(updated.code, first.computer.id)
            check(saved.computer.id == first.computer.id && saved.computer.port == 4444 && f.book.entries().size == 1)
            check(f.vault.load(updated.address)?.fingerprint == updated.fingerprint)
        }
        test("legacy address-keyed code with changed port repairs only endpoint metadata") {
            val f = AccessFixture(); val code = accessPairing(port = 4444)
            val old = f.book.add("Nome antigo", code.address, 64321); f.book.markSeen(old.id, 500)
            f.vault.save(code)
            val prepared = f.access.prepare(old.id)
            check(prepared.repairedPort && prepared.computer.id == old.id && prepared.computer.name == old.name)
            check(prepared.computer.port == 4444 && prepared.computer.lastSeen == 500L)
            check(!f.access.prepare(old.id).repairedPort)
        }
        test("failed registry commit rolls back a new code and preserves the original card and ciphertext") {
            val f = AccessFixture(); val old = f.access.saveInvitation(accessPairing().code, chosenName = "Preservado")
            val before = f.ciphertext.values.getValue(old.pairing.address).copyOf(); val raw = f.registry.value
            val replacement = accessPairing(3)
            f.registry.fail = true
            expectFailure<IOException> { f.access.saveInvitation(replacement.code, old.computer.id) }
            check(f.registry.value == raw && f.book.entries() == listOf(old.computer))
            check(f.vault.load(replacement.address) == null && before.contentEquals(f.ciphertext.values.getValue(old.pairing.address)))
        }
        test("partial code replacement failure never publishes registry metadata") {
            val f = AccessFixture(); f.ciphertext.failNextWrite = true
            expectFailure<IOException> { f.access.saveInvitation(accessPairing().code) }
            check(f.book.entries().isEmpty() && f.ciphertext.values.isEmpty())
        }
        test("corrupt ciphertext is reported as failure and explicit updated invitation repairs it") {
            val f = AccessFixture(); val p = accessPairing(); val record = f.book.add("Recuperável", p.address, p.port)
            f.ciphertext.values[p.address] = byteArrayOf(1, 2, 3)
            val failure = expectFailure<IOException> { f.access.prepare(record.id, p) }
            check(failure.message!!.contains("protegido") && f.ciphertext.values.getValue(p.address).contentEquals(byteArrayOf(1,2,3)))
            val repaired = f.access.saveInvitation(p.code, record.id)
            check(repaired.computer.id == record.id && f.access.prepare(record.id).pairing?.code == p.code)
        }
        test("unavailable key cannot masquerade as an absent code or mutate encrypted records") {
            val f = AccessFixture(); val saved = f.access.saveInvitation(accessPairing().code)
            val before = f.ciphertext.values.getValue(saved.pairing.address).copyOf(); f.keys.available = false
            expectFailure<IOException> { f.access.prepare(saved.computer.id) }
            check(before.contentEquals(f.ciphertext.values.getValue(saved.pairing.address)))
        }
        test("failed removal preserves code while a cleanup failure is an explicit warning after successful removal") {
            val f = AccessFixture(); val saved = f.access.saveInvitation(accessPairing().code)
            f.registry.fail = true
            expectFailure<IOException> { f.access.remove(saved.computer.id) }
            check(f.vault.load(saved.pairing.address) != null && f.book.entries().size == 1)
            f.registry.fail = false; f.ciphertext.failDelete = true
            check(f.access.remove(saved.computer.id)?.contains("código antigo") == true)
            check(f.book.entries().isEmpty() && f.vault.load(saved.pairing.address) != null)
        }
        test("a legacy sibling card keeps its shared protected code until the final card is removed") {
            val f = AccessFixture(); val saved = f.access.saveInvitation(accessPairing().code)
            val sibling = f.book.add("Porta anterior", saved.pairing.address, 443)
            check(f.access.remove(saved.computer.id) == null && f.vault.load(saved.pairing.address) != null)
            check(f.access.remove(sibling.id) == null && f.vault.load(saved.pairing.address) == null)
        }
        test("four independent session coordinators cannot duplicate one saved access") {
            val f = AccessFixture(); val pool = Executors.newFixedThreadPool(4)
            try {
                val records = pool.invokeAll((0..3).map { Callable {
                    SavedComputerAccess(ComputerBook(f.registry), MeshCodeVault(f.ciphertext, AccessKeys()))
                        .saveInvitation(accessPairing().code).computer.id
                } }).map { it.get() }
                check(records.distinct().size == 1 && f.book.entries().size == 1)
            } finally { pool.shutdownNow() }
        }
        test("stale card or invalid invitation cannot create a replacement record or overwrite its code") {
            val f = AccessFixture(); val saved = f.access.saveInvitation(accessPairing().code); f.book.remove(saved.computer.id)
            val before = f.ciphertext.values.getValue(saved.pairing.address).copyOf()
            expectFailure<IllegalArgumentException> { f.access.saveInvitation(accessPairing(1).code, saved.computer.id) }
            expectFailure<IllegalArgumentException> { f.access.saveInvitation("MDA6-incompleto") }
            check(f.book.entries().isEmpty() && before.contentEquals(f.ciphertext.values.getValue(saved.pairing.address)))
        }
        test("a registry that claims success without storing is detected before saved access is reported") {
            val f = AccessFixture(); f.registry.ignoreNext = true
            expectFailure<IOException> { f.access.saveInvitation(accessPairing().code) }
            check(f.registry.value == null && f.ciphertext.values.isEmpty())
        }
        test("late authentication cannot recreate a card updated by a second session") {
            val f = AccessFixture(); val old = f.access.saveInvitation(accessPairing().code, chosenName = "PC estável")
            val captured = f.access.captureAttempt(old.computer.address, old.computer.port, old.computer.id)
            val updated = SavedComputerAccess(ComputerBook(f.registry), MeshCodeVault(f.ciphertext, AccessKeys()))
                .saveInvitation(accessPairing(5).code, old.computer.id)
            check(f.access.authenticatedRecord(old.computer.address, old.computer.port, captured, "Nome remoto antigo") == null)
            check(f.book.entries() == listOf(updated.computer) && f.vault.load(old.pairing.address) == null)
            expectFailure<IllegalArgumentException> { f.access.captureAttempt(old.computer.address, old.computer.port, old.computer.id) }
        }
        test("late authentication cannot resurrect a deleted card and fresh authentication may add metadata only") {
            val f = AccessFixture(); val old = f.access.saveInvitation(accessPairing().code)
            val captured = f.access.captureAttempt(old.computer.address, old.computer.port, old.computer.id)
            f.access.remove(old.computer.id)
            check(f.access.authenticatedRecord(old.computer.address, old.computer.port, captured, "PC antigo") == null)
            check(f.book.entries().isEmpty() && f.vault.load(old.pairing.address) == null)
            val fresh = f.access.authenticatedRecord(old.computer.address, old.computer.port, null, "Novo acesso")!!
            check(fresh.id != old.computer.id && f.vault.load(old.pairing.address) == null)
        }
        test("unchanged authentication preserves the alias and the registry contains no password field") {
            val f = AccessFixture(); val old = f.access.saveInvitation(accessPairing().code, chosenName = "Meu apelido")
            val captured = f.access.captureAttempt(old.computer.address, old.computer.port, null)
            check(captured == old.computer.id)
            check(f.access.authenticatedRecord(old.computer.address, old.computer.port, captured, "Nome remoto") == old.computer)
            check(f.book.entries().single().name == "Meu apelido" && !f.registry.value!!.contains("password"))
        }
        println("$cases saved-access persistence and reconnection checks passed")
    }
}
