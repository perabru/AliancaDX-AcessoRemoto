package br.com.perabru.minianydesk

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Opt-in, authenticated storage bound to this Android app's keystore. */
class MeshCodeVault(context: Context) {
    private val directory = File(context.applicationContext.filesDir, "remembered_codes")

    private fun file(address: String): AtomicFile {
        require(MeshPairing.isAddress(address)) { "Computador salvo inválido." }
        return AtomicFile(File(directory, address.removePrefix("mesh-").removeSuffix(".invalid") + ".bin"))
    }

    @Synchronized private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").run {
            init(KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true).build())
            generateKey()
        }
    }

    @Synchronized fun save(pairing: MeshPairing) {
        check(directory.isDirectory || directory.mkdirs()) { "Não foi possível guardar o código neste celular." }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        cipher.updateAAD(pairing.address.toByteArray())
        val bytes = byteArrayOf(1) + cipher.iv + cipher.doFinal(pairing.code.toByteArray(Charsets.UTF_8))
        val target = file(pairing.address)
        val output = target.startWrite()
        try { output.write(bytes); target.finishWrite(output) }
        catch (error: Exception) { target.failWrite(output); throw error }
    }

    @Synchronized fun load(address: String): MeshPairing? {
        if (!MeshPairing.isAddress(address)) return null
        return try {
            val target = file(address)
            if (!target.baseFile.exists()) return null
            require(target.baseFile.length() in 30..512)
            val bytes = target.readFully()
            require(bytes.size in 30..512 && bytes[0] == 1.toByte())
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes.copyOfRange(1, 13)))
            cipher.updateAAD(address.toByteArray())
            val pairing = MeshPairing.parseCode(String(cipher.doFinal(bytes.copyOfRange(13, bytes.size)), Charsets.UTF_8))
            pairing.takeIf { it.address == address }
        } catch (_: Exception) { null }
    }

    @Synchronized fun forget(address: String) {
        if (MeshPairing.isAddress(address)) file(address).delete()
    }

    private companion object { const val ALIAS = "MiniAnyDesk-private-code-v1" }
}
