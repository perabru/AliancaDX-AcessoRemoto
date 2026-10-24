package br.com.perabru.minianydesk

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import java.io.File
import java.io.FileNotFoundException
import java.io.IOException
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Opt-in, authenticated storage bound to this Android app's keystore.
 * A missing record is distinct from unreadable/corrupt ciphertext or an unavailable key.
 */
class MeshCodeVault(private val store: Store, private val keys: KeyProvider) {
    /** Implementations must preserve atomic replacement and never store decrypted codes. */
    interface Store {
        /** Null means absent, including any recoverable atomic-write backup. */
        fun read(address: String): ByteArray?
        fun write(address: String, bytes: ByteArray)
        fun delete(address: String)
    }

    /** Reads never create a replacement key for an encrypted record they cannot unlock. */
    interface KeyProvider {
        fun existing(): SecretKey?
        fun create(): SecretKey
    }

    constructor(context: Context) : this(AtomicStore(File(context.applicationContext.filesDir,
        "remembered_codes")), AndroidKeys())

    internal constructor(context: Context, keys: KeyProvider) : this(AtomicStore(
        File(context.applicationContext.filesDir, "remembered_codes")), keys)

    private class AtomicStore(private val directory: File) : Store {
        private fun file(address: String) = AtomicFile(File(directory,
            address.removePrefix("mesh-").removeSuffix(".invalid") + ".bin"))

        override fun read(address: String): ByteArray? {
            val target = file(address)
            val input = try { target.openRead() }
            catch (error: FileNotFoundException) {
                // openRead restores .bak first. Testing baseFile before it would
                // misclassify an interrupted write's recoverable data as absent.
                val base = target.baseFile
                if (!base.exists() && !File(base.path + ".bak").exists() &&
                    !File(base.path + ".new").exists()) return null
                throw IOException("Não foi possível abrir o código protegido neste celular.", error)
            }
            return input.use {
                val bytes = ByteArray(MAX_BYTES + 1)
                var size = 0
                while (size < bytes.size) {
                    val count = it.read(bytes, size, bytes.size - size)
                    if (count < 0) break
                    if (count == 0) throw IOException("Leitura do código protegido sem progresso.")
                    size += count
                }
                if (size > MAX_BYTES) throw IOException("O código protegido está danificado ou excede o limite. Os dados foram preservados.")
                bytes.copyOf(size)
            }
        }

        override fun write(address: String, bytes: ByteArray) {
            check(directory.isDirectory || directory.mkdirs()) { "Não foi possível guardar o código neste celular." }
            val target = file(address)
            val output = target.startWrite()
            try { output.write(bytes); target.finishWrite(output) }
            catch (error: Exception) { target.failWrite(output); throw error }
        }

        override fun delete(address: String) { file(address).delete() }
    }

    private class AndroidKeys : KeyProvider {
        override fun existing(): SecretKey? {
            val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
            if (!store.containsAlias(ALIAS)) return null
            return store.getKey(ALIAS, null) as? SecretKey
                ?: throw IOException("A chave do código protegido não está disponível neste celular.")
        }

