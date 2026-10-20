package br.com.perabru.minianydesk

import java.util.Locale

/** Pure preference-key migration. Only unambiguous, valid certificate pins survive. */
object ConnectionPins {
    private val fingerprint = Regex("[0-9A-F]{64}")

    fun preferenceKey(address: String, port: Int): String =
        "pin:${ComputerBook.endpointKey(address, port)}"

    /**
     * Normalize legacy DNS/IDNA and bracketed or unbracketed IPv6 aliases.
     * Conflicting aliases invalidate that endpoint for the entire migration, regardless
     * of iteration order; a later matching alias cannot restore trust. Non-pin preferences
     * and malformed values are excluded, so passwords can never enter the returned map.
     */
    fun migrate(values: Map<String, *>): Map<String, String> {
        val normalized = LinkedHashMap<String, String>()
        val conflicts = HashSet<String>()
        for ((oldKey, value) in values) {
            if (!oldKey.startsWith("pin:")) continue
            val pin = (value as? String)?.replace(":", "")?.uppercase(Locale.ROOT) ?: continue
            if (!fingerprint.matches(pin)) continue
            val endpoint = oldKey.removePrefix("pin:")
            val delimiter = endpoint.lastIndexOf(':')
            if (delimiter <= 0) continue
            val port = endpoint.substring(delimiter + 1).toIntOrNull() ?: continue
            val key = try { preferenceKey(endpoint.substring(0, delimiter), port) }
                catch (_: IllegalArgumentException) { continue }
            if (key in conflicts) continue
            val previous = normalized[key]
            if (previous != null && previous != pin) {
                normalized.remove(key)
                conflicts.add(key)
            } else normalized[key] = pin
        }
        return normalized.toMap()
    }
}
