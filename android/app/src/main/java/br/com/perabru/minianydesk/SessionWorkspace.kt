package br.com.perabru.minianydesk

/** Bounded ownership for live sessions. Selection never replaces or closes another owner. */
internal class SessionWorkspace<T>(val capacity: Int = 4) {
    init { require(capacity in 1..8) }
    private val owners = LinkedHashMap<Long, T>()
    private var nextId = 1L
    var selectedId: Long? = null
        private set
    val size: Int get() = owners.size
    val entries: List<Pair<Long, T>> get() = owners.entries.map { it.key to it.value }
    val selected: T? get() = selectedId?.let { owners[it] }
    operator fun get(id: Long): T? = owners[id]
    fun add(factory: (Long) -> T): Pair<Long, T>? {
        if (owners.size >= capacity) return null
        val id = nextId++
        val owner = factory(id)
        owners[id] = owner
        return id to owner
    }
    fun select(id: Long): Boolean {
        if (!owners.containsKey(id)) return false
        selectedId = id
        return true
    }
    fun remove(id: Long): T? {
        val removed = owners.remove(id) ?: return null
        if (selectedId == id) selectedId = null
        return removed
    }
    fun owns(id: Long, owner: T): Boolean = owners[id] === owner
}
