package br.com.perabru.minianydesk

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.IDN
import java.util.Locale
import java.util.UUID

/** Only explicit user-saved connection metadata. Passwords and certificates belong elsewhere. */
data class SavedComputer(
    val id: String,
    val name: String,
    val address: String,
    val port: Int,
    val lastSeen: Long?
)

/**
 * Persistent, local device book. All instances within this app process share one mutation lock.
 * SharedPreferences is deliberately used only within one app process, as required by Android.
 * Invalid existing JSON is reported and preserved, never replaced by an empty registry.
 * The Store constructor allows the actual validation/JSON/CRUD code to run in JVM tests.
 */
class ComputerBook(private val store: Store) {
    interface Store {
        fun read(): String?
        /** commit-like result; implementations can change their memory even when returning false. */
        fun write(value: String?): Boolean
    }

    private class PreferencesStore(private val preferences: SharedPreferences) : Store {
        override fun read(): String? = preferences.getString(DATA_KEY, null)
        override fun write(value: String?): Boolean {
            val editor = preferences.edit()
            if (value == null) editor.remove(DATA_KEY) else editor.putString(DATA_KEY, value)
            return editor.commit()
        }
    }

    constructor(context: Context) : this(PreferencesStore(
        context.applicationContext.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
    ))

    init { synchronized(mutationLock) { readEntries() } }

    fun entries(): List<SavedComputer> = synchronized(mutationLock) { readEntries().toList() }

    fun add(name: String, address: String, port: Int): SavedComputer {
        val label = friendlyName(name)
        val endpoint = canonicalAddress(address)
        validPort(port)
        return mutate { records ->
            require(records.size < MAX_COMPUTERS) { "O cadastro já contém 256 computadores. Remova um antes de adicionar outro." }
            requireUnique(records, endpoint, port)
            SavedComputer(UUID.randomUUID().toString(), label, endpoint, port, null).also { records.add(it) }
        }
    }

    fun update(id: String, name: String, address: String, port: Int): SavedComputer {
        validId(id)
        val label = friendlyName(name)
        val endpoint = canonicalAddress(address)
        validPort(port)
        return mutate { records ->
            val index = findIndex(records, id)
            requireUnique(records, endpoint, port, id)
            records[index].copy(name = label, address = endpoint, port = port).also { records[index] = it }
        }
    }

    fun remove(id: String) {
        validId(id)
        mutate { records -> records.removeAt(findIndex(records, id)); Unit }
    }

    fun markSeen(id: String, now: Long = System.currentTimeMillis()): SavedComputer {
        validId(id)
        require(now >= 0) { "A data da última conexão é inválida." }
        return mutate { records ->
            val index = findIndex(records, id)
            records[index].copy(lastSeen = now).also { records[index] = it }
        }
    }

    fun findEndpoint(address: String, port: Int): SavedComputer? {
        val endpoint = canonicalAddress(address)
        validPort(port)
        return synchronized(mutationLock) { readEntries().firstOrNull { it.address == endpoint && it.port == port } }
    }

    private fun <T> mutate(operation: (MutableList<SavedComputer>) -> T): T = synchronized(mutationLock) {
        val original = readStored()
        val records = readEntries(original).toMutableList()
        val result = operation(records)
        val serialized = serialize(records)
        try {
            if (!store.write(serialized)) throw IOException("A gravação no armazenamento do Android falhou.")
        } catch (error: Exception) {
            // Editor.commit(false) can still update SharedPreferences' in-process memory. Restore it.
            try { store.write(original) } catch (_: Exception) { }
            val restored = try { store.read() == original } catch (_: Exception) { false }
            throw IOException(if (restored)
                "Não foi possível salvar a lista. Confira o espaço disponível e tente novamente; os dados anteriores foram mantidos."
                else "Não foi possível salvar nem restaurar a lista. Feche o app e confira o armazenamento antes de tentar novamente.", error)
        }
        result
    }

    private fun readStored(): String? = try { store.read() }
        catch (error: IOException) { throw IOException("Não foi possível ler a lista de computadores. Confira o armazenamento do app.", error) }
        catch (error: RuntimeException) { throw IllegalArgumentException("A lista não pôde ser lida. Os dados foram preservados; restaure uma cópia válida do cadastro.", error) }

