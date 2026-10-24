package br.com.perabru.minianydesk

/** Registry and opt-in code storage form one saved access. Passwords never enter this owner. */
internal class SavedComputerAccess(private val book: ComputerBook, private val codes: Codes) {
    interface Codes {
        fun load(address: String): MeshPairing?
        fun <T> withSaved(pairing: MeshPairing, commit: () -> T): T
        fun forget(address: String)
    }

    constructor(book: ComputerBook, vault: MeshCodeVault) : this(book, object : Codes {
        override fun load(address: String) = vault.load(address)
        override fun <T> withSaved(pairing: MeshPairing, commit: () -> T) = vault.withSaved(pairing, commit)
        override fun forget(address: String) = vault.forget(address)
    })

    enum class CodeSource { LOCAL, PROTECTED, MEMORY_ONLY, MISSING }
    data class Prepared(val computer: SavedComputer, val pairing: MeshPairing?, val source: CodeSource,
        val repairedPort: Boolean = false)
    data class Saved(val computer: SavedComputer, val pairing: MeshPairing, val warning: String? = null)

    /** Bind the pending authentication to a durable card identity, not just its old endpoint. */
    fun captureAttempt(address: String, port: Int, preferredId: String?): String? = synchronized(lock) {
        val endpoint = ComputerBook.endpointKey(address, port)
        if (preferredId != null) {
            val record = find(preferredId)
            require(ComputerBook.endpointKey(record.address, record.port) == endpoint) {
                "Este acesso salvo foi atualizado em outra sessão. Abra o cartão novamente em Salvos antes de conectar."
            }
            preferredId
        } else book.findEndpoint(address, port)?.id
    }

    /** A delayed successful login cannot resurrect a card deleted or updated by another owner. */
    fun authenticatedRecord(address: String, port: Int, attemptedId: String?, title: String): SavedComputer? = synchronized(lock) {
        if (attemptedId != null) {
            val record = book.entries().firstOrNull { it.id == attemptedId }
            record?.takeIf { ComputerBook.endpointKey(it.address, it.port) == ComputerBook.endpointKey(address, port) }
        } else book.findEndpoint(address, port) ?: book.add(title, address, port)
    }

    /** Reload the durable record, not a card snapshot from another session. */
    fun prepare(id: String, memoryCode: MeshPairing? = null): Prepared = synchronized(lock) {
        var record = find(id)
        if (!MeshPairing.isAddress(record.address)) return@synchronized Prepared(record, null, CodeSource.LOCAL)
        val protected = codes.load(record.address)
        if (protected != null) {
            require(protected.address == record.address) { "O código protegido não pertence a este computador. Atualize o código pelo convite do Host." }
            val repaired = protected.port != record.port
            // Older builds keyed the vault by address while the registry also stored
            // the port. Repair that legacy mismatch from the validated protected code.
            if (repaired) record = book.update(record.id, record.name, record.address, protected.port)
            return@synchronized Prepared(record, protected, CodeSource.PROTECTED, repaired)
        }
        val memory = memoryCode?.takeIf { it.address == record.address && it.port == record.port }
        Prepared(record, memory, if (memory == null) CodeSource.MISSING else CodeSource.MEMORY_ONLY)
    }

    /** Explicit save/update can run before the first connection or without a Host password. */
    fun saveInvitation(text: String, existingId: String? = null, chosenName: String? = null): Saved = synchronized(lock) {
        val pairing = MeshPairing.parseInvitation(text)
        val existing = existingId?.let { find(it) } ?: run {
            val candidates = book.entries().filter { it.address == pairing.address }
            require(candidates.size <= 1) { "Há mais de um cartão deste acesso. Use Atualizar código no computador que deseja manter." }
            candidates.singleOrNull()
        }
        require(existing == null || MeshPairing.isAddress(existing.address)) { "Para trocar um acesso local por convite, salve um novo acesso." }
        val title = chosenName ?: existing?.name ?: invitationName(text).ifBlank { "Meu computador" }
        val record = codes.withSaved(pairing) {
            if (existing == null) book.add(title, pairing.address, pairing.port)
            else book.update(existing.id, title, pairing.address, pairing.port)
        }
        val warning = existing?.address?.takeIf { it != pairing.address }?.let { old -> cleanup(old) }
        Saved(record, pairing, warning)
    }

    /** A failed registry removal must never erase the code of the remaining card. */
    fun remove(id: String): String? = synchronized(lock) {
        val record = find(id)
        book.remove(record.id)
        if (MeshPairing.isAddress(record.address)) cleanup(record.address) else null
    }

    private fun cleanup(address: String): String? {
        if (book.entries().any { it.address == address }) return null
        return try { codes.forget(address); null }
        catch (error: Exception) {
            "O cadastro foi atualizado, mas não foi possível apagar o código antigo protegido: ${error.message.orEmpty()} " +
                "O código antigo continua protegido no armazenamento deste app."
        }
    }

    private fun find(id: String) = book.entries().firstOrNull { it.id == id }
        ?: throw IllegalArgumentException("O computador não está mais na lista. Atualize os salvos e tente novamente.")

    private fun invitationName(text: String): String = Regex("(?m)^Computador:[ \\t]*([^\\r\\n]*)$")
        .find(text)?.groupValues?.get(1)?.trim()?.take(80).orEmpty()

    companion object { private val lock = Any() }
}
