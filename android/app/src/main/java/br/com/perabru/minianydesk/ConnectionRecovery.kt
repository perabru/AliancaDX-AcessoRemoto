package br.com.perabru.minianydesk

import java.util.Locale

/** Only recover identifiable transport loss within one fixed two-minute window. */
object ConnectionRecovery {
    const val WINDOW_MS = 120_000L
    private val delays = longArrayOf(2_000, 4_000, 8_000, 15_000, 30_000)

    fun retryable(reason: String): Boolean {
        val text = reason.lowercase(Locale.ROOT)
        // A terminal/intentional reason always wins over an incidental socket
        // error in the same message. An unclassified EOF is never retried.
        if (listOf("session_end", "não autoriz", "não aprovad", "negad", "recus", "rejeitad", "denied", "rejected", "unauthor", "senha", "password",
                "certific", "identidade", "hospedeiro parou", "host parou", "encerrada pelo",
                "encerrado pelo", "cancelad").any { it in text }) return false
        return listOf("sem resposta do outro dispositivo", "envio sem progresso", "connection reset",
            "connection aborted", "connection abort", "broken pipe", "timed out", "timeout",
            "network is unreachable", "no route to host", "econnreset", "econnaborted", "epipe",
            "enetunreach", "ehostunreach", "etimedout", "conexão redefinida", "conexão foi forçada",
            "host ainda não foi encontrado", "pc ainda não foi encontrado").any { it in text }
    }

    fun delayMs(attempt: Int): Long = delays[attempt.coerceIn(0, delays.lastIndex)]

    fun deadline(now: Long): Long = if (now > Long.MAX_VALUE - WINDOW_MS) Long.MAX_VALUE else now + WINDOW_MS

    fun remaining(now: Long, deadline: Long): Long = when {
        deadline <= now -> 0
        now < 0 && deadline > Long.MAX_VALUE + now -> Long.MAX_VALUE
        else -> deadline - now
    }
}