        override fun create(): SecretKey {
            existing()?.let { return it }
            return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").run {
                init(KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setRandomizedEncryptionRequired(true).build())
                generateKey()
            }
        }
    }

    private fun validAddress(address: String) {
        require(MeshPairing.isAddress(address)) { "Computador salvo inválido." }
    }

    private fun encrypted(pairing: MeshPairing): ByteArray {
        validAddress(pairing.address)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, keys.existing() ?: keys.create())
        cipher.updateAAD(pairing.address.toByteArray(Charsets.UTF_8))
        check(cipher.iv.size == IV_BYTES) { "O armazenamento protegido não forneceu um IV compatível." }
        val bytes = byteArrayOf(1) + cipher.iv + cipher.doFinal(pairing.code.toByteArray(Charsets.UTF_8))
        check(bytes.size in MIN_BYTES..MAX_BYTES) { "O código protegido excede o limite permitido." }
        return bytes
    }

    private fun decrypted(address: String, bytes: ByteArray): MeshPairing {
        try {
            require(bytes.size in MIN_BYTES..MAX_BYTES && bytes[0] == 1.toByte())
            val secret = keys.existing()
                ?: throw IOException("A chave do código protegido não está disponível. Cole um convite atualizado do Host para salvá-lo novamente.")
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, secret, GCMParameterSpec(128, bytes.copyOfRange(1, 1 + IV_BYTES)))
            cipher.updateAAD(address.toByteArray(Charsets.UTF_8))
            val pairing = MeshPairing.parseCode(String(cipher.doFinal(bytes.copyOfRange(1 + IV_BYTES, bytes.size)), Charsets.UTF_8))
            require(pairing.address == address)
            return pairing
        } catch (error: IOException) { throw error }
        catch (error: Exception) {
            throw IOException("Não foi possível abrir o código protegido. Ele pode estar danificado ou a chave pode ter mudado. Os dados foram preservados; cole um convite atualizado do Host.", error)
        }
    }

    @Throws(IOException::class)
    fun load(address: String): MeshPairing? = synchronized(mutationLock) {
        validAddress(address)
        val bytes = try { store.read(address)?.copyOf() }
            catch (error: Exception) { throw IOException("Não foi possível ler o código protegido. Os dados foram preservados; confira o armazenamento do celular.", error) }
        bytes?.let { decrypted(address, it) }
    }

    @Throws(IOException::class)
    fun save(pairing: MeshPairing) { withSaved(pairing) { Unit } }

    /** Save ciphertext first, verify it by reading/decrypting, then commit its registry link.
     * Any failure restores and verifies the previous raw bytes, including corrupt data.
     * The process-wide lock covers all four ControllerSession vault instances.
     */
    @Throws(IOException::class)
    fun <T> withSaved(pairing: MeshPairing, commit: () -> T): T = synchronized(mutationLock) {
        validAddress(pairing.address)
        val original = try { store.read(pairing.address)?.copyOf() }
            catch (error: Exception) { throw IOException("Não foi possível ler o código protegido anterior. Nada foi substituído.", error) }
        var changed = false
        try {
            val bytes = encrypted(pairing)
            changed = true // A failing store may already have changed its bytes.
            store.write(pairing.address, bytes.copyOf())
            val confirmed = store.read(pairing.address)
            check(confirmed != null && confirmed.contentEquals(bytes)) { "A gravação do código não foi confirmada pelo armazenamento." }
            check(decrypted(pairing.address, confirmed).code == pairing.code) { "O código protegido gravado não corresponde ao convite." }
            commit()
        } catch (error: Exception) {
            if (!changed) throw IOException("Não foi possível guardar o código protegido. Os dados anteriores foram mantidos.", error)
            val restored = try {
                if (original == null) store.delete(pairing.address) else store.write(pairing.address, original.copyOf())
                val actual = store.read(pairing.address)
                if (original == null) actual == null else actual != null && actual.contentEquals(original)
            } catch (rollback: Exception) { error.addSuppressed(rollback); false }
            throw IOException(if (restored)
                "Não foi possível salvar o código e seu cadastro. Os dados anteriores foram mantidos."
                else "Não foi possível salvar nem restaurar o código protegido. Feche o app e confira o armazenamento antes de tentar novamente.", error)
        }
    }

    @Throws(IOException::class)
    fun forget(address: String) = synchronized(mutationLock) {
        validAddress(address)
        try {
            store.delete(address)
            check(store.read(address) == null) { "O armazenamento ainda contém o código protegido." }
        } catch (error: Exception) {
            throw IOException("Não foi possível remover o código protegido deste celular. Confira o armazenamento e tente novamente.", error)
        }
    }

    private companion object {
        const val ALIAS = "MiniAnyDesk-private-code-v1"
        const val IV_BYTES = 12
        const val MIN_BYTES = 30
        const val MAX_BYTES = 512
        val mutationLock = Any()
    }
}
