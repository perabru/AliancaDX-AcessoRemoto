package br.com.perabru.minianydesk

import java.io.File
import java.io.IOException
import java.nio.ByteBuffer
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import javax.crypto.Cipher
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/** Synthetic fixture keys/codes only. Production AES-GCM/validation/transactions run unchanged. */
private class VaultFixtureKeys(var key: SecretKey? = fixtureKey()) : MeshCodeVault.KeyProvider {
    var creates = 0
    override fun existing(): SecretKey? = key
    override fun create(): SecretKey { creates++; return fixtureKey().also { key = it } }
}

private fun fixtureKey() = SecretKeySpec(ByteArray(32) { (it + 17).toByte() }, "AES")

private open class VaultFixtureStore : MeshCodeVault.Store {
    val records = ConcurrentHashMap<String, ByteArray>()
    var writes = 0
    var deletes = 0
    var failRead = false
    var dropWrites = 0
    var failAfterWrite = 0
    var failBeforeWrite = 0
    var dropDelete = false
    override fun read(address: String): ByteArray? {
        if (failRead) throw IOException("fixture storage unavailable")
        return records[address]?.copyOf()
    }
    override fun write(address: String, bytes: ByteArray) {
        writes++
        if (failBeforeWrite > 0) { failBeforeWrite--; throw IOException("fixture write failure") }
        if (dropWrites > 0) { dropWrites--; return }
        records[address] = bytes.copyOf()
        if (failAfterWrite > 0) { failAfterWrite--; throw IOException("fixture write changed bytes then failed") }
    }
    override fun delete(address: String) { deletes++; if (!dropDelete) records.remove(address) }
}

