package br.com.perabru.minianydesk

import java.util.ArrayDeque
import java.util.Locale

/** Observed connection traffic, never a speed test or available network capacity. */
internal data class SessionTrafficRate(val receivedMbps: Double, val sentMbps: Double) {
    fun compactLabel(): String = String.format(Locale.forLanguageTag("pt-BR"),
        "Sessão ↓ %.2f · ↑ %.2f Mb/s", receivedMbps, sentMbps)

    companion object { val ZERO = SessionTrafficRate(0.0, 0.0) }
}

/** Main-thread sampler of cumulative, per-Peer byte counters over a rolling 3s window.
 * Linear interpolation only estimates the part of the oldest sampled interval inside
 * the window. Clock/counter resets establish a fresh baseline rather than a rate spike. */
internal class SessionTrafficWindow(private val windowMs: Long = 3_000) {
    private data class Sample(val at: Long, val received: Long, val sent: Long)
    private val samples = ArrayDeque<Sample>()

    init { require(windowMs > 0) }

    fun reset(now: Long, received: Long = 0, sent: Long = 0) {
        samples.clear()
        samples.addLast(Sample(now, received.coerceAtLeast(0), sent.coerceAtLeast(0)))
    }

    fun sample(now: Long, received: Long, sent: Long): SessionTrafficRate {
        val previous = samples.peekLast()
        if (previous == null || now < previous.at || received < previous.received || sent < previous.sent ||
            received < 0 || sent < 0) {
            reset(now, received, sent)
            return SessionTrafficRate.ZERO
        }
        // No elapsed time means no measured rate; retain the older baseline so
        // bytes observed by two UI updates in one millisecond are not lost.
        if (now == previous.at) return SessionTrafficRate.ZERO
        samples.addLast(Sample(now, received, sent))
        val cutoff = now - windowMs
        while (samples.size > 2 && samples.elementAt(1).at <= cutoff) samples.removeFirst()
        val first = samples.first()
        val second = samples.elementAt(1)
        val startedAt = maxOf(first.at, cutoff)
        val fraction = if (first.at < cutoff)
            (cutoff - first.at).toDouble() / (second.at - first.at).toDouble() else 0.0
        fun rate(total: Long, a: Long, b: Long): Double {
            val baseline = a.toDouble() + (b - a).toDouble() * fraction
            return ((total.toDouble() - baseline).coerceAtLeast(0.0) * 8.0 /
                (now - startedAt).toDouble() / 1_000.0)
        }
        return SessionTrafficRate(rate(received, first.received, second.received),
            rate(sent, first.sent, second.sent))
    }
}

/** Session-local visibility, with an explicit recovery path for every hidden state. */
internal class SessionCommandsVisibility {
    var hidden: Boolean = false
        private set

    fun hide(fullscreen: Boolean, selected: Boolean, inputReady: Boolean): Boolean {
        hidden = fullscreen && selected && inputReady
        return hidden
    }

    fun restore() { hidden = false }

    fun reconcile(fullscreen: Boolean, selected: Boolean, inputReady: Boolean) {
        if (!fullscreen || !selected || !inputReady) restore()
    }
}