    private fun readEntries(raw: String? = readStored()): List<SavedComputer> {
        if (raw == null) return emptyList()
        try {
            require(raw.toByteArray(Charsets.UTF_8).size <= MAX_JSON_BYTES) { "arquivo maior que 1 MiB" }
            boundedJson(raw)
            val document = JSONObject(raw)
            require(keys(document) == setOf("schema", "computers") && integer(document.opt("schema")) == 1L) {
                "formato ou versão não reconhecidos"
            }
            val array = document.opt("computers") as? JSONArray
                ?: throw IllegalArgumentException("lista de computadores inválida")
            require(array.length() <= MAX_COMPUTERS) { "mais de 256 computadores" }
            val ids = mutableSetOf<String>()
            val endpoints = mutableSetOf<String>()
            return (0 until array.length()).map { index ->
                val record = array.opt(index) as? JSONObject
                    ?: throw IllegalArgumentException("registro de computador inválido")
                require(keys(record) == RECORD_FIELDS) { "campos inválidos; senhas não pertencem à lista" }
                val id = record.opt("id") as? String ?: throw IllegalArgumentException("identificador ausente")
                validId(id)
                val name = friendlyName(record.opt("name") as? String ?: throw IllegalArgumentException("nome ausente"))
                val address = canonicalAddress(record.opt("address") as? String ?: throw IllegalArgumentException("endereço ausente"))
                val number = integer(record.opt("port")) ?: throw IllegalArgumentException("porta inválida")
                require(number in 1L..65535L) { "porta fora do intervalo" }
                val port = number.toInt()
                val lastSeen = if (record.isNull("lastSeen")) null else
                    integer(record.opt("lastSeen")) ?: throw IllegalArgumentException("data da última conexão inválida")
                require(lastSeen == null || lastSeen >= 0) { "data da última conexão inválida" }
                require(ids.add(id) && endpoints.add(endpointKey(address, port))) { "identificador ou endereço duplicado" }
                SavedComputer(id, name, address, port, lastSeen)
            }
        } catch (error: Exception) {
            throw IllegalArgumentException(
                "A lista de computadores é inválida: ${error.message ?: "dados não reconhecidos"}. " +
                    "Os dados foram preservados. Restaure uma cópia válida do cadastro antes de tentar editar a lista.", error)
        }
    }

    private fun serialize(records: List<SavedComputer>): String {
        val array = JSONArray()
        records.forEach {
            array.put(JSONObject().put("id", it.id).put("name", it.name).put("address", it.address)
                .put("port", it.port).put("lastSeen", it.lastSeen ?: JSONObject.NULL))
        }
        return JSONObject().put("schema", 1).put("computers", array).toString()
    }