private class VaultFixtureFileStore(private val directory: File) : MeshCodeVault.Store {
    private fun path(address: String) = File(directory, address + ".encrypted").toPath()
    override fun read(address: String): ByteArray? = if (Files.exists(path(address))) Files.readAllBytes(path(address)) else null
    override fun write(address: String, bytes: ByteArray) {
        directory.mkdirs()
        val temp = Files.createTempFile(directory.toPath(), ".fixture-", ".tmp")
        try {
            Files.write(temp, bytes)
            Files.move(temp, path(address), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
        } finally { Files.deleteIfExists(temp) }
    }
    override fun delete(address: String) { Files.deleteIfExists(path(address)) }
}

private fun fixturePairing(seed: Int = 0, port: Int = 64321, fingerprintSeed: Int = 170): MeshPairing {
    val body = byteArrayOf(1) + ByteArray(32) { (it + seed).toByte() } +
        ByteBuffer.allocate(2).putShort(port.toShort()).array() + ByteArray(32) { fingerprintSeed.toByte() }
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

private fun legacyCiphertext(pairing: MeshPairing, aad: String = pairing.address): ByteArray {
    val iv = ByteArray(12) { it.toByte() }
    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
    cipher.init(Cipher.ENCRYPT_MODE, fixtureKey(), GCMParameterSpec(128, iv))
    cipher.updateAAD(aad.toByteArray(Charsets.UTF_8))
    return byteArrayOf(1) + iv + cipher.doFinal(pairing.code.toByteArray(Charsets.UTF_8))
}

private fun fails(action: () -> Unit): IOException {
    try { action() } catch (error: IOException) { return error }
    error("Expected an explicit storage/unlock failure")
}

object MeshCodeVaultHarness {
    @JvmStatic fun main(args: Array<String>) {
        if (args.isNotEmpty()) {
            val pairing = fixturePairing()
            val vault = MeshCodeVault(VaultFixtureFileStore(File(args[1])), VaultFixtureKeys())
            when (args[0]) {
                "write-process" -> vault.save(pairing)
                "read-process" -> check(vault.load(pairing.address)?.code == pairing.code)
                else -> error("Unknown fixture process")
            }
            println("PASS fresh JVM process ${args[0]}")
            return
        }
        var count = 0
        fun test(label: String, action: () -> Unit) { action(); count++; println("PASS $label") }
        test("absence does not generate an Android keystore replacement") {
            val keys = VaultFixtureKeys(null); val vault = MeshCodeVault(VaultFixtureStore(), keys)
            check(vault.load(fixturePairing().address) == null && keys.creates == 0)
        }
        test("format1 IV AAD and legacy alias-compatible AES-GCM records still decrypt") {
            val p = fixturePairing(); val store = VaultFixtureStore(); store.records[p.address] = legacyCiphertext(p)
            check(MeshCodeVault(store, VaultFixtureKeys()).load(p.address)?.code == p.code)
        }
        test("ciphertext survives a fresh vault instance without retaining plaintext") {
            val p = fixturePairing(); val store = VaultFixtureStore(); val keys = VaultFixtureKeys()
            MeshCodeVault(store, keys).save(p)
            val raw = store.records.getValue(p.address)
            check(raw[0] == 1.toByte() && raw.size == 148)
            check(!String(raw, Charsets.ISO_8859_1).contains(p.code))
            check(MeshCodeVault(store, VaultFixtureKeys()).load(p.address)?.code == p.code)
        }
        test("fresh random GCM IVs preserve the existing version1 format") {
            val p = fixturePairing(); val store = VaultFixtureStore(); val vault = MeshCodeVault(store, VaultFixtureKeys())
            vault.save(p); val first = store.records.getValue(p.address).copyOf()
            vault.save(p); val second = store.records.getValue(p.address)
            check(!first.copyOfRange(1, 13).contentEquals(second.copyOfRange(1, 13)))
            check(vault.load(p.address)?.code == p.code)
        }
        test("bad authentication tags are reported and preserved rather than absent") {
            val p = fixturePairing(); val store = VaultFixtureStore(); val raw = legacyCiphertext(p)
            raw[raw.lastIndex] = (raw.last().toInt() xor 1).toByte(); store.records[p.address] = raw.copyOf()
            fails { MeshCodeVault(store, VaultFixtureKeys()).load(p.address) }
            check(store.records.getValue(p.address).contentEquals(raw) && store.writes == 0 && store.deletes == 0)
        }
        test("missing decryption keys never generate replacements or erase records") {
            val p = fixturePairing(); val store = VaultFixtureStore(); store.records[p.address] = legacyCiphertext(p)
            val keys = VaultFixtureKeys(null)
            check(fails { MeshCodeVault(store, keys).load(p.address) }.message!!.contains("chave"))
            check(keys.creates == 0 && store.records.containsKey(p.address))
        }
        test("address AAD prevents encrypted records moving between computers") {
            val p = fixturePairing(); val other = fixturePairing(seed = 50); val store = VaultFixtureStore()
            store.records[other.address] = legacyCiphertext(p)
            fails { MeshCodeVault(store, VaultFixtureKeys()).load(other.address) }
        }
        test("decrypted invitations must bind the same address as their ciphertext") {
            val p = fixturePairing(); val other = fixturePairing(seed = 50); val store = VaultFixtureStore()
            store.records[other.address] = legacyCiphertext(p, other.address)
            fails { MeshCodeVault(store, VaultFixtureKeys()).load(other.address) }
        }
        test("invalid format and empty records are explicit errors without destruction") {
            val p = fixturePairing(); val store = VaultFixtureStore(); val vault = MeshCodeVault(store, VaultFixtureKeys())
            for (raw in listOf(byteArrayOf(), byteArrayOf(2) + ByteArray(147), ByteArray(513))) {
                store.records[p.address] = raw.copyOf(); fails { vault.load(p.address) }
                check(store.records.getValue(p.address).contentEquals(raw))
            }
        }
        test("storage read failures are not misreported as an absent code") {
            val store = VaultFixtureStore().apply { failRead = true }; val vault = MeshCodeVault(store, VaultFixtureKeys())
            fails { vault.load(fixturePairing().address) }
            check(store.writes == 0 && store.deletes == 0)
        }
        test("a silent dropped write cannot commit a successful saved-computer card") {
            val p = fixturePairing(); val updated = fixturePairing(port = 64322); val store = VaultFixtureStore()
            val vault = MeshCodeVault(store, VaultFixtureKeys()); vault.save(p); val original = store.records.getValue(p.address).copyOf()
            var committed = false; store.dropWrites = 1
            fails { vault.withSaved(updated) { committed = true } }
            check(!committed && store.records.getValue(p.address).contentEquals(original))
        }
        test("writes that mutate and then throw restore the exact previous ciphertext") {
            val p = fixturePairing(); val store = VaultFixtureStore(); val vault = MeshCodeVault(store, VaultFixtureKeys())
            vault.save(p); val original = store.records.getValue(p.address).copyOf(); store.failAfterWrite = 1
            fails { vault.save(fixturePairing(port = 64322)) }
            check(store.records.getValue(p.address).contentEquals(original) && vault.load(p.address)?.port == p.port)
        }
        test("registry failure restores even previously corrupt raw bytes") {
            val p = fixturePairing(); val store = VaultFixtureStore(); val original = byteArrayOf(7, 4, 8, 2)
            store.records[p.address] = original.copyOf(); val vault = MeshCodeVault(store, VaultFixtureKeys())
            fails { vault.withSaved(p) { throw IOException("fixture registry commit failure") } }
            check(store.records.getValue(p.address).contentEquals(original))
        }
        test("registry failure removes a new orphan ciphertext and rollback failures stay explicit") {
            val p = fixturePairing(); val store = VaultFixtureStore(); val vault = MeshCodeVault(store, VaultFixtureKeys())
            fails { vault.withSaved(p) { throw IOException("fixture registry commit failure") } }
            check(!store.records.containsKey(p.address))
            vault.save(p); store.failAfterWrite = 1; store.failBeforeWrite = 1
            // First write fails before mutation, and restoring the prior bytes
            // then throws after mutation: caller must still see a rollback error.
            val error = fails { vault.save(fixturePairing(port = 64322)) }
            check(error.message!!.contains("nem restaurar"))
        }
        test("forget confirms removal and surfaces storage that ignores deletion") {
            val p = fixturePairing(); val store = VaultFixtureStore(); val vault = MeshCodeVault(store, VaultFixtureKeys())
            vault.save(p); store.dropDelete = true; fails { vault.forget(p.address) }
            check(store.records.containsKey(p.address)); store.dropDelete = false; vault.forget(p.address)
            check(vault.load(p.address) == null)
        }
        test("two session vaults serialize save registry commit and rollback") {
            val p = fixturePairing(); val store = VaultFixtureStore(); val a = MeshCodeVault(store, VaultFixtureKeys())
            val b = MeshCodeVault(store, VaultFixtureKeys()); a.save(p)
            val inside = CountDownLatch(1); val release = CountDownLatch(1); val secondDone = CountDownLatch(1)
            val firstError = AtomicReference<Throwable?>(); val secondError = AtomicReference<Throwable?>()
            val first = Thread {
                try { a.withSaved(fixturePairing(port = 64322)) {
                    inside.countDown(); check(release.await(2, TimeUnit.SECONDS)); throw IOException("fixture first commit failed")
                } } catch (error: Throwable) { firstError.set(error) }
            }
            val second = Thread {
                try { b.save(fixturePairing(port = 64323)) } catch (error: Throwable) { secondError.set(error) }
                finally { secondDone.countDown() }
            }
            first.start(); check(inside.await(2, TimeUnit.SECONDS)); second.start()
            check(!secondDone.await(80, TimeUnit.MILLISECONDS)) { "Another session entered the transaction before rollback finished" }
            release.countDown(); first.join(2_000); second.join(2_000)
            check(!first.isAlive && !second.isAlive && firstError.get() is IOException && secondError.get() == null)
            check(a.load(p.address)?.port == 64323)
        }
        println("$count authenticated vault persistence and transaction checks passed")
    }
}
