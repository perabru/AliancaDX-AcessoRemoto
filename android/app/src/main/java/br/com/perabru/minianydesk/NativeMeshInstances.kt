package br.com.perabru.minianydesk

/** Process-global FFI boundary. All app-only networks share this one registry. */
internal interface NativeMeshApi {
    fun ensureLoaded()
    fun parse(config: String): Int
    fun run(config: String): Int
    fun retain(instanceNames: Array<String>): Int
}

/**
 * Keeps each private network alive until its own owner releases it.
 *
 * EasyTier's retain call removes every name omitted from its argument. Taking the
 * snapshot and calling FFI under the same lock prevents one session's cleanup
 * from removing a different session that is starting at the same time.
 */
internal class NativeMeshInstances(private val api: NativeMeshApi) {
    private val lock = Any()
    private val instances = linkedSetOf<String>()

    fun start(instanceName: String, config: String, isCurrent: () -> Boolean): Boolean = synchronized(lock) {
        require(instanceName.matches(Regex("MiniAnyDesk-android-[0-9a-f-]{36}")))
        check(instanceName !in instances) { "Private network already registered" }
        check(instances.size < 100) { "Too many private networks" }
        if (!isCurrent()) return@synchronized false
        api.ensureLoaded()
        if (!isCurrent()) return@synchronized false
        // Also retries cleanup of a previously stopped instance if native cleanup
        // failed. Every still-live owner is included in this snapshot.
        check(api.retain(instances.toTypedArray()) == 0)
        check(api.parse(config) == 0)
        if (!isCurrent()) return@synchronized false
        try {
            check(api.run(config) == 0)
            instances.add(instanceName)
            true
        } catch (error: Throwable) {
            // A failed native startup may have created a partial instance. Its
            // name is intentionally absent, while all other owners are retained.
            runCatching { api.retain(instances.toTypedArray()) }
            throw error
        }
    }

    fun release(instanceName: String) = synchronized(lock) {
        if (!instances.remove(instanceName)) return@synchronized
        check(api.retain(instances.toTypedArray()) == 0)
    }
}
