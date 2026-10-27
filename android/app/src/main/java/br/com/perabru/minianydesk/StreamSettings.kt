package br.com.perabru.minianydesk

import org.json.JSONObject

/** One authenticated session's preference and the mode actually seen on its drawn frames. */
internal class StreamSettings(initialMode: String = "auto") {
    var preferredMode: String = validMode(initialMode) ?: "auto"
        private set
    var appliedMode: String? = null
        private set
    var capable = false
        private set
    var hasFrame = false
        private set
    private var sentMode: String? = null
    private var awaitingConfirmation = false
    val displayedMode: String get() = if (capable) preferredMode else "auto"
    val pending: Boolean get() = capable && (preferredMode != appliedMode || sentMode != preferredMode || awaitingConfirmation)

    fun reset(initialMode: String = preferredMode) {
        preferredMode = validMode(initialMode) ?: "auto"
        appliedMode = null; capable = false; hasFrame = false; sentMode = null; awaitingConfirmation = false
    }

    fun select(mode: String): Boolean {
        val value = validMode(mode) ?: return false
        if (!capable || value == preferredMode) return false
        preferredMode = value
        return true
    }

    /** Call only with metadata of a frame actually drawn in the current capture generation. */
    fun observe(frame: JSONObject) {
        hasFrame = true
        val version = frame.opt("streamSettingsVersion")
        val mode = validMode(frame.opt("streamMode"))
        if (version !is Number || version.toDouble() != 1.0 || mode == null) return
        capable = true; appliedMode = mode
        if (mode == sentMode && mode == preferredMode) awaitingConfirmation = false
    }

    /** No request before explicit support and authentication; no per-frame request loop. */
    fun request(authenticated: Boolean): JSONObject? {
        if (!authenticated || !capable || sentMode == preferredMode) return null
        sentMode = preferredMode; awaitingConfirmation = true
        return JSONObject().put("type", "stream_settings").put("version", 1).put("mode", preferredMode)
    }

    companion object {
        fun validMode(value: Any?): String? = (value as? String)?.takeIf { it == "auto" || it == "latency" || it == "quality" }
        fun title(mode: String): String = when (mode) { "latency" -> "Resposta rápida"; "quality" -> "Mais nitidez"; else -> "Automático" }
    }
}