    companion object {
        const val MAX_COMPUTERS = 256
        private const val MAX_JSON_BYTES = 1024 * 1024
        private const val PREFERENCES = "saved_computers"
        private const val DATA_KEY = "registry"
        private val mutationLock = Any()
        private val RECORD_FIELDS = setOf("id", "name", "address", "port", "lastSeen")
        private val DNS_LABEL = Regex("[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
        private val SCOPE = Regex("[A-Za-z0-9_.-]{1,64}")

        /** Pure text normalization: this method never resolves DNS or opens a network connection. */
        @JvmStatic fun canonicalAddress(value: String): String {
            require(value.isNotEmpty() && value.codePointCount(0, value.length) <= 253 && !hasControls(value) &&
                value.none { it.isWhitespace() || Character.isSpaceChar(it) }) {
                "Informe um IP ou DNS de até 253 caracteres, sem espaços."
            }
            var address = value
            val bracketed = address.startsWith('[') || address.endsWith(']')
            if (bracketed) {
                require(address.startsWith('[') && address.endsWith(']')) { "Use os dois colchetes para IPv6, ou retire ambos." }
                address = address.substring(1, address.length - 1)
            }
            if (':' in address || bracketed || '%' in address) return ipv6(address)
            if (address.endsWith('.')) address = address.dropLast(1)
            val pieces = address.split('.')
            if (pieces.size == 4 && pieces.all { it.isNotEmpty() && it.all(Char::isDigit) })
                return ipv4(address).joinToString(".")
            val canonical = try { IDN.toASCII(address, IDN.USE_STD3_ASCII_RULES).lowercase(Locale.ROOT) }
                catch (_: IllegalArgumentException) { throw IllegalArgumentException("Informe um nome DNS válido.") }
            require(canonical.isNotEmpty() && canonical.length <= 253 && canonical.split('.').all { DNS_LABEL.matches(it) }) {
                "Informe somente um IP ou DNS, sem protocolo, caminho ou porta."
            }
            return canonical
        }

        @JvmStatic fun endpointKey(address: String, port: Int): String {
            validPort(port)
            val canonical = canonicalAddress(address)
            return if (':' in canonical) "[$canonical]:$port" else "$canonical:$port"
        }

        private fun ipv4(address: String): List<Int> {
            val parts = address.split('.')
            require(parts.size == 4) { "Informe um endereço IPv4 válido." }
            return parts.map {
                require(it.length in 1..3 && it.all { char -> char in '0'..'9' } && (it.length == 1 || it[0] != '0')) {
                    "Informe um endereço IPv4 válido, sem zeros extras."
                }
                val value = it.toInt()
                require(value in 0..255) { "Informe um endereço IPv4 válido." }
                value
            }
        }

        private fun ipv6(value: String): String {
            val scopePosition = value.indexOf('%')
            val scope = if (scopePosition >= 0) value.substring(scopePosition + 1) else null
            require(scope == null || SCOPE.matches(scope)) { "O identificador de interface do IPv6 é inválido." }
            val literal = if (scopePosition >= 0) value.substring(0, scopePosition) else value
            require(':' in literal) { "Colchetes e identificadores de interface são exclusivos de IPv6." }
            val compression = literal.indexOf("::")
            require(compression < 0 || compression == literal.lastIndexOf("::")) { "Informe um endereço IPv6 válido." }
            fun groups(part: String): List<Int> {
                if (part.isEmpty()) return emptyList()
                val tokens = part.split(':')
                val groups = mutableListOf<Int>()
                tokens.forEachIndexed { index, token ->
                    if ('.' in token) {
                        require(index == tokens.lastIndex && literal.endsWith(token)) { "Informe um endereço IPv6 válido." }
                        val bytes = ipv4(token)
                        groups.add(bytes[0] * 256 + bytes[1]); groups.add(bytes[2] * 256 + bytes[3])
                    } else {
                        require(token.length in 1..4 && token.all { it in '0'..'9' || it.lowercaseChar() in 'a'..'f' }) {
                            "Informe um endereço IPv6 válido."
                        }
                        groups.add(token.toInt(16))
                    }
                }
                return groups
            }
            val words: List<Int> = if (compression >= 0) {
                val left = groups(literal.substring(0, compression))
                val right = groups(literal.substring(compression + 2))
                require(left.size + right.size < 8) { "Informe um endereço IPv6 válido." }
                left + List(8 - left.size - right.size) { 0 } + right
            } else {
                groups(literal).also { require(it.size == 8) { "Informe um endereço IPv6 válido." } }
            }
            var bestStart = -1
            var bestLength = 1
            var index = 0
            while (index < words.size) {
                if (words[index] != 0) { index++; continue }
                val start = index
                while (index < words.size && words[index] == 0) index++
                if (index - start > bestLength) { bestStart = start; bestLength = index - start }
            }
            val text = if (bestStart < 0) words.joinToString(":") { it.toString(16) } else {
                val left = words.take(bestStart).joinToString(":") { it.toString(16) }
                val right = words.drop(bestStart + bestLength).joinToString(":") { it.toString(16) }
                "$left::$right"
            }
            return text + (if (scope != null) "%$scope" else "")
        }

        private fun friendlyName(value: String): String {
            require(!hasControls(value)) { "Use um nome sem caracteres de controle." }
            val name = value.trim()
            require(name.codePointCount(0, name.length) in 1..80) { "O nome deve ter de 1 a 80 caracteres." }
            return name
        }

        private fun hasControls(value: String): Boolean {
            var offset = 0
            while (offset < value.length) {
                val codepoint = Character.codePointAt(value, offset)
                val type = Character.getType(codepoint)
                if (Character.isISOControl(codepoint) || type == Character.FORMAT.toInt() || type == Character.SURROGATE.toInt()) return true
                offset += Character.charCount(codepoint)
            }
            return false
        }

        private fun validPort(port: Int) { require(port in 1..65535) { "Use uma porta inteira entre 1 e 65535." } }
        private fun validId(id: String) {
            val valid = try { UUID.fromString(id).toString() == id } catch (_: IllegalArgumentException) { false }
            require(valid) { "O identificador do computador é inválido." }
        }
        private fun findIndex(records: List<SavedComputer>, id: String): Int = records.indexOfFirst { it.id == id }.also {
            require(it >= 0) { "O computador não está mais na lista. Atualize-a e tente novamente." }
        }
        private fun requireUnique(records: List<SavedComputer>, address: String, port: Int, id: String? = null) {
            require(records.none { it.id != id && it.address == address && it.port == port }) {
                "Este IP ou DNS e porta já estão cadastrados. Edite o computador existente."
            }
        }
        private fun integer(value: Any?): Long? = when (value) { is Int -> value.toLong(); is Long -> value; else -> null }
        private fun keys(value: JSONObject): Set<String> = value.keys().asSequence().toSet()
        private fun boundedJson(value: String) {
            var depth = 0
            var quoted = false
            var escaped = false
            value.forEach { character ->
                if (quoted) {
                    if (escaped) escaped = false
                    else if (character == '\\') escaped = true
                    else if (character == '"') quoted = false
                } else when (character) {
                    '"' -> quoted = true
                    '{', '[' -> { depth++; require(depth <= 32) { "estrutura JSON muito complexa" } }
                    '}', ']' -> depth--
                }
            }
        }
    }
}
