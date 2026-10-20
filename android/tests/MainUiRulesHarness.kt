package br.com.perabru.minianydesk

/** Calls the production MainUiRules object, compiled together with the real Activity.
 * Android's SDK jar supplies framework types only; no Android method is invoked.
 */
object MainUiRulesHarness {
    private class Store : ComputerBook.Store {
        private var value: String? = null
        override fun read() = value
        override fun write(value: String?): Boolean { this.value = value; return true }
    }

    @JvmStatic fun main(args: Array<String>) {
        var cases = 0
        fun test(name: String, run: () -> Unit) { run(); cases++; println("PASS $name") }
        val book = ComputerBook(Store())
        val first = book.add("Laboratório São Paulo", "lab.example", 64321)
        val ipv4 = book.add("Recepção", "192.168.10.50", 5905)
        val ipv6 = book.add("Servidor IPv6", "[2001:db8::1]", 65000)

        test("saved names match case accents and trimmed queries") {
            check(MainUiRules.matchesComputerQuery(first, "  LABORATORIO sao  "))
            check(MainUiRules.matchesComputerQuery(first, "São Paulo"))
            check(MainUiRules.matchesComputerQuery(first, ""))
            check(MainUiRules.matchesComputerQuery(first, "   "))
            check(!MainUiRules.matchesComputerQuery(first, "Recepção"))
        }
        test("DNS IPv4 IPv6 and port queries select the intended saved endpoint") {
            check(MainUiRules.matchesComputerQuery(first, "LAB.EXAMPLE"))
            check(MainUiRules.matchesComputerQuery(ipv4, "192.168.10.50"))
            check(MainUiRules.matchesComputerQuery(ipv4, "5905"))
            check(MainUiRules.matchesComputerQuery(ipv6, "2001:db8::1"))
            check(MainUiRules.matchesComputerQuery(ipv6, "65000"))
            check(!MainUiRules.matchesComputerQuery(ipv4, "65000"))
        }
        test("presence timestamps preserve aliases while a renamed saved computer changes search") {
            val before = book.entries()
            val seen = book.markSeen(first.id, 1_791_628_200_000)
            check(MainUiRules.matchesComputerQuery(seen, "laboratorio"))
            check(seen.id == first.id && seen.name == first.name)
            val renamed = book.update(first.id, "Computador do professor", first.address, first.port)
            val snapshot = book.entries()
            val selected = snapshot.filter { MainUiRules.matchesComputerQuery(it, "professor") }
            check(selected == listOf(renamed))
            check(!MainUiRules.matchesComputerQuery(renamed, "laboratorio"))
            check(book.entries() == snapshot && before.size == snapshot.size)
            check(snapshot.filter { MainUiRules.matchesComputerQuery(it, "unknown-device") }.isEmpty())
        }
        test("a new transfer opens only the visible Controller session") {
            check(MainUiRules.shouldOpenTransferPanel(false, true, true, "session", false))
            check(!MainUiRules.shouldOpenTransferPanel(false, false, true, "session", false))
            check(!MainUiRules.shouldOpenTransferPanel(false, true, true, "computers", false))
            check(!MainUiRules.shouldOpenTransferPanel(false, true, true, "connect", false))
        }
        test("transfer progress and fullscreen preserve the user's closed panel") {
            check(!MainUiRules.shouldOpenTransferPanel(true, true, true, "session", false))
            check(!MainUiRules.shouldOpenTransferPanel(false, true, true, "session", true))
            check(!MainUiRules.shouldOpenTransferPanel(false, true, false, "session", false))
            check(!MainUiRules.shouldOpenTransferPanel(true, false, true, "session", false))
        }
        println("$cases Main UI rules checks passed")
    }
}
