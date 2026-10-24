package br.com.perabru.minianydesk

import kotlin.math.abs

/** Deterministic production rates and hide/recovery rules; no Android rendering claim. */
object SessionPresentationHarness {
    @JvmStatic fun main(args: Array<String>) {
        var cases = 0
        fun test(name: String, run: () -> Unit) { run(); cases++; println("PASS $name") }
        fun close(actual: Double, expected: Double) { check(abs(actual - expected) < 0.000001) { "$actual != $expected" } }
        test("measured UTF8/framing counters become independent decimal megabits per second") {
            val window = SessionTrafficWindow()
            window.reset(0)
            val rate = window.sample(1_000, 125_000, 250_000)
            close(rate.receivedMbps, 1.0); close(rate.sentMbps, 2.0)
            check(rate.compactLabel() == "Sessão ↓ 1,00 · ↑ 2,00 Mb/s")
        }
        test("rolling three second traffic window decays completely during idle") {
            val window = SessionTrafficWindow()
            window.reset(0)
            close(window.sample(1_000, 375_000, 0).receivedMbps, 3.0)
            close(window.sample(2_000, 375_000, 0).receivedMbps, 1.5)
            close(window.sample(3_000, 375_000, 0).receivedMbps, 1.0)
            close(window.sample(4_000, 375_000, 0).receivedMbps, 0.0)
            close(window.sample(30_000, 375_000, 0).receivedMbps, 0.0)
        }
        test("irregular sampling weights the oldest interval rather than counting a whole stale burst") {
            val window = SessionTrafficWindow()
            window.reset(0)
            window.sample(2_000, 500_000, 125_000)
            val rate = window.sample(4_000, 500_000, 125_000)
            close(rate.receivedMbps, 2.0 / 3.0); close(rate.sentMbps, 1.0 / 6.0)
            close(window.sample(5_000, 500_000, 125_000).receivedMbps, 0.0)
        }
        test("same instant samples do not divide by zero or discard bytes") {
            val window = SessionTrafficWindow()
            window.reset(100)
            check(window.sample(100, 125_000, 0) == SessionTrafficRate.ZERO)
            close(window.sample(1_100, 125_000, 0).receivedMbps, 1.0)
        }
        test("reconnect resume clock regression and counter reset establish fresh baselines") {
            val window = SessionTrafficWindow()
            window.reset(0); window.sample(1_000, 125_000, 500_000)
            window.reset(2_000, 9_000_000, 6_000_000)
            check(window.sample(3_000, 9_000_000, 6_000_000) == SessionTrafficRate.ZERO)
            check(window.sample(4_000, 0, 0) == SessionTrafficRate.ZERO)
            close(window.sample(5_000, 125_000, 0).receivedMbps, 1.0)
            check(window.sample(10, 500_000, 0) == SessionTrafficRate.ZERO)
            check(window.sample(1_010, 500_000, 0) == SessionTrafficRate.ZERO)
        }
        test("two connected PCs retain separate traffic histories") {
            val first = SessionTrafficWindow(); val second = SessionTrafficWindow()
            first.reset(0); second.reset(0)
            close(first.sample(1_000, 125_000, 0).receivedMbps, 1.0)
            close(second.sample(1_000, 625_000, 0).receivedMbps, 5.0)
            first.reset(1_500)
            close(second.sample(2_000, 1_250_000, 0).receivedMbps, 5.0)
        }
        test("hiding requires fullscreen selected and currently controllable session") {
            for (fullscreen in listOf(false, true)) for (selected in listOf(false, true)) for (ready in listOf(false, true)) {
                val visibility = SessionCommandsVisibility()
                check(visibility.hide(fullscreen, selected, ready) == (fullscreen && selected && ready))
                check(visibility.hidden == (fullscreen && selected && ready))
            }
        }
        test("lost eligibility restores commands and does not automatically hide them again") {
            for ((fullscreen, selected, ready) in listOf(Triple(false, true, true), Triple(true, false, true), Triple(true, true, false))) {
                val visibility = SessionCommandsVisibility()
                check(visibility.hide(true, true, true))
                visibility.reconcile(fullscreen, selected, ready)
                check(!visibility.hidden)
                visibility.reconcile(true, true, true)
                check(!visibility.hidden)
            }
        }
        test("explicit recovery restores only the chosen session and permits another deliberate hide") {
            val first = SessionCommandsVisibility(); val second = SessionCommandsVisibility()
            first.hide(true, true, true); second.hide(true, true, true)
            first.restore(); check(!first.hidden && second.hidden)
            first.reconcile(true, true, true); check(!first.hidden)
            first.hide(true, true, true); check(first.hidden)
            first.restore(); check(!first.hidden)
        }
        println("$cases session traffic and commands visibility checks passed")
    }
}
