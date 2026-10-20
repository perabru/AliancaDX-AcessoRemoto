package br.com.perabru.minianydesk

/**
 * UI-thread bookkeeping for saved-device availability polls.
 *
 * This class owns no network, Android, password, or cached-result state. A caller snapshots
 * endpoint and pin on the UI thread, runs its bounded worker, then returns the unchanged
 * token to [finish] on the same UI thread. Pending work survives edits and deletion until
 * its callback returns, so those actions cannot start an overlapping poll for that ID.
 */
class PresenceListModel {
    data class Item(val id: String, val revisionKey: String, val endpoint: String)

    data class Token(
        val id: String,
        val revision: Long,
        val endpoint: String,
        val pin: String?,
        val foregroundGeneration: Int,
        val requestId: Long = 0L
    )

    data class Finish(val accepted: Boolean, val refresh: Boolean, val unknown: Boolean)

    private data class Entry(val item: Item, val revision: Long)
    private val entries = LinkedHashMap<String, Entry>()
    private val pending = HashMap<String, Token>()
    private val deferred = HashSet<String>()
    private var nextRevision = 0L
    private var nextRequest = 0L

    val entryCount: Int get() = entries.size
    val pendingCount: Int get() = pending.size

    /** Reconcile a saved-device snapshot without cancelling an already running worker. */
    fun sync(items: List<Item>) {
        require(items.map { it.id }.toSet().size == items.size) { "Duplicate device ID" }
        val updated = LinkedHashMap<String, Entry>()
        for (item in items) {
            val previous = entries[item.id]
            val unchanged = previous != null && previous.item.revisionKey == item.revisionKey &&
                previous.item.endpoint == item.endpoint
            updated[item.id] = if (unchanged) Entry(item, previous!!.revision)
                else Entry(item, ++nextRevision)
        }
        entries.clear()
        entries.putAll(updated)
        deferred.retainAll(entries.keys)
    }

    /**
     * Return one worker token, or null while that ID already has a worker.
     * Repeated forced refreshes coalesce into one follow-up when the worker completes.
     */
    fun begin(id: String, pin: String?, foregroundGeneration: Int,
              force: Boolean = false): Token? {
        val entry = entries[id] ?: return null
        if (pending.containsKey(id)) {
            if (force) deferred.add(id)
            return null
        }
        val token = Token(id, entry.revision, entry.item.endpoint, pin,
            foregroundGeneration, ++nextRequest)
        pending[id] = token
        deferred.remove(id)
        return token
    }

    /**
     * Accept only the current entry, identity pin, and foreground generation.
     * A stale or duplicate callback never removes a different outstanding request.
     * A rejected owned callback requests fresh work only while the caller is foreground.
     */
    fun finish(token: Token, currentPin: String?, currentGeneration: Int,
               foreground: Boolean = true): Finish {
        val entry = entries[token.id]
        if (pending[token.id] != token) {
            return Finish(accepted = false, refresh = false, unknown = entry == null)
        }
        pending.remove(token.id)
        val followUp = deferred.remove(token.id)
        if (entry == null) return Finish(accepted = false, refresh = false, unknown = true)
        val accepted = foreground && entry.revision == token.revision &&
            entry.item.endpoint == token.endpoint && currentPin == token.pin &&
            currentGeneration == token.foregroundGeneration
        return Finish(accepted, foreground && (!accepted || followUp), unknown = false)
    }

    /** Invalidate all aliases for a session endpoint, including work already in flight. */
    fun invalidateEndpoint(endpoint: String): Set<String> {
        val affected = entries.values.filter { it.item.endpoint == endpoint }.map { it.item.id }
        for (id in affected) {
            val previous = entries.getValue(id)
            entries[id] = previous.copy(revision = ++nextRevision)
            deferred.add(id)
        }
        return affected.toSet()
    }

    fun isPending(id: String): Boolean = pending.containsKey(id)

    /** Clear requested follow-ups when leaving foreground; running tokens remain tracked. */
    fun cancelDeferred(id: String? = null) {
        if (id == null) deferred.clear() else deferred.remove(id)
    }
}
